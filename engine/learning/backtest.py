"""
백테스트 실행 모듈
- 룰북 + OHLCV → 전체 기간 진입/청산 시뮬레이션
- 시점별 시장 컨텍스트 시계열 지원 (market_history_df)
- 결과 요약: 승률, 기대값, MDD, Profit Factor, Sharpe-like, fitness
"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import numpy as np
import pandas as pd

from engine.core.feature_lag import (
    DEFAULT_LAG_DAYS,
    DEFAULT_MAX_AGE_DAYS,
    lookup_lagged_daily_dict,
    lookup_market_at_lagged,
)
from engine.core.logger import get_logger
from engine.strategies.rulebook import Rulebook
from engine.strategies.evaluator import evaluate_signal, calc_position_size_krw
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.news_features import precompute_topic_features

log = get_logger("backtest")

FEATURE_LAG_DAYS = DEFAULT_LAG_DAYS
FEATURE_LAG_MAX_AGE_DAYS = DEFAULT_MAX_AGE_DAYS
EVENT_FLAG_KEYS = (
    "has_war", "has_rate_hike", "has_rate_cut", "has_geopolitical",
    "has_tariff", "has_export_ban", "has_earnings_shock",
    "has_oil_surge", "has_banking_crisis", "has_inflation",
    "has_fed_statement",
)
COMPLEXITY_MASK_FIELDS = (
    "use_news_global",
    "use_event_block",
    "use_market_entry_adjustment",
)


@dataclass
class BacktestResult:
    rulebook: Rulebook
    trades: list = field(default_factory=list)
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    profit_concentration: float = 0.0
    sharpe_like: float = 0.0
    fitness: float = 0.0

    def to_dict(self) -> dict:
        return {
            "rulebook": asdict(self.rulebook),
            "trades": self.trades,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": self.win_rate,
            "avg_return_pct": self.avg_return_pct,
            "avg_win_pct": self.avg_win_pct,
            "avg_loss_pct": self.avg_loss_pct,
            "expectancy_pct": self.expectancy_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "profit_factor": self.profit_factor,
            "profit_concentration": self.profit_concentration,
            "sharpe_like": self.sharpe_like,
            "fitness": self.fitness,
        }


_SPREAD_CTX_CACHE: dict = {}
_SPREAD_RET_CACHE: dict = {}
_TOPIC_FEATURE_CACHE: dict = {}


def _zero_event_flags() -> dict:
    return {k: 0 for k in EVENT_FLAG_KEYS}


def _news_zscore_window(rb: Rulebook) -> int:
    try:
        window = int(getattr(rb, "news_zscore_window", 60) or 60)
    except Exception:
        window = 60
    return max(1, min(window, 252))


def _precompute_topic_feature_map(ticker_sentiment: Optional[dict], window: int) -> dict:
    """Precompute ticker topic-news z-score features for one rulebook window.

    ``precompute_topic_features`` itself uses only prior samples for each raw
    news date. The backtest later applies FEATURE_LAG_DAYS again at lookup time,
    so D-day entries only see topic features available through D-1 or earlier.
    """
    if not isinstance(ticker_sentiment, dict) or not ticker_sentiment:
        return {}
    key = (id(ticker_sentiment), len(ticker_sentiment), int(window))
    cached = _TOPIC_FEATURE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        features = precompute_topic_features(ticker_sentiment, int(window))
        if not isinstance(features, dict):
            features = {}
    except Exception:
        features = {}
    _TOPIC_FEATURE_CACHE[key] = features
    return features


def _lookup_signal_context(
    *,
    df: pd.DataFrame,
    idx: int,
    market_score: float,
    sector_score: float,
    vix_level: float,
    market_history_df: Optional[pd.DataFrame],
    sector_name: str,
    ticker_sentiment: Optional[dict],
    topic_feature_map: Optional[dict],
    use_llm_events: bool,
) -> tuple[float, float, float, float, dict, dict]:
    event_flags = _zero_event_flags()
    if market_history_df is not None:
        mkt = lookup_market_at_lagged(market_history_df, df.index[idx], lag_days=FEATURE_LAG_DAYS)
        cur_market = float(mkt.get("score", market_score))
        cur_sector = float(mkt.get(f"sector_{sector_name}", sector_score))
        cur_vix = float(mkt.get("vix", vix_level))
        if use_llm_events:
            for key in EVENT_FLAG_KEYS:
                event_flags[key] = int(mkt.get(key, 0) or 0)
    else:
        cur_market = market_score
        cur_sector = sector_score
        cur_vix = vix_level

    cur_sentiment = 0.0
    if ticker_sentiment:
        try:
            s = lookup_lagged_daily_dict(
                ticker_sentiment,
                df.index[idx],
                lag_days=FEATURE_LAG_DAYS,
                max_age_days=FEATURE_LAG_MAX_AGE_DAYS,
            )
            if s:
                cur_sentiment = float(s.get("sentiment_avg", 0.0))
        except Exception:
            cur_sentiment = 0.0

    cur_topic_features: dict = {}
    if topic_feature_map:
        try:
            t = lookup_lagged_daily_dict(
                topic_feature_map,
                df.index[idx],
                lag_days=FEATURE_LAG_DAYS,
                max_age_days=FEATURE_LAG_MAX_AGE_DAYS,
            )
            cur_topic_features = dict(t or {}) if isinstance(t, dict) else {}
        except Exception:
            cur_topic_features = {}
    return cur_market, cur_sector, cur_vix, cur_sentiment, event_flags, cur_topic_features


def _signal_snapshot(
    prefix: str,
    sig,
    *,
    sentiment: float,
    market: float,
    sector: float,
    vix: float,
    event_flags: dict,
    topic_features: Optional[dict] = None,
) -> dict:
    reasons = list(getattr(sig, "reasons", []) or [])
    reason_key = f"{prefix}_reason"
    reasons_key = f"{prefix}_reasons"
    if prefix == "exit":
        reason_key = "exit_signal_reason"
        reasons_key = "exit_signal_reasons"
    return {
        reason_key: "; ".join(str(x) for x in reasons),
        reasons_key: reasons,
        f"{prefix}_signal_score": float(getattr(sig, "score", 0.0) or 0.0),
        f"{prefix}_signal_raw_score": float(getattr(sig, "raw_score", 0.0) or 0.0),
        f"{prefix}_signal_threshold": float(getattr(sig, "threshold", 0.0) or 0.0),
        f"{prefix}_market_adjustment": float(getattr(sig, "market_adjustment", 0.0) or 0.0),
        f"{prefix}_signal_components": dict(getattr(sig, "components", {}) or {}),
        f"{prefix}_news_sentiment": float(sentiment or 0.0),
        f"{prefix}_topic_features": dict(topic_features or {}),
        f"{prefix}_market_score": float(market or 0.0),
        f"{prefix}_sector_score": float(sector or 0.0),
        f"{prefix}_vix_level": float(vix or 0.0),
        f"{prefix}_event_flags": dict(event_flags or {}),
    }


def _count_active_complexity_masks(rb: Rulebook) -> int:
    return sum(bool(getattr(rb, field, True)) for field in COMPLEXITY_MASK_FIELDS)


def _calc_complexity_penalty(active_count: int, coefficient: float) -> float:
    coeff = max(float(coefficient or 0.0), 0.0)
    return float(max(int(active_count or 0), 0)) * coeff


def _apply_complexity_penalty(rb: Rulebook, raw_fitness: float, coefficient: float) -> float:
    return float(raw_fitness) - _calc_complexity_penalty(_count_active_complexity_masks(rb), coefficient)


def run_backtest(
    rb: Rulebook,
    df: pd.DataFrame,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    position_limit_krw: float = 120000.0,
    commission_rate: float = 0.0005,
    cooldown_days: int = 1,
    warmup: int = 200,
    market_history_df: Optional[pd.DataFrame] = None,
    sector_name: str = "tech",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    ticker_sentiment: Optional[dict] = None,
    fitness_mode: str = "legacy",
    complexity_penalty_per_mask: float = 0.0,
    use_llm_events: bool = True,
) -> BacktestResult:
    trades: list = []
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    topic_window = _news_zscore_window(rb)
    topic_feature_map = _precompute_topic_feature_map(ticker_sentiment, topic_window)

    if "date" in df.columns:
        date_series = pd.to_datetime(df["date"])
    elif isinstance(df.index, pd.DatetimeIndex):
        date_series = pd.Series(df.index, index=df.index)
    else:
        date_series = None

    i = max(warmup, 0)
    n = len(df)
    all_scores: list = []
    all_rets: list = []

    if fitness_mode == "spread":
        ck = (
            id(df),
            id(ticker_sentiment),
            topic_window,
            start_date,
            end_date,
            FEATURE_LAG_DAYS,
            FEATURE_LAG_MAX_AGE_DAYS,
            bool(use_llm_events),
        )
        if ck not in _SPREAD_CTX_CACHE:
            ctx_list = []
            for j in range(max(warmup, 0), n):
                if date_series is not None:
                    try:
                        ts = pd.Timestamp(date_series.iloc[j] if hasattr(date_series, "iloc") else date_series[j])
                        if start_ts is not None and ts < start_ts:
                            ctx_list.append(None)
                            continue
                        if end_ts is not None and ts > end_ts:
                            ctx_list.append("BREAK")
                            break
                    except Exception:
                        pass
                cm, cs, cv, snt, ef, topic = _lookup_signal_context(
                    df=df,
                    idx=j,
                    market_score=market_score,
                    sector_score=sector_score,
                    vix_level=vix_level,
                    market_history_df=market_history_df,
                    sector_name=sector_name,
                    ticker_sentiment=ticker_sentiment,
                    topic_feature_map=topic_feature_map,
                    use_llm_events=use_llm_events,
                )
                px = float(df.iloc[j]["Close"])
                sh = int(position_limit_krw / px) if px > 0 else 0
                ctx_list.append((j, cm, cs, cv, ef, snt, topic, px, sh))
            _SPREAD_CTX_CACHE[ck] = ctx_list
        ctx_list = _SPREAD_CTX_CACHE[ck]

        exit_key = (
            getattr(rb, "direction", "long"),
            getattr(rb, "exit_strategy", "hybrid"),
            round(float(getattr(rb, "stop_loss_atr", 0) or 0), 4),
            round(float(getattr(rb, "take_profit_atr", 0) or 0), 4),
            round(float(getattr(rb, "trailing_atr", 0) or 0), 4),
            round(float(getattr(rb, "trailing_activation_profit_pct", 0) or 0), 4),
            int(getattr(rb, "max_holding_days", 0) or 0),
            bool(getattr(rb, "add_buy_enabled", False)),
            int(getattr(rb, "add_buy_max_count", 0) or 0),
            round(float(getattr(rb, "add_buy_trigger_profit_pct", 0) or 0), 4),
            round(float(getattr(rb, "add_buy_size_ratio", 0) or 0), 4),
        )
        rk = (ck, exit_key)
        ret_map = _SPREAD_RET_CACHE.get(rk)
        if ret_map is None:
            ret_map = {}
            for item in ctx_list:
                if item is None or item == "BREAK":
                    continue
                j, cm, cs, cv, ef, snt, topic, px, sh = item
                if sh <= 0:
                    continue
                tr = simulate_exit(
                    rb,
                    df,
                    j,
                    sh,
                    position_limit_krw,
                    commission_rate=commission_rate,
                    cur_market_score=cm,
                    cur_vix_level=cv,
                    cur_sector_score=cs,
                )
                if tr is None:
                    continue
                d = asdict(tr) if hasattr(tr, "__dataclass_fields__") else tr
                pnl = d.get("pnl_pct") if isinstance(d, dict) else getattr(tr, "pnl_pct", None)
                if pnl is not None:
                    ret_map[j] = float(pnl)
            _SPREAD_RET_CACHE[rk] = ret_map

        for item in ctx_list:
            if item is None:
                continue
            if item == "BREAK":
                break
            j, cm, cs, cv, ef, snt, topic, px, sh = item
            if j not in ret_map:
                continue
            sig = evaluate_signal(
                rb,
                df.iloc[:j + 1],
                market_score=cm,
                sector_score=cs,
                vix_level=cv,
                news_sentiment=snt,
                event_flags=ef,
                topic_features=topic,
            )
            all_scores.append(float(sig.score))
            all_rets.append(ret_map[j])

    while i < n:
        if date_series is not None:
            try:
                cur_ts = pd.Timestamp(date_series.iloc[i] if hasattr(date_series, "iloc") else date_series[i])
                if start_ts is not None and cur_ts < start_ts:
                    i += 1
                    continue
                if end_ts is not None and cur_ts > end_ts:
                    break
            except Exception:
                pass

        sub_df = df.iloc[: i + 1]
        cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = _lookup_signal_context(
            df=df,
            idx=i,
            market_score=market_score,
            sector_score=sector_score,
            vix_level=vix_level,
            market_history_df=market_history_df,
            sector_name=sector_name,
            ticker_sentiment=ticker_sentiment,
            topic_feature_map=topic_feature_map,
            use_llm_events=use_llm_events,
        )
        sig = evaluate_signal(
            rb,
            sub_df,
            market_score=cur_market,
            sector_score=cur_sector,
            vix_level=cur_vix,
            news_sentiment=cur_sentiment,
            event_flags=cur_event_flags,
            topic_features=cur_topic_features,
        )
        if not sig.should_buy:
            i += 1
            continue

        amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
        entry_price = float(df.iloc[i]["Close"])
        shares = int(amt_krw / entry_price) if entry_price > 0 else 0
        if shares <= 0:
            i += 1
            continue

        trade_obj = simulate_exit(
            rb,
            df,
            i,
            shares,
            position_limit_krw,
            commission_rate=commission_rate,
            cur_market_score=cur_market,
            cur_vix_level=cur_vix,
            cur_sector_score=cur_sector,
        )
        if trade_obj is None:
            break

        trade = asdict(trade_obj) if hasattr(trade_obj, "__dataclass_fields__") else trade_obj
        if isinstance(trade, dict):
            trade.update(
                _signal_snapshot(
                    "entry",
                    sig,
                    sentiment=cur_sentiment,
                    market=cur_market,
                    sector=cur_sector,
                    vix=cur_vix,
                    event_flags=cur_event_flags,
                    topic_features=cur_topic_features,
                )
            )
            exit_date = trade.get("exit_date")
            try:
                exit_idx = df.index.get_loc(pd.Timestamp(exit_date)) if exit_date is not None else None
                if isinstance(exit_idx, slice):
                    exit_idx = exit_idx.start
                if exit_idx is not None:
                    exit_idx = int(exit_idx)
                    ex_market, ex_sector, ex_vix, ex_sentiment, ex_event_flags, ex_topic_features = _lookup_signal_context(
                        df=df,
                        idx=exit_idx,
                        market_score=market_score,
                        sector_score=sector_score,
                        vix_level=vix_level,
                        market_history_df=market_history_df,
                        sector_name=sector_name,
                        ticker_sentiment=ticker_sentiment,
                        topic_feature_map=topic_feature_map,
                        use_llm_events=use_llm_events,
                    )
                    ex_sig = evaluate_signal(
                        rb,
                        df.iloc[: exit_idx + 1],
                        market_score=ex_market,
                        sector_score=ex_sector,
                        vix_level=ex_vix,
                        news_sentiment=ex_sentiment,
                        event_flags=ex_event_flags,
                        topic_features=ex_topic_features,
                    )
                    trade.update(
                        _signal_snapshot(
                            "exit",
                            ex_sig,
                            sentiment=ex_sentiment,
                            market=ex_market,
                            sector=ex_sector,
                            vix=ex_vix,
                            event_flags=ex_event_flags,
                            topic_features=ex_topic_features,
                        )
                    )
                    trade["exit_snapshot_date"] = str(pd.Timestamp(df.index[exit_idx]).date())
            except Exception as e:
                trade["exit_snapshot_error"] = str(e)
        trades.append(trade)

        exit_date = trade.get("exit_date")
        if exit_date is None:
            i += 1
            continue
        try:
            exit_idx = df.index.get_loc(pd.Timestamp(exit_date))
            if isinstance(exit_idx, slice):
                exit_idx = exit_idx.start
        except KeyError:
            exit_idx = i + 1
        i = max(exit_idx + 1 + cooldown_days, i + 1)

    res = _summarize(rb, trades)
    if fitness_mode == "spread":
        res.fitness = _calc_fitness_spread(all_scores, all_rets)
        rb.fitness = res.fitness
    elif fitness_mode == "swing":
        raw_fitness = _calc_fitness_swing(
            expectancy_pct=res.expectancy_pct,
            win_rate=res.win_rate,
            profit_factor=res.profit_factor,
            max_drawdown_pct=res.max_drawdown_pct,
            trade_count=res.trade_count,
            loss_count=res.loss_count,
            profit_concentration=res.profit_concentration,
        )
        res.fitness = _apply_complexity_penalty(rb, raw_fitness, complexity_penalty_per_mask)
        rb.fitness = res.fitness
    return res


def _trade_numeric(trade: object, key: str) -> float | None:
    try:
        value = trade.get(key) if isinstance(trade, dict) else getattr(trade, key, None)
    except Exception:
        return None
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    return v if np.isfinite(v) else None


def _calc_profit_concentration(trades: list) -> float:
    """Return max single positive profit share among total positive profit.

    Prefer pnl_krw because it reflects actual sized contribution. Fall back to
    pnl_pct for older or synthetic trade rows without pnl_krw.
    """
    profits: list[float] = []
    for trade in trades or []:
        pnl = _trade_numeric(trade, "pnl_krw")
        if pnl is None:
            pnl = _trade_numeric(trade, "pnl_pct")
        if pnl is not None and pnl > 0.0:
            profits.append(float(pnl))
    total_profit = float(sum(profits))
    if total_profit <= 0.0:
        return 0.0
    return _clamp(max(profits) / total_profit, 0.0, 1.0)


def _summarize(rb: Rulebook, trades: list) -> BacktestResult:
    if not trades:
        return BacktestResult(rulebook=rb, trades=[], fitness=-1.0)

    pnl_pcts = np.array([t.get("pnl_pct", 0.0) for t in trades], dtype=float)
    pnl_krw = np.array([t.get("pnl_krw", 0.0) for t in trades], dtype=float)
    win_mask = pnl_pcts > 0
    loss_mask = pnl_pcts <= 0
    win_count = int(win_mask.sum())
    loss_count = int(loss_mask.sum())
    trade_count = len(trades)
    win_rate = (win_count / trade_count) * 100.0 if trade_count else 0.0
    avg_return = float(pnl_pcts.mean())
    avg_win = float(pnl_pcts[win_mask].mean()) if win_count else 0.0
    avg_loss = float(pnl_pcts[loss_mask].mean()) if loss_count else 0.0
    expectancy = avg_return

    cum = np.cumsum(pnl_pcts)
    running_max = np.maximum.accumulate(cum)
    drawdown = cum - running_max
    mdd = float(drawdown.min()) if len(drawdown) else 0.0
    gross_profit = float(pnl_krw[win_mask].sum()) if win_count else 0.0
    gross_loss = float(-pnl_krw[loss_mask].sum()) if loss_count else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
    profit_concentration = _calc_profit_concentration(trades)
    std = float(pnl_pcts.std()) if len(pnl_pcts) > 1 else 1.0
    sharpe = avg_return / std if std > 0 else 0.0
    fitness = _calc_fitness(expectancy=expectancy, win_rate=win_rate, profit_factor=pf, mdd=mdd, trade_count=trade_count)

    res = BacktestResult(
        rulebook=rb,
        trades=trades,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        avg_return_pct=avg_return,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        expectancy_pct=expectancy,
        max_drawdown_pct=mdd,
        profit_factor=pf,
        profit_concentration=profit_concentration,
        sharpe_like=sharpe,
        fitness=fitness,
    )
    rb.fitness = fitness
    rb.win_rate = win_rate
    rb.avg_return_pct = avg_return
    rb.expectancy_pct = expectancy
    rb.max_drawdown_pct = mdd
    rb.trade_count = trade_count
    return res


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _calc_concentration_penalty(profit_concentration: float) -> float:
    concentration = _clamp(float(profit_concentration or 0.0), 0.0, 1.0)
    if concentration <= 0.50:
        return 0.0
    return _clamp((concentration - 0.50) / 0.25 * 20.0, 0.0, 20.0)


def _calc_fitness_swing(*, expectancy_pct: float, win_rate: float, profit_factor: float, max_drawdown_pct: float, trade_count: int, loss_count: int, profit_concentration: float = 0.0) -> float:
    if trade_count <= 0:
        return -100.0
    exp = float(expectancy_pct or 0.0)
    if exp <= 0.0:
        return -100.0 + max(exp * 10.0, -50.0)
    try:
        pf = float(profit_factor or 0.0)
    except Exception:
        pf = 1.0
    if not np.isfinite(pf):
        pf = 1.0
    if loss_count == 0:
        pf = 1.5 if trade_count >= 10 else 1.0
    pf = max(0.0, min(pf, 4.0))
    wr = float(win_rate or 0.0)
    mdd_abs = abs(float(max_drawdown_pct or 0.0))
    exp_score = _clamp((exp / 2.0) * 60.0, 0.0, 120.0)
    if exp < 0.5:
        exp_score -= 25.0
    elif exp < 1.0:
        exp_score -= 10.0
    pf_score = _clamp((pf - 1.0) * 15.0, -20.0, 35.0)
    wr_score = _clamp((wr - 50.0) / 50.0 * 5.0, 0.0, 5.0)
    mdd_penalty = -_clamp(mdd_abs * 0.8, 0.0, 40.0)
    concentration_penalty = _calc_concentration_penalty(profit_concentration)
    base = exp_score + pf_score + wr_score + mdd_penalty
    if trade_count < 5:
        trade_factor = 0.10
    elif trade_count < 10:
        trade_factor = 0.35
    elif trade_count < 20:
        trade_factor = 0.70
    elif trade_count <= 80:
        trade_factor = 1.00
    else:
        trade_factor = max(0.65, 1.0 - (trade_count - 80) / 250.0)
    return float(base * trade_factor - concentration_penalty)


def _calc_fitness(expectancy: float, win_rate: float, profit_factor: float, mdd: float, trade_count: int) -> float:
    if trade_count == 0:
        return -50.0
    if trade_count < 5:
        sample_factor = trade_count / 5.0 * 0.2
    elif trade_count < 10:
        sample_factor = 0.3 + (trade_count - 5) / 5 * 0.3
    elif trade_count < 20:
        sample_factor = 0.6 + (trade_count - 10) / 10 * 0.3
    elif trade_count < 100:
        sample_factor = 0.9 + (trade_count - 20) / 80 * 0.1
    else:
        sample_factor = max(1.0 - (trade_count - 100) / 500, 0.85)
    exp_score = max(min(expectancy / 3.0 * 40.0, 50.0), -30.0)
    wr_score = max(min((win_rate - 50.0) / 50.0 * 30.0, 30.0), -30.0)
    pf_score = max(min((profit_factor - 1.0) / 2.0 * 20.0, 30.0), -20.0)
    mdd_penalty = max(min(mdd, 0.0), -30.0)
    return (exp_score + wr_score + pf_score + mdd_penalty) * sample_factor


def _calc_fitness_spread(scores: list, rets: list) -> float:
    import numpy as _np
    if not scores or len(scores) < 10:
        return -1.0
    sc = _np.array(scores, dtype=float)
    rt = _np.array(rets, dtype=float)
    order = _np.argsort(sc)
    k = max(1, int(len(sc) * 0.3))
    lo = float(rt[order[:k]].mean())
    hi = float(rt[order[-k:]].mean())
    spread = hi - lo
    if k < 5:
        sf = 0.3
    elif k < 10:
        sf = 0.6
    elif k < 30:
        sf = 0.85
    else:
        sf = 1.0
    try:
        from scipy.stats import spearmanr as _sr
        rho, _ = _sr(sc, rt)
        rho = 0.0 if rho != rho else float(rho)
    except Exception:
        rho = 0.0
    return float(spread * sf + rho * 1.0)
