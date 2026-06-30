"""Elite shadow candidate report builder.

무엇을 하는 파일인가:
- 기존 중단/완료 batch 산출물에서 FIX형 정예 후보를 읽어온다.
- broker 주문, live runner, positions.json, parameters.json은 절대 수정하지 않는다.
- stage2/stage3 룰북별 과거 trade dump를 모아 "그 룰북대로 거래했다면" 거래별 수익표를 만든다.
- API 서버가 /api/live/elite_shadow 에서 read-only dashboard 데이터로 사용한다.

주의:
- 이 모듈은 research artifact를 읽기만 한다.
- 느린 파일 스캔이므로 api_server_aftermarket 쪽에서 메모리 캐시를 둔다.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path("exp_batch_stage123_2009_20260616_full")
CENTRAL_INDEX = ROOT / "central_index.jsonl"
TICKERS_ROOT = ROOT / "tickers"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _load_jsonl(path: Path, *, limit: int | None = None):
    if not path.exists():
        return
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
                count += 1
                if limit is not None and count >= limit:
                    return
            except Exception:
                continue


def _load_source_row(rel_path: str | None, one_based_index: int) -> dict[str, Any] | None:
    if not rel_path or one_based_index <= 0:
        return None
    path = ROOT / rel_path
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, 1):
            if idx != one_based_index:
                continue
            try:
                return json.loads(line)
            except Exception:
                return None
    return None


def _metrics_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    labels = [
        "train_1_eval",
        "train_2_eval",
        "train_3_eval",
        "oos_2025h2",
        "stress_pre_2022h1",
    ]
    periods = [label for label in labels if isinstance(metrics.get(label), dict)]
    exps = [_safe_float(metrics[label].get("expectancy_pct")) for label in periods]
    fits = [_safe_float(metrics[label].get("fitness")) for label in periods]
    dds = [_safe_float(metrics[label].get("max_drawdown_pct")) for label in periods]
    wins = [_safe_float(metrics[label].get("win_rate")) for label in periods]
    trades = [_safe_int(metrics[label].get("trade_count")) for label in periods]
    oos = metrics.get("oos_2025h2") if isinstance(metrics.get("oos_2025h2"), dict) else {}
    stress = metrics.get("stress_pre_2022h1") if isinstance(metrics.get("stress_pre_2022h1"), dict) else {}
    return {
        "periods": periods,
        "min_expectancy_pct": min(exps) if exps else 0.0,
        "avg_expectancy_pct": sum(exps) / len(exps) if exps else 0.0,
        "oos_expectancy_pct": _safe_float(oos.get("expectancy_pct")),
        "stress_expectancy_pct": _safe_float(stress.get("expectancy_pct")),
        "oos_fitness": _safe_float(oos.get("fitness")),
        "min_fitness": min(fits) if fits else 0.0,
        "worst_drawdown_pct": min(dds) if dds else 0.0,
        "oos_drawdown_pct": _safe_float(oos.get("max_drawdown_pct")),
        "oos_win_rate": _safe_float(oos.get("win_rate")),
        "min_win_rate": min(wins) if wins else 0.0,
        "oos_trade_count": _safe_int(oos.get("trade_count")),
        "min_trade_count": min(trades) if trades else 0,
    }


def _elite_score(metrics: dict[str, Any], rulebook: dict[str, Any]) -> float:
    exit_strategy = str(rulebook.get("exit_strategy") or "")
    exit_bonus = 8.0 if exit_strategy == "trailing" else 2.0 if exit_strategy == "hybrid" else -6.0
    omen_bonus = 4.0 if bool(rulebook.get("sell_omen_enabled")) else -2.0
    hold = _safe_int(rulebook.get("max_holding_days"), 99)
    hold_bonus = 6.0 if 8 <= hold <= 19 else 2.0 if 5 <= hold <= 24 else -6.0
    market_adj = _safe_float(rulebook.get("market_adjustment_strength"))
    adj_bonus = 4.0 if market_adj >= 0.25 else -5.0 if market_adj < 0.05 else 0.0
    dd_bonus = max(-20.0, min(12.0, (20.0 + _safe_float(metrics.get("worst_drawdown_pct"))) / 2.0))
    return (
        _safe_float(metrics.get("oos_fitness")) * 0.25
        + _safe_float(metrics.get("oos_expectancy_pct")) * 8.0
        + _safe_float(metrics.get("oos_win_rate")) * 0.15
        + _safe_float(metrics.get("avg_expectancy_pct")) * 3.0
        + dd_bonus
        + exit_bonus
        + omen_bonus
        + hold_bonus
        + adj_bonus
    )


def _is_market_adj_ok(rulebook: dict[str, Any], metrics: dict[str, Any]) -> bool:
    market_adj = _safe_float(rulebook.get("market_adjustment_strength"))
    if market_adj >= 0.05:
        return True
    # FCFS 같은 안정형 예외: 시장 보정이 약해도 승률/fitness/DD가 압도적으로 좋으면 shadow에는 남긴다.
    return (
        _safe_float(metrics.get("oos_win_rate")) >= 90.0
        and _safe_float(metrics.get("oos_fitness")) >= 95.0
        and _safe_float(metrics.get("worst_drawdown_pct")) > -8.0
    )


def _rulebook_passes_anti_pattern_filter(rulebook: dict[str, Any], metrics: dict[str, Any], *, stage: str) -> tuple[bool, str]:
    exp = _safe_float(rulebook.get("expectancy_pct"), _safe_float(metrics.get("oos_expectancy_pct")))
    fit = _safe_float(rulebook.get("fitness"), _safe_float(metrics.get("oos_fitness")))
    hold = _safe_int(rulebook.get("max_holding_days"), 99)
    target_atr = _safe_float(rulebook.get("take_profit_atr"))
    stop_atr = _safe_float(rulebook.get("stop_loss_atr"))
    if exp < 2.7:
        return False, "rulebook_expectancy_lt_2.7"
    if fit < (70.0 if stage == "stage2" else 45.0):
        return False, "rulebook_fitness_too_low"
    if not _is_market_adj_ok(rulebook, metrics):
        return False, "market_adjustment_too_weak"
    if str(rulebook.get("exit_strategy") or "") == "fixed" and hold > 19:
        return False, "fixed_exit_long_holding"
    if hold > 24:
        return False, "holding_days_gt_24"
    if target_atr and target_atr < 1.2:
        return False, "target_too_small"
    if stop_atr and stop_atr > 3.5 and target_atr and target_atr < 2.5:
        return False, "bad_target_stop_shape"
    return True, "pass"


def _stage2_candidate_from_row(row: dict[str, Any], source_row: dict[str, Any]) -> dict[str, Any]:
    rulebook = source_row.get("rulebook") or {}
    metrics = _metrics_summary(row.get("metrics") or {})
    candidate = {
        "candidate_id": f"stage2:{row.get('ticker')}:{str(row.get('rulebook_hash') or '')[:12]}",
        "stage": "stage2",
        "ticker": row.get("ticker"),
        "rulebook_hash": row.get("rulebook_hash") or source_row.get("rulebook_hash"),
        "rulebook_hash_short": str(row.get("rulebook_hash") or source_row.get("rulebook_hash") or "")[:12],
        "source_file": row.get("source_file"),
        "source_row_index": _safe_int(row.get("source_row_index")),
        "trade_file": (row.get("artifact_paths") or {}).get("trades"),
        "rulebook": {
            "exit_strategy": rulebook.get("exit_strategy"),
            "max_holding_days": _safe_int(rulebook.get("max_holding_days")),
            "sell_omen_enabled": bool(rulebook.get("sell_omen_enabled")),
            "market_adjustment_strength": _safe_float(rulebook.get("market_adjustment_strength")),
            "market_score_weight": _safe_float(rulebook.get("market_score_weight")),
            "sector_strength_weight": _safe_float(rulebook.get("sector_strength_weight")),
            "take_profit_atr": _safe_float(rulebook.get("take_profit_atr")),
            "stop_loss_atr": _safe_float(rulebook.get("stop_loss_atr")),
            "trailing_atr": _safe_float(rulebook.get("trailing_atr")),
            "signal_threshold": _safe_float(rulebook.get("signal_threshold")),
            "position_sizing_strategy": rulebook.get("position_sizing_strategy"),
        },
        "metrics": metrics,
    }
    candidate["elite_score"] = round(_elite_score(metrics, rulebook), 6)
    candidate["bucket"] = _bucket_for_candidate(candidate)
    return candidate


def _bucket_for_candidate(candidate: dict[str, Any]) -> str:
    m = candidate.get("metrics") or {}
    rb = candidate.get("rulebook") or {}
    if (
        _safe_float(m.get("oos_expectancy_pct")) >= 3.0
        and _safe_float(m.get("oos_fitness")) >= 90.0
        and _safe_float(m.get("oos_win_rate")) >= 80.0
        and _safe_int(m.get("oos_trade_count")) >= 15
        and _safe_float(m.get("stress_expectancy_pct")) >= 1.0
        and _safe_float(m.get("worst_drawdown_pct")) > -16.0
        and str(rb.get("exit_strategy") or "") in {"trailing", "hybrid"}
    ):
        return "A_core"
    if _safe_float(m.get("oos_win_rate")) >= 90.0 and _safe_float(m.get("oos_fitness")) >= 95.0:
        return "B_stable"
    if bool(rb.get("sell_omen_enabled")) and str(rb.get("exit_strategy") or "") == "trailing":
        return "C_momentum"
    return "watch"


def collect_stage2_elite(*, max_unique: int = 60) -> tuple[list[dict[str, Any]], Counter]:
    skipped: Counter = Counter()
    rows: list[dict[str, Any]] = []
    if not CENTRAL_INDEX.exists():
        skipped["central_index_missing"] += 1
        return [], skipped
    for row in _load_jsonl(CENTRAL_INDEX):
        if not row:
            continue
        if not row.get("eligible", True):
            skipped["not_eligible"] += 1
            continue
        if row.get("stage") != "stage2":
            skipped["not_stage2"] += 1
            continue
        metrics = _metrics_summary(row.get("metrics") or {})
        if metrics["oos_expectancy_pct"] < 2.7:
            skipped["oos_expectancy_lt_2.7"] += 1
            continue
        if metrics["oos_fitness"] < 70.0:
            skipped["oos_fitness_lt_70"] += 1
            continue
        if metrics["oos_trade_count"] < 15:
            skipped["oos_trades_lt_15"] += 1
            continue
        if metrics["oos_win_rate"] < 70.0:
            skipped["oos_win_lt_70"] += 1
            continue
        if metrics["stress_expectancy_pct"] < 0.5:
            skipped["stress_expectancy_lt_0.5"] += 1
            continue
        if metrics["worst_drawdown_pct"] <= -18.0:
            skipped["worst_drawdown_lte_-18"] += 1
            continue
        if metrics["min_trade_count"] < 8:
            skipped["min_trades_lt_8"] += 1
            continue
        source = _load_source_row(row.get("source_file"), _safe_int(row.get("source_row_index")))
        if not source:
            skipped["source_row_missing"] += 1
            continue
        ok, reason = _rulebook_passes_anti_pattern_filter(source.get("rulebook") or {}, metrics, stage="stage2")
        if not ok:
            skipped[reason] += 1
            continue
        rows.append(_stage2_candidate_from_row(row, source))

    rows.sort(key=lambda item: (item["elite_score"], item["metrics"]["oos_fitness"], item["metrics"]["oos_expectancy_pct"]), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(row)
        if len(out) >= max_unique:
            break
    return out, skipped


def collect_stage3_elite(*, max_unique: int = 80) -> tuple[list[dict[str, Any]], Counter]:
    skipped: Counter = Counter()
    rows: list[dict[str, Any]] = []
    if not TICKERS_ROOT.exists():
        skipped["tickers_root_missing"] += 1
        return [], skipped
    for path in TICKERS_ROOT.glob("*/stage3/final_rulebooks.jsonl"):
        ticker = path.parts[-3]
        for row in _load_jsonl(path):
            rulebook = row.get("rulebook") or {}
            metrics_raw = row.get("bull_metrics") or row.get("stress_metrics") or {}
            metrics = {
                "periods": ["stage3_bull"],
                "min_expectancy_pct": _safe_float(metrics_raw.get("expectancy_pct") or metrics_raw.get("avg_return_pct")),
                "avg_expectancy_pct": _safe_float(metrics_raw.get("expectancy_pct") or metrics_raw.get("avg_return_pct")),
                "oos_expectancy_pct": _safe_float(metrics_raw.get("expectancy_pct") or metrics_raw.get("avg_return_pct")),
                "stress_expectancy_pct": _safe_float((row.get("stress_metrics") or {}).get("expectancy_pct")),
                "oos_fitness": _safe_float(metrics_raw.get("fitness")),
                "min_fitness": _safe_float(metrics_raw.get("fitness")),
                "worst_drawdown_pct": _safe_float(metrics_raw.get("max_drawdown_pct")),
                "oos_drawdown_pct": _safe_float(metrics_raw.get("max_drawdown_pct")),
                "oos_win_rate": _safe_float(metrics_raw.get("win_rate")),
                "min_win_rate": _safe_float(metrics_raw.get("win_rate")),
                "oos_trade_count": _safe_int(metrics_raw.get("trade_count")),
                "min_trade_count": _safe_int(metrics_raw.get("trade_count")),
            }
            if metrics["oos_expectancy_pct"] < 2.7:
                skipped["expectancy_lt_2.7"] += 1
                continue
            if metrics["oos_fitness"] < 45.0:
                skipped["fitness_lt_45"] += 1
                continue
            if metrics["oos_win_rate"] < 70.0:
                skipped["win_lt_70"] += 1
                continue
            if metrics["oos_trade_count"] < 8:
                skipped["trades_lt_8"] += 1
                continue
            if metrics["worst_drawdown_pct"] <= -18.0:
                skipped["drawdown_lte_-18"] += 1
                continue
            ok, reason = _rulebook_passes_anti_pattern_filter(rulebook, metrics, stage="stage3")
            if not ok:
                skipped[reason] += 1
                continue
            candidate = {
                "candidate_id": f"stage3:{ticker}:{str(row.get('rulebook_hash') or '')[:12]}",
                "stage": "stage3",
                "ticker": ticker,
                "rulebook_hash": row.get("rulebook_hash"),
                "rulebook_hash_short": str(row.get("rulebook_hash") or "")[:12],
                "entry_rulebook_hash": row.get("entry_rulebook_hash"),
                "entry_rank": row.get("entry_rank"),
                "exit_rank": row.get("exit_rank"),
                "trade_file": str(path.parent / "exit_trades.jsonl"),
                "source_file": str(path),
                "rulebook": {
                    "exit_strategy": rulebook.get("exit_strategy"),
                    "max_holding_days": _safe_int(rulebook.get("max_holding_days")),
                    "sell_omen_enabled": bool(rulebook.get("sell_omen_enabled")),
                    "market_adjustment_strength": _safe_float(rulebook.get("market_adjustment_strength")),
                    "market_score_weight": _safe_float(rulebook.get("market_score_weight")),
                    "sector_strength_weight": _safe_float(rulebook.get("sector_strength_weight")),
                    "take_profit_atr": _safe_float(rulebook.get("take_profit_atr")),
                    "stop_loss_atr": _safe_float(rulebook.get("stop_loss_atr")),
                    "trailing_atr": _safe_float(rulebook.get("trailing_atr")),
                    "signal_threshold": _safe_float(rulebook.get("signal_threshold")),
                    "position_sizing_strategy": rulebook.get("position_sizing_strategy"),
                },
                "metrics": metrics,
                "composite_fitness": _safe_float(row.get("composite_fitness")),
            }
            candidate["elite_score"] = round(_elite_score(metrics, rulebook), 6)
            candidate["bucket"] = _bucket_for_candidate(candidate)
            rows.append(candidate)
    rows.sort(key=lambda item: (item["elite_score"], item["metrics"]["oos_fitness"], item["metrics"]["oos_expectancy_pct"]), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(row)
        if len(out) >= max_unique:
            break
    return out, skipped


def _normalize_trade(row: dict[str, Any], *, stage: str) -> dict[str, Any]:
    return {
        "period_label": row.get("period_label"),
        "entry_date": row.get("entry_date") or row.get("entry_signal_date"),
        "exit_date": row.get("exit_date"),
        "entry_price": _safe_float(row.get("entry_price")),
        "exit_price": _safe_float(row.get("exit_price")),
        "pnl_pct": _safe_float(row.get("pnl_pct") or row.get("stress_pnl_pct")),
        "holding_days": _safe_int(row.get("holding_days")),
        "exit_reason": row.get("exit_reason"),
        "max_profit_during_hold": _safe_float(row.get("max_profit_during_hold")),
        "max_loss_during_hold": _safe_float(row.get("max_loss_during_hold")),
        "entry_signal_score": _safe_float(row.get("entry_signal_score")),
        "entry_signal_threshold": _safe_float(row.get("entry_signal_threshold")),
        "stage": stage,
    }


def _summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "avg_pnl_pct": 0.0,
            "sum_pnl_pct": 0.0,
            "compounded_pct": 0.0,
            "avg_mfe_pct": 0.0,
            "avg_mae_pct": 0.0,
            "best_trade_pct": 0.0,
            "worst_trade_pct": 0.0,
        }
    pnls = [_safe_float(t.get("pnl_pct")) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    compounded = 1.0
    for pnl in pnls:
        compounded *= 1.0 + pnl / 100.0
    mfes = [_safe_float(t.get("max_profit_during_hold")) for t in trades]
    maes = [_safe_float(t.get("max_loss_during_hold")) for t in trades]
    return {
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(trades) * 100.0,
        "avg_pnl_pct": sum(pnls) / len(pnls),
        "sum_pnl_pct": sum(pnls),
        "compounded_pct": (compounded - 1.0) * 100.0,
        "avg_mfe_pct": sum(mfes) / len(mfes) if mfes else 0.0,
        "avg_mae_pct": sum(maes) / len(maes) if maes else 0.0,
        "best_trade_pct": max(pnls),
        "worst_trade_pct": min(pnls),
    }


def attach_trades(candidates: list[dict[str, Any]], *, max_trades_per_candidate: int = 300) -> None:
    for candidate in candidates:
        stage = str(candidate.get("stage") or "")
        ticker = str(candidate.get("ticker") or "").upper()
        rulebook_hash = str(candidate.get("rulebook_hash") or "")
        if stage == "stage2":
            trade_rel = candidate.get("trade_file")
            trade_path = ROOT / str(trade_rel) if trade_rel else None
            hash_key = "rulebook_hash"
        elif stage == "stage3":
            trade_path = Path(str(candidate.get("trade_file") or ""))
            hash_key = "final_rulebook_hash"
        else:
            trade_path = None
            hash_key = "rulebook_hash"
        trades: list[dict[str, Any]] = []
        if trade_path and trade_path.exists():
            for row in _load_jsonl(trade_path):
                if str(row.get(hash_key) or "") != rulebook_hash:
                    continue
                trades.append(_normalize_trade(row, stage=stage))
                if len(trades) >= max_trades_per_candidate:
                    break
        trades.sort(key=lambda item: str(item.get("entry_date") or ""))
        candidate["trade_summary"] = _summarize_trades(trades)
        candidate["trades"] = trades
        candidate["shadow_label"] = f"{ticker} {stage.upper()} {candidate.get('rulebook_hash_short')}"


def build_elite_shadow_report(*, stage2_limit: int = 60, stage3_limit: int = 80, include_trades: bool = True) -> dict[str, Any]:
    stage2, skip2 = collect_stage2_elite(max_unique=stage2_limit)
    stage3, skip3 = collect_stage3_elite(max_unique=stage3_limit)
    candidates = stage2 + stage3
    candidates.sort(key=lambda item: (item.get("bucket") != "A_core", -float(item.get("elite_score") or 0.0)))
    if include_trades:
        attach_trades(candidates)
    buckets = Counter(str(c.get("bucket") or "watch") for c in candidates)
    stages = Counter(str(c.get("stage") or "") for c in candidates)
    return {
        "_comment": "Read-only elite shadow report. This is not broker/paper trading; it reconstructs historical trades from batch artifacts and ranks candidates for shadow promotion.",
        "source_root": str(ROOT),
        "filters": {
            "oos_expectancy_pct_min": 2.7,
            "stage2_oos_fitness_min": 70.0,
            "stage3_fitness_min": 45.0,
            "win_rate_min": "70% 기본, 안정형 예외는 90%+",
            "trade_count_min": "stage2 15, stage3 8",
            "stress_expectancy_min_stage2": 0.5,
            "worst_drawdown_pct_gt": -18.0,
            "max_holding_days_lte": 24,
            "fixed_exit_max_holding_days_lte": 19,
            "market_adjustment_exception": "market_adj >= 0.05 OR win>=90 fitness>=95 worstDD>-8",
        },
        "summary": {
            "candidate_count": len(candidates),
            "stage_counts": dict(stages),
            "bucket_counts": dict(buckets),
            "stage2_skip_top": skip2.most_common(12),
            "stage3_skip_top": skip3.most_common(12),
        },
        "candidates": candidates,
    }
