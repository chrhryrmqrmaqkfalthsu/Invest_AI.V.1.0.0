#!/usr/bin/env python3
"""Isolated CE materials-sector retrain harness.

This script intentionally avoids modifying the production source modules,
`data/_system/market_history.csv`, live settings, or the existing
`exp_batch_stage123_2009_20260616_full` artifacts.

It patches the CE training process in-memory only:
- adds `materials: XLB` to engine.market.context.SECTOR_ETFS,
- uses an isolated copy of market_history with `sector_materials`,
- forces CE `sector_name` and `base_rulebook.sector_name` to `materials`.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUN_ROOT = PROJECT_ROOT / "exp_batch_CE_materials_retrain_20260708"
TICKER = "CE"
XLB_CACHE = PROJECT_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/XLB.pkl"
BASE_MARKET_HISTORY = PROJECT_ROOT / "data/_system/market_history.csv"
PATCHED_MARKET_HISTORY = RUN_ROOT / "inputs/market_history_with_sector_materials.csv"
DRY_CHECK_JSON = RUN_ROOT / "dry_check.json"
DRY_CHECK_MD = RUN_ROOT / "dry_check_readout.md"
STATUS_JSON = RUN_ROOT / "run_status.json"
COMPARE_MD = RUN_ROOT / "comparison_readout.md"
COMPARE_CSV = RUN_ROOT / "comparison.csv"

OLD_BATCH_ROOT = PROJECT_ROOT / "exp_batch_stage123_2009_20260616_full"
OLD_STAGE2 = OLD_BATCH_ROOT / "tickers/CE/stage2"
OLD_STAGE3 = OLD_BATCH_ROOT / "tickers/CE/stage3"
NEW_STAGE2 = RUN_ROOT / "tickers/CE/stage2"
NEW_STAGE3 = RUN_ROOT / "tickers/CE/stage3"

CHECK_START = pd.Timestamp("2020-05-18")
CHECK_END = pd.Timestamp("2026-06-12")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(obj), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _append_status(event: str, **payload: Any) -> None:
    current: list[dict[str, Any]] = []
    if STATUS_JSON.exists():
        try:
            current = json.loads(STATUS_JSON.read_text(encoding="utf-8"))
            if not isinstance(current, list):
                current = []
        except Exception:
            current = []
    row = {"event": event, **payload}
    current.append(row)
    _write_json(STATUS_JSON, current)


def _load_base_market_history() -> pd.DataFrame:
    if not BASE_MARKET_HISTORY.exists():
        raise FileNotFoundError(f"base market_history missing: {BASE_MARKET_HISTORY}")
    df = pd.read_csv(BASE_MARKET_HISTORY, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def _load_xlb() -> pd.DataFrame:
    if not XLB_CACHE.exists():
        raise FileNotFoundError(f"XLB cache missing: {XLB_CACHE}")
    xlb = pd.read_pickle(XLB_CACHE)
    if not isinstance(xlb.index, pd.DatetimeIndex):
        if "Date" in xlb.columns:
            xlb.index = pd.to_datetime(xlb["Date"])
        elif "date" in xlb.columns:
            xlb.index = pd.to_datetime(xlb["date"])
        else:
            xlb.index = pd.to_datetime(xlb.index)
    xlb = xlb.copy()
    xlb.index = pd.to_datetime(xlb.index).tz_localize(None).normalize()
    return xlb.sort_index()


def build_materials_market_history() -> pd.DataFrame:
    """Create isolated market_history copy with sector_materials from XLB."""
    mh = _load_base_market_history()
    xlb = _load_xlb()
    close = pd.to_numeric(xlb["Close"], errors="coerce").sort_index()
    ret_60d = (close / close.shift(60) - 1.0) * 100.0
    score = (50.0 + ret_60d * 5.0).clip(lower=0.0, upper=100.0)
    # Match the current sector-history fallback: insufficient lookback is neutral.
    score = score.fillna(50.0)
    aligned = score.reindex(mh.index).ffill()
    # Before first XLB date use neutral, after last date ffill last known value so no missing
    # is introduced in the isolated copy.
    aligned = aligned.fillna(50.0)
    out = mh.copy()
    out["sector_materials"] = aligned.astype(float)
    PATCHED_MARKET_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(PATCHED_MARKET_HISTORY)
    return out


def _patched_market_history_df() -> pd.DataFrame:
    if PATCHED_MARKET_HISTORY.exists():
        df = pd.read_csv(PATCHED_MARKET_HISTORY, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        return df.sort_index()
    return build_materials_market_history()


def _make_prepare_context_patch():
    """Return a patched prepare_ticker_context function for CE only."""
    import engine.pipeline.context as pipeline_context
    from engine.market import context as market_context

    market_context.SECTOR_ETFS["materials"] = "XLB"

    original_prepare = pipeline_context.prepare_ticker_context

    def patched_get_market_history(years: int = 6) -> pd.DataFrame:  # noqa: ARG001
        return _patched_market_history_df().copy()

    market_context.get_market_history = patched_get_market_history
    pipeline_context.get_market_history = patched_get_market_history

    def patched_prepare_ticker_context(ticker: str) -> dict[str, Any]:
        ctx = original_prepare(ticker)
        if str(ticker).upper().strip() == TICKER:
            mh = _patched_market_history_df().copy()
            ctx["market_history_df"] = mh
            ctx["sector_name"] = "materials"
            rb = ctx.get("base_rulebook")
            if rb is not None:
                rb.sector_name = "materials"
        return ctx

    pipeline_context.prepare_ticker_context = patched_prepare_ticker_context
    return patched_prepare_ticker_context


def dry_check(print_json: bool = False) -> dict[str, Any]:
    mh = build_materials_market_history()
    patched_prepare = _make_prepare_context_patch()
    ctx = patched_prepare(TICKER)
    sector_name = str(ctx.get("sector_name") or "")
    rb_sector_name = str(getattr(ctx.get("base_rulebook"), "sector_name", "") or "")
    has_col = "sector_materials" in mh.columns
    check_slice = mh.loc[(mh.index >= CHECK_START) & (mh.index <= CHECK_END), "sector_materials"] if has_col else pd.Series(dtype=float)
    non_null = bool(len(check_slice) > 0 and check_slice.notna().all())
    non_neutral_count = int((check_slice.round(8) != 50.0).sum()) if len(check_slice) else 0
    unique_count = int(check_slice.round(6).nunique()) if len(check_slice) else 0
    value_reflects_xlb = bool(non_neutral_count > 0 and unique_count > 3)
    xlb = _load_xlb()
    result = {
        "ticker": TICKER,
        "run_root": str(RUN_ROOT),
        "patched_market_history": str(PATCHED_MARKET_HISTORY),
        "xlb_cache": str(XLB_CACHE),
        "base_market_history": str(BASE_MARKET_HISTORY),
        "sector_name": sector_name,
        "base_rulebook_sector_name": rb_sector_name,
        "has_sector_materials": has_col,
        "check_start": str(CHECK_START.date()),
        "check_end": str(CHECK_END.date()),
        "check_rows": int(len(check_slice)),
        "check_null_count": int(check_slice.isna().sum()) if len(check_slice) else None,
        "check_non_null": non_null,
        "non_neutral_count": non_neutral_count,
        "unique_count": unique_count,
        "materials_min": float(check_slice.min()) if len(check_slice) else None,
        "materials_max": float(check_slice.max()) if len(check_slice) else None,
        "materials_latest_in_check": float(check_slice.iloc[-1]) if len(check_slice) else None,
        "xlb_first": str(xlb.index.min().date()),
        "xlb_last": str(xlb.index.max().date()),
        "market_history_first": str(mh.index.min().date()),
        "market_history_last": str(mh.index.max().date()),
    }
    ok_a = sector_name == "materials" and rb_sector_name == "materials"
    ok_b = bool(has_col and value_reflects_xlb)
    ok_c = bool(non_null)
    result.update({"ok_sector_name": ok_a, "ok_materials_column": ok_b, "ok_no_missing": ok_c})
    result["status"] = "INJECT_OK" if ok_a and ok_b and ok_c else "INJECT_FAIL"
    _write_json(DRY_CHECK_JSON, result)
    DRY_CHECK_MD.write_text(
        "# CE materials isolated injection dry check\n\n"
        f"status: `{result['status']}`\n\n"
        f"- sector_name: `{sector_name}`\n"
        f"- base_rulebook.sector_name: `{rb_sector_name}`\n"
        f"- sector_materials column: `{has_col}`\n"
        f"- rows checked: `{result['check_rows']}`\n"
        f"- null count: `{result['check_null_count']}`\n"
        f"- non-neutral values: `{non_neutral_count}`\n"
        f"- unique values: `{unique_count}`\n"
        f"- min/max: `{result['materials_min']}` / `{result['materials_max']}`\n"
        f"- xlb range: `{result['xlb_first']}` ~ `{result['xlb_last']}`\n"
        f"- patched market history: `{PATCHED_MARKET_HISTORY}`\n",
        encoding="utf-8",
    )
    print(result["status"])
    if print_json:
        print(json.dumps(_json_safe(result), ensure_ascii=False, indent=2, sort_keys=True))
    return result


def _require_inject_ok() -> None:
    result = dry_check(print_json=True)
    if result.get("status") != "INJECT_OK":
        _append_status("inject_failed", result=result)
        raise SystemExit("INJECT_FAIL")
    _append_status("inject_ok", result=result)


def run_stage2() -> int:
    _require_inject_ok()
    if NEW_STAGE2.exists():
        raise SystemExit(f"refusing to overwrite existing Stage2 dir: {NEW_STAGE2}")
    import scripts.research.run_stage2 as stage2

    patched_prepare = _make_prepare_context_patch()
    stage2.prepare_ticker_context = patched_prepare
    args = ["--ticker", TICKER, "--out-dir", str(NEW_STAGE2)]
    _append_status("stage2_start", command=["scripts/research/run_stage2.py", *args])
    rc = int(stage2.main(args) or 0)
    _append_status("stage2_done", returncode=rc)
    return rc


def run_stage3() -> int:
    _require_inject_ok()
    if NEW_STAGE3.exists():
        raise SystemExit(f"refusing to overwrite existing Stage3 dir: {NEW_STAGE3}")
    import scripts.research.run_stage3_aggressive as stage3

    patched_prepare = _make_prepare_context_patch()
    # The public wrapper delegates to its loaded original module.
    stage3._base.prepare_ticker_context = patched_prepare  # type: ignore[attr-defined]
    args = ["--ticker", TICKER, "--stage", "all", "--out-dir", str(NEW_STAGE3)]
    _append_status("stage3_start", command=["scripts/research/run_stage3_aggressive.py", *args])
    rc = int(stage3.main(args) or 0)
    _append_status("stage3_done", returncode=rc)
    return rc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:
            continue
    return rows


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
        if not math.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _pctile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    idx = int(round((len(vals) - 1) * q))
    return vals[max(0, min(len(vals) - 1, idx))]


def _dist(values: Iterable[Any]) -> dict[str, Any]:
    vals = [v for v in (_safe_float(x) for x in values) if v is not None]
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "mean": statistics.fmean(vals),
        "median": statistics.median(vals),
        "min": min(vals),
        "p10": _pctile(vals, 0.10),
        "p90": _pctile(vals, 0.90),
        "max": max(vals),
    }


def _rulebook_from_row(row: dict[str, Any]) -> dict[str, Any]:
    rb = row.get("rulebook") or row.get("best_rulebook") or row.get("final_rulebook") or {}
    return rb if isinstance(rb, dict) else {}


def _pick_best_stage2(stage_dir: Path) -> dict[str, Any] | None:
    survivors = _read_jsonl(stage_dir / "survivors.jsonl")
    if survivors:
        return max(survivors, key=lambda r: _safe_float(r.get("fitness") or r.get("member_score") or 0.0) or 0.0)
    rows = _read_jsonl(stage_dir / "rulebooks_all.jsonl")
    if rows:
        return max(rows, key=lambda r: _safe_float(r.get("train_fitness") or 0.0) or 0.0)
    return None


def _pick_best_stage3(stage_dir: Path) -> dict[str, Any] | None:
    rows = _read_jsonl(stage_dir / "final_rulebooks.jsonl")
    if rows:
        def score(row: dict[str, Any]) -> float:
            for key in ("composite_fitness", "fitness", "member_score"):
                val = _safe_float(row.get(key))
                if val is not None:
                    return val
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            return _safe_float(metrics.get("fitness")) or 0.0
        return max(rows, key=score)
    return None


def _metrics_from_stage2(stage_dir: Path, row: dict[str, Any] | None) -> dict[str, Any]:
    metrics_rows = []
    p = stage_dir / "period_metrics_all.csv"
    if p.exists():
        with p.open("r", encoding="utf-8", newline="") as fp:
            metrics_rows = list(csv.DictReader(fp))
    h = str((row or {}).get("rulebook_hash") or "")
    selected = [r for r in metrics_rows if str(r.get("rulebook_hash") or "") == h]
    # Prefer the last OOS row, otherwise last evaluated row.
    chosen = None
    for r in selected:
        if r.get("period_kind") == "oos" and r.get("status") == "evaluated":
            chosen = r
    if chosen is None and selected:
        chosen = selected[-1]
    return chosen or {}


def _metrics_from_stage3(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    for key in ("oos", "oos_metrics", "metrics", "validation_metrics", "bull_metrics"):
        val = row.get(key)
        if isinstance(val, dict):
            return val
    return row


def _extract_compare_fields(stage: str, label: str, stage_dir: Path, row: dict[str, Any] | None) -> dict[str, Any]:
    rb = _rulebook_from_row(row or {})
    if not rb and row:
        rb = row
    metrics = _metrics_from_stage2(stage_dir, row) if stage == "stage2" else _metrics_from_stage3(row)
    trades_path = stage_dir / ("trades.jsonl" if stage == "stage2" else "exit_trades.jsonl")
    trades = _read_jsonl(trades_path)
    return {
        "stage": stage,
        "label": label,
        "stage_dir": str(stage_dir),
        "row_found": bool(row),
        "rulebook_hash": (row or {}).get("rulebook_hash") or (row or {}).get("final_rulebook_hash"),
        "sector_name": rb.get("sector_name"),
        "use_market_entry_adjustment": rb.get("use_market_entry_adjustment"),
        "sector_strength_weight": rb.get("sector_strength_weight"),
        "signal_threshold": rb.get("signal_threshold"),
        "stop_loss_atr": rb.get("stop_loss_atr"),
        "trailing_atr": rb.get("trailing_atr"),
        "max_holding_days": rb.get("max_holding_days"),
        "win_rate": metrics.get("win_rate"),
        "expectancy": metrics.get("expectancy_pct") or metrics.get("expectancy"),
        "MDD": metrics.get("max_drawdown_pct") or metrics.get("mdd_pct"),
        "trade_count": metrics.get("trade_count"),
        "MAE_distribution": _dist([t.get("max_loss_during_hold") or t.get("mae_pct") for t in trades]),
        "MFE_distribution": _dist([t.get("max_profit_during_hold") or t.get("mfe_pct") for t in trades]),
    }


def _materiality(old: dict[str, Any], new: dict[str, Any]) -> str:
    if not old.get("row_found") or not new.get("row_found"):
        return "INCONCLUSIVE"
    checks = []
    for key in ["sector_name", "use_market_entry_adjustment"]:
        checks.append(old.get(key) != new.get(key))
    for key, threshold in [("win_rate", 10.0), ("expectancy", 2.0), ("MDD", 5.0), ("trade_count", 3.0), ("signal_threshold", 0.5), ("stop_loss_atr", 0.5), ("trailing_atr", 0.5)]:
        a = _safe_float(old.get(key))
        b = _safe_float(new.get(key))
        if a is not None and b is not None:
            checks.append(abs(b - a) >= threshold)
    return "SECTOR_MATTERS_MUCH" if sum(bool(x) for x in checks) >= 3 else "SECTOR_MATTERS_LITTLE"


def compare() -> int:
    rows = []
    old2 = _extract_compare_fields("stage2", "old", OLD_STAGE2, _pick_best_stage2(OLD_STAGE2))
    new2 = _extract_compare_fields("stage2", "new_materials", NEW_STAGE2, _pick_best_stage2(NEW_STAGE2))
    old3 = _extract_compare_fields("stage3", "old", OLD_STAGE3, _pick_best_stage3(OLD_STAGE3))
    new3 = _extract_compare_fields("stage3", "new_materials", NEW_STAGE3, _pick_best_stage3(NEW_STAGE3))
    rows.extend([old2, new2, old3, new3])
    stage2_judgement = _materiality(old2, new2)
    stage3_judgement = _materiality(old3, new3)

    COMPARE_CSV.parent.mkdir(parents=True, exist_ok=True)
    flat_rows = []
    for row in rows:
        flat = dict(row)
        flat["MAE_distribution"] = json.dumps(_json_safe(flat.get("MAE_distribution")), ensure_ascii=False, sort_keys=True)
        flat["MFE_distribution"] = json.dumps(_json_safe(flat.get("MFE_distribution")), ensure_ascii=False, sort_keys=True)
        flat_rows.append(flat)
    with COMPARE_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(flat_rows[0].keys()))
        writer.writeheader()
        writer.writerows(flat_rows)

    COMPARE_MD.write_text(
        "# CE materials isolated retrain comparison\n\n"
        f"Stage2 판정: `{stage2_judgement}`\n\n"
        f"Stage3 판정: `{stage3_judgement}`\n\n"
        f"CSV: `{COMPARE_CSV}`\n\n"
        "## Rows\n\n"
        "```json\n"
        + json.dumps(_json_safe(rows), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n",
        encoding="utf-8",
    )
    _append_status("compare_done", stage2=stage2_judgement, stage3=stage3_judgement, csv=str(COMPARE_CSV), readout=str(COMPARE_MD))
    print(json.dumps({"stage2": stage2_judgement, "stage3": stage3_judgement, "readout": str(COMPARE_MD), "csv": str(COMPARE_CSV)}, ensure_ascii=False, indent=2))
    return 0


def run_all() -> int:
    _require_inject_ok()
    if NEW_STAGE2.exists() or NEW_STAGE3.exists():
        raise SystemExit(f"refusing to overwrite existing outputs: {NEW_STAGE2} / {NEW_STAGE3}")
    rc2 = run_stage2()
    if rc2 != 0:
        return rc2
    rc3 = run_stage3()
    if rc3 != 0:
        return rc3
    return compare()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated CE materials-sector retrain harness")
    parser.add_argument("mode", choices=["dry", "stage2", "stage3", "compare", "all"])
    parser.add_argument("--json", action="store_true", help="Print dry-check JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "dry":
        res = dry_check(print_json=bool(args.json))
        return 0 if res.get("status") == "INJECT_OK" else 2
    if args.mode == "stage2":
        return run_stage2()
    if args.mode == "stage3":
        return run_stage3()
    if args.mode == "compare":
        return compare()
    if args.mode == "all":
        return run_all()
    raise ValueError(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
