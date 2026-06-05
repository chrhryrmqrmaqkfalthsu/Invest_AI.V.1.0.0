#!/usr/bin/env python3
"""Promote vetted pipeline members into live ``data/symbols`` parameters.

Safety model
------------
* Default execution is dry-run. It only builds and saves a manifest outside the
  live symbols directory.
* Applying requires both ``--apply`` and an exact ``--confirm-promotion-id``.
* Existing symbol directories are backed up before replacement.
* Each parameters.json is written to a temporary file, parsed and hash-checked,
  then atomically replaced. A failed ticker is rolled back independently.
* The live runner is never started by this script.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.metadata import compute_member_hash, compute_rulebook_hash  # noqa: E402
from engine.strategies.rulebook import Rulebook  # noqa: E402

DEFAULT_ROLLING_RUN_ID = "au_1173_20260604"
DEFAULT_FULL_TRAINING_RUN_ID = "bc_full_training_203_20260604"
DEFAULT_MIN_PASS_COUNT = 2
DEFAULT_MIN_STOCK_SCORE = 60.0
DEFAULT_MAX_OVERFIT_GAP_PP = 5.0

PIPELINE_RUNS_ROOT = ROOT / "data/_system/pipeline/v1/runs"
PROMOTIONS_ROOT = ROOT / "data/_system/pipeline/v1/promotions"
SYMBOLS_ROOT = ROOT / "data/symbols"
BACKUPS_ROOT = ROOT / "data/backups"


class PromotionError(RuntimeError):
    """Raised when a promotion safety invariant is violated."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_promotion_id() -> str:
    return datetime.now(timezone.utc).strftime("promote_%Y%m%dT%H%M%SZ")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return default if value is None else int(float(value))
    except Exception:
        return default


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise PromotionError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise PromotionError(f"member row must be object: {path}:{line_no}")
            rows.append(row)
    return rows


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON atomically in the target directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # Re-read before replace so malformed serialization can never replace live.
        json.loads(tmp.read_text(encoding="utf-8"))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def select_best_qualified_member(members: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Select qualified member_score winner with deterministic tie-breakers."""
    qualified = [dict(m) for m in members if bool(m.get("qualified"))]
    if not qualified:
        raise PromotionError("no qualified full-training member")
    return max(
        qualified,
        key=lambda m: (
            safe_float(m.get("member_score"), float("-inf")),
            -safe_int(m.get("rank"), 10**9),
            str(m.get("member_hash") or ""),
        ),
    )


def validate_member(ticker: str, member: Mapping[str, Any]) -> dict[str, Any]:
    """Validate member rulebook and hashes, returning normalized rulebook dict."""
    raw_rulebook = member.get("rulebook")
    if not isinstance(raw_rulebook, Mapping) or not raw_rulebook:
        raise PromotionError(f"{ticker}: member rulebook missing")
    rb = Rulebook.from_dict(dict(raw_rulebook))
    normalized = rb.to_dict()
    if str(rb.ticker).upper() != str(ticker).upper():
        raise PromotionError(f"{ticker}: rulebook ticker mismatch: {rb.ticker}")
    if str(rb.direction).lower() != "long":
        raise PromotionError(f"{ticker}: only long rulebooks may be promoted")
    expected_member_hash = str(member.get("member_hash") or "")
    actual_member_hash = compute_member_hash(normalized)
    if not expected_member_hash or actual_member_hash != expected_member_hash:
        raise PromotionError(
            f"{ticker}: member_hash mismatch expected={expected_member_hash} actual={actual_member_hash}"
        )
    expected_rulebook_hash = str(member.get("rulebook_hash") or "")
    actual_rulebook_hash = compute_rulebook_hash(normalized)
    if expected_rulebook_hash and actual_rulebook_hash != expected_rulebook_hash:
        raise PromotionError(
            f"{ticker}: rulebook_hash mismatch expected={expected_rulebook_hash} actual={actual_rulebook_hash}"
        )
    return normalized


def extract_rolling_metrics(rolling: Mapping[str, Any]) -> dict[str, Any]:
    score_block = rolling.get("stock_score") or {}
    raw = score_block.get("raw_metrics") or {}
    return {
        "stock_score": safe_float(score_block.get("stock_score"), 0.0),
        "pass_count": safe_int(raw.get("pass_count"), 0),
        "oos_avg_expectancy_pct": safe_float(raw.get("avg_expectancy_pct_all"), 0.0),
        "asset_meta": dict(rolling.get("asset_meta") or {}),
        "data_start": rolling.get("data_start"),
        "data_end": rolling.get("data_end"),
    }


def build_parameters_payload(
    *,
    ticker: str,
    rulebook: Mapping[str, Any],
    member: Mapping[str, Any],
    rolling_metrics: Mapping[str, Any],
    promotion_id: str,
    rolling_run_id: str,
    full_training_run_id: str,
    min_pass_count: int,
    min_stock_score: float,
    max_overfit_gap_pp: float,
    created_at: str,
) -> dict[str, Any]:
    full_expectancy = safe_float(member.get("expectancy_pct"), 0.0)
    oos_expectancy = safe_float(rolling_metrics.get("oos_avg_expectancy_pct"), 0.0)
    gap = full_expectancy - oos_expectancy
    return {
        "saved_at": created_at,
        "version": str(rulebook.get("version") or "v5"),
        "asset_meta": dict(rolling_metrics.get("asset_meta") or {}),
        "rulebook": dict(rulebook),
        "promotion": {
            "promotion_id": promotion_id,
            "created_at": created_at,
            "source_rolling_run_id": rolling_run_id,
            "source_full_training_run_id": full_training_run_id,
            "member_hash": str(member.get("member_hash") or ""),
            "rulebook_hash": str(member.get("rulebook_hash") or compute_rulebook_hash(rulebook)),
            "selected_member": {
                "rank": safe_int(member.get("rank"), 0),
                "qualified": bool(member.get("qualified")),
                "member_score": safe_float(member.get("member_score"), 0.0),
                "fitness": safe_float(member.get("fitness"), 0.0),
                "trade_count": safe_int(member.get("trade_count"), 0),
                "win_rate": safe_float(member.get("win_rate"), 0.0),
                "expectancy_pct": full_expectancy,
                "max_drawdown_pct": safe_float(member.get("max_drawdown_pct"), 0.0),
            },
            "selection": {
                "oos_pass_count": safe_int(rolling_metrics.get("pass_count"), 0),
                "stock_score": safe_float(rolling_metrics.get("stock_score"), 0.0),
                "oos_avg_expectancy_pct": oos_expectancy,
                "full_training_expectancy_pct": full_expectancy,
                "overfit_gap_pct_points": gap,
                "criteria": {
                    "min_oos_pass_count": int(min_pass_count),
                    "min_stock_score": float(min_stock_score),
                    "max_overfit_gap_pct_points": float(max_overfit_gap_pp),
                },
            },
            "source_data": {
                "rolling_data_start": rolling_metrics.get("data_start"),
                "rolling_data_end": rolling_metrics.get("data_end"),
            },
        },
    }


def validate_parameters_payload(ticker: str, payload: Mapping[str, Any]) -> Rulebook:
    """Validate exact structure consumed by LearnedRuleBook."""
    raw_rulebook = payload.get("rulebook")
    promotion = payload.get("promotion")
    if not isinstance(raw_rulebook, Mapping) or not raw_rulebook:
        raise PromotionError(f"{ticker}: parameters.rulebook missing")
    if not isinstance(promotion, Mapping):
        raise PromotionError(f"{ticker}: parameters.promotion missing")
    rb = Rulebook.from_dict(dict(raw_rulebook))
    if str(rb.ticker).upper() != str(ticker).upper():
        raise PromotionError(f"{ticker}: payload rulebook ticker mismatch: {rb.ticker}")
    if str(rb.direction).lower() != "long":
        raise PromotionError(f"{ticker}: payload is not long-only")
    expected_member_hash = str(promotion.get("member_hash") or "")
    actual_member_hash = compute_member_hash(rb)
    if actual_member_hash != expected_member_hash:
        raise PromotionError(
            f"{ticker}: payload member hash mismatch expected={expected_member_hash} actual={actual_member_hash}"
        )
    expected_rulebook_hash = str(promotion.get("rulebook_hash") or "")
    actual_rulebook_hash = compute_rulebook_hash(rb)
    if expected_rulebook_hash and actual_rulebook_hash != expected_rulebook_hash:
        raise PromotionError(
            f"{ticker}: payload rulebook hash mismatch expected={expected_rulebook_hash} actual={actual_rulebook_hash}"
        )
    return rb


@dataclass(frozen=True)
class PromotionPlan:
    ticker: str
    target_path: Path
    backup_path: Path | None
    payload: dict[str, Any]
    manifest_row: dict[str, Any]


def build_promotion_manifest(
    *,
    rolling_run_id: str = DEFAULT_ROLLING_RUN_ID,
    full_training_run_id: str = DEFAULT_FULL_TRAINING_RUN_ID,
    promotion_id: str,
    min_pass_count: int = DEFAULT_MIN_PASS_COUNT,
    min_stock_score: float = DEFAULT_MIN_STOCK_SCORE,
    max_overfit_gap_pp: float = DEFAULT_MAX_OVERFIT_GAP_PP,
    runs_root: Path = PIPELINE_RUNS_ROOT,
    symbols_root: Path = SYMBOLS_ROOT,
    backups_root: Path = BACKUPS_ROOT,
    created_at: str | None = None,
) -> tuple[dict[str, Any], list[PromotionPlan]]:
    """Build a deterministic, fully validated promotion manifest without writing live files."""
    created_at = created_at or utc_now()
    rolling_root = runs_root / rolling_run_id
    full_root = runs_root / full_training_run_id
    candidate_path = rolling_root / "cutoff_60_candidates.json"
    if not candidate_path.exists():
        raise PromotionError(f"candidate file missing: {candidate_path}")
    candidate_payload = load_json(candidate_path)
    candidates = candidate_payload.get("candidates") if isinstance(candidate_payload, dict) else None
    if not isinstance(candidates, list):
        raise PromotionError(f"candidate list missing: {candidate_path}")

    plans: list[PromotionPlan] = []
    excluded: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for candidate in candidates:
        ticker = str((candidate or {}).get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            rolling_path = rolling_root / ticker / "rolling_validation.json"
            members_path = full_root / ticker / "members.jsonl"
            if not rolling_path.exists() or not members_path.exists():
                raise PromotionError(f"source missing rolling={rolling_path.exists()} members={members_path.exists()}")
            rolling = load_json(rolling_path)
            metrics = extract_rolling_metrics(rolling)
            member = select_best_qualified_member(load_jsonl(members_path))
            rulebook = validate_member(ticker, member)
            gap = safe_float(member.get("expectancy_pct"), 0.0) - metrics["oos_avg_expectancy_pct"]
            failures: list[str] = []
            if metrics["pass_count"] < int(min_pass_count):
                failures.append("PASS_COUNT_BELOW_MIN")
            if metrics["stock_score"] < float(min_stock_score):
                failures.append("STOCK_SCORE_BELOW_MIN")
            if gap > float(max_overfit_gap_pp):
                failures.append("OVERFIT_GAP_ABOVE_MAX")
            if failures:
                excluded.append(
                    {
                        "ticker": ticker,
                        "reason_codes": failures,
                        "stock_score": metrics["stock_score"],
                        "pass_count": metrics["pass_count"],
                        "overfit_gap_pct_points": gap,
                    }
                )
                continue

            payload = build_parameters_payload(
                ticker=ticker,
                rulebook=rulebook,
                member=member,
                rolling_metrics=metrics,
                promotion_id=promotion_id,
                rolling_run_id=rolling_run_id,
                full_training_run_id=full_training_run_id,
                min_pass_count=min_pass_count,
                min_stock_score=min_stock_score,
                max_overfit_gap_pp=max_overfit_gap_pp,
                created_at=created_at,
            )
            validate_parameters_payload(ticker, payload)
            target = symbols_root / ticker / "parameters.json"
            existing = target.exists()
            backup = backups_root / f"promote_{promotion_id}" / ticker if existing else None
            old_hash = ""
            if existing:
                try:
                    old = load_json(target)
                    old_hash = compute_member_hash(old.get("rulebook") or {})
                except Exception:
                    old_hash = "UNREADABLE"
            row = {
                "ticker": ticker,
                "action": "OVERWRITE" if existing else "CREATE",
                "target_path": str(target),
                "backup_path": str(backup) if backup else None,
                "source_rolling_path": str(rolling_path),
                "source_members_path": str(members_path),
                "stock_score": metrics["stock_score"],
                "pass_count": metrics["pass_count"],
                "oos_avg_expectancy_pct": metrics["oos_avg_expectancy_pct"],
                "full_training_expectancy_pct": safe_float(member.get("expectancy_pct"), 0.0),
                "overfit_gap_pct_points": gap,
                "member_score": safe_float(member.get("member_score"), 0.0),
                "member_rank": safe_int(member.get("rank"), 0),
                "member_hash": str(member.get("member_hash") or ""),
                "rulebook_hash": str(member.get("rulebook_hash") or compute_rulebook_hash(rulebook)),
                "existing_member_hash": old_hash or None,
                "hash_changes": bool(existing and old_hash != str(member.get("member_hash") or "")),
                "asset_type": rulebook.get("asset_type"),
                "direction": rulebook.get("direction"),
                "market": metrics["asset_meta"].get("market"),
                "currency": metrics["asset_meta"].get("currency"),
            }
            plans.append(PromotionPlan(ticker, target, backup, payload, row))
        except Exception as exc:
            errors.append({"ticker": ticker, "type": type(exc).__name__, "message": str(exc)})

    plans.sort(key=lambda p: p.ticker)
    excluded.sort(key=lambda r: r["ticker"])
    action_counts = Counter(p.manifest_row["action"] for p in plans)
    exclusion_counts = Counter(code for row in excluded for code in row["reason_codes"])
    manifest = {
        "promotion_id": promotion_id,
        "mode": "DRY_RUN",
        "created_at": created_at,
        "source": {
            "rolling_run_id": rolling_run_id,
            "full_training_run_id": full_training_run_id,
            "candidate_path": str(candidate_path),
        },
        "criteria": {
            "min_oos_pass_count": int(min_pass_count),
            "min_stock_score": float(min_stock_score),
            "max_overfit_gap_pct_points": float(max_overfit_gap_pp),
            "overfit_gap_formula": "best_qualified_member.expectancy_pct - rolling.stock_score.raw_metrics.avg_expectancy_pct_all",
            "best_member_rule": "qualified=True; max(member_score); tie: min(rank); tie: max(member_hash)",
        },
        "counts": {
            "source_candidates": len(candidates),
            "selected": len(plans),
            "create": int(action_counts.get("CREATE", 0)),
            "overwrite": int(action_counts.get("OVERWRITE", 0)),
            "excluded": len(excluded),
            "errors": len(errors),
        },
        "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        "selected": [p.manifest_row for p in plans],
        "excluded": excluded,
        "errors": errors,
        "safety": {
            "live_files_written": False,
            "run_live_executed": False,
            "apply_requires": ["--apply", f"--confirm-promotion-id {promotion_id}"],
            "backup_root": str(backups_root / f"promote_{promotion_id}"),
            "warning": "run_live.py must remain off until Task BP provides a US-only live universe",
        },
    }
    return manifest, plans


def backup_existing_symbol(plan: PromotionPlan) -> None:
    if plan.backup_path is None:
        return
    source_dir = plan.target_path.parent
    if not source_dir.exists():
        raise PromotionError(f"{plan.ticker}: existing target disappeared before backup")
    if plan.backup_path.exists():
        raise PromotionError(f"{plan.ticker}: backup already exists: {plan.backup_path}")
    plan.backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, plan.backup_path)
    backup_target = plan.backup_path / "parameters.json"
    if not backup_target.exists():
        raise PromotionError(f"{plan.ticker}: backup parameters.json missing after copy")


def restore_plan(plan: PromotionPlan, *, created_new_dir: bool) -> None:
    """Best-effort rollback for one failed ticker."""
    if plan.backup_path is not None and plan.backup_path.exists():
        target_dir = plan.target_path.parent
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(plan.backup_path, target_dir)
        return
    if created_new_dir and plan.target_path.parent.exists():
        shutil.rmtree(plan.target_path.parent)


def apply_promotion_plans(
    plans: Iterable[PromotionPlan],
    *,
    promotion_id: str,
    confirm_promotion_id: str,
) -> dict[str, Any]:
    """Apply plans after explicit confirmation; never called by dry-run."""
    if not confirm_promotion_id or confirm_promotion_id != promotion_id:
        raise PromotionError("apply confirmation mismatch; refusing to write live parameters")
    results: list[dict[str, Any]] = []
    for plan in plans:
        existed_before = plan.target_path.parent.exists()
        try:
            validate_parameters_payload(plan.ticker, plan.payload)
            backup_existing_symbol(plan)
            atomic_write_json(plan.target_path, plan.payload)
            written = load_json(plan.target_path)
            rb = validate_parameters_payload(plan.ticker, written)
            actual_hash = compute_member_hash(rb)
            expected_hash = plan.manifest_row["member_hash"]
            if actual_hash != expected_hash:
                raise PromotionError(
                    f"{plan.ticker}: post-write live hash mismatch expected={expected_hash} actual={actual_hash}"
                )
            results.append(
                {
                    "ticker": plan.ticker,
                    "status": "PROMOTED",
                    "target_path": str(plan.target_path),
                    "backup_path": str(plan.backup_path) if plan.backup_path else None,
                    "member_hash": actual_hash,
                }
            )
        except Exception as exc:
            restore_plan(plan, created_new_dir=not existed_before)
            results.append(
                {
                    "ticker": plan.ticker,
                    "status": "ROLLED_BACK",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
    counts = Counter(r["status"] for r in results)
    return {
        "promotion_id": promotion_id,
        "mode": "APPLY",
        "created_at": utc_now(),
        "counts": dict(counts),
        "results": results,
    }


def save_manifest(manifest: Mapping[str, Any], promotions_root: Path = PROMOTIONS_ROOT) -> Path:
    promotion_id = str(manifest.get("promotion_id") or "unknown")
    path = promotions_root / promotion_id / "dry_run_manifest.json"
    atomic_write_json(path, manifest)
    return path


def print_manifest_summary(manifest: Mapping[str, Any], manifest_path: Path | None = None) -> None:
    counts = manifest.get("counts") or {}
    print("=" * 120)
    print("PROMOTE DRY-RUN MANIFEST — NO LIVE FILES WRITTEN")
    print("=" * 120)
    print(f"promotion_id:  {manifest.get('promotion_id')}")
    print(f"manifest_path: {manifest_path or '(not saved)'}")
    print(f"criteria:      {manifest.get('criteria')}")
    print(f"counts:        {counts}")
    print(f"exclusions:    {manifest.get('exclusion_reason_counts')}")
    print()
    print("ticker | action    | score | pass | gap_pp | member_score | rank | member_hash")
    print("-" * 120)
    for row in manifest.get("selected", []):
        print(
            f"{row['ticker']:8s} | {row['action']:9s} | {row['stock_score']:5.2f} | "
            f"{row['pass_count']:4d} | {row['overfit_gap_pct_points']:6.3f} | "
            f"{row['member_score']:12.6f} | {row['member_rank']:4d} | {row['member_hash']}"
        )
    if manifest.get("errors"):
        print("\nERRORS")
        for row in manifest["errors"]:
            print(f"  {row}")
    print("\nSTOP: 실제 data/symbols 쓰기는 별도 승인 전 실행하지 않습니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run/apply promotion of vetted pipeline candidates.")
    parser.add_argument("--rolling-run-id", default=DEFAULT_ROLLING_RUN_ID)
    parser.add_argument("--full-training-run-id", default=DEFAULT_FULL_TRAINING_RUN_ID)
    parser.add_argument("--promotion-id", default=default_promotion_id())
    parser.add_argument("--min-pass-count", type=int, default=DEFAULT_MIN_PASS_COUNT)
    parser.add_argument("--min-stock-score", type=float, default=DEFAULT_MIN_STOCK_SCORE)
    parser.add_argument("--max-overfit-gap-pp", type=float, default=DEFAULT_MAX_OVERFIT_GAP_PP)
    parser.add_argument("--manifest-out", help="Optional dry-run manifest path override.")
    parser.add_argument("--apply", action="store_true", help="Actually write live parameters; requires exact confirmation.")
    parser.add_argument("--confirm-promotion-id", default="", help="Must exactly match --promotion-id when --apply is used.")
    args = parser.parse_args(argv)

    manifest, plans = build_promotion_manifest(
        rolling_run_id=args.rolling_run_id,
        full_training_run_id=args.full_training_run_id,
        promotion_id=args.promotion_id,
        min_pass_count=args.min_pass_count,
        min_stock_score=args.min_stock_score,
        max_overfit_gap_pp=args.max_overfit_gap_pp,
    )
    if manifest["counts"]["errors"]:
        raise PromotionError(f"manifest contains source/validation errors: {manifest['errors'][:5]}")

    if not args.apply:
        path = Path(args.manifest_out) if args.manifest_out else save_manifest(manifest)
        if args.manifest_out:
            atomic_write_json(path, manifest)
        print_manifest_summary(manifest, path)
        return 0

    result = apply_promotion_plans(
        plans,
        promotion_id=args.promotion_id,
        confirm_promotion_id=args.confirm_promotion_id,
    )
    result_path = PROMOTIONS_ROOT / args.promotion_id / "apply_result.json"
    atomic_write_json(result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("counts", {}).get("ROLLED_BACK", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
