#!/usr/bin/env python3
"""
Dense feature-weight pattern detector with separable HIGH/LOW objectives.

Base: 3b24382 dense-feature pattern detector.
추가 기능:
- --head-objective both: 기존 HIGH/LOW 동시 signal detector
- --head-objective high: HIGH 전용 detector로 signal/fitness/gate 계산
- --head-objective low: LOW 전용 detector로 signal/fitness/gate 계산
- feature LOOKBACK을 5일에서 10일로 확장해 STK_lag1~10, STAGE2_lag1~10을 모두 사용.

목적:
HIGH detector와 LOW detector를 따로 학습한 뒤, OOS에서 signal 날짜 overlap을 확인한다.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_COMMIT = "3b24382"
SELF_PATH = "scripts/research/run_range_predictor_stage2_v3.py"
TARGET_MODE_BASE = "next_day_hilo_true_coarse3_pattern_detector_dense_feature_weights_head_objective_stage2"
HEAD_OBJECTIVE = "both"
FEATURE_LOOKBACK_DAYS = 10


def _load_base_module() -> types.ModuleType:
    code = subprocess.check_output(["git", "show", f"{BASE_COMMIT}:{SELF_PATH}"], cwd=str(PROJECT_ROOT), text=True)
    mod = types.ModuleType("_km_dense_weights_3b24382")
    mod.__file__ = str(PROJECT_ROOT / SELF_PATH)
    mod.__name__ = "_km_dense_weights_3b24382"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


P = _load_base_module()
TARGET_MODE = TARGET_MODE_BASE
P.TARGET_MODE = TARGET_MODE


def _apply_feature_lookback_days() -> None:
    """Force the loaded dataset builder to generate lag1~FEATURE_LOOKBACK_DAYS features."""
    for target in (getattr(P, "L", None), P):
        if target is None:
            continue
        if hasattr(target, "LOOKBACK"):
            setattr(target, "LOOKBACK", int(FEATURE_LOOKBACK_DAYS))
        setattr(target, "FEATURE_LOOKBACK_DAYS", int(FEATURE_LOOKBACK_DAYS))


_apply_feature_lookback_days()

_BASE_predict_signal = P.predict_signal
_BASE_predict = P.predict
_BASE_evaluate_predictor = P.evaluate_predictor
_BASE_predictor_fitness = P.predictor_fitness
_BASE_dual_fail_reasons = P.dual_fail_reasons
_BASE_dual_head_params = P.dual_head_params
_BASE_individual_to_dict = P.individual_to_dict
_BASE_predictor_signature = P.predictor_signature
_BASE_parse_args = P.parse_args
_BASE_install_dual_head_target = P.install_dual_head_target
_BASE_run_original_stage2_predictor = P.run_original_stage2_predictor


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _period_count(df: Any) -> int:
    try:
        return int(len(df))
    except Exception:
        return 0


def _objective_mode() -> str:
    return HEAD_OBJECTIVE if HEAD_OBJECTIVE in {"both", "high", "low"} else "both"


def _objective_target_mode() -> str:
    return f"{TARGET_MODE_BASE}_{_objective_mode()}"


def _coverage_penalty(metrics: Mapping[str, Any]) -> float:
    fn = getattr(P, "coverage_penalty", None)
    if callable(fn):
        return float(fn(metrics))
    sc = _sf(metrics.get("signal_count"))
    cov = _sf(metrics.get("signal_coverage_pct"))
    min_count = _sf(getattr(P, "MIN_SIGNAL_COUNT", 0))
    min_cov = _sf(getattr(P, "MIN_SIGNAL_COVERAGE_PCT", 0))
    max_cov = _sf(getattr(P, "MAX_SIGNAL_COVERAGE_PCT", 100))
    target_cov = _sf(getattr(P, "TARGET_SIGNAL_COVERAGE_PCT", 10))
    penalty = max(0.0, min_count - sc) * 4.0
    penalty += max(0.0, min_cov - cov) * 3.0
    penalty += max(0.0, cov - max_cov) * 1.5
    penalty += abs(cov - target_cov) * 0.05
    return float(penalty)


def _empty_int() -> np.ndarray:
    return np.asarray([], dtype=int)


def _coarse(values: Any) -> np.ndarray:
    fn = getattr(P, "_coarse", None)
    if callable(fn):
        return fn(values)
    return np.clip(np.asarray(values, dtype=int) // 2, 0, 2)


def _cb(value: Any) -> int:
    fn = getattr(P, "_cb", None)
    if callable(fn):
        return int(fn(value))
    return int(max(0, min(2, _si(value))))


def predict_signal(ind: Any, X: Any, qspec: dict[str, Any]):
    ph, pl, both_signal, high_signal, low_signal, diag = _BASE_predict_signal(ind, X, qspec)
    mode = _objective_mode()
    ph2 = np.asarray(ph, dtype=int).copy()
    pl2 = np.asarray(pl, dtype=int).copy()

    if mode in {"high", "low"}:
        # Base dense wrapper only fully applies dense prediction on both-head signal days.
        # For single-head training, recompute the corresponding dense head and apply it on that head's signal days.
        try:
            if hasattr(P, "_ensure_dense_attrs"):
                P._ensure_dense_attrs(ind, qspec)
            features, mat = P._feature_quantile_matrix(X, qspec)
            if mode == "high":
                h_pred, _h_score, _h_conf = P._dense_head(
                    features,
                    mat,
                    getattr(ind, "high_feature_weights", {}),
                    getattr(ind, "high_dense_bias", 0.0),
                    getattr(ind, "dense_high_cut1", 0.35),
                    getattr(ind, "dense_high_cut2", 0.66),
                )
                ph2[np.asarray(high_signal, dtype=bool)] = h_pred[np.asarray(high_signal, dtype=bool)]
            else:
                l_pred, _l_score, _l_conf = P._dense_head(
                    features,
                    mat,
                    getattr(ind, "low_feature_weights", {}),
                    getattr(ind, "low_dense_bias", 0.0),
                    getattr(ind, "dense_low_cut1", 0.35),
                    getattr(ind, "dense_low_cut2", 0.66),
                )
                pl2[np.asarray(low_signal, dtype=bool)] = l_pred[np.asarray(low_signal, dtype=bool)]
        except Exception:
            pass

    if mode == "high":
        signal = np.asarray(high_signal, dtype=bool)
    elif mode == "low":
        signal = np.asarray(low_signal, dtype=bool)
    else:
        signal = np.asarray(both_signal, dtype=bool)

    period_count = int(len(signal))
    signal_count = int(np.sum(signal))
    diag = dict(diag)
    diag.update(
        {
            "head_objective": mode,
            "period_sample_count": period_count,
            "sample_count": signal_count,
            "signal_count": signal_count,
            "no_signal_count": int(period_count - signal_count),
            "signal_coverage_pct": float(signal_count / max(1, period_count) * 100.0),
            "objective_high_signal_count": int(np.sum(high_signal)),
            "objective_low_signal_count": int(np.sum(low_signal)),
            "objective_both_signal_count": int(np.sum(both_signal)),
        }
    )
    return ph2, pl2, signal, np.asarray(high_signal, dtype=bool), np.asarray(low_signal, dtype=bool), diag


def predict(ind: Any, X: Any, qspec: dict[str, Any]):
    ph, pl, _signal, _hs, _ls, diag = predict_signal(ind, X, qspec)
    return ph, pl, diag


def _prediction_penalty(ind: Any, yh3: np.ndarray, yl3: np.ndarray, ph3: np.ndarray, pl3: np.ndarray) -> dict[str, Any]:
    fn = getattr(P, "prediction_penalty", None)
    if callable(fn):
        return fn(ind, yh3, yl3, ph3, pl3)
    return {"total_penalty": 0.0, "max_pred_share_high_pct": 0.0, "max_pred_share_low_pct": 0.0}


def evaluate_predictor(ind: Any, df: Any, features: list[str], qspec: dict[str, Any]) -> dict[str, Any]:
    yh3_all = _coarse(df["high_bin"].to_numpy(dtype=int))
    yl3_all = _coarse(df["low_bin"].to_numpy(dtype=int))
    ph3_all, pl3_all, signal, high_signal, low_signal, diag = predict_signal(ind, df[features], qspec)
    signal = np.asarray(signal, dtype=bool)
    if int(np.sum(signal)) > 0:
        yh3 = yh3_all[signal]
        yl3 = yl3_all[signal]
        ph3 = ph3_all[signal]
        pl3 = pl3_all[signal]
        bph = np.full(len(yh3), _cb(ind.baseline_spec.get("exact_high_coarse_bin", getattr(ind, "default_high_bin", 1))), dtype=int)
        bpl = np.full(len(yl3), _cb(ind.baseline_spec.get("exact_low_coarse_bin", getattr(ind, "default_low_bin", 1))), dtype=int)
    else:
        yh3 = yl3 = ph3 = pl3 = bph = bpl = _empty_int()

    metrics = P.true3_bin_metrics(yh3, yl3, ph3, pl3, bph, bpl)
    metrics.update(
        {
            "target_mode": _objective_target_mode(),
            "head_objective": _objective_mode(),
            "period_sample_count": _period_count(df),
            "sample_count": int(np.sum(signal)),
            "signal_count": int(np.sum(signal)),
            "high_rule_count": len(getattr(ind, "high_rules", [])),
            "low_rule_count": len(getattr(ind, "low_rules", [])),
            "coarse_bin_count": 3,
            **_prediction_penalty(ind, yh3, yl3, ph3, pl3),
            **diag,
        }
    )
    metrics["coverage_penalty"] = _coverage_penalty(metrics)
    metrics["dense_weight_l2_penalty"] = _sf(metrics.get("dense_weight_l2"), 0.0) * _sf(getattr(P, "DENSE_WEIGHT_L2_PENALTY", 0.0))
    metrics["fitness"] = predictor_fitness(metrics)
    return metrics


def _head_component(metrics: Mapping[str, Any], head: str) -> float:
    if head == "high":
        return (
            _sf(metrics.get("high_coarse_lift_pp")) * 1.20
            + _sf(metrics.get("high_coarse_acc_pct")) * 0.18
            + _sf(metrics.get("high_no_danger_lift_pp")) * 0.35
            + _sf(metrics.get("high_asymmetric_bin_error_lift")) * 1.10
            + _sf(metrics.get("high_coarse_error_lift")) * 0.45
        )
    return (
        _sf(metrics.get("low_coarse_lift_pp")) * 1.25
        + _sf(metrics.get("low_coarse_acc_pct")) * 0.18
        + _sf(metrics.get("low_no_danger_lift_pp")) * 0.75
        + _sf(metrics.get("low_no_danger_acc_pct")) * 0.08
        + _sf(metrics.get("low_asymmetric_bin_error_lift")) * 1.25
        + _sf(metrics.get("low_coarse_error_lift")) * 0.45
    )


def predictor_fitness(metrics: Mapping[str, Any]) -> float:
    mode = _objective_mode()
    if mode == "both":
        return float(_BASE_predictor_fitness(metrics))
    dense_penalty = _sf(metrics.get("dense_weight_l2_penalty"), 0.0)
    coverage = _coverage_penalty(metrics)
    total_penalty = _sf(metrics.get("total_penalty"), 0.0)
    return float(_head_component(metrics, mode) - coverage - total_penalty - dense_penalty)


def _fail(metric: str, value: float, threshold: float, rule: str) -> dict[str, Any] | None:
    failed = (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold)
    if failed:
        return {"metric": metric, "value": value, "threshold": threshold, "rule": rule}
    return None


def dual_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    mode = _objective_mode()
    if mode == "both":
        return list(_BASE_dual_fail_reasons(metrics, kind))
    checks: list[tuple[str, float, float, str]] = [
        ("signal_count", _sf(metrics.get("signal_count")), _sf(getattr(P, "MIN_SIGNAL_COUNT", 0)), ">="),
        ("signal_coverage_pct", _sf(metrics.get("signal_coverage_pct")), _sf(getattr(P, "MIN_SIGNAL_COVERAGE_PCT", 0)), ">="),
        ("signal_coverage_pct", _sf(metrics.get("signal_coverage_pct")), _sf(getattr(P, "MAX_SIGNAL_COVERAGE_PCT", 100)), "<="),
        ("total_penalty", _sf(metrics.get("total_penalty")), _sf(getattr(P, "MAX_TOTAL_PENALTY_TRUE3", 999)), "<="),
    ]
    if mode == "high":
        checks.extend(
            [
                ("high_coarse_acc_pct", _sf(metrics.get("high_coarse_acc_pct")), _sf(getattr(P, "MIN_HIGH_COARSE_ACC", -999)), ">="),
                ("high_coarse_lift_pp", _sf(metrics.get("high_coarse_lift_pp")), _sf(getattr(P, "MIN_HIGH_COARSE_LIFT", -999)), ">="),
                ("high_no_danger_acc_pct", _sf(metrics.get("high_no_danger_acc_pct")), _sf(getattr(P, "MIN_HIGH_NO_DANGER", -999)), ">="),
                ("high_dangerous_bin_error_mean", _sf(metrics.get("high_dangerous_bin_error_mean")), _sf(getattr(P, "MAX_HIGH_DANGEROUS_BIN_ERROR", 999)), "<="),
                ("high_safe_bin_error_mean", _sf(metrics.get("high_safe_bin_error_mean")), _sf(getattr(P, "MAX_HIGH_SAFE_BIN_ERROR", 999)), "<="),
                ("max_pred_share_high_pct", _sf(metrics.get("max_pred_share_high_pct")), _sf(getattr(P, "MAX_HIGH_PRED_SHARE_TRUE3", 100)), "<="),
            ]
        )
    else:
        checks.extend(
            [
                ("low_coarse_acc_pct", _sf(metrics.get("low_coarse_acc_pct")), _sf(getattr(P, "MIN_LOW_COARSE_ACC", -999)), ">="),
                ("low_coarse_lift_pp", _sf(metrics.get("low_coarse_lift_pp")), _sf(getattr(P, "MIN_LOW_COARSE_LIFT", -999)), ">="),
                ("low_no_danger_acc_pct", _sf(metrics.get("low_no_danger_acc_pct")), _sf(getattr(P, "MIN_LOW_NO_DANGER", -999)), ">="),
                ("low_dangerous_bin_error_mean", _sf(metrics.get("low_dangerous_bin_error_mean")), _sf(getattr(P, "MAX_LOW_DANGEROUS_BIN_ERROR", 999)), "<="),
                ("low_safe_bin_error_mean", _sf(metrics.get("low_safe_bin_error_mean")), _sf(getattr(P, "MAX_LOW_SAFE_BIN_ERROR", 999)), "<="),
                ("max_pred_share_low_pct", _sf(metrics.get("max_pred_share_low_pct")), _sf(getattr(P, "MAX_LOW_PRED_SHARE_TRUE3", 100)), "<="),
            ]
        )
    out: list[dict[str, Any]] = []
    for metric, value, threshold, rule in checks:
        item = _fail(metric, value, threshold, rule)
        if item:
            out.append(item)
    return out


def individual_to_dict(ind: Any) -> dict[str, Any]:
    d = _BASE_individual_to_dict(ind)
    d["head_objective"] = _objective_mode()
    d["target_mode"] = _objective_target_mode()
    d["signature"] = predictor_signature(ind)
    return d


def predictor_signature(ind: Any) -> str:
    payload = {"base_signature": _BASE_predictor_signature(ind), "head_objective": _objective_mode(), "target_mode": _objective_target_mode(), "feature_lookback_days": FEATURE_LOOKBACK_DAYS}
    return P.hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def dual_head_params() -> dict[str, Any]:
    params = _BASE_dual_head_params()
    params["mode"] = _objective_target_mode()
    params["head_objective"] = _objective_mode()
    params["separable_head_training"] = True
    params["feature_lookback_days"] = int(FEATURE_LOOKBACK_DAYS)
    params["lag_feature_expansion"] = {
        "enabled": True,
        "from_days": 5,
        "to_days": int(FEATURE_LOOKBACK_DAYS),
        "description": "Underlying dataset LOOKBACK is forced to 10, so STK_lag1~10 and STAGE2_lag1~10 are generated and used by qspec/dense weights.",
    }
    return params


def _parse_head_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--head-objective", choices=["both", "high", "low"], default=HEAD_OBJECTIVE)
    return parser.parse_known_args(argv)


def _apply_head_args(args: argparse.Namespace) -> None:
    global HEAD_OBJECTIVE, TARGET_MODE
    HEAD_OBJECTIVE = str(args.head_objective)
    TARGET_MODE = _objective_target_mode()
    P.TARGET_MODE = TARGET_MODE


def install_dual_head_target(args: Any) -> None:
    _BASE_install_dual_head_target(args)
    _apply_feature_lookback_days()
    replacements = {
        "predict_signal": predict_signal,
        "predict": predict,
        "evaluate_predictor": evaluate_predictor,
        "predictor_fitness": predictor_fitness,
        "dual_fail_reasons": dual_fail_reasons,
        "individual_to_dict": individual_to_dict,
        "predictor_signature": predictor_signature,
        "dual_head_params": dual_head_params,
    }
    for name, value in replacements.items():
        setattr(P, name, value)
    P.L.predict = predict
    P.L.evaluate_predictor = evaluate_predictor
    P.L.individual_to_dict = individual_to_dict
    P.L.predictor_signature = predictor_signature
    P.TARGET_MODE = TARGET_MODE


def parse_args(argv: list[str] | None = None):
    head_args, remaining = _parse_head_args(sys.argv[1:] if argv is None else argv)
    _apply_head_args(head_args)
    _apply_feature_lookback_days()
    return _BASE_parse_args(remaining)


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: Any):
    _apply_feature_lookback_days()
    P.install_dual_head_target = install_dual_head_target
    P.dual_head_params = dual_head_params
    return _BASE_run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


_apply_feature_lookback_days()
P.predict_signal = predict_signal
P.predict = predict
P.evaluate_predictor = evaluate_predictor
P.predictor_fitness = predictor_fitness
P.dual_fail_reasons = dual_fail_reasons
P.individual_to_dict = individual_to_dict
P.predictor_signature = predictor_signature
P.dual_head_params = dual_head_params
P.install_dual_head_target = install_dual_head_target
P.parse_args = parse_args
P.run_original_stage2_predictor = run_original_stage2_predictor

for _name in dir(P):
    if not _name.startswith("__") and _name not in globals():
        globals()[_name] = getattr(P, _name)

for _name, _value in {
    "TARGET_MODE": TARGET_MODE,
    "HEAD_OBJECTIVE": HEAD_OBJECTIVE,
    "FEATURE_LOOKBACK_DAYS": FEATURE_LOOKBACK_DAYS,
    "predict_signal": predict_signal,
    "predict": predict,
    "evaluate_predictor": evaluate_predictor,
    "predictor_fitness": predictor_fitness,
    "dual_fail_reasons": dual_fail_reasons,
    "individual_to_dict": individual_to_dict,
    "predictor_signature": predictor_signature,
    "dual_head_params": dual_head_params,
    "install_dual_head_target": install_dual_head_target,
    "parse_args": parse_args,
    "run_original_stage2_predictor": run_original_stage2_predictor,
}.items():
    globals()[_name] = _value

_apply_feature_lookback_days()


def default_seed_base(ticker: str) -> int:
    return int(P.default_seed_base(ticker)) if hasattr(P, "default_seed_base") else int(P.L.default_seed_base(ticker))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else P.auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
