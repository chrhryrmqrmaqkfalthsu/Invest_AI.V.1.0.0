#!/usr/bin/env python3
"""Export validated /dashboard-real candidates into real_dashboard_buy_candidates.json.

Safety contract:
- Never copies compact live_slots_state rows as orderable candidates.
- Rebuilds current full elite candidates, loads full final_rulebooks.jsonl/survivor rulebooks,
  and re-validates should_buy with the full rulebook before exporting.
- Writes a temporary JSON first and validates it before optional atomic replacement.
- Default execution is dry-run: it leaves the canonical output file untouched.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STATE_PATH = Path("data/_system/live_slots_state.json")
OUTPUT_PATH = Path("data/_system/real_dashboard_buy_candidates.json")
ELITE_ROOT = Path("exp_batch_stage123_2009_20260616_full")
EXPORT_SOURCE = "real_dashboard_buy_candidates_export"
FORBIDDEN_FALLBACK_SOURCE = "live_slots_state_fallback"

FULL_RULEBOOK_MIN_KEYS = 50
FULL_RULEBOOK_REQUIRED_KEYS = (
    "signal_threshold",
    "rsi_low",
    "rsi_high",
    "event_response_war",
    "vix_sensitivity",
    "stop_loss_atr",
    "take_profit_atr",
    "trailing_atr",
    "max_holding_days",
)

TOP_LEVEL_REQUIRED = (
    "schema_version",
    "buy_mode",
    "source",
    "isolated",
    "trade_date",
    "updated_at",
    "manual_buy_enabled",
    "candidates",
)

CONSUMER_FIELD_MAP = [
    {
        "consumer": "_real_candidate_state: candidates[cid] must be dict",
        "export_field": "candidates.<candidate_id>",
        "required": True,
        "export_source": "validated candidate row dict",
    },
    {
        "consumer": "_candidate_for_real: status in {'pending','manual_requested'}",
        "export_field": "status",
        "required": True,
        "export_source": "constant 'pending'",
    },
    {
        "consumer": "_candidate_for_real: manual_buy_enabled is not False",
        "export_field": "manual_buy_enabled",
        "required": True,
        "export_source": "constant True",
    },
    {
        "consumer": "_create_real_buy_intent: ticker required",
        "export_field": "ticker",
        "required": True,
        "export_source": "evaluate_candidate/full candidate ticker",
    },
    {
        "consumer": "_create_real_buy_intent: default_notional from candidate.notional",
        "export_field": "notional",
        "required": False,
        "export_source": "live row notional if present else 0.0",
    },
    {
        "consumer": "_create_real_buy_intent: trade_date or execution_session",
        "export_field": "trade_date",
        "required": False,
        "export_source": "live state updated_at date/export date",
    },
    {
        "consumer": "_create_real_buy_intent: entity_id metadata",
        "export_field": "entity_id",
        "required": False,
        "export_source": "full candidate entity_id if present",
    },
    {
        "consumer": "_create_real_buy_intent: price metadata and broker price fallback",
        "export_field": "price",
        "required": True,
        "export_source": "evaluate_candidate price",
    },
    {
        "consumer": "_create_real_buy_intent: candidate_snapshot stores whole row",
        "export_field": "selected_rulebook/rulebook/source_file/full_rulebook_verified",
        "required": True,
        "export_source": "final_rulebooks/survivor full row and validation metadata",
    },
    {
        "consumer": "SAFETY guard: candidate_source must not be live_slots_state_fallback",
        "export_field": "candidate_source",
        "required": True,
        "export_source": "constant real_dashboard_buy_candidates_export",
    },
    {
        "consumer": "SAFETY guard: real_candidate_fallback must not be True",
        "export_field": "real_candidate_fallback",
        "required": True,
        "export_source": "constant False",
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_sanitize(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            out = item()
            if isinstance(out, (str, int, float, bool)) or out is None:
                return out
        except Exception:
            pass
    return str(value)


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def candidate_id_for(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("candidate_id") or f"{candidate.get('stage')}:{candidate.get('ticker')}:{candidate.get('rulebook_hash_short')}")


def resolved_source_file(candidate: Mapping[str, Any]) -> str:
    raw = str(candidate.get("source_file") or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.exists():
        return str(path)
    elite_path = ELITE_ROOT / raw
    if elite_path.exists():
        return str(elite_path)
    return raw


def resolved_trade_file(candidate: Mapping[str, Any]) -> str | None:
    raw = str(candidate.get("trade_file") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return str(path)
    elite_path = ELITE_ROOT / raw
    if elite_path.exists():
        return str(elite_path)
    return raw


def full_rulebook_validation_errors(rulebook: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(rulebook, dict):
        return ["rulebook_not_dict"]
    if len(rulebook) < FULL_RULEBOOK_MIN_KEYS:
        errors.append(f"rulebook_key_count_lt_{FULL_RULEBOOK_MIN_KEYS}:{len(rulebook)}")
    for key in FULL_RULEBOOK_REQUIRED_KEYS:
        if key not in rulebook:
            errors.append(f"missing_{key}")
    return errors


def trade_date_from_state(state: Mapping[str, Any], exported_at: str) -> str:
    for raw in (
        state.get("trade_date"),
        state.get("updated_at"),
        (state.get("last_refresh") or {}).get("time") if isinstance(state.get("last_refresh"), dict) else "",
        exported_at,
    ):
        text = str(raw or "")
        if len(text) >= 10:
            return text[:10]
    return ""


def sort_source_rows(rows: list[dict[str, Any]], section: str) -> list[dict[str, Any]]:
    if section == "slots":
        return sorted(rows, key=lambda r: safe_int(r.get("slot_no") or r.get("slot"), 9999))
    return sorted(
        rows,
        key=lambda r: (
            safe_int(r.get("priority_group"), 0),
            -(safe_float(r.get("final_score"), 0.0) or 0.0),
            str(r.get("ticker") or ""),
            str(r.get("candidate_id") or ""),
        ),
    )


def source_rows_from_state(state: Mapping[str, Any], *, source_section: str, limit: int) -> list[dict[str, Any]]:
    raw = state.get(source_section)
    if not isinstance(raw, list):
        raise ValueError(f"source section is not a list: {source_section}")
    rows = [dict(r) for r in raw if isinstance(r, dict) and r.get("candidate_id")]
    return sort_source_rows(rows, source_section)[: max(0, int(limit))]


def metric_value(metrics: Mapping[str, Any], live: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in metrics and metrics.get(key) not in (None, ""):
            return metrics.get(key)
    for key in keys:
        if key in live and live.get(key) not in (None, ""):
            return live.get(key)
    return default


def build_candidate_row(
    *,
    live_row: Mapping[str, Any],
    full_candidate: Mapping[str, Any],
    full_rulebook: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    exported_at: str,
    trade_date: str,
) -> dict[str, Any]:
    metrics = full_candidate.get("metrics") if isinstance(full_candidate.get("metrics"), Mapping) else {}
    rb = dict(full_rulebook)
    cid = str(live_row.get("candidate_id") or candidate_id_for(full_candidate))
    ticker = str(evaluation.get("ticker") or full_candidate.get("ticker") or live_row.get("ticker") or "").upper()
    rulebook_hash = str(full_candidate.get("rulebook_hash") or "")
    source_file = resolved_source_file(full_candidate)
    row = {
        "candidate_id": cid,
        "ticker": ticker,
        "stage": full_candidate.get("stage"),
        "bucket": full_candidate.get("bucket"),
        "status": "pending",
        "manual_buy_enabled": True,
        "candidate_source": EXPORT_SOURCE,
        "real_candidate_fallback": False,
        "trade_date": trade_date,
        "execution_session": trade_date,
        "entity_id": full_candidate.get("entity_id"),
        "notional": safe_float(live_row.get("notional"), 0.0) or 0.0,
        "source_file": source_file,
        "source_row_index": full_candidate.get("source_row_index"),
        "trade_file": resolved_trade_file(full_candidate),
        "rulebook_hash": rulebook_hash,
        "rulebook_hash_short": str(full_candidate.get("rulebook_hash_short") or rulebook_hash[:12]),
        "selected_rulebook_hash": rulebook_hash,
        "selected_rulebook": rb,
        "rulebook": dict(rb),
        "full_rulebook_verified": True,
        "full_rulebook_verified_at": exported_at,
        "full_rulebook_source": source_file,
        "full_rulebook_key": rulebook_hash or str(full_candidate.get("source_row_index") or ""),
        "full_rulebook_field_count": len(rb),
        "should_buy_verified": True,
        "should_buy_verified_at": exported_at,
        "price": safe_float(evaluation.get("price"), 0.0) or 0.0,
        "atr": safe_float(evaluation.get("atr"), 0.0) or 0.0,
        "final_score": safe_float(evaluation.get("score"), 0.0) or 0.0,
        "raw_score": safe_float(evaluation.get("raw_score"), 0.0) or 0.0,
        "threshold": safe_float(evaluation.get("threshold"), 0.0) or 0.0,
        "ratio": safe_float(evaluation.get("ratio"), 0.0) or 0.0,
        "reasons": list(evaluation.get("reasons") or []),
        "components": json_sanitize(evaluation.get("components") or {}),
        "market_score": safe_float(evaluation.get("market_score"), 0.0) or 0.0,
        "sector_score": safe_float(evaluation.get("sector_score"), 0.0) or 0.0,
        "vix_level": safe_float(evaluation.get("vix_level"), 0.0) or 0.0,
        "first_signal_at": live_row.get("first_signal_at"),
        "first_signal_price": safe_float(live_row.get("first_signal_price"), None),
        "first_final_score": safe_float(live_row.get("first_final_score"), None),
        "last_seen_at": live_row.get("last_seen_at"),
        "slot_no": safe_int(live_row.get("slot_no") or live_row.get("slot"), 0),
        "slot": safe_int(live_row.get("slot_no") or live_row.get("slot"), 0),
        "source_rank": live_row.get("source_rank") or full_candidate.get("rank"),
        "priority_group": safe_int(live_row.get("priority_group"), 0),
        "vol_group": live_row.get("vol_group") or full_candidate.get("vol_group"),
        "gate_status": live_row.get("gate_status"),
        "gate_keep": bool(live_row.get("gate_keep", True)),
        "down_deprioritize": bool(live_row.get("down_deprioritize")),
        "win_rate": safe_float(metric_value(metrics, live_row, "oos_win_rate", "min_win_rate", "win_rate"), None),
        "expectancy_pct": safe_float(metric_value(metrics, live_row, "oos_expectancy_pct", "avg_expectancy_pct", "min_expectancy_pct", "expectancy_pct"), None),
        "mdd_pct": safe_float(metric_value(metrics, live_row, "worst_drawdown_pct", "oos_drawdown_pct", "mdd_pct"), None),
        "fitness": safe_float(metric_value(metrics, live_row, "oos_fitness", "min_fitness", "fitness"), None),
        "trade_count": safe_int(metric_value(metrics, live_row, "oos_trade_count", "min_trade_count", "trade_count"), 0),
        "max_holding_days": safe_int(rb.get("max_holding_days"), 0),
        "exit_strategy": rb.get("exit_strategy"),
        "exit_strategy_name": rb.get("exit_strategy"),
        "stop_loss_atr": safe_float(rb.get("stop_loss_atr"), None),
        "take_profit_atr": safe_float(rb.get("take_profit_atr"), None),
        "trailing_atr": safe_float(rb.get("trailing_atr"), None),
        "created_at": exported_at,
        "updated_at": exported_at,
        "exported_at": exported_at,
        "exporter": "scripts/export_real_dashboard_buy_candidates.py",
    }
    return json_sanitize(row)


def validate_candidate_row(cid: str, row: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return ["row_not_dict"]
    required = (
        "candidate_id",
        "ticker",
        "status",
        "manual_buy_enabled",
        "candidate_source",
        "real_candidate_fallback",
        "price",
        "source_file",
        "selected_rulebook",
        "rulebook",
        "full_rulebook_verified",
        "should_buy_verified",
    )
    for field in required:
        if field not in row:
            errors.append(f"missing_{field}")
    if row.get("candidate_id") != cid:
        errors.append("candidate_id_key_mismatch")
    if row.get("status") != "pending":
        errors.append("status_not_pending")
    if row.get("manual_buy_enabled") is not True:
        errors.append("manual_buy_enabled_not_true")
    if row.get("candidate_source") != EXPORT_SOURCE:
        errors.append("candidate_source_not_export")
    if row.get("candidate_source") == FORBIDDEN_FALLBACK_SOURCE:
        errors.append("candidate_source_is_fallback")
    if row.get("real_candidate_fallback") is not False:
        errors.append("real_candidate_fallback_not_false")
    if not str(row.get("ticker") or "").strip():
        errors.append("ticker_missing")
    if (safe_float(row.get("price"), 0.0) or 0.0) <= 0:
        errors.append("price_not_positive")
    source_file = Path(str(row.get("source_file") or ""))
    if not source_file.exists():
        errors.append("source_file_missing")
    selected = row.get("selected_rulebook")
    rulebook = row.get("rulebook")
    errors.extend(f"selected_{e}" for e in full_rulebook_validation_errors(selected))
    errors.extend(f"rulebook_{e}" for e in full_rulebook_validation_errors(rulebook))
    if isinstance(selected, dict) and isinstance(rulebook, dict) and selected != rulebook:
        errors.append("selected_rulebook_rulebook_mismatch")
    if row.get("full_rulebook_verified") is not True:
        errors.append("full_rulebook_verified_not_true")
    if row.get("should_buy_verified") is not True:
        errors.append("should_buy_verified_not_true")
    return errors


def validate_payload(payload: Any, *, allow_empty: bool = False) -> tuple[bool, list[str], dict[str, Any]]:
    errors: list[str] = []
    stats: dict[str, Any] = {"candidate_errors": {}}
    if not isinstance(payload, dict):
        return False, ["payload_not_dict"], stats
    for field in TOP_LEVEL_REQUIRED:
        if field not in payload:
            errors.append(f"missing_top_level_{field}")
    if payload.get("schema_version") != 1:
        errors.append("schema_version_not_1")
    if payload.get("buy_mode") != "real_isolated":
        errors.append("buy_mode_not_real_isolated")
    if payload.get("source") != "real_dashboard_buy_candidates":
        errors.append("source_not_real_dashboard_buy_candidates")
    if payload.get("isolated") is not True:
        errors.append("isolated_not_true")
    if payload.get("manual_buy_enabled") is not True:
        errors.append("manual_buy_enabled_not_true")
    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        errors.append("candidates_not_dict")
        return False, errors, stats
    if not candidates and not allow_empty:
        errors.append("candidate_count_zero")
    candidate_errors: dict[str, list[str]] = {}
    for cid, row in candidates.items():
        row_errors = validate_candidate_row(str(cid), row)
        if row_errors:
            candidate_errors[str(cid)] = row_errors
    stats["candidate_errors"] = candidate_errors
    if candidate_errors:
        errors.append(f"candidate_validation_failed:{len(candidate_errors)}")
    return not errors, errors, stats


def build_export_payload(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    from engine.live.elite_shadow_report import build_elite_shadow_report
    from engine.live.elite_shadow_trader import _load_rulebook_for_candidate, evaluate_candidate
    from engine.market.context import get_market_context

    state_path = Path(args.state_path)
    state = load_json(state_path)
    if not isinstance(state, dict):
        raise ValueError("live_slots_state payload is not a dict")
    source_rows = source_rows_from_state(state, source_section=args.source_section, limit=args.limit)
    exported_at = utc_now_iso()
    trade_date = trade_date_from_state(state, exported_at)
    report = build_elite_shadow_report(stage2_limit=args.stage2_limit, stage3_limit=args.stage3_limit, include_trades=False)
    full_candidates = [dict(c) for c in (report.get("candidates") or []) if isinstance(c, Mapping)]
    full_by_id = {candidate_id_for(c): c for c in full_candidates}
    try:
        ctx = get_market_context()
    except Exception:
        ctx = None

    candidates: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    def skip(row: Mapping[str, Any], reason: str, extra: Mapping[str, Any] | None = None) -> None:
        item = {
            "candidate_id": row.get("candidate_id"),
            "ticker": row.get("ticker"),
            "stage": row.get("stage"),
            "reason": reason,
        }
        if extra:
            item.update(json_sanitize(dict(extra)))
        skipped.append(item)

    for live_row in source_rows:
        cid = str(live_row.get("candidate_id") or "")
        if not cid:
            skip(live_row, "candidate_id_missing")
            continue
        full_candidate = full_by_id.get(cid)
        if not isinstance(full_candidate, dict):
            skip(live_row, "candidate_not_found_in_current_report")
            continue
        full_rulebook = _load_rulebook_for_candidate(full_candidate)
        rb_errors = full_rulebook_validation_errors(full_rulebook)
        if rb_errors:
            skip(live_row, "full_rulebook_validation_failed", {"errors": rb_errors})
            continue
        try:
            evaluation = evaluate_candidate(full_candidate, ctx=ctx)
        except Exception as exc:
            skip(live_row, "evaluate_candidate_failed", {"error": f"{type(exc).__name__}: {exc}"})
            continue
        if not bool(evaluation.get("ok")):
            skip(live_row, str(evaluation.get("reason") or "evaluate_not_ok"), {"evaluation": evaluation})
            continue
        if not bool(evaluation.get("should_buy")):
            skip(live_row, "should_buy_false_at_export_check", {"score": evaluation.get("score"), "threshold": evaluation.get("threshold")})
            continue
        row = build_candidate_row(
            live_row=live_row,
            full_candidate=full_candidate,
            full_rulebook=full_rulebook,
            evaluation=evaluation,
            exported_at=exported_at,
            trade_date=trade_date,
        )
        row_errors = validate_candidate_row(cid, row)
        if row_errors:
            skip(live_row, "candidate_row_validation_failed", {"errors": row_errors})
            continue
        candidates[cid] = row

    skipped_summary = dict(Counter(str(x.get("reason") or "unknown") for x in skipped))
    live_ids = [str(r.get("candidate_id") or "") for r in source_rows if r.get("candidate_id")]
    exported_ids = list(candidates.keys())
    payload = {
        "schema_version": 1,
        "buy_mode": "real_isolated",
        "source": "real_dashboard_buy_candidates",
        "isolated": True,
        "trade_date": trade_date,
        "updated_at": exported_at,
        "manual_buy_enabled": True,
        "candidates": candidates,
        "note": "Generated by scripts/export_real_dashboard_buy_candidates.py from live slot candidate_ids after full rulebook lookup and should_buy revalidation.",
        "export_meta": {
            "exporter": "scripts/export_real_dashboard_buy_candidates.py",
            "exported_at": exported_at,
            "state_path": str(state_path),
            "state_updated_at": state.get("updated_at"),
            "source_section": args.source_section,
            "limit": int(args.limit),
            "live_slot_count": len(source_rows),
            "live_candidate_ids": live_ids,
            "report_candidate_count": len(full_candidates),
            "exported_count": len(candidates),
            "exported_candidate_ids": exported_ids,
            "matched_count": len(set(live_ids) & set(exported_ids)),
            "skipped_count": len(skipped),
            "skipped_summary": skipped_summary,
            "skipped": skipped,
            "consumer_field_map": CONSUMER_FIELD_MAP,
            "replacement_requested": bool(args.write),
        },
    }
    summary = {
        "ok": True,
        "state_path": str(state_path),
        "state_updated_at": state.get("updated_at"),
        "output_path": str(args.output_path),
        "source_section": args.source_section,
        "limit": int(args.limit),
        "live_slot_count": len(source_rows),
        "report_candidate_count": len(full_candidates),
        "exported_count": len(candidates),
        "skipped_count": len(skipped),
        "skipped_summary": skipped_summary,
        "live_candidate_ids": live_ids,
        "exported_candidate_ids": exported_ids,
        "skipped": skipped,
        "replacement_requested": bool(args.write),
    }
    return payload, summary


def make_temp_path(output_path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return output_path.parent / f".{output_path.name}.tmp.{os.getpid()}.{ts}"


def run(args: argparse.Namespace) -> int:
    output_path = Path(args.output_path)
    payload, summary = build_export_payload(args)
    temp_path = Path(args.temp_path) if args.temp_path else make_temp_path(output_path)
    temp_path = temp_path.resolve()
    write_json(temp_path, payload)
    read_back = load_json(temp_path)
    valid, validation_errors, validation_stats = validate_payload(read_back, allow_empty=bool(args.allow_empty))
    summary.update(
        {
            "temp_path": str(temp_path),
            "validation_ok": valid,
            "validation_errors": validation_errors,
            "validation_stats": validation_stats,
            "canonical_output_replaced": False,
        }
    )
    if not valid:
        summary["ok"] = False
        if args.summary_path:
            write_json(Path(args.summary_path), summary)
        print(json.dumps(json_sanitize(summary), ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    if args.write:
        os.replace(temp_path, output_path)
        post = load_json(output_path)
        post_valid, post_errors, post_stats = validate_payload(post, allow_empty=bool(args.allow_empty))
        summary.update(
            {
                "canonical_output_replaced": True,
                "post_write_validation_ok": post_valid,
                "post_write_validation_errors": post_errors,
                "post_write_validation_stats": post_stats,
            }
        )
        if not post_valid:
            summary["ok"] = False
            if args.summary_path:
                write_json(Path(args.summary_path), summary)
            print(json.dumps(json_sanitize(summary), ensure_ascii=False, indent=2, sort_keys=True))
            return 3
    if args.summary_path:
        write_json(Path(args.summary_path), summary)
    print(json.dumps(json_sanitize(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export full-rulebook verified real dashboard buy candidates.")
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--output-path", default=str(OUTPUT_PATH))
    parser.add_argument("--source-section", default="slots", choices=("slots", "candidate_pool", "waitlist"))
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--stage2-limit", type=int, default=60)
    parser.add_argument("--stage3-limit", type=int, default=80)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--summary-path", default="")
    parser.add_argument("--temp-path", default="")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Replace the canonical output file after validation. Omitted by default for dry-run/temp-only validation.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
