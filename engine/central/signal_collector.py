"""Collect entity-level daily buy signals for central-controller backtests."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from engine.central.entity_loader import EntityRecord
from engine.core.indicators import calc_indicators
from engine.learning.backtest import _lookup_signal_context, _precompute_topic_feature_map
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook

DEFAULT_SELL_OMEN_SCORE_PATH = Path("data/_system/ml_sell_omen/sell_omen_scores_lr8d85.csv")
SELL_OMEN_COLUMNS = ("sell_omen_score", "sell_omen_model_train_end", "sell_omen_score_year")


@dataclass(frozen=True)
class SignalSnapshot:
    entity_id: str
    ticker: str
    date: str
    should_buy: bool
    score: float
    raw_score: float
    threshold: float
    strength: float
    components: dict
    reasons: list
    market_adjustment: float
    price: float
    confidence: float


class CacheOnlyDataProvider:
    """Read-only provider that refuses to download market data."""

    def __init__(
        self,
        *,
        cache_roots: Optional[Iterable[str | Path]] = None,
        system_dir: str | Path = "data/_system",
        recompute_indicators: bool = True,
        sell_omen_score_path: Optional[str | Path] = DEFAULT_SELL_OMEN_SCORE_PATH,
    ) -> None:
        self.cache_roots = [Path(p) for p in (cache_roots or ["data/_system/research"])]
        self.system_dir = Path(system_dir)
        self.recompute_indicators = bool(recompute_indicators)
        self.sell_omen_score_path = Path(sell_omen_score_path) if sell_omen_score_path else None
        self.indicator_fallback_warnings: list[str] = []
        self.sell_omen_warnings: list[str] = []
        self.sell_omen_guard_violations: int = 0
        self.sell_omen_missing_tickers: set[str] = set()
        self.sell_omen_loaded_rows: int = 0
        self._price_cache: dict[str, pd.DataFrame] = {}
        self._market_history: Optional[pd.DataFrame] = None
        self._sentiment_cache: dict[str, dict] = {}
        self._sell_omen_score_table: Optional[pd.DataFrame] = None
        self._sell_omen_score_table_loaded: bool = False

    def load_price_df(self, ticker: str) -> pd.DataFrame:
        ticker_u = str(ticker or "").upper()
        if ticker_u in self._price_cache:
            return self._price_cache[ticker_u]
        path = self._find_price_cache(ticker_u)
        if path is None:
            raise FileNotFoundError(f"cache-only OHLCV not found for {ticker_u}")
        df = _normalize_price_df(_read_df(path))
        if self.recompute_indicators and _has_raw_ohlcv(df):
            df = calc_indicators(df[["Open", "High", "Low", "Close", "Volume"]].copy())
        else:
            if self.recompute_indicators and not _has_raw_ohlcv(df):
                self.indicator_fallback_warnings.append(f"{ticker_u}: raw OHLCV columns missing; using cached indicators")
            missing = [c for c in ("MA5", "MA20", "MACD", "RSI", "ATR") if c not in df.columns]
            if missing and _has_raw_ohlcv(df):
                df = calc_indicators(df[["Open", "High", "Low", "Close", "Volume"]].copy())
            elif missing:
                raise ValueError(f"{ticker_u}: missing indicator columns and raw OHLCV unavailable: {missing}")
        df = self._attach_sell_omen_scores(ticker_u, df)
        self._price_cache[ticker_u] = df
        return df

    def load_market_history(self) -> Optional[pd.DataFrame]:
        if self._market_history is not None:
            return self._market_history
        for name in ("market_history_v2.csv", "market_history.csv"):
            path = self.system_dir / name
            if path.exists():
                df = pd.read_csv(path)
                date_col = "Date" if "Date" in df.columns else ("date" if "date" in df.columns else None)
                if date_col:
                    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                self._market_history = df
                return df
        self._market_history = None
        return None

    def load_ticker_sentiment(self, ticker: str) -> dict:
        ticker_u = str(ticker or "").upper()
        if ticker_u in self._sentiment_cache:
            return self._sentiment_cache[ticker_u]
        path = self.system_dir / "sentiment_daily.csv"
        if not path.exists():
            self._sentiment_cache[ticker_u] = {}
            return {}
        try:
            df = pd.read_csv(path)
        except Exception:
            self._sentiment_cache[ticker_u] = {}
            return {}
        ticker_col = "ticker" if "ticker" in df.columns else ("symbol" if "symbol" in df.columns else None)
        date_col = "Date" if "Date" in df.columns else ("date" if "date" in df.columns else None)
        if date_col is None:
            self._sentiment_cache[ticker_u] = {}
            return {}
        if ticker_col:
            df = df[df[ticker_col].astype(str).str.upper() == ticker_u]
        out = {}
        for _, row in df.iterrows():
            try:
                key = pd.Timestamp(row[date_col]).strftime("%Y-%m-%d")
            except Exception:
                continue
            out[key] = row.to_dict()
        self._sentiment_cache[ticker_u] = out
        return out

    def _find_price_cache(self, ticker: str) -> Optional[Path]:
        patterns = [f"**/ohlcv_cache/{ticker}.pkl", f"**/ohlcv_cache/{ticker}.parquet", f"**/ohlcv_cache/{ticker}.csv"]
        found: list[Path] = []
        for root in self.cache_roots:
            for pattern in patterns:
                found.extend(root.glob(pattern))
        if not found:
            return None
        return sorted(found, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)[0]

    def _load_sell_omen_score_table(self) -> Optional[pd.DataFrame]:
        if self._sell_omen_score_table_loaded:
            return self._sell_omen_score_table
        self._sell_omen_score_table_loaded = True
        path = self.sell_omen_score_path
        if path is None:
            self.sell_omen_warnings.append("sell_omen_score_path disabled")
            self._sell_omen_score_table = None
            return None
        if not path.exists():
            self.sell_omen_warnings.append(f"sell omen score table missing: {path}")
            self._sell_omen_score_table = None
            return None
        try:
            table = pd.read_csv(path)
        except Exception as exc:
            self.sell_omen_warnings.append(f"sell omen score table read failed: {path}: {exc}")
            self._sell_omen_score_table = None
            return None
        required = {"ticker", "Date", "sell_omen_score", "model_train_end"}
        missing = sorted(required - set(table.columns))
        if missing:
            self.sell_omen_warnings.append(f"sell omen score table missing columns: {missing}")
            self._sell_omen_score_table = None
            return None
        out = table.copy()
        out["ticker"] = out["ticker"].astype(str).str.upper()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
        out["model_train_end"] = pd.to_datetime(out["model_train_end"], errors="coerce").dt.normalize()
        out["sell_omen_score"] = pd.to_numeric(out["sell_omen_score"], errors="coerce")
        if "score_year" not in out.columns:
            out["score_year"] = pd.NA
        out = out.dropna(subset=["ticker", "Date"])
        self.sell_omen_loaded_rows = int(len(out))
        self._sell_omen_score_table = out
        return self._sell_omen_score_table

    def _attach_sell_omen_scores(self, ticker: str, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        _ensure_sell_omen_columns(out)
        table = self._load_sell_omen_score_table()
        if table is None or table.empty:
            self.sell_omen_missing_tickers.add(str(ticker or "").upper())
            return out
        ticker_u = str(ticker or "").upper()
        sub = table[table["ticker"] == ticker_u].copy()
        if sub.empty:
            self.sell_omen_missing_tickers.add(ticker_u)
            return out
        sub = sub.drop_duplicates(subset=["Date"], keep="last").set_index("Date").sort_index()
        dates = pd.Series(pd.to_datetime(out.index, errors="coerce").normalize(), index=out.index)
        score = dates.map(sub["sell_omen_score"])
        model_train_end = pd.to_datetime(dates.map(sub["model_train_end"]), errors="coerce")
        score_year = dates.map(sub["score_year"])
        violations = model_train_end.notna() & dates.notna() & (model_train_end >= dates)
        violation_count = int(violations.sum())
        if violation_count:
            self.sell_omen_guard_violations += violation_count
        out["sell_omen_score"] = pd.to_numeric(score, errors="coerce")
        out.loc[violations, "sell_omen_score"] = pd.NA
        out["sell_omen_model_train_end"] = model_train_end.dt.strftime("%Y-%m-%d")
        out["sell_omen_score_year"] = score_year
        return out


class SignalCollector:
    def __init__(self, data_provider: CacheOnlyDataProvider, *, use_llm_events: bool = False) -> None:
        self.data_provider = data_provider
        self.use_llm_events = bool(use_llm_events)
        self._rb_cache: dict[str, Rulebook] = {}
        self._topic_cache: dict[tuple[str, int], dict] = {}

    def signal_for_date(self, entity: EntityRecord, date) -> Optional[SignalSnapshot]:
        df = self.data_provider.load_price_df(entity.ticker)
        idx = _index_for_date(df, date)
        if idx is None:
            return None
        rb = self._rulebook(entity)
        ticker_sentiment = self.data_provider.load_ticker_sentiment(entity.ticker)
        topic_map = self._topic_features(entity, ticker_sentiment)
        market_history = self.data_provider.load_market_history()
        market, sector, vix, sentiment, event_flags, topic_features = _lookup_signal_context(
            df=df,
            idx=idx,
            market_score=50.0,
            sector_score=50.0,
            vix_level=18.0,
            market_history_df=market_history,
            sector_name=str(getattr(rb, "sector_name", "tech") or "tech"),
            ticker_sentiment=ticker_sentiment,
            topic_feature_map=topic_map,
            use_llm_events=self.use_llm_events,
        )
        sig = evaluate_signal(
            rb,
            df.iloc[: idx + 1],
            market_score=market,
            sector_score=sector,
            vix_level=vix,
            news_sentiment=sentiment,
            event_flags=event_flags,
            topic_features=topic_features,
        )
        threshold = float(getattr(sig, "threshold", 0.0) or 0.0)
        score = float(getattr(sig, "score", 0.0) or 0.0)
        row = df.iloc[idx]
        price = float(row.get("Close", row.get("close", 0.0)) or 0.0)
        return SignalSnapshot(
            entity_id=entity.entity_id,
            ticker=entity.ticker,
            date=pd.Timestamp(df.index[idx]).strftime("%Y-%m-%d"),
            should_buy=bool(getattr(sig, "should_buy", False)),
            score=score,
            raw_score=float(getattr(sig, "raw_score", 0.0) or 0.0),
            threshold=threshold,
            strength=(score / threshold if threshold > 0 else 0.0),
            components=dict(getattr(sig, "components", {}) or {}),
            reasons=list(getattr(sig, "reasons", []) or []),
            market_adjustment=float(getattr(sig, "market_adjustment", 0.0) or 0.0),
            price=price,
            confidence=float(entity.confidence or 0.0),
        )

    def collect(self, entities: Iterable[EntityRecord], date) -> list[SignalSnapshot]:
        out: list[SignalSnapshot] = []
        for entity in entities:
            snap = self.signal_for_date(entity, date)
            if snap is not None and snap.should_buy:
                out.append(snap)
        return out

    def _rulebook(self, entity: EntityRecord) -> Rulebook:
        key = entity.entity_id
        if key not in self._rb_cache:
            self._rb_cache[key] = Rulebook.from_dict(entity.rulebook)
        return self._rb_cache[key]

    def _topic_features(self, entity: EntityRecord, ticker_sentiment: dict) -> dict:
        rb = self._rulebook(entity)
        try:
            window = int(getattr(rb, "news_zscore_window", 60) or 60)
        except Exception:
            window = 60
        key = (entity.entity_id, window)
        if key not in self._topic_cache:
            self._topic_cache[key] = _precompute_topic_feature_map(ticker_sentiment, window)
        return self._topic_cache[key]


def _read_df(path: Path) -> pd.DataFrame:
    if path.suffix == ".pkl":
        return pd.read_pickle(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _normalize_price_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Date" in out.columns:
        out.index = pd.to_datetime(out["Date"], errors="coerce")
    elif "date" in out.columns:
        out.index = pd.to_datetime(out["date"], errors="coerce")
    else:
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    return out


def _has_raw_ohlcv(df: pd.DataFrame) -> bool:
    return {"Open", "High", "Low", "Close", "Volume"}.issubset(set(df.columns))


def _ensure_sell_omen_columns(df: pd.DataFrame) -> None:
    for col in SELL_OMEN_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA


def _index_for_date(df: pd.DataFrame, date) -> Optional[int]:
    ts = pd.Timestamp(date).normalize()
    idx = df.index.normalize().get_indexer([ts], method=None)
    if len(idx) and idx[0] >= 0:
        return int(idx[0])
    return None
