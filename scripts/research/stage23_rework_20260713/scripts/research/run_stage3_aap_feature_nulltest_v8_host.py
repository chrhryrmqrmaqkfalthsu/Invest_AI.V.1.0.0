#!/usr/bin/env python3
"""AAP strict-entry one-feature null-test v8 runner.

This runner does not change entry/exit execution, should_buy semantics, legacy
paths, fixed sizing, EEC penalty, trade-count factor, or win-rate gates.  It
adds exactly one requested candidate feature to the existing strict-AND entry
interval list for the current process, then delegates to the v5/v6 EEC runner.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
V5_PATH = HERE.with_name("run_stage3_aap_eec_penalty_v5_host.py")
REPO_ROOT = HERE.parents[4]
WORK_ROOT = HERE.parents[2]
PEER_CACHE = REPO_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
ALLOWED_FEATURES = {"trend_chop20", "atr14_pct", "range_pct_rank60", "rs_peer3_ret20"}
VARIANTS = {"REAL", "SHUFFLED"}
LOW_FIELD = "entry_extra_feature_low"
HIGH_FIELD = "entry_extra_feature_high"
NAME_FIELD = "entry_extra_feature_name"
PATCH_TOKEN = "entry_strict_extra_feature_v8_20260715"


def _load_v5() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_feature_nulltest_v8_base", V5_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v5 host runner: {V5_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v5 = _load_v5()
runner = v5.runner

from engine.strategies import evaluator as evaluator_mod  # noqa: E402
from engine.strategies import rulebook as rulebook_mod  # noqa: E402
from engine.learning import genetic as genetic_mod  # noqa: E402

_ORIG_TO_DICT = rulebook_mod.Rulebook.to_dict
_ORIG_FROM_DICT = rulebook_mod.Rulebook.from_dict
_ORIG_EXTRACT_ENTRY_FEATURES = evaluator_mod.extract_entry_features
_ORIG_LOAD_SNAPSHOT_CONTEXT = runner.support._load_snapshot_context
_ORIG_BUILD_ENTRY_FEATURE_DOMAIN = runner.mod.build_entry_feature_domain
_ORIG_V5_POSTPROCESS = v5._postprocess

_ACTIVE: dict[str, Any] = {}


def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        number = float(value)
    except Exception:
        return float(default)
    return number if math.isfinite(number) else float(default)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date_series(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        return pd.Series(pd.to_datetime(df["date"], errors="coerce").to_numpy(), index=df.index)
    if "Date" in df.columns:
        return pd.Series(pd.to_datetime(df["Date"], errors="coerce").to_numpy(), index=df.index)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(df.index, errors="coerce"), index=df.index)
    raise ValueError("extra-feature domain requires a date column or DatetimeIndex")


def _numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def _load_peer_close(symbol: str) -> pd.Series:
    path = PEER_CACHE / f"{symbol}.pkl"
    if not path.is_file():
        raise FileNotFoundError(f"peer OHLCV cache missing: {path}")
    frame = pd.read_pickle(path)
    if "Close" not in frame.columns:
        raise RuntimeError(f"peer OHLCV Close column missing: {path}")
    idx = pd.to_datetime(frame.index, errors="coerce")
    if idx.isna().any():
        raise RuntimeError(f"peer OHLCV invalid dates: {path}")
    close = pd.Series(pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float), index=idx, name=symbol)
    close = close.sort_index()
    if not np.isfinite(close.dropna().to_numpy(dtype=float)).all():
        raise RuntimeError(f"peer OHLCV Close has NaN/Inf after dropna check: {path}")
    return close


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
                lambda arr: float(pd.Series(arr).rank(pct=True).iloc[-1]),
                raw=False,
            ).rename(feature)
    if feature == "rs_peer3_ret20":
        dates = _date_series(df)
        aap_close = pd.Series(close.to_numpy(dtype=float), index=pd.to_datetime(dates), name="AAP").sort_index()
        aap_rebased = aap_close / aap_close.dropna().iloc[0] * 100.0
        aap_ret20 = aap_rebased.pct_change(20) * 100.0
        peers = []
        for symbol in ("GPC", "ORLY", "AZO"):
            peer_close = _load_peer_close(symbol)
            peer_rebased = peer_close / peer_close.dropna().iloc[0] * 100.0
            peers.append((peer_rebased.pct_change(20) * 100.0).rename(symbol))
        peer_median = pd.concat(peers, axis=1).median(axis=1, skipna=False)
        rs = (aap_ret20 - peer_median).rename(feature)
        return pd.Series(rs.reindex(pd.to_datetime(dates)).to_numpy(dtype=float), index=df.index, name=feature)
    raise ValueError(f"unsupported extra feature: {feature}")


def _apply_shuffle(series: pd.Series, *, seed: int) -> pd.Series:
    output = series.copy()
    valid = np.isfinite(output.to_numpy(dtype=float))
    values = output.to_numpy(dtype=float)
    shuffled = values.copy()
    rng = np.random.default_rng(int(seed))
    shuffled[valid] = rng.permutation(values[valid])
    return pd.Series(shuffled, index=series.index, name=series.name)


def _augment_df(df: pd.DataFrame) -> pd.DataFrame:
    feature = str(_ACTIVE["feature"])
    variant = str(_ACTIVE["variant"])
    seed = int(_ACTIVE["shuffle_seed"])
    frame = df.copy()
    raw = _candidate_raw_series(frame, feature)
    if variant == "SHUFFLED":
        raw = _apply_shuffle(raw, seed=seed)
    frame[feature] = raw.to_numpy(dtype=float)
    _ACTIVE["last_feature_non_null_count"] = int(np.isfinite(frame[feature].to_numpy(dtype=float)).sum())
    return frame


def _patch_rulebook(feature: str) -> None:
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
        payload = dict(_ORIG_TO_DICT(self))
        payload[LOW_FIELD] = float(getattr(self, LOW_FIELD, 0.0))
        payload[HIGH_FIELD] = float(getattr(self, HIGH_FIELD, 0.0))
        payload[NAME_FIELD] = str(getattr(self, NAME_FIELD, feature) or feature)
        return payload

    def from_dict_with_extra(cls: Any, payload: Mapping[str, Any]) -> Any:
        data = dict(payload)
        saved_spec = rulebook_mod.ENTRY_INTERVAL_SPECS.pop(feature, None)
        try:
            rb = _ORIG_FROM_DICT(data)
        finally:
            if saved_spec is not None:
                rulebook_mod.ENTRY_INTERVAL_SPECS[feature] = saved_spec
        setattr(rb, LOW_FIELD, float(data.get(LOW_FIELD, 0.0) or 0.0))
        setattr(rb, HIGH_FIELD, float(data.get(HIGH_FIELD, 0.0) or 0.0))
        setattr(rb, NAME_FIELD, str(data.get(NAME_FIELD, feature) or feature))
        try:
            schema_version = int(getattr(rb, "entry_interval_schema_version", 1))
        except Exception:
            schema_version = 1
        if schema_version >= rulebook_mod.STRICT_ENTRY_INTERVAL_SCHEMA_VERSION:
            errors = rulebook_mod.validate_entry_intervals(rb) + rulebook_mod.validate_entry_feature_domains(rb)
            if errors:
                raise ValueError("invalid strict entry interval payload: " + "; ".join(errors))
        return rb

    rulebook_mod.Rulebook.to_dict = to_dict_with_extra
    rulebook_mod.Rulebook.from_dict = classmethod(from_dict_with_extra)


def _patch_evaluator(feature: str) -> None:
    lag = evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS

    def extract_with_extra(df: pd.DataFrame) -> dict[str, float]:
        features = dict(_ORIG_EXTRACT_ENTRY_FEATURES(df))
        if df is None or len(df) <= lag or feature not in df.columns:
            features[feature] = float("nan")
            return features
        row = df.iloc[-1 - lag]
        value = _safe_float(row.get(feature), float("nan"))
        features[feature] = float(value)
        return features

    evaluator_mod.extract_entry_features = extract_with_extra


def _build_domain_with_extra(ctx: Mapping[str, Any], *, start: str | None, end: str | None) -> dict[str, dict[str, Any]]:
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
    feature = str(_ACTIVE["feature"])
    frame[feature] = _numeric(df, feature)
    frame.loc[:, list(feature_names)] = frame.loc[:, list(feature_names)].shift(
        evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS
    )
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


def _patch_context_loader(feature: str) -> None:
    def load_snapshot_context_with_extra(ticker: str, market_frame: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
        ctx, metadata = _ORIG_LOAD_SNAPSHOT_CONTEXT(ticker, market_frame)
        ctx = dict(ctx)
        ctx["df"] = _augment_df(ctx["df"])
        metadata = dict(metadata)
        metadata["feature_nulltest_v8"] = {
            "patch_token": PATCH_TOKEN,
            "feature": feature,
            "variant": str(_ACTIVE["variant"]),
            "shuffle_seed": int(_ACTIVE["shuffle_seed"]),
            "non_null_count": int(_ACTIVE.get("last_feature_non_null_count", 0)),
            "strict_and_added_feature": True,
            "d_minus_5_shift_trading_days": evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS,
        }
        return ctx, metadata

    runner.support._load_snapshot_context = load_snapshot_context_with_extra
    runner.mod.build_entry_feature_domain = _build_domain_with_extra


def _feature_series_d5_sha(feature: str, variant: str, shuffle_seed: int) -> dict[str, Any]:
    aap_path = PEER_CACHE / "AAP.pkl"
    df = pd.read_pickle(aap_path).copy()
    raw = _candidate_raw_series(df, feature)
    if variant == "SHUFFLED":
        raw = _apply_shuffle(raw, seed=shuffle_seed)
    d5 = raw.shift(evaluator_mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS)
    out = pd.DataFrame({feature: d5})
    out.index = pd.to_datetime(out.index).strftime("%Y-%m-%d")
    return {
        "feature": feature,
        "variant": variant,
        "shuffle_seed": int(shuffle_seed),
        "sha256": _sha256_bytes(out.to_csv(float_format="%.12g").encode("utf-8")),
        "valid_count": int(np.isfinite(d5.to_numpy(dtype=float)).sum()),
        "first_valid_date": str(d5.dropna().index.min().date()) if len(d5.dropna()) else None,
        "last_valid_date": str(d5.dropna().index.max().date()) if len(d5.dropna()) else None,
    }


def _patch_postprocess(feature: str, variant: str, shuffle_seed: int) -> None:
    def postprocess_with_feature(out_dir: Path, baseline_dir: Path, args: argparse.Namespace, original_argv: list[str]) -> None:
        _ORIG_V5_POSTPROCESS(out_dir, baseline_dir, args, original_argv)
        meta = {
            "patch_token": PATCH_TOKEN,
            "feature": feature,
            "variant": variant,
            "shuffle_seed": int(shuffle_seed),
            "strict_and_feature_count": len(rulebook_mod.ENTRY_INTERVAL_SPECS),
            "added_low_field": LOW_FIELD,
            "added_high_field": HIGH_FIELD,
            "candidate_series_d5": _feature_series_d5_sha(feature, variant, shuffle_seed),
            "peer_cache": str(PEER_CACHE),
            "peer_inputs": {
                symbol: {
                    "path": str(PEER_CACHE / f"{symbol}.pkl"),
                    "sha256": _sha256_file(PEER_CACHE / f"{symbol}.pkl") if (PEER_CACHE / f"{symbol}.pkl").is_file() else None,
                }
                for symbol in ("AAP", "GPC", "ORLY", "AZO")
            },
            "entry_exit_impact_note": "The added feature is part of strict-AND, so entry_interval_break can occur when this feature exits its learned interval. This is recorded, not blocked.",
        }
        (out_dir / "feature_nulltest_metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        for name in ("launch_command.json", "manifest.json", "official_final_summary.json"):
            path = out_dir / name
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["feature_nulltest_v8"] = meta
                payload["actual_launcher_note"] = "run_stage3_aap_feature_nulltest_v8_host.py delegates to v5 runner after installing one strict-AND feature patch"
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        runner._write_sha_manifest(out_dir)

    v5._postprocess = postprocess_with_feature


def install(feature: str, variant: str, shuffle_seed: int) -> None:
    feature = str(feature).strip()
    variant = str(variant).strip().upper()
    if feature not in ALLOWED_FEATURES:
        raise ValueError(f"unsupported feature {feature!r}; allowed={sorted(ALLOWED_FEATURES)}")
    if variant not in VARIANTS:
        raise ValueError(f"unsupported variant {variant!r}; allowed={sorted(VARIANTS)}")
    _ACTIVE.update({"feature": feature, "variant": variant, "shuffle_seed": int(shuffle_seed)})
    os.environ["KINGMAKER_ENTRY_EXTRA_FEATURE"] = feature
    os.environ["KINGMAKER_FEATURE_NULLTEST_VARIANT"] = variant
    os.environ["KINGMAKER_FEATURE_NULLTEST_SHUFFLE_SEED"] = str(int(shuffle_seed))
    _patch_rulebook(feature)
    _patch_evaluator(feature)
    _patch_context_loader(feature)
    _patch_postprocess(feature, variant, int(shuffle_seed))


def verify(feature: str, variant: str, shuffle_seed: int) -> dict[str, Any]:
    install(feature, variant, shuffle_seed)
    import ast
    source = (WORK_ROOT / "engine/learning/genetic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"mutate", "crossover", "random_rulebook"}:
            selected.append(ast.dump(node, include_attributes=False))
    mutation_sha = hashlib.sha256("\n".join(selected).encode("utf-8")).hexdigest()
    rb = rulebook_mod.Rulebook(ticker="VERIFY", asset_type="us_stock", direction="long")
    rb.entry_interval_schema_version = 1
    before = json.dumps(_ORIG_TO_DICT(rb), sort_keys=True, default=str)
    after = json.dumps({k: v for k, v in rb.to_dict().items() if k not in {LOW_FIELD, HIGH_FIELD, NAME_FIELD}}, sort_keys=True, default=str)
    spec_present = feature in rulebook_mod.ENTRY_INTERVAL_SPECS
    rt = rulebook_mod.Rulebook(ticker="VERIFY", asset_type="us_stock", direction="long")
    setattr(rt, LOW_FIELD, -1.0)
    setattr(rt, HIGH_FIELD, 1.0)
    payload = rt.to_dict()
    restored = rulebook_mod.Rulebook.from_dict(payload)
    peer = {}
    for symbol in ("AAP", "GPC", "ORLY", "AZO"):
        path = PEER_CACHE / f"{symbol}.pkl"
        peer[symbol] = {"exists": path.is_file(), "sha256": _sha256_file(path) if path.is_file() else None}
    series_meta = _feature_series_d5_sha(feature, variant, shuffle_seed)
    return {
        "passed": bool(spec_present and before == after and getattr(restored, LOW_FIELD) == -1.0 and getattr(restored, HIGH_FIELD) == 1.0 and all(row["exists"] for row in peer.values())),
        "feature": feature,
        "variant": variant,
        "shuffle_seed": int(shuffle_seed),
        "strict_feature_count": len(rulebook_mod.ENTRY_INTERVAL_SPECS),
        "spec_present": spec_present,
        "legacy_bitwise_without_dynamic_fields": before == after,
        "roundtrip_extra_low_high": [getattr(restored, LOW_FIELD), getattr(restored, HIGH_FIELD)],
        "mutation_helper_ast_sha": mutation_sha,
        "candidate_series_d5": series_meta,
        "peer_inputs": peer,
        "entry_interval_break_impact": "recorded: added feature participates in strict-AND and can trigger interval-break exits",
    }


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--extra-feature", required=True, choices=sorted(ALLOWED_FEATURES))
    parser.add_argument("--feature-variant", required=True, choices=sorted(VARIANTS))
    parser.add_argument("--shuffle-seed", type=int, default=2026071501)
    parser.add_argument("--verify-only", action="store_true")
    known, rest = parser.parse_known_args(raw)
    return known, rest


def main(argv: list[str] | None = None) -> int:
    known, rest = parse_args(argv)
    if known.verify_only:
        result = verify(known.extra_feature, known.feature_variant, known.shuffle_seed)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("passed") else 1
    install(known.extra_feature, known.feature_variant, known.shuffle_seed)
    return int(v5.main(rest))


if __name__ == "__main__":
    v5.base.mp.freeze_support()
    raise SystemExit(main())
