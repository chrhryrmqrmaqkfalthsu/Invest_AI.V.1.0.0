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

log = get_logger("backtest")

FEATURE_LAG_DAYS = DEFAULT_LAG_DAYS
FEATURE_LAG_MAX_AGE_DAYS = DEFAULT_MAX_AGE_DAYS
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
            "sharpe_like": self.sharpe_like,
            "fitness": self.fitness,
        }


# spread fitness 캐시 (GA 수명 동안 재사용)
_SPREAD_CTX_CACHE: dict = {}   # key: (id(df), start, end, lag, max_age) -> list[(cm,cs,cv,ef,snt,px,sh)]
_SPREAD_RET_CACHE: dict = {}   # key: (ctx_key, exit_param_tuple) -> dict{j: pnl}


def _count_active_complexity_masks(rb: Rulebook) -> int:
    """Return the number of active entry feature masks used for complexity penalty."""
    return sum(bool(getattr(rb, field, True)) for field in COMPLEXITY_MASK_FIELDS)


def _calc_complexity_penalty(active_count: int, coefficient: float) -> float:
    """Linear complexity penalty: coefficient per active mask."""
    coeff = max(float(coefficient or 0.0), 0.0)
    return float(max(int(active_count or 0), 0)) * coeff


def _apply_complexity_penalty(rb: Rulebook, raw_fitness: float, coefficient: float) -> float:
    """Apply additive fitness penalty without changing trades or signal behavior."""
    penalty = _calc_complexity_penalty(_count_active_complexity_masks(rb), coefficient)
    return float(raw_fitness) - penalty


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
) -> BacktestResult:
    """
    전체 기간을 순회하며 신호 발생 시 진입 → 청산 시뮬레이션 → 다음 진입.

    Feature lag policy:
        D일 신호에는 D-1 이하의 뉴스/이벤트만 사용한다.
        ticker sentiment: lag_days=1, max_age_days=7
        market events: lag_days=1

    Args:
        rb: 룰북
        df: OHLCV + 지표 DataFrame
        market_score/sector_score/vix_level: 시계열이 없을 때 사용할 고정값
        position_limit_krw: 종목당 한도
        commission_rate: 왕복 수수료
        cooldown_days: 청산 후 재진입 대기일수
        warmup: 지표 안정화를 위한 시작 인덱스
        market_history_df: 시점별 시장 시계열 DataFrame (있으면 우선 사용)
        sector_name: market_history_df에서 조회할 섹터명 (tech/finance/energy/...)
        complexity_penalty_per_mask: swing fitness에서 활성 entry mask 1개당 차감할 점수
    """
    trades: list = []
    # walk-forward: 날짜 범위 (df는 그대로 — 지표 안정성 유지, 루프 내에서 필터)
    _start_ts = pd.Timestamp(start_date) if start_date else None
    _end_ts = pd.Timestamp(end_date) if end_date else None

    # 날짜 시리즈 준비 (날짜별 체크용)
    if 'date' in df.columns:
        _date_series = pd.to_datetime(df['date'])
    elif isinstance(df.index, pd.DatetimeIndex):
        _date_series = pd.Series(df.index, index=df.index)
    else:
        _date_series = None

    i = max(warmup, 0)
    n = len(df)
    _all_scores: list = []
    _all_rets: list = []

    # === spread fitness: 거래 루프와 독립적으로 "모든 날" 신호점수+실현수익 수집 (캐시 적용) ===
    if fitness_mode == "spread":
        _ck = (id(df), start_date, end_date, FEATURE_LAG_DAYS, FEATURE_LAG_MAX_AGE_DAYS)
        # 1) 시장 컨텍스트 캐시 (GA 전체 불변) — 1회만 계산
        if _ck not in _SPREAD_CTX_CACHE:
            _ctx_list = []
            for j in range(max(warmup, 0), n):
                if _date_series is not None:
                    try:
                        _cts = pd.Timestamp(_date_series.iloc[j] if hasattr(_date_series, 'iloc') else _date_series[j])
                        if _start_ts is not None and _cts < _start_ts:
                            _ctx_list.append(None); continue
                        if _end_ts is not None and _cts > _end_ts:
                            _ctx_list.append("BREAK"); break
                    except Exception:
                        pass
                _ef = {}
                if market_history_df is not None:
                    _m = lookup_market_at_lagged(market_history_df, df.index[j], lag_days=FEATURE_LAG_DAYS)
                    _cm = float(_m.get("score", market_score))
                    _cs = float(_m.get(f"sector_{sector_name}", sector_score))
                    _cv = float(_m.get("vix", vix_level))
                    for _k in ("has_war", "has_rate_hike", "has_rate_cut", "has_geopolitical",
                               "has_tariff", "has_export_ban", "has_earnings_shock",
                               "has_oil_surge", "has_banking_crisis", "has_inflation",
                               "has_fed_statement"):
                        _ef[_k] = int(_m.get(_k, 0) or 0)
                else:
                    _cm, _cs, _cv = market_score, sector_score, vix_level
                _snt = 0.0
                if ticker_sentiment:
                    try:
                        _sv = lookup_lagged_daily_dict(
                            ticker_sentiment,
                            df.index[j],
                            lag_days=FEATURE_LAG_DAYS,
                            max_age_days=FEATURE_LAG_MAX_AGE_DAYS,
                        )
                        if _sv:
                            _snt = float(_sv.get('sentiment_avg', 0.0))
                    except Exception:
                        _snt = 0.0
                _px = float(df.iloc[j]["Close"])
                _sh = int(position_limit_krw / _px) if _px > 0 else 0
                _ctx_list.append((j, _cm, _cs, _cv, _ef, _snt, _px, _sh))
            _SPREAD_CTX_CACHE[_ck] = _ctx_list
        _ctx_list = _SPREAD_CTX_CACHE[_ck]

        # 2) 실현수익 캐시 — exit 파라미터 튜플에만 의존
        _exit_key = (
            getattr(rb, "direction", "long"),
            getattr(rb, "exit_strategy", "hybrid"),
            round(float(getattr(rb, "stop_loss_atr", 0) or 0), 4),
            round(float(getattr(rb, "take_profit_atr", 0) or 0), 4),
            round(float(getattr(rb, "trailing_atr", 0) or 0), 4),
            int(getattr(rb, "max_holding_days", 0) or 0),
            bool(getattr(rb, "add_buy_enabled", False)),
            int(getattr(rb, "add_buy_max_count", 0) or 0),
            round(float(getattr(rb, "add_buy_trigger_profit_pct", 0) or 0), 4),
            round(float(getattr(rb, "add_buy_size_ratio", 0) or 0), 4),
        )
        _rk = (_ck, _exit_key)
        _ret_map = _SPREAD_RET_CACHE.get(_rk)
        if _ret_map is None:
            _ret_map = {}
            for _item in _ctx_list:
                if _item is None or _item == "BREAK":
                    continue
                j, _cm, _cs, _cv, _ef, _snt, _px, _sh = _item
                if _sh <= 0:
                    continue
                _tr = simulate_exit(
                    rb, df, j, _sh, position_limit_krw,
                    commission_rate=commission_rate,
                    cur_market_score=_cm,
                    cur_vix_level=_cv,
                    cur_sector_score=_cs,
                )
                if _tr is None:
                    continue
                _d = asdict(_tr) if hasattr(_tr, "__dataclass_fields__") else _tr
                _pnl = _d.get("pnl_pct") if isinstance(_d, dict) else getattr(_tr, "pnl_pct", None)
                if _pnl is not None:
                    _ret_map[j] = float(_pnl)
            _SPREAD_RET_CACHE[_rk] = _ret_map

        # 3) 신호점수는 entry 가중치마다 변하므로 매번 계산 (캐시 불가)
        for _item in _ctx_list:
            if _item is None:
                continue
            if _item == "BREAK":
                break
            j, _cm, _cs, _cv, _ef, _snt, _px, _sh = _item
            if j not in _ret_map:
                continue
            _sig = evaluate_signal(rb, df.iloc[:j + 1], market_score=_cm,
                                   sector_score=_cs, vix_level=_cv,
                                   news_sentiment=_snt, event_flags=_ef)
            _all_scores.append(float(_sig.score))
            _all_rets.append(_ret_map[j])
    # === end spread 수집 루프 ===

    while i < n:
        # walk-forward 날짜 필터: start 이전이면 skip, end 이후면 break
        if _date_series is not None:
            try:
                cur_d = _date_series.iloc[i] if hasattr(_date_series, 'iloc') else _date_series[i]
                cur_ts = pd.Timestamp(cur_d)
                if _start_ts is not None and cur_ts < _start_ts:
                    i += 1
                    continue
                if _end_ts is not None and cur_ts > _end_ts:
                    break
            except Exception:
                pass

        sub_df = df.iloc[: i + 1]

        # 시점별 시장 컨텍스트 조회 (시계열이 있으면 사용, 없으면 고정값)
        # D일 신호에는 D-1 이하의 이벤트/시장 컨텍스트만 사용한다.
        cur_event_flags = {}
        if market_history_df is not None:
            cur_date = df.index[i]
            mkt = lookup_market_at_lagged(market_history_df, cur_date, lag_days=FEATURE_LAG_DAYS)
            cur_market = float(mkt.get("score", market_score))
            cur_sector = float(mkt.get(f"sector_{sector_name}", sector_score))
            cur_vix = float(mkt.get("vix", vix_level))
            # v5: 11개 이벤트 플래그 추출
            for key in ("has_war", "has_rate_hike", "has_rate_cut", "has_geopolitical",
                        "has_tariff", "has_export_ban", "has_earnings_shock",
                        "has_oil_surge", "has_banking_crisis", "has_inflation",
                        "has_fed_statement"):
                cur_event_flags[key] = int(mkt.get(key, 0) or 0)
        else:
            cur_market = market_score
            cur_sector = sector_score
            cur_vix = vix_level

        # v6: 종목별 뉴스 감성 조회 (CSV 없으면 0.0 폴백)
        # D일 신호에는 D-1 이하의 최신 뉴스 sentiment만 사용한다. max_age=7일.
        cur_sentiment = 0.0
        if ticker_sentiment:
            try:
                _s = lookup_lagged_daily_dict(
                    ticker_sentiment,
                    df.index[i],
                    lag_days=FEATURE_LAG_DAYS,
                    max_age_days=FEATURE_LAG_MAX_AGE_DAYS,
                )
                if _s:
                    cur_sentiment = float(_s.get('sentiment_avg', 0.0))
            except Exception:
                cur_sentiment = 0.0

        sig = evaluate_signal(
            rb, sub_df,
            market_score=cur_market,
            sector_score=cur_sector,
            vix_level=cur_vix,
            news_sentiment=cur_sentiment,
            event_flags=cur_event_flags,  # v5
        )
        if not sig.should_buy:
            i += 1
            continue

        # 포지션 사이징
        amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
        entry_price = float(df.iloc[i]["Close"])
        shares = int(amt_krw / entry_price) if entry_price > 0 else 0
        if shares <= 0:
            i += 1
            continue

        trade_obj = simulate_exit(
            rb, df, i, shares, position_limit_krw,
            commission_rate=commission_rate,
            cur_market_score=cur_market,  # v5: 동적 손절익절용
            cur_vix_level=cur_vix,
            cur_sector_score=cur_sector,
        )
        if trade_obj is None:
            break
        # Trade 데이터클래스 → dict로 변환 (storage 호환)
        trade = asdict(trade_obj) if hasattr(trade_obj, "__dataclass_fields__") else trade_obj
        if isinstance(trade, dict):
            entry_reasons = list(getattr(sig, "reasons", []) or [])
            trade["entry_reason"] = "; ".join(str(x) for x in entry_reasons)
            trade["entry_reasons"] = entry_reasons
            trade["entry_signal_score"] = float(getattr(sig, "score", 0.0) or 0.0)
            trade["entry_signal_raw_score"] = float(getattr(sig, "raw_score", 0.0) or 0.0)
            trade["entry_signal_threshold"] = float(getattr(sig, "threshold", 0.0) or 0.0)
            trade["entry_market_adjustment"] = float(getattr(sig, "market_adjustment", 0.0) or 0.0)
            trade["entry_signal_components"] = dict(getattr(sig, "components", {}) or {})
            trade["entry_news_sentiment"] = float(cur_sentiment or 0.0)
            trade["entry_event_flags"] = dict(cur_event_flags or {})
        trades.append(trade)

        # 청산 시점 인덱스 찾기 (날짜로 매칭)
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

    _res = _summarize(rb, trades)
    if fitness_mode == "spread":
        _res.fitness = _calc_fitness_spread(_all_scores, _all_rets)
        rb.fitness = _res.fitness
    elif fitness_mode == "swing":
        raw_fitness = _calc_fitness_swing(
            expectancy_pct=_res.expectancy_pct,
            win_rate=_res.win_rate,
            profit_factor=_res.profit_factor,
            max_drawdown_pct=_res.max_drawdown_pct,
            trade_count=_res.trade_count,
            loss_count=_res.loss_count,
        )
        _res.fitness = _apply_complexity_penalty(rb, raw_fitness, complexity_penalty_per_mask)
        rb.fitness = _res.fitness
    return _res


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
    expectancy = avg_return  # 평균 거래당 기대수익률 (%)

    # 최대 낙폭 (누적 수익률 기반)
    cum = np.cumsum(pnl_pcts)
    running_max = np.maximum.accumulate(cum)
    drawdown = cum - running_max
    mdd = float(drawdown.min()) if len(drawdown) else 0.0

    # Profit Factor
    gross_profit = float(pnl_krw[win_mask].sum()) if win_count else 0.0
    gross_loss = float(-pnl_krw[loss_mask].sum()) if loss_count else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    # Sharpe-like
    std = float(pnl_pcts.std()) if len(pnl_pcts) > 1 else 1.0
    sharpe = avg_return / std if std > 0 else 0.0

    fitness = _calc_fitness(
        expectancy=expectancy,
        win_rate=win_rate,
        profit_factor=pf,
        mdd=mdd,
        trade_count=trade_count,
    )

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
        sharpe_like=sharpe,
        fitness=fitness,
    )

    # 룰북에도 백테스트 성과 기록
    rb.fitness = fitness
    rb.win_rate = win_rate
    rb.avg_return_pct = avg_return
    rb.expectancy_pct = expectancy
    rb.max_drawdown_pct = mdd
    rb.trade_count = trade_count

    return res


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _calc_fitness_swing(
    *,
    expectancy_pct: float,
    win_rate: float,
    profit_factor: float,
    max_drawdown_pct: float,
    trade_count: int,
    loss_count: int,
) -> float:
    """스윙 단타용 TRAIN-only fitness.

    원칙:
    - 비용 차감 expectancy가 0 이하이면 거래수와 무관하게 탈락시킨다.
    - 거래수 부족은 강하게 감점한다.
    - Profit Factor는 손실 0건일 때 원화 gross_profit으로 들어오는 기존 구조를 보수적으로 방어한다.
    - 승률은 보조 지표로만 약하게 사용한다.
    - TEST/연도별 안정성/true-WF 결과는 절대 사용하지 않는다.
    """
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
    wr_score = _clamp((wr - 50.0) / 50.0 * 8.0, -8.0, 8.0)
    mdd_penalty = -_clamp(mdd_abs * 0.8, 0.0, 40.0)

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

    return float(base * trade_factor)


def _calc_fitness(
    expectancy: float,
    win_rate: float,
    profit_factor: float,
    mdd: float,
    trade_count: int,
) -> float:
    """
    종합 적합도. 거래 표본이 충분해야 신뢰할 수 있음.
    - 거래 5건 미만: fitness 강하게 깎음 (overfitting 방지)
    - 거래 5~20건: 표본 부족 페널티
    - 거래 20건 이상: 정상 평가
    """
    if trade_count == 0:
        return -50.0

    # 거래 수 신뢰도 계수
    if trade_count < 5:
        sample_factor = trade_count / 5.0 * 0.2   # 0.04 ~ 0.16
    elif trade_count < 10:
        sample_factor = 0.3 + (trade_count - 5) / 5 * 0.3  # 0.3 ~ 0.6
    elif trade_count < 20:
        sample_factor = 0.6 + (trade_count - 10) / 10 * 0.3  # 0.6 ~ 0.9
    elif trade_count < 100:
        sample_factor = 0.9 + (trade_count - 20) / 80 * 0.1  # 0.9 ~ 1.0
    else:
        sample_factor = max(1.0 - (trade_count - 100) / 500, 0.85)

    exp_score = max(min(expectancy / 3.0 * 40.0, 50.0), -30.0)
    wr_score = max(min((win_rate - 50.0) / 50.0 * 30.0, 30.0), -30.0)
    pf_score = max(min((profit_factor - 1.0) / 2.0 * 20.0, 30.0), -20.0)
    mdd_penalty = max(min(mdd, 0.0), -30.0)

    base = exp_score + wr_score + pf_score + mdd_penalty
    return base * sample_factor


    exp_score = max(min(expectancy / 3.0 * 40.0, 50.0), -30.0)
    wr_score = max(min((win_rate - 50.0) / 50.0 * 30.0, 30.0), -30.0)
    pf_score = max(min((profit_factor - 1.0) / 2.0 * 20.0, 30.0), -20.0)
    mdd_penalty = max(min(mdd, 0.0), -30.0)  # mdd는 음수
    trade_penalty = 0.0
    if trade_count < 5:
        trade_penalty = -20.0
    elif trade_count < 10:
        trade_penalty = -10.0

    return exp_score + wr_score + pf_score + mdd_penalty + trade_penalty


if __name__ == "__main__":
    from engine.core.data_loader import load_ohlcv
    from engine.core.indicators import calc_indicators
    from engine.strategies.rulebook import default_rulebook
    from engine.market.context import get_market_history

    print("=== Backtest 테스트 (시계열 시장 컨텍스트 사용) ===")
    df = load_ohlcv("379800", years=5)
    df = calc_indicators(df)
    print(f"OHLCV: {len(df)} rows")

    market_hist = get_market_history(years=6)
    print(f"market_history: {len(market_hist)} rows")

    rb = default_rulebook("379800", asset_type="korean_etf", direction="long")
    rb.signal_threshold = 2.0
    rb.exit_strategy = "hybrid"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 3.0
    rb.trailing_atr = 1.5
    rb.max_holding_days = 20
    rb.base_position_ratio = 0.7
    rb.add_buy_enabled = True
    rb.add_buy_trigger_profit_pct = 1.5
    rb.add_buy_max_count = 1
    rb.add_buy_size_ratio = 0.5
    rb.market_score_weight = 0.5

    result = run_backtest(
        rb, df,
        position_limit_krw=120000,
        market_history_df=market_hist,
        sector_name="tech",
    )
    print(f"\n결과:")
    print(f"  거래수: {result.trade_count} (승 {result.win_count} / 패 {result.loss_count})")
    print(f"  승률: {result.win_rate:.2f}%")
    print(f"  평균 수익률: {result.avg_return_pct:+.3f}%")
    print(f"  평균 이익: {result.avg_win_pct:+.3f}% / 평균 손실: {result.avg_loss_pct:+.3f}%")
    print(f"  기대값: {result.expectancy_pct:+.3f}%")
    print(f"  MDD: {result.max_drawdown_pct:.2f}%")
    print(f"  Profit Factor: {result.profit_factor:.3f}")
    print(f"  Sharpe-like: {result.sharpe_like:.3f}")
    print(f"  Fitness: {result.fitness:.3f}")
    if result.trades:
        print(f"\n샘플 거래:")
        t = result.trades[0]
        print(f"  진입 {t.get('entry_date')} @ {t.get('entry_price'):.0f} ({t.get('shares')}주)")
        print(f"  청산 {t.get('exit_date')} @ {t.get('exit_price'):.2f} ({t.get('exit_reason')})")
        print(f"  PnL: {t.get('pnl_pct'):+.3f}% ({t.get('pnl_krw'):+.0f} KRW)")


def _calc_fitness_spread(scores: list, rets: list) -> float:
    """
    전체 날 기준 fitness: 신호점수 상위30% 실현수익 - 하위30% 실현수익(SPREAD).
    표본수 보정(sample_factor)으로 강신호 날이 너무 적으면 패널티.
    """
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

    # 강신호(상위30%) 표본수 보정
    if k < 5:
        sf = 0.3
    elif k < 10:
        sf = 0.6
    elif k < 30:
        sf = 0.85
    else:
        sf = 1.0

    # 순위상관 보조항 (약하게 반영)
    try:
        from scipy.stats import spearmanr as _sr
        rho, _ = _sr(sc, rt)
        rho = 0.0 if rho != rho else float(rho)
    except Exception:
        rho = 0.0

    return float(spread * sf + rho * 1.0)
