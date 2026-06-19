"""Policy-search driver for the central-controller backtester.

This module does not implement a new simulator. It repeatedly calls
``run_central_backtest`` with different coarse policy parameters and ranks the
results with a robustness-oriented score.
"""
from __future__ import annotations

import statistics
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from engine.central.allocation_policy import AllocationParams
from engine.central.backtester import BacktestResult, run_central_backtest
from engine.central.entity_loader import EntityRecord
from engine.central.search_space import SearchSpace, default_search_space
from engine.central.signal_collector import CacheOnlyDataProvider


@dataclass(frozen=True)
class EvalPeriod:
    label: str
    start: str
    end: str


@dataclass(frozen=True)
class SearchSettings:
    total_capital: float = 100_000.0
    per_ticker_exposure_cap: float = 0.25
    cash_buffer_ratio: float = 0.98
    min_trades_for_full_score: int = 10
    low_trade_penalty_floor: float = 0.25
    low_trade_shortfall_penalty: float = 5.0
    random_seed: int = 0
    ledger_root: str = ""


@dataclass(frozen=True)
class PeriodScore:
    label: str
    start: str
    end: str
    total_return: float
    max_drawdown_pct: float
    trades: int
    rejected: int
    final_equity: float
    reconcile_failures: int


@dataclass(frozen=True)
class CandidateResult:
    rank: int
    params: dict
    allocation_params: dict
    robust_score: float
    mean_return: float
    worst_return: float
    return_stdev: float
    total_return_sum: float
    max_drawdown_pct: float
    trades: int
    rejected: int
    reconcile_failures: int
    period_scores: list[PeriodScore] = field(default_factory=list)


@dataclass(frozen=True)
class SearchResult:
    best: Optional[CandidateResult]
    candidates: list[CandidateResult]
    method: str
    evaluated_count: int
    period_count: int

    def to_dict(self) -> dict:
        return {
            "best": _candidate_to_dict(self.best) if self.best else None,
            "candidates": [_candidate_to_dict(c) for c in self.candidates],
            "method": self.method,
            "evaluated_count": self.evaluated_count,
            "period_count": self.period_count,
        }


def run_policy_search(
    entities: Iterable[EntityRecord],
    eval_periods: Sequence[EvalPeriod | dict | tuple],
    space: Optional[SearchSpace] = None,
    *,
    method: str = "grid",
    n_random: Optional[int] = None,
    settings: Optional[SearchSettings] = None,
    data_provider_factory: Optional[Callable[[], CacheOnlyDataProvider]] = None,
) -> SearchResult:
    entity_list = list(entities)
    periods = normalize_eval_periods(eval_periods)
    if not entity_list:
        raise ValueError("entities required")
    if not periods:
        raise ValueError("eval_periods required")
    search_space = space or default_search_space()
    cfg = settings or SearchSettings()
    combos = _parameter_combinations(search_space, method=method, n_random=n_random, seed=cfg.random_seed)
    provider_factory = data_provider_factory or (lambda: CacheOnlyDataProvider())

    rows: list[CandidateResult] = []
    for idx, params in enumerate(combos, start=1):
        metric = str(params.get("confidence_metric") or "expectancy")
        adjusted_entities = apply_confidence_metric(entity_list, metric)
        alloc = AllocationParams(
            max_positions=int(params["max_positions"]),
            confidence_weight=float(params["confidence_weight"]),
            signal_strength_weight=float(params["signal_strength_weight"]),
            min_confidence=float(params["min_confidence"]),
            per_ticker_exposure_cap=float(cfg.per_ticker_exposure_cap),
            total_capital=float(cfg.total_capital),
            position_sizing=str(params["position_sizing"]),
            cash_buffer_ratio=float(cfg.cash_buffer_ratio),
        )
        period_scores: list[PeriodScore] = []
        for period in periods:
            provider = provider_factory()
            ledger_dir = _ledger_dir(cfg, idx, period.label)
            bt = run_central_backtest(
                adjusted_entities,
                period.start,
                period.end,
                alloc,
                data_provider=provider,
                ledger_dir=ledger_dir,
            )
            period_scores.append(_period_score(period, bt))
        rows.append(_candidate_result(0, params, alloc, period_scores, cfg))

    rows.sort(key=lambda r: (r.robust_score, r.mean_return, -abs(r.max_drawdown_pct), r.trades), reverse=True)
    ranked = [replace(row, rank=i) for i, row in enumerate(rows, start=1)]
    return SearchResult(
        best=ranked[0] if ranked else None,
        candidates=ranked,
        method=str(method or "grid"),
        evaluated_count=len(ranked),
        period_count=len(periods),
    )


def normalize_eval_periods(eval_periods: Sequence[EvalPeriod | dict | tuple]) -> list[EvalPeriod]:
    periods: list[EvalPeriod] = []
    for i, value in enumerate(eval_periods, start=1):
        if isinstance(value, EvalPeriod):
            periods.append(value)
        elif isinstance(value, dict):
            start = str(value.get("start") or "")
            end = str(value.get("end") or "")
            label = str(value.get("label") or f"period_{i}")
            periods.append(EvalPeriod(label=label, start=start, end=end))
        elif isinstance(value, tuple) and len(value) >= 2:
            if len(value) >= 3:
                label, start, end = str(value[0]), str(value[1]), str(value[2])
            else:
                label, start, end = f"period_{i}", str(value[0]), str(value[1])
            periods.append(EvalPeriod(label=label, start=start, end=end))
        else:
            raise ValueError(f"unsupported eval period: {value!r}")
    for p in periods:
        if not p.start or not p.end:
            raise ValueError(f"invalid eval period: {p}")
    return periods


def apply_confidence_metric(entities: Iterable[EntityRecord], metric: str) -> list[EntityRecord]:
    metric_name = str(metric or "expectancy").lower()
    return [replace(entity, confidence=confidence_from_metrics(entity.validation_metrics, metric_name)) for entity in entities]


def confidence_from_metrics(validation_metrics: dict, metric: str) -> float:
    rows = [dict(v or {}) for v in (validation_metrics or {}).values()]
    if not rows:
        return 0.0
    if metric == "win_rate":
        values = [_float(row.get("win_rate")) for row in rows]
        avg = sum(values) / len(values)
        return avg / 100.0 if avg > 1.0 else avg
    if metric == "profit_factor":
        values = [_float(row.get("profit_factor")) for row in rows]
        return sum(values) / len(values) - 1.0
    values = [_float(row.get("expectancy_pct")) for row in rows]
    return sum(values) / len(values) / 10.0


def robust_score_from_returns(
    returns: Sequence[float],
    *,
    max_drawdown_pct: float = 0.0,
    trades: int = 0,
    min_trades_for_full_score: int = 10,
    low_trade_penalty_floor: float = 0.25,
    low_trade_shortfall_penalty: float = 5.0,
) -> float:
    if not returns:
        return float("-inf")
    rets = [float(x or 0.0) for x in returns]
    mean_return = sum(rets) / len(rets)
    worst_return = min(rets)
    stdev = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    drawdown_penalty = abs(float(max_drawdown_pct or 0.0)) * 0.25
    raw = mean_return + worst_return - stdev - drawdown_penalty
    if min_trades_for_full_score <= 0:
        return float(raw)
    trade_ratio = max(0.0, min(float(trades) / float(min_trades_for_full_score), 1.0))
    floor = max(0.0, min(float(low_trade_penalty_floor), 1.0))
    multiplier = floor + (1.0 - floor) * trade_ratio
    shortfall_penalty = max(float(low_trade_shortfall_penalty or 0.0), 0.0) * (1.0 - trade_ratio)
    return float(raw * multiplier - shortfall_penalty)


def _parameter_combinations(space: SearchSpace, *, method: str, n_random: Optional[int], seed: int) -> list[dict]:
    mode = str(method or "grid").lower()
    if mode == "grid":
        return list(space.grid())
    if mode == "random":
        return space.random_sample(int(n_random or 0), seed=seed)
    raise ValueError(f"unsupported search method: {method}")


def _period_score(period: EvalPeriod, result: BacktestResult) -> PeriodScore:
    return PeriodScore(
        label=period.label,
        start=period.start,
        end=period.end,
        total_return=float(result.total_return or 0.0),
        max_drawdown_pct=float(result.max_drawdown_pct or 0.0),
        trades=len(result.trades),
        rejected=int(getattr(result, "rejected_order_count", 0) or 0),
        final_equity=float(result.final_equity or 0.0),
        reconcile_failures=len(result.reconcile_failures),
    )


def _candidate_result(rank: int, params: dict, alloc: AllocationParams, periods: list[PeriodScore], settings: SearchSettings) -> CandidateResult:
    returns = [p.total_return for p in periods]
    mean_return = sum(returns) / len(returns) if returns else 0.0
    worst_return = min(returns) if returns else 0.0
    stdev = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    max_dd = min((p.max_drawdown_pct for p in periods), default=0.0)
    trades = sum(p.trades for p in periods)
    rejected = sum(p.rejected for p in periods)
    reconcile_failures = sum(p.reconcile_failures for p in periods)
    robust = robust_score_from_returns(
        returns,
        max_drawdown_pct=max_dd,
        trades=trades,
        min_trades_for_full_score=settings.min_trades_for_full_score,
        low_trade_penalty_floor=settings.low_trade_penalty_floor,
        low_trade_shortfall_penalty=settings.low_trade_shortfall_penalty,
    )
    if reconcile_failures:
        robust -= 1_000_000.0
    return CandidateResult(
        rank=rank,
        params=dict(params),
        allocation_params=asdict(alloc),
        robust_score=float(robust),
        mean_return=float(mean_return),
        worst_return=float(worst_return),
        return_stdev=float(stdev),
        total_return_sum=float(sum(returns)),
        max_drawdown_pct=float(max_dd),
        trades=int(trades),
        rejected=int(rejected),
        reconcile_failures=int(reconcile_failures),
        period_scores=list(periods),
    )


def _ledger_dir(settings: SearchSettings, combo_idx: int, label: str) -> str:
    safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(label or "period"))[:40]
    if settings.ledger_root:
        root = Path(settings.ledger_root)
        return str(root / f"combo_{combo_idx:05d}_{safe_label}")
    return tempfile.mkdtemp(prefix=f"central_policy_search_{combo_idx:05d}_{safe_label}_")


def _candidate_to_dict(row: CandidateResult) -> dict:
    payload = asdict(row)
    payload["period_scores"] = [asdict(p) for p in row.period_scores]
    return payload


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0
