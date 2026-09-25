"""
LearnedRuleBook - 학습된 Rulebook + MarketContext 통합 라이브 룰북.

PositionManager 연동 캐시:
- 가장 최근 ATR / Rulebook
- 매수 신호 평가에 실제 사용한 ticker별 MarketContext
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from engine.core.feature_lag import DEFAULT_LAG_DAYS, DEFAULT_MAX_AGE_DAYS, lookup_lagged_daily_dict
from engine.core.indicators import calc_indicators
from engine.live.event_policy import append_shadow_direct_event_log, live_event_flags
from engine.market.context import MarketContext, get_market_context
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.demo_rulebook import RuleBook, Signal, SignalResult
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.news_features import precompute_topic_features
from engine.strategies.rulebook import Rulebook as LearnedRule

log = logging.getLogger("learned_rulebook")

SYMBOLS_DIR = Path("data/symbols")
SEED_PATTERNS_PATH = Path("data/_system/seed_patterns.json")
OHLCV_CACHE_TTL_SEC = 600


class LearnedRuleBook(RuleBook):
    """학습된 룰북 + 시장 컨텍스트 기반 라이브 룰북."""

    def __init__(
        self,
        ohlcv_lookback_years: int = 1,
        ohlcv_cache_ttl_sec: int = OHLCV_CACHE_TTL_SEC,
    ):
        self.lookback_years = ohlcv_lookback_years
        self.cache_ttl = ohlcv_cache_ttl_sec
        self._rulebook_cache: Dict[str, LearnedRule] = {}
        self._adapter_cache: Dict[str, object] = {}
        self._ohlcv_cache: Dict[str, tuple[pd.DataFrame, float]] = {}
        self._sentiment_cache: Dict[str, tuple[dict, float]] = {}
        self._topic_feature_cache: Dict[tuple[str, int, int, str], tuple[dict, float]] = {}
        self._last_atr: dict[str, float] = {}
        self._rulebook_by_ticker: dict[str, LearnedRule] = {}
        self._last_market_context: dict[str, dict] = {}
        log.info(
            f"LearnedRuleBook 초기화: lookback={ohlcv_lookback_years}y, "
            f"cache_ttl={ohlcv_cache_ttl_sec}s"
        )

    def name(self) -> str:
        return "LearnedRuleBook(parameters.json + MarketContext)"

    def _load_rulebook(self, ticker: str) -> Optional[LearnedRule]:
        """ticker별 학습 룰북 로드: parameters.json → seed_patterns → None."""
        if ticker in self._rulebook_cache:
            return self._rulebook_cache[ticker]

        params_path = SYMBOLS_DIR / ticker / "parameters.json"
        if params_path.exists():
            try:
                with open(params_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rb_dict = data.get("rulebook")
                if rb_dict:
                    rb = LearnedRule.from_dict(rb_dict)
                    self._rulebook_cache[ticker] = rb
                    log.info(
                        f"{ticker} 룰북 로드 (parameters.json): "
                        f"win_rate={rb.win_rate:.1f}%, fitness={rb.fitness:.2f}"
                    )
                    return rb
            except Exception as e:
                log.warning(f"{ticker} parameters.json 로드 실패: {e}")

        if SEED_PATTERNS_PATH.exists():
            try:
                with open(SEED_PATTERNS_PATH, "r", encoding="utf-8") as f:
                    seeds = json.load(f)
                for direction in ("long", "short"):
                    for seed in seeds.get(direction, []):
                        rb_dict = seed.get("rulebook", {})
                        if rb_dict.get("ticker") == ticker:
                            rb = LearnedRule.from_dict(rb_dict)
                            self._rulebook_cache[ticker] = rb
                            log.info(f"{ticker} 룰북 로드 (seed_patterns.json): fitness={rb.fitness:.2f}")
                            return rb
            except Exception as e:
                log.warning(f"seed_patterns.json 로드 실패: {e}")

        log.warning(f"{ticker} 학습된 룰북 없음 - HOLD로 처리")
        self._rulebook_cache[ticker] = None
        return None

    def _get_ohlcv(self, ticker: str) -> Optional[pd.DataFrame]:
        """ticker 최근 OHLCV+지표 DataFrame. 캐시 10분."""
        now = time.time()
        if ticker in self._ohlcv_cache:
            df, ts = self._ohlcv_cache[ticker]
            if now - ts < self.cache_ttl:
                return df

        try:
            # Lazy import prevents KRX/KIS adapter side effects during US-only startup.
            from engine.adapters.factory import get_adapter

            adapter = self._adapter_cache.get(ticker)
            if adapter is None:
                adapter = get_adapter(ticker)
                self._adapter_cache[ticker] = adapter
            df = adapter.load_history(years=self.lookback_years)
            if df is None or df.empty or len(df) < 60:
                log.warning(f"{ticker} OHLCV 부족: {0 if df is None else len(df)}봉")
                return None
            df = calc_indicators(df)
            self._ohlcv_cache[ticker] = (df, now)
            log.info(f"{ticker} OHLCV 로드: {len(df)}봉 (캐시 갱신)")
            return df
        except Exception as e:
            log.error(f"{ticker} OHLCV 조달 실패: {e}")
            return None

    def _load_ticker_sentiment(self, ticker: str) -> dict:
        """Load ticker_sentiment daily CSV with the same source used by backtests."""
        now = time.time()
        cached = self._sentiment_cache.get(ticker)
        if cached is not None:
            data, ts = cached
            if now - ts < self.cache_ttl:
                return data
        try:
            data = load_ticker_sentiment(ticker) or {}
            if not isinstance(data, dict):
                data = {}
        except Exception as exc:
            log.warning(f"{ticker} ticker_sentiment 로드 실패: {exc}")
            data = {}
        self._sentiment_cache[ticker] = (data, now)
        return data

    def _news_zscore_window(self, rb: LearnedRule) -> int:
        try:
            window = int(getattr(rb, "news_zscore_window", 60) or 60)
        except Exception:
            window = 60
        return max(1, min(window, 252))

    def _precompute_topic_feature_map(self, ticker: str, sentiment: dict, window: int) -> dict:
        """Precompute topic features from ticker_sentiment and cache briefly."""
        if not isinstance(sentiment, dict) or not sentiment:
            return {}
        latest_key = ""
        try:
            latest_key = max(str(k)[:10] for k in sentiment.keys())
        except Exception:
            latest_key = ""
        cache_key = (ticker, int(window), len(sentiment), latest_key)
        now = time.time()
        cached = self._topic_feature_cache.get(cache_key)
        if cached is not None:
            features, ts = cached
            if now - ts < self.cache_ttl:
                return features
        try:
            features = precompute_topic_features(sentiment, int(window))
            if not isinstance(features, dict):
                features = {}
        except Exception as exc:
            log.warning(f"{ticker} topic_features 계산 실패: {exc}")
            features = {}
        self._topic_feature_cache[cache_key] = (features, now)
        return features

    def _signal_date(self, df: pd.DataFrame) -> Any:
        """Return the date key used for lagged live feature lookup.

        Backtests use the OHLCV row date. Live mirrors that policy by preferring
        the latest row's date column when present, otherwise the index.
        """
        try:
            if "Date" in df.columns:
                return df["Date"].iloc[-1]
            if "date" in df.columns:
                return df["date"].iloc[-1]
            return df.index[-1]
        except Exception:
            return None

    def _lookup_lagged_news_context(self, ticker: str, rb: LearnedRule, signal_date: Any) -> tuple[float, dict, str]:
        """Return backtest-aligned global sentiment and topic features for live.

        Policy is intentionally identical to engine.learning.backtest:
        D-day signal may use newest ticker_sentiment row at or before D-1, and
        stale rows older than DEFAULT_MAX_AGE_DAYS are ignored.
        """
        sentiment = self._load_ticker_sentiment(ticker)
        if not sentiment:
            return 0.0, {}, "ticker_sentiment_missing"

        row = lookup_lagged_daily_dict(
            sentiment,
            signal_date,
            lag_days=DEFAULT_LAG_DAYS,
            max_age_days=DEFAULT_MAX_AGE_DAYS,
        )
        try:
            news_sentiment = float(row.get("sentiment_avg", 0.0)) if row else 0.0
        except Exception:
            news_sentiment = 0.0

        window = self._news_zscore_window(rb)
        topic_map = self._precompute_topic_feature_map(ticker, sentiment, window)
        topic_features = lookup_lagged_daily_dict(
            topic_map,
            signal_date,
            lag_days=DEFAULT_LAG_DAYS,
            max_age_days=DEFAULT_MAX_AGE_DAYS,
        ) if topic_map else {}
        if not isinstance(topic_features, dict):
            topic_features = {}
        note = (
            f"lag={DEFAULT_LAG_DAYS} max_age={DEFAULT_MAX_AGE_DAYS} "
            f"sent={'yes' if row else 'no'} topic_n={len(topic_features)} window={window}"
        )
        return news_sentiment, dict(topic_features), note

    def evaluate(self, ticker: str, price: float, df=None) -> SignalResult:
        rb = self._load_rulebook(ticker)
        if rb is None:
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason="학습된 룰북 없음")

        if df is None:
            df = self._get_ohlcv(ticker)
        try:
            if df is not None and "ATR" in df.columns and len(df) > 0:
                self._last_atr[ticker] = float(df["ATR"].iloc[-1])
            self._rulebook_by_ticker[ticker] = rb
        except Exception as exc:
            log.warning(f"{ticker} ATR 캐시 실패: {exc}")

        if df is None or len(df) < 60:
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason="OHLCV 데이터 부족")

        try:
            ctx: MarketContext = get_market_context()
        except Exception as e:
            log.warning(f"MarketContext 로드 실패, 중립 사용: {e}")
            ctx = None

        if ctx is not None:
            market_score = float(ctx.score)
            sector_score = float(ctx.sector_strength.get(rb.sector_name, 50.0))
            vix_level = float(ctx.vix_level)
            context_timestamp = str(ctx.timestamp)
        else:
            market_score, sector_score, vix_level = 50.0, 50.0, 18.0
            context_timestamp = ""

        self._last_market_context[ticker] = {
            "score": market_score,
            "market_score": market_score,
            "vix_level": vix_level,
            "sector_score": sector_score,
            "sector_strength": {str(rb.sector_name or ""): sector_score},
            "timestamp": context_timestamp,
        }

        signal_date = self._signal_date(df)
        news_normalized, topic_features, news_note = self._lookup_lagged_news_context(ticker, rb, signal_date)
        log.info(
            f"{ticker} 뉴스 context(backtest-aligned): sentiment_avg={news_normalized:+.3f}, "
            f"topic_features={len(topic_features)} ({news_note})"
        )

        try:
            event_flags = live_event_flags(ctx)
            res = evaluate_signal(
                rb=rb,
                df=df,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=news_normalized,
                event_flags=event_flags,
                topic_features=topic_features,
            )
        except Exception as e:
            log.error(f"{ticker} evaluate_signal 실패: {e}")
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"evaluate 예외: {e}")

        try:
            shadow_off = evaluate_signal(
                rb=rb,
                df=df,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=news_normalized,
                event_flags=live_event_flags(ctx, enabled_override=False),
                topic_features=topic_features,
            )
            append_shadow_direct_event_log(
                candidate_id=f"learned:{ticker}",
                mode="runner",
                path="engine.strategies.learned_rulebook.evaluate",
                market_score_on=market_score,
                market_score_off=market_score,
                result_on=res,
                result_off=shadow_off,
            )
        except Exception as shadow_exc:
            log.warning("%s direct Event shadow compare skipped: %s", ticker, shadow_exc)

        news_tag = ""
        if abs(news_normalized) >= 0.1:
            kind = "호재" if news_normalized > 0 else "악재"
            news_tag = f" 뉴스{kind}({news_normalized:+.2f})"
        topic_tag = f" topic_n={len(topic_features)}" if topic_features else ""
        reason_str = (
            f"score={res.score:.2f}/threshold={res.threshold:.2f} "
            f"raw={res.raw_score:.2f} mkt_adj×{res.market_adjustment:.2f}{news_tag}{topic_tag} "
            f"reasons=[{', '.join(res.reasons[:4])}]"
        )
        signal_kwargs = {
            "score": float(res.score),
            "raw_score": float(res.raw_score),
            "threshold": float(res.threshold),
            "market_adjustment": float(res.market_adjustment),
            "reasons": list(res.reasons),
        }
        if res.should_buy:
            return SignalResult(ticker=ticker, signal=Signal.BUY, price=price, reason=f"[{rb.direction}] {reason_str}", **signal_kwargs)
        return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"미달({rb.direction}) {reason_str}", **signal_kwargs)

    def get_last_atr(self, ticker: str):
        return self._last_atr.get(ticker)

    def get_rulebook(self, ticker: str):
        return self._rulebook_by_ticker.get(ticker)

    def get_last_market_context(self, ticker: str):
        ctx = self._last_market_context.get(ticker)
        return dict(ctx) if isinstance(ctx, dict) else None


if __name__ == "__main__":
    print("LearnedRuleBook smoke")
    rb = LearnedRuleBook()
    print(rb.name())
