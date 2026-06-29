"""Manual-only early-exit/stagnation profiles for the live dashboard.

These profiles are intentionally advisory.  They must not trigger broker orders.
They summarize offline, entity-specific checks for cases where capital efficiency
improved by cutting stagnant trades earlier.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


EARLY_EXIT_PROFILE_VERSION = "current_queue_stage2_20260629_manual_only_v1"

# Metrics are expressed in percentage points.
# validation.*_daily values are sum(pnl_pct) / sum(holding_days), in %/day.
EARLY_EXIT_PROFILES: list[dict[str, Any]] = [
    {
        "ticker": "FIX",
        "rulebook_hash_prefix": "cab7d458767d",
        "validation_status": "pass",
        "confidence_label": "검증통과",
        "check_day": 3,
        "metric": "close_return_pct",
        "operator": "lt",
        "threshold": 2.0,
        "rule_label": "3일째 수익률 < +2.0%",
        "action_label": "정체 청산 검토",
        "manual_only": True,
        "note": "FIX는 3일차까지 충분히 못 가면 회전율 개선 효과가 컸다. 자동청산이 아니라 수동 참고용이다.",
        "validation": {
            "trade_count": 104,
            "baseline_daily": {"all": 0.5201, "train": 0.6181, "oos": 0.6611, "stress": 0.1903},
            "profile_daily": {"all": 0.6524, "train": 0.7516, "oos": 0.7460, "stress": 0.3840},
            "delta_daily": {"all": 0.1323, "train": 0.1335, "oos": 0.0849, "stress": 0.1937},
            "early_count": 54,
        },
    },
    {
        "ticker": "DDS",
        "rulebook_hash_prefix": "57c9bbec4376",
        "validation_status": "pass",
        "confidence_label": "검증통과",
        "check_day": 5,
        "metric": "target_progress_pct",
        "operator": "lt",
        "threshold": 75.0,
        "rule_label": "5일째 목표 진행률 < 75%",
        "action_label": "정체 청산 검토",
        "manual_only": True,
        "note": "DDS는 목표 진행률 기준 조기 회전이 TRAIN/OOS/STRESS 모두에서 일평균 효율 개선으로 나왔다.",
        "validation": {
            "trade_count": 75,
            "baseline_daily": {"all": 0.3734, "train": 0.2837, "oos": 0.4404, "stress": 0.6379},
            "profile_daily": {"all": 0.7082, "train": 0.6210, "oos": 0.4739, "stress": 1.1204},
            "delta_daily": {"all": 0.3348, "train": 0.3373, "oos": 0.0335, "stress": 0.4825},
            "early_count": 42,
        },
    },
    {
        "ticker": "CMC",
        "rulebook_hash_prefix": "3e8513cf9a80",
        "validation_status": "pass",
        "confidence_label": "검증통과",
        "check_day": 3,
        "metric": "target_progress_pct",
        "operator": "lt",
        "threshold": 75.0,
        "rule_label": "3일째 목표 진행률 < 75%",
        "action_label": "정체 청산 검토",
        "manual_only": True,
        "note": "CMC는 빠른 목표 진행률 체크가 자본 회전율 기준으로 가장 강하게 개선됐다.",
        "validation": {
            "trade_count": 77,
            "baseline_daily": {"all": 0.4384, "train": 0.4027, "oos": 0.6386, "stress": 0.3877},
            "profile_daily": {"all": 0.6414, "train": 0.5501, "oos": 0.7593, "stress": 0.7235},
            "delta_daily": {"all": 0.2030, "train": 0.1474, "oos": 0.1207, "stress": 0.3358},
            "early_count": 49,
        },
    },
    {
        "ticker": "CPRX",
        "rulebook_hash_prefix": "5fd0bffc0d05",
        "validation_status": "watch",
        "confidence_label": "추가검증",
        "check_day": 7,
        "metric": "close_return_pct",
        "operator": "lt",
        "threshold": 2.0,
        "rule_label": "7일째 수익률 < +2.0%",
        "action_label": "정체 청산 후보",
        "manual_only": True,
        "note": "TRAIN 최적 조건은 OOS/STRESS에서 실패했지만, robust 대안은 OOS/STRESS가 개선됐다. 자동 적용 금지, 수동 참고만.",
        "validation": {
            "trade_count": 104,
            "baseline_daily": {"all": 0.4314, "train": 0.4622, "oos": 0.3932, "stress": 0.3976},
            "profile_daily": {"all": 0.4943, "train": 0.5295, "oos": 0.4201, "stress": 0.4763},
            "delta_daily": {"all": 0.0629, "train": 0.0673, "oos": 0.0269, "stress": 0.0787},
            "early_count": 40,
        },
    },
    {
        "ticker": "FCFS",
        "rulebook_hash_prefix": "095a81be33a5",
        "validation_status": "watch",
        "confidence_label": "추가검증",
        "check_day": 4,
        "metric": "close_return_pct",
        "operator": "lt",
        "threshold": -1.0,
        "rule_label": "4일째 수익률 < -1.0%",
        "action_label": "정체 청산 후보",
        "manual_only": True,
        "note": "TRAIN 최적 조건은 STRESS가 깨졌고, 이 보수적 대안은 OOS/STRESS가 개선됐다. 자동 적용 금지, 수동 참고만.",
        "validation": {
            "trade_count": 91,
            "baseline_daily": {"all": 0.2861, "train": 0.3066, "oos": 0.3892, "stress": 0.1597},
            "profile_daily": {"all": 0.3373, "train": 0.3294, "oos": 0.4194, "stress": 0.2897},
            "delta_daily": {"all": 0.0512, "train": 0.0228, "oos": 0.0302, "stress": 0.1300},
            "early_count": 21,
        },
    },
    {
        "ticker": "CHEF",
        "rulebook_hash_prefix": "32a2b0644e5a",
        "validation_status": "hold",
        "confidence_label": "보류",
        "check_day": 5,
        "metric": "max_profit_pct",
        "operator": "lt",
        "threshold": 0.5,
        "rule_label": "5일째 최대상승폭 < +0.5%",
        "action_label": "정체 참고만",
        "manual_only": True,
        "note": "개선폭이 작고 선택 조건은 OOS가 악화됐다. 경고가 떠도 자동청산 근거로 쓰지 않는다.",
        "validation": {
            "trade_count": 73,
            "baseline_daily": {"all": 0.3736, "train": 0.4467, "oos": 0.1029, "stress": 0.4742},
            "profile_daily": {"all": 0.3790, "train": 0.4483, "oos": 0.1176, "stress": 0.4742},
            "delta_daily": {"all": 0.0054, "train": 0.0016, "oos": 0.0147, "stress": 0.0000},
            "early_count": 5,
        },
    },
    {
        "ticker": "COPX",
        "rulebook_hash_prefix": "8f8208b83e15",
        "validation_status": "hold",
        "confidence_label": "보류",
        "check_day": 8,
        "metric": "close_return_pct",
        "operator": "lt",
        "threshold": 1.5,
        "rule_label": "8일째 수익률 < +1.5%",
        "action_label": "정체 참고만",
        "manual_only": True,
        "note": "일부 robust 대안은 있으나 선택 조건의 OOS 악화와 상품 성격상 자동 적용 보류.",
        "validation": {
            "trade_count": 111,
            "baseline_daily": {"all": 0.2898, "train": 0.3270, "oos": 0.3701, "stress": 0.1550},
            "profile_daily": {"all": 0.3049, "train": 0.3457, "oos": 0.3701, "stress": 0.1728},
            "delta_daily": {"all": 0.0151, "train": 0.0187, "oos": 0.0000, "stress": 0.0178},
            "early_count": 18,
        },
    },
    {
        "ticker": "ERX",
        "rulebook_hash_prefix": "e7b649250271",
        "validation_status": "hold",
        "confidence_label": "보류",
        "check_day": 5,
        "metric": "target_progress_pct",
        "operator": "lt",
        "threshold": 20.0,
        "rule_label": "5일째 목표 진행률 < 20%",
        "action_label": "정체 참고만",
        "manual_only": True,
        "note": "STRESS는 좋아졌지만 OOS가 악화됐다. 최대보유일도 짧아서 지표 영향이 제한적이다.",
        "validation": {
            "trade_count": 80,
            "baseline_daily": {"all": 0.7108, "train": 0.8164, "oos": 0.4320, "stress": 0.5398},
            "profile_daily": {"all": 0.7970, "train": 0.8861, "oos": 0.4122, "stress": 0.7123},
            "delta_daily": {"all": 0.0862, "train": 0.0697, "oos": -0.0198, "stress": 0.1725},
            "early_count": 24,
        },
    },
]


def _norm_ticker(value: Any) -> str:
    return str(value or "").upper().strip()


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def holding_days_from_entry(entry_date: Any, *, now: datetime | None = None) -> int | None:
    dt = _parse_dt(entry_date)
    if dt is None:
        return None
    cur = now or datetime.now(timezone.utc)
    return max(0, int((cur - dt).total_seconds() // 86400))


def find_early_exit_profile(ticker: Any, *, member_hash: Any = "", rulebook_hash: Any = "") -> dict[str, Any] | None:
    t = _norm_ticker(ticker)
    if not t:
        return None
    hashes = [str(member_hash or "").lower().strip(), str(rulebook_hash or "").lower().strip()]
    ticker_matches = [p for p in EARLY_EXIT_PROFILES if _norm_ticker(p.get("ticker")) == t]
    for profile in ticker_matches:
        prefix = str(profile.get("rulebook_hash_prefix") or "").lower().strip()
        if prefix and any(h.startswith(prefix) or prefix.startswith(h) for h in hashes if h):
            out = dict(profile)
            out["match_confidence"] = "rulebook_hash_prefix"
            return out
    if ticker_matches:
        out = dict(ticker_matches[0])
        out["match_confidence"] = "ticker_only"
        return out
    return None


def _metric_label(metric: str) -> str:
    return {
        "close_return_pct": "현재 수익률",
        "target_progress_pct": "목표 진행률",
        "daily_return_pct": "일평균 수익률",
        "max_profit_pct": "보유 중 최대상승폭",
    }.get(metric, metric)


def dashboard_early_exit_profile(
    ticker: Any,
    *,
    position: dict[str, Any] | None = None,
    current_price: Any = None,
    pnl_pct: Any = None,
    holding_days: Any = None,
) -> dict[str, Any] | None:
    """Return advisory early-exit profile plus current evaluation for dashboard.

    The returned data is explicitly manual-only and must not be used as an order
    trigger.  It is designed to help a human inspect stagnant holdings.
    """
    pos = position if isinstance(position, dict) else {}
    rb = pos.get("rulebook_snapshot") if isinstance(pos.get("rulebook_snapshot"), dict) else {}
    profile = find_early_exit_profile(
        ticker,
        member_hash=pos.get("member_hash"),
        rulebook_hash=pos.get("rulebook_hash") or rb.get("rulebook_hash") or rb.get("member_hash"),
    )
    if profile is None:
        return None
    entry = _safe_float(pos.get("entry_price"))
    cur = _safe_float(current_price, _safe_float(pos.get("current_price")))
    target = _safe_float(pos.get("target_price"))
    highest = _safe_float(pos.get("highest_price"))
    if highest is None and cur is not None:
        highest = cur
    pnl = _safe_float(pnl_pct)
    if pnl is None and entry and cur:
        pnl = (cur / entry - 1.0) * 100.0
    try:
        days = int(holding_days) if holding_days is not None else None
    except Exception:
        days = None
    if days is None:
        days = holding_days_from_entry(pos.get("entry_date"))
    target_progress = None
    if entry and target and target != entry and cur is not None:
        target_progress = ((cur - entry) / (target - entry)) * 100.0
    daily_return = None
    if pnl is not None and days is not None and days > 0:
        daily_return = pnl / float(days)
    max_profit = None
    if entry and highest:
        max_profit = (highest / entry - 1.0) * 100.0
    metric = str(profile.get("metric") or "")
    metric_value = {
        "close_return_pct": pnl,
        "target_progress_pct": target_progress,
        "daily_return_pct": daily_return,
        "max_profit_pct": max_profit,
    }.get(metric)
    threshold = _safe_float(profile.get("threshold"))
    check_day = int(profile.get("check_day") or 0)
    due = days is not None and days >= check_day > 0
    can_evaluate = metric_value is not None and threshold is not None
    triggered = bool(due and can_evaluate and metric_value < threshold)
    if not due:
        state = "not_due"
        state_label = f"{check_day}일차 확인 예정"
    elif not can_evaluate:
        state = "unavailable"
        state_label = "현재값 부족"
    elif triggered:
        state = "review"
        state_label = "수동 청산 검토"
    else:
        state = "ok"
        state_label = "정체 기준 통과"
    return {
        "version": EARLY_EXIT_PROFILE_VERSION,
        "enabled": True,
        "manual_only": True,
        "ticker": _norm_ticker(ticker),
        "validation_status": profile.get("validation_status"),
        "confidence_label": profile.get("confidence_label"),
        "match_confidence": profile.get("match_confidence"),
        "rulebook_hash_prefix": profile.get("rulebook_hash_prefix"),
        "check_day": check_day,
        "metric": metric,
        "metric_label": _metric_label(metric),
        "operator": profile.get("operator", "lt"),
        "threshold": threshold,
        "rule_label": profile.get("rule_label"),
        "action_label": profile.get("action_label"),
        "note": profile.get("note"),
        "validation": profile.get("validation") if isinstance(profile.get("validation"), dict) else {},
        "current": {
            "holding_days": days,
            "pnl_pct": round(float(pnl), 6) if pnl is not None else None,
            "target_progress_pct": round(float(target_progress), 6) if target_progress is not None else None,
            "daily_return_pct": round(float(daily_return), 6) if daily_return is not None else None,
            "max_profit_pct": round(float(max_profit), 6) if max_profit is not None else None,
            "metric_value": round(float(metric_value), 6) if metric_value is not None else None,
        },
        "evaluation": {
            "due": due,
            "can_evaluate": can_evaluate,
            "triggered": triggered,
            "state": state,
            "state_label": state_label,
        },
    }
