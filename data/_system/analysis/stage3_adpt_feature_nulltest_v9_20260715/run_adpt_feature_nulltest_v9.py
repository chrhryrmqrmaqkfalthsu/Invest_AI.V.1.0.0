#!/usr/bin/env python3
"""ADPT strict-entry one-feature null-test v9 runner.

Runtime-only helper for stage3_adpt_feature_nulltest_v9_20260715.
It does not edit source files. It patches the v5 AAP host runner in-process so
ADPT OHLCV cache is used, adds exactly one extra strict-AND entry feature, and
runs the qualify phase with v5 EEC parameters target=6/floor=0.5.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
ROOT = HERE.parents[4]
STAGE = ROOT / "scripts/research/stage23_rework_20260713"
V5_PATH = STAGE / "scripts/research/run_stage3_aap_eec_penalty_v5_host.py"
CACHE_ROOT = ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
SELL_OMEN = ROOT / "data/_system/ml_sell_omen/sell_omen_scores.csv"

TICKER = "ADPT"
PEER_TICKERS = ("NTRA", "GH", "PSNL", "VCYT", "NEO", "CDNA")
XBI_TICKER = "XBI"
ALLOWED_FEATURES = {"trend_chop20", "atr14_pct", "range_pct_rank60", "rs_peer_ret20", "rs_xbi_ret20"}
VARIANTS = {"REAL", "SHUFFLED"}
LOW_FIELD = "entry_extra_feature_low"
HIGH_FIELD = "entry_extra_feature_high"
NAME_FIELD = "entry_extra_feature_name"
PATCH_TOKEN = "adpt_feature_nulltest_v9_20260715"
TRAIN_START = pd.Timestamp("2022-07-01")
TRAIN_END = pd.Timestamp("2025-06-30")
FOLDS = {
    "train_1": ("2022-07-01", "2023-06-30"),
    "train_2": ("2023-07-01", "2024-06-30"),
    "train_3": ("2024-07-01", "2025-06-30"),
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        number = float(value)
    except Exception:
        return float(default)
    return number if math.isfinite(number) else float(default)


def _load_v5() -> Any:
    spec = importlib.util.spec_from_file_location("_adpt_feature_nulltest_v9_base", V5_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v5 host runner: {V5_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _date_series(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        return pd.Series(pd.to_datetime(df["date"], errors="coerce").to_numpy(), index=df.index)
    if "Date" in df.columns:
        return pd.Series(pd.to_datetime(df["Date"], errors="coerce").to_numpy(), index=df.index)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(df.index, errors="coerce"), index=df.index)
    raise ValueError("feature domain requires a date column or DatetimeIndex")


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def _load_close(symbol: str) -> pd.Series:
    path = CACHE_ROOT / f"{symbol}.pkl"
    if not path.is_file():
        raise FileNotFoundError(f"OHLCV cache missing: {path}")
    frame = pd.read_pickle(path)
    if "Close" not in frame.columns:
        raise RuntimeError(f"Close column missing: {path}")
    idx = pd.to_datetime(frame.index, errors="coerce")
    if idx.isna().any():
        raise RuntimeError(f"invalid dates in OHLCV: {path}")
    close = pd.Series(pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float), index=idx, name=symbol).sort_index()
    sub = close.loc[(close.index >= TRAIN_START) & (close.index <= TRAIN_END)]
    if sub.empty or sub.index.min() > TRAIN_START or sub.index.max() < TRAIN_END:
        raise RuntimeError(f"{symbol} does not fully cover train period")
    if not np.isfinite(sub.to_numpy(dtype=float)).all():
        raise RuntimeError(f"{symbol} has NaN/Inf Close in train period")
    return close


def validate_inputs() -> dict[str, Any]:
    required = ("Open", "High", "Low", "Close", "Volume")
    out: dict[str, Any] = {"ticker": TICKER, "peers": {}, "xbi": {}, "adpt": {}}
    for symbol in (TICKER, *PEER_TICKERS, XBI_TICKER):
        path = CACHE_ROOT / f"{symbol}.pkl"
        rec: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            frame = pd.read_pickle(path)
            frame.index = pd.to_datetime(frame.index, errors="coerce")
            frame = frame.sort_index()
            sub = frame.loc[(frame.index >= TRAIN_START) & (frame.index <= TRAIN_END)]
            rec.update(
                {
                    "sha256": _sha256_file(path),
                    "rows": int(len(frame)),
                    "first_date": str(frame.index.min().date()),
                    "last_date": str(frame.index.max().date()),
                    "train_rows": int(len(sub)),
                    "required_columns_present": all(col in frame.columns for col in required),
                }
            )
            if all(col in frame.columns for col in required):
                null_count = int(sub.loc[:, list(required)].isna().sum().sum())
                rec["train_ohlcv_null_count"] = null_count
                rec["covers_train"] = bool(len(sub) and sub.index.min() <= TRAIN_START and sub.index.max() >= TRAIN_END and null_count == 0)
            else:
                rec["train_ohlcv_null_count"] = None
                rec["covers_train"] = False
        if symbol == TICKER:
            out["adpt"] = rec
        elif symbol == XBI_TICKER:
            out["xbi"] = rec
        else:
            out["peers"][symbol] = rec
    out["accepted_peers"] = [s for s, rec in out["peers"].items() if rec.get("covers_train")]
    out["peer_feature_available"] = len(out["accepted_peers"]) >= 3
    out["xbi_feature_available"] = bool(out["xbi"].get("covers_train"))
    if not out["adpt"].get("covers_train"):
        raise RuntimeError("ADPT train OHLCV coverage failed")
    return out


def _adpt_rebased_ret20(df: pd.DataFrame) -> pd.Series:
    dates = pd.to_datetime(_date_series(df))
    close = pd.Series(_numeric(df, "Close").to_numpy(dtype=float), index=dates, name=TICKER).sort_index()
    rebased = close / close.dropna().iloc[0] * 100.0
    return (rebased.pct_change(20) * 100.0).rename("adpt_ret20")


def _candidate_raw_series(df: pd.DataFrame, feature: str) -> pd.Series:
    close = _numeric(df, "Close")
    if feature == "trend_chop20":
        denom = close.pct_change().abs().rolling(20, min_periods=20).sum().replace(0.0, np.nan)
        return (close.pct_change(20).abs() / denom).rename(feature)
    if feature == "atr14_pct":
        if "ATR_pct" in df.columns:
            return _numeric(df, "ATR_pct").rename(feature)
        atr = _numeric(df, "ATR")
        return (atr / close * 100.0).rename(feature)
    if feature == "range_pct_rank60":
        range_pct = (_numeric(df, "High") - _numeric(df, "Low")) / close * 100.0
        try:
            return range_pct.rolling(60, min_periods=20).rank(pct=True).rename(feature)
        except Exception:
            return range_pct.rolling(60, min_periods=20).apply(
                lambda arr: float(pd.Series(arr).rank(pct=True).iloc[-1]), raw=False
            ).rename(feature)
    if feature == "rs_peer_ret20":
        validation = validate_inputs()
        accepted = tuple(validation["accepted_peers"])
        if len(accepted) < 3:
            raise RuntimeError(f"rs_peer_ret20 requires at least 3 accepted peers; accepted={accepted}")
        dates = pd.to_datetime(_date_series(df))
        adpt_ret20 = _adpt_rebased_ret20(df)
        rebased_peers = []
        for symbol in accepted:
            close_peer = _load_close(symbol)
            rebased = close_peer / close_peer.dropna().iloc[0] * 100.0
            rebased_peers.append(rebased.rename(symbol))
        basket = pd.concat(rebased_peers, axis=1).mean(axis=1, skipna=False)
        basket_ret20 = basket.pct_change(20) * 100.0
        rs = (adpt_ret20 - basket_ret20).rename(feature)
        return pd.Series(rs.reindex(dates).to_numpy(dtype=float), index=df.index, name=feature)
    if feature == "rs_xbi_ret20":
        validation = validate_inputs()
        if not validation["xbi_feature_available"]:
            raise RuntimeError("rs_xbi_ret20 requires XBI full train coverage")
        dates = pd.to_datetime(_date_series(df))
        adpt_ret20 = _adpt_rebased_ret20(df)
        xbi_close = _load_close(XBI_TICKER)
        xbi_rebased = xbi_close / xbi_close.dropna().iloc[0] * 100.0
        xbi_ret20 = xbi_rebased.pct_change(20) * 100.0
        rs = (adpt_ret20 - xbi_ret20).rename(feature)
        return pd.Series(rs.reindex(dates).to_numpy(dtype=float), index=df.index, name=feature)
    raise ValueError(f"unsupported extra feature: {feature}")


def _apply_shuffle(series: pd.Series, *, seed: int) -> pd.Series:
    output = series.copy()
    values = output.to_numpy(dtype=float)
    valid = np.isfinite(values)
    shuffled = values.copy()
    rng = np.random.default_rng(int(seed))
    shuffled[valid] = rng.permutation(values[valid])
    return pd.Series(shuffled, index=series.index, name=series.name)


def _feature_series_d5_sha(feature: str, variant: str, shuffle_seed: int) -> dict[str, Any]:
    df = pd.read_pickle(CACHE_ROOT / f"{TICKER}.pkl").copy()
    raw = _candidate_raw_series(df, feature)
    if variant == "SHUFFLED":
        raw = _apply_shuffle(raw, seed=shuffle_seed)
    # evaluator/domain will shift by the same lag. This metadata records the aligned D-5 series.
    import engine.strategies.evaluator as evaluator_mod
    d5 = raw.shift(evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS)
    out = pd.DataFrame({feature: d5})
    out.index = pd.to_datetime(out.index).strftime("%Y-%m-%d")
    finite = d5[np.isfinite(d5.to_numpy(dtype=float))]
    train = d5.loc[(pd.to_datetime(d5.index) >= TRAIN_START) & (pd.to_datetime(d5.index) <= TRAIN_END)]
    return {
        "feature": feature,
        "variant": variant,
        "shuffle_seed": int(shuffle_seed),
        "d_minus_shift_trading_days": int(evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS),
        "sha256": _sha256_bytes(out.to_csv(float_format="%.12g").encode("utf-8")),
        "valid_count": int(np.isfinite(d5.to_numpy(dtype=float)).sum()),
        "train_valid_count": int(np.isfinite(train.to_numpy(dtype=float)).sum()),
        "first_valid_date": str(finite.index.min().date()) if len(finite) else None,
        "last_valid_date": str(finite.index.max().date()) if len(finite) else None,
    }


def force_v5_eec_params(v5: Any) -> None:
    os.environ["KINGMAKER_ENTRY_EEC_TARGET"] = "6"
    os.environ["KINGMAKER_ENTRY_EEC_FLOOR"] = "0.5"
    os.environ["KINGMAKER_ENTRY_EEC_CLUSTER_GAP_TRADING_DAYS"] = "8"
    if hasattr(v5, "eec_v5"):
        v5.eec_v5.ENTRY_FITNESS_EEC_TARGET = 6.0
        v5.eec_v5.ENTRY_FITNESS_EEC_FLOOR = 0.5
    if hasattr(v5, "execution_bt"):
        v5.execution_bt.ENTRY_FITNESS_EEC_TARGET = 6.0
        v5.execution_bt.ENTRY_FITNESS_EEC_FLOOR = 0.5


def patch_for_ticker(v5: Any, ticker: str = TICKER) -> None:
    ticker = ticker.upper().strip()
    support = v5.runner.support
    from engine.learning.learner import _detect_sector_name

    def load_cache_context(requested: str, market_history_df: pd.DataFrame):
        requested = ticker
        path = CACHE_ROOT / f"{requested}.pkl"
        if not path.is_file():
            raise FileNotFoundError(f"OHLCV cache missing: {path}")
        src = pd.read_pickle(path).copy()
        src.index = pd.to_datetime(src.index, errors="coerce")
        if src.index.isna().any():
            raise RuntimeError(f"invalid OHLCV cache index dates: {requested}")
        src = src.sort_index()
        required = ["Open", "High", "Low", "Close", "Volume"]
        missing = [c for c in required if c not in src.columns]
        if missing:
            raise RuntimeError(f"OHLCV cache missing columns for {requested}: {missing}")
        raw = src[required].copy()
        for col in required:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
            if not np.isfinite(raw[col].to_numpy(dtype=float)).all():
                raise RuntimeError(f"OHLCV NaN/Inf: {requested}:{col}")
        df = support.calc_indicators(raw)
        df, sell_omen_info = support.attach_sell_omen_scores(df, requested, score_table_path=SELL_OMEN)
        adapter = support.mod._pipeline_context.get_adapter(requested)
        meta = adapter.meta
        sector_name = _detect_sector_name(meta.name)
        base_rulebook = support.default_rulebook(requested, asset_type=meta.asset_type, direction=meta.direction)
        base_rulebook.sector_name = sector_name
        data_start = str(pd.Timestamp(df.index.min()).date())
        data_end = str(pd.Timestamp(df.index.max()).date())
        context = {
            "ticker": requested,
            "adapter": adapter,
            "meta": meta,
            "df": df,
            "rows": int(len(df)),
            "data_min": data_start,
            "data_max": data_end,
            "data_start": data_start,
            "data_end": data_end,
            "market_history_df": market_history_df.copy(),
            "ticker_sentiment": None,
            "sector_name": sector_name,
            "base_rulebook": base_rulebook,
            "sell_omen_info": sell_omen_info,
        }
        metadata = {
            "path": str(path.resolve()),
            "sha256": _sha256_file(path),
            "rows": int(len(df)),
            "first_date": data_start,
            "last_date": data_end,
            "external_fetch": False,
            "auto_regenerate": False,
            "source": "stage0_ohlcv_cache_pkl_runtime_loader",
            "sell_omen_score_table": str(SELL_OMEN.resolve()),
            "sell_omen_info": sell_omen_info,
        }
        return context, metadata

    modules = []
    for obj in [v5, getattr(v5, "runner", None), getattr(v5, "base", None), getattr(v5, "v3", None), getattr(v5, "v4", None)]:
        if obj is not None:
            modules.append(obj)
            r = getattr(obj, "runner", None)
            if r is not None:
                modules.append(r)
    seen = set()
    for mod in modules:
        if id(mod) in seen:
            continue
        seen.add(id(mod))
        if hasattr(mod, "TICKER"):
            setattr(mod, "TICKER", ticker)
    support._load_snapshot_context = load_cache_context


def install_qualify_only_stub(v5: Any, feature: str, variant: str) -> None:
    def _run_entry_qualify_only(out_dir: Path, ctx: dict[str, Any], seed_base: int, call_index: int):
        summary = {
            "ticker": TICKER,
            "stage": "entry",
            "skipped_by_adpt_feature_nulltest_v9": True,
            "skip_reason": "qualify_only_nulltest_metrics_all3_all2_foldbest",
            "feature": feature,
            "variant": variant,
            "selected_count": 0,
            "pool_count": 0,
            "seed_base": int(seed_base),
            "call_index": int(call_index),
        }
        (out_dir / "entry_rulebooks.jsonl").write_text("", encoding="utf-8")
        (out_dir / "entry_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return summary, []
    v5.runner._run_entry = _run_entry_qualify_only
    v5.runner.ADPT_FEATURE_NULLTEST_V9_QUALIFY_ONLY = True


def install_feature_patch(v5: Any, feature: str, variant: str, shuffle_seed: int) -> None:
    feature = str(feature).strip()
    variant = str(variant).strip().upper()
    if feature not in ALLOWED_FEATURES:
        raise ValueError(f"unsupported feature {feature!r}; allowed={sorted(ALLOWED_FEATURES)}")
    if variant not in VARIANTS:
        raise ValueError(f"unsupported variant {variant!r}; allowed={sorted(VARIANTS)}")

    runner = v5.runner
    from engine.strategies import evaluator as evaluator_mod
    from engine.strategies import rulebook as rulebook_mod

    orig_to_dict = rulebook_mod.Rulebook.to_dict
    orig_from_dict = rulebook_mod.Rulebook.from_dict
    orig_extract = evaluator_mod.extract_entry_features
    orig_loader = runner.support._load_snapshot_context
    orig_postprocess = v5._postprocess

    active: dict[str, Any] = {"feature": feature, "variant": variant, "shuffle_seed": int(shuffle_seed)}

    spec = {
        "low_field": LOW_FIELD,
        "high_field": HIGH_FIELD,
        "hard_min": None,
        "hard_max": None,
        "min_width_iqr_ratio": rulebook_mod.ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO,
        "near_full_ratio": rulebook_mod.ENTRY_INTERVAL_NEAR_FULL_RATIO,
    }
    rulebook_mod.ENTRY_INTERVAL_SPECS[feature] = spec
    setattr(rulebook_mod.Rulebook, LOW_FIELD, 0.0)
    setattr(rulebook_mod.Rulebook, HIGH_FIELD, 0.0)
    setattr(rulebook_mod.Rulebook, NAME_FIELD, feature)

    def to_dict_with_extra(self: Any) -> dict[str, Any]:
        payload = dict(orig_to_dict(self))
        payload[LOW_FIELD] = float(getattr(self, LOW_FIELD, 0.0))
        payload[HIGH_FIELD] = float(getattr(self, HIGH_FIELD, 0.0))
        payload[NAME_FIELD] = str(getattr(self, NAME_FIELD, feature) or feature)
        return payload

    def from_dict_with_extra(cls: Any, payload: Mapping[str, Any]) -> Any:
        data = dict(payload)
        saved_spec = rulebook_mod.ENTRY_INTERVAL_SPECS.pop(feature, None)
        try:
            rb = orig_from_dict(data)
        finally:
            if saved_spec is not None:
                rulebook_mod.ENTRY_INTERVAL_SPECS[feature] = saved_spec
        setattr(rb, LOW_FIELD, float(data.get(LOW_FIELD, 0.0) or 0.0))
        setattr(rb, HIGH_FIELD, float(data.get(HIGH_FIELD, 0.0) or 0.0))
        setattr(rb, NAME_FIELD, str(data.get(NAME_FIELD, feature) or feature))
        return rb

    rulebook_mod.Rulebook.to_dict = to_dict_with_extra
    rulebook_mod.Rulebook.from_dict = classmethod(from_dict_with_extra)

    lag = evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS

    def extract_with_extra(df: pd.DataFrame) -> dict[str, float]:
        features = dict(orig_extract(df))
        if df is None or len(df) <= lag or feature not in df.columns:
            features[feature] = float("nan")
            return features
        row = df.iloc[-1 - lag]
        features[feature] = float(_safe_float(row.get(feature), float("nan")))
        return features

    evaluator_mod.extract_entry_features = extract_with_extra

    def augment_df(df: pd.DataFrame) -> pd.DataFrame:
        frame = df.copy()
        raw = _candidate_raw_series(frame, feature)
        if variant == "SHUFFLED":
            raw = _apply_shuffle(raw, seed=int(shuffle_seed))
        frame[feature] = raw.to_numpy(dtype=float)
        active["last_feature_non_null_count"] = int(np.isfinite(frame[feature].to_numpy(dtype=float)).sum())
        return frame

    def load_snapshot_context_with_extra(ticker: str, market_frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
        ctx, metadata = orig_loader(ticker, market_frame)
        ctx = dict(ctx)
        ctx["df"] = augment_df(ctx["df"])
        metadata = dict(metadata)
        metadata["feature_nulltest_v9"] = {
            "patch_token": PATCH_TOKEN,
            "feature": feature,
            "variant": variant,
            "shuffle_seed": int(shuffle_seed),
            "non_null_count": int(active.get("last_feature_non_null_count", 0)),
            "strict_and_added_feature": True,
            "d_minus_5_shift_trading_days": int(lag),
            "recent_1y_excluded": True,
        }
        return ctx, metadata

    def build_domain_with_extra(ctx: Mapping[str, Any], *, start: str | None, end: str | None) -> dict[str, dict[str, Any]]:
        df = ctx.get("df")
        if not isinstance(df, pd.DataFrame) or df.empty:
            raise ValueError("ticker context df is missing or empty")
        feature_names = tuple(rulebook_mod.ENTRY_INTERVAL_SPECS)
        frame = pd.DataFrame(index=df.index)
        frame["date"] = _date_series(df)
        ma5 = _numeric(df, "MA5")
        ma20 = _numeric(df, "MA20")
        ma60 = _numeric(df, "MA60")
        close = _numeric(df, "Close")
        macd_hist = _numeric(df, "MACD_hist")
        bb_lower = _numeric(df, "BB_lower")
        bb_upper = _numeric(df, "BB_upper")
        frame["ma_trend"] = 0.5 * (((ma5 / ma20) - 1.0) + ((ma20 / ma60) - 1.0)) * 100.0
        frame["macd_hist"] = macd_hist / close * 100.0
        frame["rsi"] = _numeric(df, "RSI")
        frame["bb_position"] = (close - bb_lower) / (bb_upper - bb_lower)
        frame["volume_ratio"] = _numeric(df, "Volume_ratio")
        frame[feature] = _numeric(df, feature)
        frame.loc[:, list(feature_names)] = frame.loc[:, list(feature_names)].shift(lag)
        if start is not None:
            frame = frame.loc[frame["date"] >= pd.Timestamp(start)]
        if end is not None:
            frame = frame.loc[frame["date"] <= pd.Timestamp(end)]
        finite_mask = np.ones(len(frame), dtype=bool)
        for name in feature_names:
            finite_mask &= np.isfinite(frame[name].to_numpy(dtype=float))
        frame = frame.loc[finite_mask, ["date", *feature_names]].copy()
        if len(frame) < rulebook_mod.ENTRY_INTERVAL_MIN_FEATURE_SUPPORT:
            raise ValueError(f"entry feature fold has only {len(frame)} finite aligned rows")
        domain: dict[str, dict[str, Any]] = {}
        for name in feature_names:
            values = frame[name].to_numpy(dtype=float)
            domain[name] = {
                "train_min": float(np.min(values)),
                "train_max": float(np.max(values)),
                "q01": float(np.quantile(values, 0.01)),
                "q99": float(np.quantile(values, 0.99)),
                "iqr": float(np.quantile(values, 0.75) - np.quantile(values, 0.25)),
                "sample_count": int(len(values)),
                "values": values.tolist(),
            }
        return domain

    runner.support._load_snapshot_context = load_snapshot_context_with_extra
    runner.mod.build_entry_feature_domain = build_domain_with_extra

    def postprocess_with_feature(out_dir: Path, baseline_dir: Path, args: argparse.Namespace, original_argv: list[str]) -> None:
        orig_postprocess(out_dir, baseline_dir, args, original_argv)
        validation = validate_inputs()
        meta = {
            "patch_token": PATCH_TOKEN,
            "ticker": TICKER,
            "feature": feature,
            "variant": variant,
            "shuffle_seed": int(shuffle_seed),
            "strict_and_feature_count": len(rulebook_mod.ENTRY_INTERVAL_SPECS),
            "added_low_field": LOW_FIELD,
            "added_high_field": HIGH_FIELD,
            "candidate_series_d5": _feature_series_d5_sha(feature, variant, int(shuffle_seed)),
            "input_validation": validation,
            "peer_basket_definition": "rs_peer_ret20 = ADPT 100-rebased 20d return minus equal-weight basket 100-rebased 20d return, accepted peers only; then D-5 shift in evaluator/domain",
            "xbi_definition": "rs_xbi_ret20 = ADPT 100-rebased 20d return minus XBI 100-rebased 20d return; then D-5 shift in evaluator/domain",
            "entry_exit_impact_note": "The added feature participates in strict-AND, so entry_interval_break can occur when this feature exits its learned interval. This is recorded, not blocked.",
            "recent_1y_excluded": True,
        }
        (out_dir / "feature_nulltest_metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        for name in ("launch_command.json", "manifest.json", "official_final_summary.json"):
            path = out_dir / name
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["feature_nulltest_v9"] = meta
                payload["actual_launcher_note"] = "run_adpt_feature_nulltest_v9.py delegates to v5 runner after installing ADPT ticker and one strict-AND feature patch"
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        runner._write_sha_manifest(out_dir)

    v5._postprocess = postprocess_with_feature
    os.environ["KINGMAKER_ENTRY_EXTRA_FEATURE"] = feature
    os.environ["KINGMAKER_FEATURE_NULLTEST_VARIANT"] = variant
    os.environ["KINGMAKER_FEATURE_NULLTEST_SHUFFLE_SEED"] = str(int(shuffle_seed))


def verify(feature: str, variant: str, shuffle_seed: int) -> dict[str, Any]:
    v5 = _load_v5()
    force_v5_eec_params(v5)
    patch_for_ticker(v5, TICKER)
    if hasattr(v5.base, "_patch_market_cutoff"):
        v5.base._patch_market_cutoff(date.fromisoformat("2026-07-10"))
    install_feature_patch(v5, feature, variant, int(shuffle_seed))
    import ast
    source = (STAGE / "engine/learning/genetic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"mutate", "crossover", "random_rulebook"}:
            selected.append(ast.dump(node, include_attributes=False))
    mutation_sha = hashlib.sha256("\n".join(selected).encode("utf-8")).hexdigest()
    market_frame, market_metadata = v5.runner.support._preflight_market_snapshot()
    ctx, ohlcv = v5.runner.support._load_snapshot_context(TICKER, market_frame)
    series_meta = _feature_series_d5_sha(feature, variant, int(shuffle_seed))
    validation = validate_inputs()
    from engine.strategies import rulebook as rulebook_mod
    rb = rulebook_mod.Rulebook(ticker="VERIFY", asset_type="us_stock", direction="long")
    base_payload = {k: v for k, v in rb.to_dict().items() if k not in {LOW_FIELD, HIGH_FIELD, NAME_FIELD}}
    restored = rulebook_mod.Rulebook.from_dict(rb.to_dict())
    return {
        "passed": bool(
            ohlcv.get("sha256") == validation["adpt"].get("sha256")
            and series_meta["train_valid_count"] > 700
            and mutation_sha == "aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d"
            and getattr(restored, NAME_FIELD, feature) == feature
        ),
        "feature": feature,
        "variant": variant,
        "shuffle_seed": int(shuffle_seed),
        "ticker": TICKER,
        "market_auto_fetch": market_metadata.get("auto_fetch_enabled"),
        "market_auto_regenerate": market_metadata.get("auto_regenerate_enabled"),
        "ohlcv": ohlcv,
        "rows": int(len(ctx["df"])),
        "fold_rows": {k: int(len(ctx["df"].loc[(ctx["df"].index >= pd.Timestamp(a)) & (ctx["df"].index <= pd.Timestamp(b))])) for k, (a, b) in FOLDS.items()},
        "input_validation": validation,
        "candidate_series_d5": series_meta,
        "strict_feature_count": len(rulebook_mod.ENTRY_INTERVAL_SPECS),
        "mutation_helper_ast_sha": mutation_sha,
        "legacy_roundtrip_dynamic_name": getattr(restored, NAME_FIELD, None),
        "base_payload_key_count_without_dynamic": len(base_payload),
        "entry_interval_break_impact": "recorded: added feature participates in strict-AND and can trigger interval-break exits",
        "recent_1y_excluded": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature", required=True, choices=sorted(ALLOWED_FEATURES))
    parser.add_argument("--variant", required=True, choices=sorted(VARIANTS))
    parser.add_argument("--shuffle-seed", type=int, default=2026071501)
    parser.add_argument("--out-dir")
    parser.add_argument("--baseline-dir")
    parser.add_argument("--seed-base", type=int, default=2026071401)
    parser.add_argument("--workers", type=int, default=28)
    parser.add_argument("--host-role", default="notebook")
    parser.add_argument("--market-cutoff-date", default="2026-07-10")
    parser.add_argument("--protected-snapshot-json", default="{}")
    parser.add_argument("--daemon-snapshot-json", default="{}")
    parser.add_argument("--source-git-commit", default="unknown")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_only:
        result = verify(args.feature, args.variant, args.shuffle_seed)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
        return 0 if result.get("passed") else 1
    if not args.out_dir or not args.baseline_dir:
        raise SystemExit("--out-dir and --baseline-dir are required for run")
    v5 = _load_v5()
    force_v5_eec_params(v5)
    patch_for_ticker(v5, TICKER)
    if hasattr(v5.base, "_patch_market_cutoff"):
        v5.base._patch_market_cutoff(date.fromisoformat(args.market_cutoff_date))
    install_feature_patch(v5, args.feature, args.variant, int(args.shuffle_seed))
    install_qualify_only_stub(v5, args.feature, args.variant)
    run_argv = [
        "--baseline-dir", args.baseline_dir,
        "--out-dir", args.out_dir,
        "--seed-base", str(args.seed_base),
        "--workers", str(args.workers),
        "--host-role", args.host_role,
        "--market-cutoff-date", args.market_cutoff_date,
        "--protected-snapshot-json", args.protected_snapshot_json,
        "--daemon-snapshot-json", args.daemon_snapshot_json,
        "--source-git-commit", args.source_git_commit,
    ]
    try:
        return int(v5.main(run_argv))
    except Exception:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "driver_failure.json").write_text(
            json.dumps({"ticker": TICKER, "feature": args.feature, "variant": args.variant, "traceback": traceback.format_exc()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
