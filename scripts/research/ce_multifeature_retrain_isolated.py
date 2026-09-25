#!/usr/bin/env python3
"""Isolated CE multi-feature retrain harness.

Production source files, live settings, existing artifacts, and the original
`data/_system/market_history.csv` are not modified.  All changes are in-memory
inside this process and all outputs go to `exp_batch_CE_multifeature_20260708`.

Injected independent features, each scored with the existing sector formula:
    score = clip(50 + ret_60d * 5, 0, 100)

Columns:
    sector_materials: XLB
    peer_EMN: EMN
    peer_DD: DD
    peer_LYB: LYB
    peer_WLK: WLK
    macro_ind: XLI
    cost_oil: USO

Learned weights:
    mf_weight_sector_materials
    mf_weight_peer_EMN
    mf_weight_peer_DD
    mf_weight_peer_LYB
    mf_weight_peer_WLK
    mf_weight_macro_ind
    mf_weight_cost_oil
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUN_ROOT = PROJECT_ROOT / "exp_batch_CE_multifeature_20260708"
TICKER = "CE"
CACHE_ROOT = PROJECT_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
BASE_MARKET_HISTORY = PROJECT_ROOT / "data/_system/market_history.csv"
PATCHED_MARKET_HISTORY = RUN_ROOT / "inputs/market_history_with_ce_multifeatures.csv"
DRY_CHECK_JSON = RUN_ROOT / "dry_check.json"
DRY_CHECK_MD = RUN_ROOT / "dry_check_readout.md"
STATUS_JSON = RUN_ROOT / "run_status.json"
COMPARE_MD = RUN_ROOT / "comparison_readout.md"
COMPARE_CSV = RUN_ROOT / "comparison.csv"

OLD_BATCH_ROOT = PROJECT_ROOT / "exp_batch_stage123_2009_20260616_full"
OLD_STAGE2 = OLD_BATCH_ROOT / "tickers/CE/stage2"
OLD_STAGE3 = OLD_BATCH_ROOT / "tickers/CE/stage3"
XLB_SINGLE_ROOT = PROJECT_ROOT / "exp_batch_CE_materials_retrain_20260708"
XLB_STAGE2 = XLB_SINGLE_ROOT / "tickers/CE/stage2"
XLB_STAGE3 = XLB_SINGLE_ROOT / "tickers/CE/stage3"
NEW_STAGE2 = RUN_ROOT / "tickers/CE/stage2"
NEW_STAGE3 = RUN_ROOT / "tickers/CE/stage3"

CHECK_START = pd.Timestamp("2020-05-18")
CHECK_END = pd.Timestamp("2026-06-12")

FEATURE_SPECS: tuple[tuple[str, str, str], ...] = (
    ("sector_materials", "XLB", "mf_weight_sector_materials"),
    ("peer_EMN", "EMN", "mf_weight_peer_EMN"),
    ("peer_DD", "DD", "mf_weight_peer_DD"),
    ("peer_LYB", "LYB", "mf_weight_peer_LYB"),
    ("peer_WLK", "WLK", "mf_weight_peer_WLK"),
    ("macro_ind", "XLI", "mf_weight_macro_ind"),
    ("cost_oil", "USO", "mf_weight_cost_oil"),
)
FEATURE_COLUMNS = tuple(col for col, _, _ in FEATURE_SPECS)
FEATURE_WEIGHT_FIELDS = tuple(weight for _, _, weight in FEATURE_SPECS)
MULTI_FEATURE_TOPIC_KEY = "__ce_multifeature_scores"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
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
    current.append({"event": event, **payload})
    _write_json(STATUS_JSON, current)


def _load_base_market_history() -> pd.DataFrame:
    if not BASE_MARKET_HISTORY.exists():
        raise FileNotFoundError(f"base market_history missing: {BASE_MARKET_HISTORY}")
    df = pd.read_csv(BASE_MARKET_HISTORY, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def _load_ohlcv(symbol: str) -> pd.DataFrame:
    path = CACHE_ROOT / f"{symbol}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"cache missing for {symbol}: {path}")
    df = pd.read_pickle(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "Date" in df.columns:
            df.index = pd.to_datetime(df["Date"])
        elif "date" in df.columns:
            df.index = pd.to_datetime(df["date"])
        else:
            df.index = pd.to_datetime(df.index)
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def _score_from_close(df: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(df["Close"], errors="coerce").sort_index()
    ret_60d = (close / close.shift(60) - 1.0) * 100.0
    return (50.0 + ret_60d * 5.0).clip(lower=0.0, upper=100.0).fillna(50.0)


def build_multifeature_market_history() -> pd.DataFrame:
    mh = _load_base_market_history()
    out = mh.copy()
    for col, symbol, _weight in FEATURE_SPECS:
        score = _score_from_close(_load_ohlcv(symbol))
        aligned = score.reindex(out.index).ffill().fillna(50.0)
        out[col] = aligned.astype(float)
    PATCHED_MARKET_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(PATCHED_MARKET_HISTORY)
    return out


def _patched_market_history_df() -> pd.DataFrame:
    if PATCHED_MARKET_HISTORY.exists():
        df = pd.read_csv(PATCHED_MARKET_HISTORY, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        return df.sort_index()
    return build_multifeature_market_history()


def _ensure_multifeature_attrs(rb: Any) -> Any:
    if rb is None:
        return rb
    for weight in FEATURE_WEIGHT_FIELDS:
        if not hasattr(rb, weight):
            setattr(rb, weight, 0.0)
    return rb


def _patch_rulebook_and_hashing() -> None:
    from engine.strategies.rulebook import PARAM_RANGES, Rulebook
    import engine.learning.genetic as genetic
    import engine.core.metadata as metadata

    for weight in FEATURE_WEIGHT_FIELDS:
        PARAM_RANGES[weight] = (-1.0, 1.0)
        genetic.PARAM_RANGES[weight] = (-1.0, 1.0)

    if not getattr(Rulebook, "_ce_multifeature_patched", False):
        original_to_dict = Rulebook.to_dict
        original_from_dict_func = Rulebook.from_dict.__func__

        def patched_to_dict(self: Any) -> dict[str, Any]:
            d = dict(original_to_dict(self))
            for weight in FEATURE_WEIGHT_FIELDS:
                d[weight] = float(getattr(self, weight, 0.0) or 0.0)
            d["ce_multifeature_schema"] = 1
            return d

        @classmethod
        def patched_from_dict(cls: Any, d: dict[str, Any]) -> Any:
            rb = original_from_dict_func(cls, d)
            for weight in FEATURE_WEIGHT_FIELDS:
                setattr(rb, weight, float(d.get(weight, 0.0) or 0.0))
            return rb

        Rulebook.to_dict = patched_to_dict  # type: ignore[method-assign]
        Rulebook.from_dict = patched_from_dict  # type: ignore[method-assign]
        Rulebook._ce_multifeature_patched = True  # type: ignore[attr-defined]

    if not getattr(metadata, "_ce_multifeature_patched", False):
        original_public = metadata._public_object_dict

        def patched_public_object_dict(obj: Any) -> dict[str, Any]:
            if isinstance(obj, Rulebook):
                return obj.to_dict()
            return original_public(obj)

        metadata._public_object_dict = patched_public_object_dict
        metadata._ce_multifeature_patched = True  # type: ignore[attr-defined]

    if not getattr(genetic, "_ce_multifeature_finalize_patched", False):
        original_finalize = genetic._finalize_rulebook_genes

        def patched_finalize(rb: Any) -> Any:
            return _ensure_multifeature_attrs(original_finalize(_ensure_multifeature_attrs(rb)))

        genetic._finalize_rulebook_genes = patched_finalize
        genetic._ce_multifeature_finalize_patched = True  # type: ignore[attr-defined]


def _patch_backtest_context_and_evaluator() -> None:
    _patch_rulebook_and_hashing()
    import engine.pipeline.context as pipeline_context
    from engine.market import context as market_context
    import engine.learning.backtest as backtest
    import engine.learning.execution_mode_backtest as execution_mode_backtest
    import engine.strategies.evaluator as evaluator

    market_context.SECTOR_ETFS["materials"] = "XLB"
    original_prepare = pipeline_context.prepare_ticker_context
    original_lookup = backtest._lookup_signal_context
    original_evaluate = evaluator.evaluate_signal

    def patched_get_market_history(years: int = 6) -> pd.DataFrame:  # noqa: ARG001
        return _patched_market_history_df().copy()

    market_context.get_market_history = patched_get_market_history
    pipeline_context.get_market_history = patched_get_market_history

    def patched_prepare_ticker_context(ticker: str) -> dict[str, Any]:
        ctx = original_prepare(ticker)
        if str(ticker).upper().strip() == TICKER:
            ctx["market_history_df"] = _patched_market_history_df().copy()
            ctx["sector_name"] = "materials"
            rb = _ensure_multifeature_attrs(ctx.get("base_rulebook"))
            if rb is not None:
                rb.sector_name = "materials"
            ctx["base_rulebook"] = rb
            ctx["ce_multifeature_columns"] = list(FEATURE_COLUMNS)
            ctx["ce_multifeature_weight_fields"] = list(FEATURE_WEIGHT_FIELDS)
        return ctx

    def patched_lookup_signal_context(**kwargs: Any) -> tuple[float, float, float, float, dict, dict]:
        cur_market, cur_sector, cur_vix, cur_sentiment, event_flags, topic_features = original_lookup(**kwargs)
        topic_features = dict(topic_features or {})
        mh = kwargs.get("market_history_df")
        df = kwargs.get("df")
        idx = kwargs.get("idx")
        scores = {col: 50.0 for col in FEATURE_COLUMNS}
        try:
            if mh is not None and df is not None and idx is not None:
                mkt = backtest.lookup_market_at_lagged(mh, df.index[int(idx)], lag_days=backtest.FEATURE_LAG_DAYS)
                for col in FEATURE_COLUMNS:
                    scores[col] = float(mkt.get(col, 50.0) or 50.0)
        except Exception:
            pass
        topic_features[MULTI_FEATURE_TOPIC_KEY] = scores
        return cur_market, cur_sector, cur_vix, cur_sentiment, event_flags, topic_features

    def patched_evaluate_signal(*args: Any, **kwargs: Any) -> Any:
        rb = args[0] if args else kwargs.get("rb")
        rb = _ensure_multifeature_attrs(rb)
        res = original_evaluate(*args, **kwargs)
        topic_features = kwargs.get("topic_features") or (args[7] if len(args) > 7 else {}) or {}
        mf_scores = dict(topic_features.get(MULTI_FEATURE_TOPIC_KEY) or {}) if isinstance(topic_features, dict) else {}
        market_score = float(kwargs.get("market_score", args[2] if len(args) > 2 else 50.0) or 50.0)
        vix_level = float(kwargs.get("vix_level", args[4] if len(args) > 4 else 18.0) or 18.0)
        raw_score = float(getattr(res, "raw_score", 0.0) or 0.0)
        market_norm = (market_score - 50.0) / 50.0
        vix_norm = (18.0 - vix_level) / 10.0
        correlation_adj = market_norm * float(getattr(rb, "market_score_weight", 0.0) or 0.0)
        correlation_adj += vix_norm * float(getattr(rb, "vix_sensitivity", 0.0) or 0.0)
        mf_components: dict[str, float] = {}
        for col, _symbol, weight_field in FEATURE_SPECS:
            score = float(mf_scores.get(col, 50.0) or 50.0)
            norm = (score - 50.0) / 50.0
            weight = float(getattr(rb, weight_field, 0.0) or 0.0)
            contrib = norm * weight
            correlation_adj += contrib
            mf_components[weight_field] = contrib
        strength = max(0.0, min(1.0, float(getattr(rb, "market_adjustment_strength", 0.0) or 0.0)))
        market_adjustment = 1.0 + max(min(correlation_adj * strength, strength), -strength)
        if not getattr(rb, "use_market_entry_adjustment", True):
            market_adjustment = 1.0
        final_score = raw_score * market_adjustment
        should_buy = final_score >= float(getattr(rb, "signal_threshold", 0.0) or 0.0)
        reasons = [r for r in list(getattr(res, "reasons", []) or []) if not str(r).startswith("시장보정×")]
        if market_adjustment != 1.0:
            reasons.append(f"멀티피처보정×{market_adjustment:.2f}")
        components = dict(getattr(res, "components", {}) or {})
        components["ce_multifeature_scores"] = mf_scores
        components["ce_multifeature_contrib"] = mf_components
        components["ce_multifeature_correlation_adj"] = correlation_adj
        return evaluator.SignalResult(
            should_buy=bool(should_buy),
            score=float(final_score),
            raw_score=raw_score,
            threshold=float(getattr(rb, "signal_threshold", 0.0) or 0.0),
            reasons=reasons,
            market_adjustment=float(market_adjustment),
            components=components,
        )

    pipeline_context.prepare_ticker_context = patched_prepare_ticker_context
    backtest._lookup_signal_context = patched_lookup_signal_context
    execution_mode_backtest._lookup_signal_context = patched_lookup_signal_context
    evaluator.evaluate_signal = patched_evaluate_signal
    execution_mode_backtest.evaluate_signal = patched_evaluate_signal


def dry_check(print_json: bool = False) -> dict[str, Any]:
    mh = build_multifeature_market_history()
    _patch_backtest_context_and_evaluator()
    import engine.pipeline.context as pipeline_context

    ctx = pipeline_context.prepare_ticker_context(TICKER)
    rb = ctx.get("base_rulebook")
    check_rows = mh.loc[(mh.index >= CHECK_START) & (mh.index <= CHECK_END), list(FEATURE_COLUMNS)]
    feature_checks = {}
    for col in FEATURE_COLUMNS:
        s = check_rows[col]
        feature_checks[col] = {
            "exists": col in mh.columns,
            "null_count": int(s.isna().sum()),
            "non_neutral_count": int((s.round(8) != 50.0).sum()),
            "unique_count": int(s.round(6).nunique()),
            "min": float(s.min()),
            "max": float(s.max()),
            "latest": float(s.iloc[-1]),
            "ok": bool(col in mh.columns and s.notna().all() and int((s.round(8) != 50.0).sum()) > 0 and int(s.round(6).nunique()) > 3),
        }
    ok_sector = str(ctx.get("sector_name") or "") == "materials" and str(getattr(rb, "sector_name", "") or "") == "materials"
    ok_features = all(v["ok"] for v in feature_checks.values())
    ok_missing = bool(len(check_rows) > 0 and check_rows.notna().all().all())
    ok_weights = all(hasattr(rb, weight) for weight in FEATURE_WEIGHT_FIELDS)
    result = {
        "ticker": TICKER,
        "run_root": str(RUN_ROOT),
        "patched_market_history": str(PATCHED_MARKET_HISTORY),
        "base_market_history": str(BASE_MARKET_HISTORY),
        "cache_root": str(CACHE_ROOT),
        "sector_name": ctx.get("sector_name"),
        "base_rulebook_sector_name": getattr(rb, "sector_name", None),
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_weight_fields": list(FEATURE_WEIGHT_FIELDS),
        "check_start": str(CHECK_START.date()),
        "check_end": str(CHECK_END.date()),
        "check_rows": int(len(check_rows)),
        "feature_checks": feature_checks,
        "ok_sector_name": ok_sector,
        "ok_feature_columns": ok_features,
        "ok_no_missing": ok_missing,
        "ok_weight_fields": ok_weights,
        "status": "INJECT_OK" if ok_sector and ok_features and ok_missing and ok_weights else "INJECT_FAIL",
    }
    _write_json(DRY_CHECK_JSON, result)
    lines = [
        "# CE multi-feature isolated injection dry check",
        "",
        f"status: `{result['status']}`",
        "",
        f"sector_name: `{result['sector_name']}`",
        f"base_rulebook.sector_name: `{result['base_rulebook_sector_name']}`",
        f"rows checked: `{result['check_rows']}`",
        "",
        "| column | ok | nulls | non_neutral | unique | min | max | latest |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for col in FEATURE_COLUMNS:
        c = feature_checks[col]
        lines.append(f"| {col} | {c['ok']} | {c['null_count']} | {c['non_neutral_count']} | {c['unique_count']} | {c['min']:.4f} | {c['max']:.4f} | {c['latest']:.4f} |")
    DRY_CHECK_MD.parent.mkdir(parents=True, exist_ok=True)
    DRY_CHECK_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    _patch_backtest_context_and_evaluator()
    import scripts.research.run_stage2 as stage2
    import engine.pipeline.context as pipeline_context

    stage2.prepare_ticker_context = pipeline_context.prepare_ticker_context
    args = ["--ticker", TICKER, "--out-dir", str(NEW_STAGE2)]
    _append_status("stage2_start", command=["scripts/research/run_stage2.py", *args])
    rc = int(stage2.main(args) or 0)
    _append_status("stage2_done", returncode=rc)
    return rc


def run_stage3() -> int:
    _require_inject_ok()
    if NEW_STAGE3.exists():
        raise SystemExit(f"refusing to overwrite existing Stage3 dir: {NEW_STAGE3}")
    _patch_backtest_context_and_evaluator()
    import scripts.research.run_stage3_aggressive as stage3
    import engine.pipeline.context as pipeline_context

    stage3._base.prepare_ticker_context = pipeline_context.prepare_ticker_context  # type: ignore[attr-defined]
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
    return {"count": len(vals), "mean": statistics.fmean(vals), "median": statistics.median(vals), "min": min(vals), "p10": _pctile(vals, 0.10), "p90": _pctile(vals, 0.90), "max": max(vals)}


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


def _load_stage2_metrics(stage_dir: Path, row: dict[str, Any] | None) -> dict[str, Any]:
    p = stage_dir / "period_metrics_all.csv"
    if not p.exists() or not row:
        return {}
    h = str(row.get("rulebook_hash") or "")
    with p.open("r", encoding="utf-8", newline="") as fp:
        all_rows = list(csv.DictReader(fp))
    rows = [r for r in all_rows if str(r.get("rulebook_hash") or "") == h]
    by_role = {"IS": [], "OOS": []}
    for r in rows:
        if r.get("period_kind") == "train":
            by_role["IS"].append(r)
        elif r.get("period_kind") == "oos":
            by_role["OOS"].append(r)
    def avg(key: str, xs: list[dict[str, Any]]) -> float | None:
        vals = [v for v in (_safe_float(x.get(key)) for x in xs) if v is not None]
        return statistics.fmean(vals) if vals else None
    return {
        "win_rate": avg("win_rate", by_role["OOS"]) or avg("win_rate", rows),
        "expectancy_pct": avg("expectancy_pct", by_role["OOS"]) or avg("expectancy_pct", rows),
        "max_drawdown_pct": avg("max_drawdown_pct", by_role["OOS"]) or avg("max_drawdown_pct", rows),
        "trade_count": avg("trade_count", by_role["OOS"]) or avg("trade_count", rows),
        "IS_expectancy": avg("expectancy_pct", by_role["IS"]),
        "OOS_expectancy": avg("expectancy_pct", by_role["OOS"]),
        "IS_win_rate": avg("win_rate", by_role["IS"]),
        "OOS_win_rate": avg("win_rate", by_role["OOS"]),
        "IS_MDD": avg("max_drawdown_pct", by_role["IS"]),
        "OOS_MDD": avg("max_drawdown_pct", by_role["OOS"]),
    }


def _metrics_from_stage3(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out = dict(row)
    for key in ("oos", "oos_metrics", "metrics", "validation_metrics", "bull_metrics"):
        val = row.get(key)
        if isinstance(val, dict):
            out.update(val)
    # Known stage3 rows often have bull/stress metrics; keep both if present.
    bull = row.get("bull_metrics") if isinstance(row.get("bull_metrics"), dict) else {}
    stress = row.get("stress_metrics") if isinstance(row.get("stress_metrics"), dict) else {}
    if bull:
        out["IS_expectancy"] = bull.get("expectancy_pct")
        out["IS_win_rate"] = bull.get("win_rate")
        out["IS_MDD"] = bull.get("max_drawdown_pct")
    if stress:
        out["OOS_expectancy"] = stress.get("expectancy_pct")
        out["OOS_win_rate"] = stress.get("win_rate")
        out["OOS_MDD"] = stress.get("max_drawdown_pct")
    return out


def _extract_compare_fields(stage: str, label: str, stage_dir: Path, row: dict[str, Any] | None) -> dict[str, Any]:
    rb = _rulebook_from_row(row or {})
    if not rb and row:
        rb = row
    metrics = _load_stage2_metrics(stage_dir, row) if stage == "stage2" else _metrics_from_stage3(row)
    trades_path = stage_dir / ("trades.jsonl" if stage == "stage2" else "exit_trades.jsonl")
    trades = _read_jsonl(trades_path)
    out = {
        "stage": stage,
        "label": label,
        "stage_dir": str(stage_dir),
        "row_found": bool(row),
        "rulebook_hash": (row or {}).get("rulebook_hash") or (row or {}).get("final_rulebook_hash"),
        "sector_name": rb.get("sector_name"),
        "use_market_entry_adjustment": rb.get("use_market_entry_adjustment"),
        "sector_strength_weight": rb.get("sector_strength_weight"),
        "signal_threshold": rb.get("signal_threshold"),
        "win_rate": metrics.get("win_rate"),
        "expectancy": metrics.get("expectancy_pct") or metrics.get("expectancy"),
        "MDD": metrics.get("max_drawdown_pct") or metrics.get("mdd_pct"),
        "trade_count": metrics.get("trade_count"),
        "IS_expectancy": metrics.get("IS_expectancy"),
        "OOS_expectancy": metrics.get("OOS_expectancy"),
        "IS_win_rate": metrics.get("IS_win_rate"),
        "OOS_win_rate": metrics.get("OOS_win_rate"),
        "IS_MDD": metrics.get("IS_MDD"),
        "OOS_MDD": metrics.get("OOS_MDD"),
        "MAE_distribution": _dist([t.get("max_loss_during_hold") or t.get("mae_pct") for t in trades]),
        "MFE_distribution": _dist([t.get("max_profit_during_hold") or t.get("mfe_pct") for t in trades]),
    }
    for weight in FEATURE_WEIGHT_FIELDS:
        out[weight] = rb.get(weight)
    return out


def _overfit_judgement(rows: list[dict[str, Any]], label: str, stage: str) -> str:
    old = next((r for r in rows if r["stage"] == stage and r["label"] == "old_tech"), None)
    new = next((r for r in rows if r["stage"] == stage and r["label"] == label), None)
    if not old or not new or not old.get("row_found") or not new.get("row_found"):
        return "INCONCLUSIVE"
    old_is = _safe_float(old.get("IS_expectancy"))
    new_is = _safe_float(new.get("IS_expectancy"))
    old_oos = _safe_float(old.get("OOS_expectancy"))
    new_oos = _safe_float(new.get("OOS_expectancy"))
    if old_is is not None and new_is is not None and old_oos is not None and new_oos is not None:
        if new_is > old_is and new_oos <= old_oos:
            return "OVERFIT"
    changes = 0
    for key, th in [("expectancy", 2.0), ("win_rate", 10.0), ("MDD", 5.0), ("trade_count", 3.0)]:
        a = _safe_float(old.get(key)); b = _safe_float(new.get(key))
        if a is not None and b is not None and abs(b - a) >= th:
            changes += 1
    return "SECTOR_MATTERS_MUCH" if changes >= 2 else "SECTOR_MATTERS_LITTLE"


def compare() -> int:
    rows: list[dict[str, Any]] = []
    for stage, old_dir, xlb_dir, multi_dir, picker in [
        ("stage2", OLD_STAGE2, XLB_STAGE2, NEW_STAGE2, _pick_best_stage2),
        ("stage3", OLD_STAGE3, XLB_STAGE3, NEW_STAGE3, _pick_best_stage3),
    ]:
        rows.append(_extract_compare_fields(stage, "old_tech", old_dir, picker(old_dir)))
        rows.append(_extract_compare_fields(stage, "xlb_single", xlb_dir, picker(xlb_dir)))
        rows.append(_extract_compare_fields(stage, "multifeature", multi_dir, picker(multi_dir)))

    judgments = {
        "stage2_multifeature": _overfit_judgement(rows, "multifeature", "stage2"),
        "stage3_multifeature": _overfit_judgement(rows, "multifeature", "stage3"),
        "stage2_xlb_single": _overfit_judgement(rows, "xlb_single", "stage2"),
        "stage3_xlb_single": _overfit_judgement(rows, "xlb_single", "stage3"),
    }
    flat_rows = []
    for row in rows:
        flat = dict(row)
        flat["MAE_distribution"] = json.dumps(_json_safe(flat.get("MAE_distribution")), ensure_ascii=False, sort_keys=True)
        flat["MFE_distribution"] = json.dumps(_json_safe(flat.get("MFE_distribution")), ensure_ascii=False, sort_keys=True)
        flat_rows.append(flat)
    COMPARE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with COMPARE_CSV.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(flat_rows[0].keys()))
        writer.writeheader(); writer.writerows(flat_rows)
    COMPARE_MD.write_text(
        "# CE three-way comparison: old tech vs XLB single vs multi-feature\n\n"
        + "## Judgement\n\n"
        + json.dumps(judgments, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n\n## Notes\n\n"
        + "If IS improves while OOS does not improve, judgement is explicitly marked `OVERFIT`.\n\n"
        + f"CSV: `{COMPARE_CSV}`\n\n"
        + "## Rows\n\n```json\n"
        + json.dumps(_json_safe(rows), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n",
        encoding="utf-8",
    )
    _append_status("compare_done", judgments=judgments, csv=str(COMPARE_CSV), readout=str(COMPARE_MD))
    print(json.dumps({"judgments": judgments, "readout": str(COMPARE_MD), "csv": str(COMPARE_CSV)}, ensure_ascii=False, indent=2, sort_keys=True))
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
    parser = argparse.ArgumentParser(description="Isolated CE multi-feature retrain harness")
    parser.add_argument("mode", choices=["dry", "stage2", "stage3", "compare", "all"])
    parser.add_argument("--json", action="store_true", help="Print dry-check JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "dry":
        result = dry_check(print_json=bool(args.json))
        return 0 if result.get("status") == "INJECT_OK" else 2
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
