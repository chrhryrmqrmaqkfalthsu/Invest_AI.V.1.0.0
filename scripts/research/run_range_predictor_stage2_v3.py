#!/usr/bin/env python3
"""
Original-Stage2-style dual-head GA runner for next-day HIGH/LOW bin prediction.

원본 scripts/research/run_stage2.py 기준 Stage2 흐름:
- train_1, train_2, train_3 각각 독립 GA를 실행한다.
- 각 train split에서 final population 100개를 후보로 모은다.
- 총 후보는 기본 300개다. 같은 signature가 중복되면 대표 1개로 합친다.
- 그 후보들을 early-cut 순서로 평가한다.

원본 Stage2 early-cut 순서:
1. stress_pre_2022h1
2. train_3_eval
3. train_2_eval
4. train_1_eval
5. oos_2025h2

이번 버전의 핵심:
- large-range 변동성 타깃을 버린다.
- 한 개체 안에 HIGH 전용 유전자와 LOW 전용 유전자를 분리한다.
- high_rules는 predicted_high_bin만 만든다.
- low_rules는 predicted_low_bin만 만든다.
- 각 헤드의 rule 개수, fitness 가중치, gate 기준을 CLI로 조정 가능하게 한다.

Read/write scope:
- OHLCV/cache/news csv는 read-only.
- 결과는 out_dir 아래 연구 산출물만 생성.
- run_live, 실거래, 캐시 갱신 없음.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import random
import subprocess
import sys
import time
import types
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_COMMIT = "b03f39b"
LEGACY_PATH = "scripts/research/run_range_predictor_stage2_v3.py"

TARGET_MODE = "next_day_hilo_dual_head_original_stage2"

TRAIN_SPLITS = (
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
)

PERIODS_TEMPLATE = (
    {"label": "stress_pre_2022h1", "kind": "stress", "start": "1900-01-01", "end": "2022-06-30", "order": 1},
    {"label": "train_3_eval", "kind": "train", "start": "2024-07-01", "end": "2025-06-30", "order": 2},
    {"label": "train_2_eval", "kind": "train", "start": "2023-07-01", "end": "2024-06-30", "order": 3},
    {"label": "train_1_eval", "kind": "train", "start": "2022-07-01", "end": "2023-06-30", "order": 4},
    {"label": "oos_2025h2", "kind": "oos", "start": "2025-07-01", "end": "2099-12-31", "order": 5},
)


def _load_legacy_module() -> types.ModuleType:
    code = subprocess.check_output(["git", "show", f"{LEGACY_COMMIT}:{LEGACY_PATH}"], cwd=str(PROJECT_ROOT), text=True)
    mod = types.ModuleType("_km_range_predictor_v3_b03f39b")
    mod.__file__ = str(PROJECT_ROOT / LEGACY_PATH)
    mod.__name__ = "_km_range_predictor_v3_b03f39b"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


L = _load_legacy_module()
LEGACY_MAKE_BASELINE_SPEC = L.make_baseline_spec

# 헤드별 기본 파라미터. install_dual_head_target()에서 CLI 값으로 덮어쓴다.
HIGH_RULE_COUNT = max(1, int(getattr(L, "RULE_COUNT", 80)) // 2)
LOW_RULE_COUNT = max(1, int(getattr(L, "RULE_COUNT", 80)) - HIGH_RULE_COUNT)

HIGH_HEAD_WEIGHT = 1.0
LOW_HEAD_WEIGHT = 1.0
BOTH_HEAD_WEIGHT = 0.55

HIGH_EXACT_WEIGHT = 0.60
HIGH_ADJACENT_WEIGHT = 0.40
HIGH_MAE_WEIGHT = 1.25
LOW_EXACT_WEIGHT = 0.60
LOW_ADJACENT_WEIGHT = 0.40
LOW_MAE_WEIGHT = 1.25
BOTH_EXACT_WEIGHT = 0.35
BOTH_ADJACENT_WEIGHT = 0.25
COMBINED_MAE_WEIGHT = 0.85
HEAD_IMBALANCE_PENALTY = 0.12

MIN_MEMBER_SCORE = 10.0
MIN_HIGH_EXACT_LIFT = -999.0
MIN_LOW_EXACT_LIFT = -999.0
MIN_HIGH_ADJACENT_LIFT = 0.0
MIN_LOW_ADJACENT_LIFT = 0.0
MIN_BOTH_EXACT_LIFT = -999.0
MIN_BOTH_ADJACENT_LIFT = -999.0
MIN_HIGH_MAE_LIFT = 0.0
MIN_LOW_MAE_LIFT = 0.0
MIN_COMBINED_MAE_LIFT = 0.0
MAX_TOTAL_PENALTY = 10.0
MAX_HIGH_PRED_SHARE = 65.0
MAX_LOW_PRED_SHARE = 65.0


def safe_float(value: Any, default: float = 0.0) -> float:
    return L.safe_float(value, default)


def safe_int(value: Any, default: int = 0) -> int:
    return L.safe_int(value, default)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    L.write_json(path, payload)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    L.write_jsonl(path, rows)


def json_clone(value: Any) -> Any:
    return json.loads(json.dumps(L.json_safe(value), ensure_ascii=False))


def auto_out_dir(ticker: str) -> Path:
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_original_stage2_dual_hilo_{time.strftime('%Y%m%d')}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("cannot allocate output directory")


@dataclass
class DualHeadPredictorIndividual:
    high_rules: list[Any]
    low_rules: list[Any]
    default_high_bin: int
    default_low_bin: int
    baseline_spec: dict[str, Any]
    fitness: float = -1e9
    metrics: dict[str, Any] | None = None
    signature: str | None = None

    @property
    def rules(self) -> list[Any]:
        # legacy 분석/호환용. 실제 예측은 high_rules/low_rules를 분리해서 쓴다.
        return list(self.high_rules) + list(self.low_rules)


def clone_rule(rule: Any) -> Any:
    return L.RuleGene(**asdict(rule)) if dataclasses.is_dataclass(rule) else L.RuleGene(**dict(rule))


def clone_individual(ind: DualHeadPredictorIndividual) -> DualHeadPredictorIndividual:
    return DualHeadPredictorIndividual(
        [clone_rule(r) for r in ind.high_rules],
        [clone_rule(r) for r in ind.low_rules],
        int(ind.default_high_bin),
        int(ind.default_low_bin),
        json_clone(ind.baseline_spec),
        float(ind.fitness),
        json_clone(ind.metrics) if ind.metrics is not None else None,
        ind.signature,
    )


def rule_payload(rule: Any) -> dict[str, Any]:
    return asdict(rule) if dataclasses.is_dataclass(rule) else dict(rule)


def predictor_signature(ind: DualHeadPredictorIndividual) -> str:
    payload = json.dumps(
        {
            "version": "dual_head_v1",
            "default_high_bin": int(ind.default_high_bin),
            "default_low_bin": int(ind.default_low_bin),
            "high_rules": [rule_payload(r) for r in ind.high_rules],
            "low_rules": [rule_payload(r) for r in ind.low_rules],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def individual_to_dict(ind: DualHeadPredictorIndividual) -> dict[str, Any]:
    return {
        "type": "dual_head_hilo_predictor",
        "high_rules": [rule_payload(r) for r in ind.high_rules],
        "low_rules": [rule_payload(r) for r in ind.low_rules],
        "rules": [rule_payload(r) for r in ind.rules],
        "default_high_bin": int(ind.default_high_bin),
        "default_low_bin": int(ind.default_low_bin),
        "baseline_spec": ind.baseline_spec,
        "dual_head_params": dual_head_params(),
        "fitness": safe_float(ind.fitness),
        "metrics": ind.metrics,
        "signature": ind.signature or predictor_signature(ind),
    }


def random_rule_for_target(target: str, rng: random.Random, qspec: dict[str, dict[str, list[float]]]) -> Any:
    feature = rng.choice(list(qspec.keys()))
    width = rng.uniform(L.MIN_BAND_WIDTH_Q, min(0.45, L.MAX_BAND_WIDTH_Q))
    lo = rng.uniform(0.0, 1.0 - width)
    return L.RuleGene(
        str(target),
        feature,
        float(lo),
        float(lo + width),
        int(rng.randrange(L.BIN_COUNT)),
        float(rng.uniform(0.4, 3.0)),
        float(rng.uniform(L.MIN_SOFTNESS, L.MAX_SOFTNESS)),
    )


def random_individual(rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any]) -> DualHeadPredictorIndividual:
    return DualHeadPredictorIndividual(
        [random_rule_for_target("HIGH", rng, qspec) for _ in range(HIGH_RULE_COUNT)],
        [random_rule_for_target("LOW", rng, qspec) for _ in range(LOW_RULE_COUNT)],
        safe_int(baseline_spec.get("exact_high_bin")),
        safe_int(baseline_spec.get("exact_low_bin")),
        dict(baseline_spec),
    )


def repair_head_rules(rules: list[Any], target: str, count: int, rng: random.Random, qspec: dict[str, dict[str, list[float]]]) -> list[Any]:
    out = [clone_rule(r) for r in rules]
    for r in out:
        r.target = target
    while len(out) < count:
        out.append(random_rule_for_target(target, rng, qspec))
    if len(out) > count:
        out = out[:count]
    return out


def predict(ind: DualHeadPredictorIndividual, X, qspec: dict[str, dict[str, list[float]]]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    n = len(X)
    hs = np.zeros((n, L.BIN_COUNT), dtype=float)
    ls = np.zeros((n, L.BIN_COUNT), dtype=float)
    hs[:, int(ind.default_high_bin)] = 1.0
    ls[:, int(ind.default_low_bin)] = 1.0

    high_active = 0
    low_active = 0
    high_strength_sum = 0.0
    low_strength_sum = 0.0
    high_band_widths: list[float] = []
    low_band_widths: list[float] = []

    for head_name, rules, scores, active_key in [
        ("HIGH", ind.high_rules, hs, "high"),
        ("LOW", ind.low_rules, ls, "low"),
    ]:
        for rule in rules:
            if rule.feature not in X.columns or rule.feature not in qspec:
                continue
            q_low, q_high = L.normalize_band(rule.q_low, rule.q_high)
            lo_val = L.q_value(qspec[rule.feature], q_low)
            hi_val = L.q_value(qspec[rule.feature], q_high)
            strength = L.band_match_strength(X[rule.feature].to_numpy(dtype=float), lo_val, hi_val, rule.softness)
            if not np.any(strength > 0):
                continue
            scores[:, int(rule.bin)] += strength * float(rule.weight)
            if active_key == "high":
                high_active += 1
                high_strength_sum += float(np.mean(strength))
                high_band_widths.append(q_high - q_low)
            else:
                low_active += 1
                low_strength_sum += float(np.mean(strength))
                low_band_widths.append(q_high - q_low)

    diag = {
        "active_rule_count": int(high_active + low_active),
        "high_active_rule_count": int(high_active),
        "low_active_rule_count": int(low_active),
        "avg_rule_match_strength": float((high_strength_sum + low_strength_sum) / max(1, high_active + low_active)),
        "high_avg_rule_match_strength": float(high_strength_sum / high_active) if high_active else 0.0,
        "low_avg_rule_match_strength": float(low_strength_sum / low_active) if low_active else 0.0,
        "avg_band_width_q": float(np.mean(high_band_widths + low_band_widths)) if (high_band_widths or low_band_widths) else 0.0,
        "high_avg_band_width_q": float(np.mean(high_band_widths)) if high_band_widths else 0.0,
        "low_avg_band_width_q": float(np.mean(low_band_widths)) if low_band_widths else 0.0,
    }
    return hs.argmax(axis=1), ls.argmax(axis=1), diag


def share_by_bin(pred: np.ndarray) -> list[float]:
    counts = np.bincount(pred.astype(int), minlength=L.BIN_COUNT)
    total = max(1, int(counts.sum()))
    return [float(c / total * 100.0) for c in counts]


def band_shape_penalty_by_head(rules: list[Any]) -> dict[str, float]:
    narrow = 0.0
    wide = 0.0
    for rule in rules:
        lo, hi = L.normalize_band(rule.q_low, rule.q_high)
        width = hi - lo
        narrow += max(0.0, L.MIN_BAND_WIDTH_Q * 1.5 - width)
        wide += max(0.0, width - 0.55)
    return {
        "narrow_band_penalty": narrow * L.NARROW_BAND_PENALTY_STRENGTH,
        "wide_band_penalty": wide * L.WIDE_BAND_PENALTY_STRENGTH,
    }


def prediction_penalty(ind: DualHeadPredictorIndividual, yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, Any]:
    hp = share_by_bin(ph)
    lp = share_by_bin(pl)
    ha = share_by_bin(yh)
    la = share_by_bin(yl)

    high_conc_excess = max(0.0, max(hp) - L.CONCENTRATION_CAP_PCT)
    low_conc_excess = max(0.0, max(lp) - L.CONCENTRATION_CAP_PCT)
    high_rare_excess = 0.0
    low_rare_excess = 0.0
    for p, a in zip(hp, ha):
        if a < L.RARE_BIN_ACTUAL_MAX_PCT:
            high_rare_excess += max(0.0, p - L.RARE_BIN_PRED_ALLOW_PCT)
    for p, a in zip(lp, la):
        if a < L.RARE_BIN_ACTUAL_MAX_PCT:
            low_rare_excess += max(0.0, p - L.RARE_BIN_PRED_ALLOW_PCT)

    hb = band_shape_penalty_by_head(ind.high_rules)
    lb = band_shape_penalty_by_head(ind.low_rules)
    high_penalty = high_conc_excess * L.CONCENTRATION_PENALTY_STRENGTH + high_rare_excess * L.RARE_BIN_PENALTY_STRENGTH + hb["narrow_band_penalty"] + hb["wide_band_penalty"]
    low_penalty = low_conc_excess * L.CONCENTRATION_PENALTY_STRENGTH + low_rare_excess * L.RARE_BIN_PENALTY_STRENGTH + lb["narrow_band_penalty"] + lb["wide_band_penalty"]
    total = high_penalty + low_penalty
    return {
        "high_concentration_penalty": high_conc_excess * L.CONCENTRATION_PENALTY_STRENGTH,
        "low_concentration_penalty": low_conc_excess * L.CONCENTRATION_PENALTY_STRENGTH,
        "high_rare_bin_penalty": high_rare_excess * L.RARE_BIN_PENALTY_STRENGTH,
        "low_rare_bin_penalty": low_rare_excess * L.RARE_BIN_PENALTY_STRENGTH,
        "high_narrow_band_penalty": hb["narrow_band_penalty"],
        "high_wide_band_penalty": hb["wide_band_penalty"],
        "low_narrow_band_penalty": lb["narrow_band_penalty"],
        "low_wide_band_penalty": lb["wide_band_penalty"],
        "high_total_penalty": high_penalty,
        "low_total_penalty": low_penalty,
        "total_penalty": total,
        "max_pred_share_high_pct": max(hp) if hp else 0.0,
        "max_pred_share_low_pct": max(lp) if lp else 0.0,
        "pred_distribution_high_pct": hp,
        "pred_distribution_low_pct": lp,
    }


def make_dual_baseline_spec(train_df) -> dict[str, Any]:
    spec = dict(LEGACY_MAKE_BASELINE_SPEC(train_df))
    spec.update(
        {
            "target_mode": TARGET_MODE,
            "source": "origin train split baseline for dual-head HIGH/LOW bin prediction",
            "high_rule_count": HIGH_RULE_COUNT,
            "low_rule_count": LOW_RULE_COUNT,
        }
    )
    return spec


def score_hilo_predictions(df, ph: np.ndarray, pl: np.ndarray, spec: Mapping[str, Any]) -> dict[str, float]:
    scores = L.score_predictions(df, ph, pl, spec)
    # legacy score_predictions의 key를 그대로 쓰되, 둘의 불균형과 실제 pct 오차합을 추가한다.
    scores["head_exact_gap_abs_pp"] = abs(safe_float(scores.get("high_exact_acc_pct")) - safe_float(scores.get("low_exact_acc_pct")))
    scores["head_adjacent_gap_abs_pp"] = abs(safe_float(scores.get("high_adjacent_acc_pct")) - safe_float(scores.get("low_adjacent_acc_pct")))
    scores["combined_mae_sum_pct"] = safe_float(scores.get("high_mae_pct")) + safe_float(scores.get("low_mae_pct"))
    return scores


def predictor_fitness(metrics: Mapping[str, Any]) -> float:
    high_component = (
        safe_float(metrics.get("high_exact_lift_pp")) * HIGH_EXACT_WEIGHT
        + safe_float(metrics.get("high_adjacent_lift_pp")) * HIGH_ADJACENT_WEIGHT
        + safe_float(metrics.get("high_mae_lift_pct")) * HIGH_MAE_WEIGHT
    )
    low_component = (
        safe_float(metrics.get("low_exact_lift_pp")) * LOW_EXACT_WEIGHT
        + safe_float(metrics.get("low_adjacent_lift_pp")) * LOW_ADJACENT_WEIGHT
        + safe_float(metrics.get("low_mae_lift_pct")) * LOW_MAE_WEIGHT
    )
    both_component = (
        safe_float(metrics.get("both_exact_lift_pp")) * BOTH_EXACT_WEIGHT
        + safe_float(metrics.get("both_adjacent_lift_pp")) * BOTH_ADJACENT_WEIGHT
        + safe_float(metrics.get("combined_mae_lift_pct")) * COMBINED_MAE_WEIGHT
    )
    imbalance_penalty = abs(high_component - low_component) * HEAD_IMBALANCE_PENALTY
    raw = high_component * HIGH_HEAD_WEIGHT + low_component * LOW_HEAD_WEIGHT + both_component * BOTH_HEAD_WEIGHT
    return float(raw - imbalance_penalty - safe_float(metrics.get("total_penalty")))


def evaluate_predictor(ind: DualHeadPredictorIndividual, df, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    yh = df["high_bin"].to_numpy(dtype=int)
    yl = df["low_bin"].to_numpy(dtype=int)
    ph, pl, pred_diag = predict(ind, df[features], qspec)
    scores = score_hilo_predictions(df, ph, pl, ind.baseline_spec)
    penalty = prediction_penalty(ind, yh, yl, ph, pl)
    bases = L.baseline_metrics(df, ind.baseline_spec)
    exact_base = bases["exact_baseline"]
    adj_base = bases["adjacent_baseline"]
    metrics = {
        **scores,
        "target_mode": TARGET_MODE,
        "sample_count": int(len(df)),
        "high_rule_count": len(ind.high_rules),
        "low_rule_count": len(ind.low_rules),
        "combined_exact_lift_pp": scores["combined_exact_acc_pct"] - exact_base["combined_exact_acc_pct"],
        "combined_adjacent_lift_pp": scores["combined_adjacent_acc_pct"] - adj_base["combined_adjacent_acc_pct"],
        "both_exact_lift_pp": scores["both_exact_acc_pct"] - exact_base["both_exact_acc_pct"],
        "both_adjacent_lift_pp": scores["both_adjacent_acc_pct"] - adj_base["both_adjacent_acc_pct"],
        "high_exact_lift_pp": scores["high_exact_acc_pct"] - exact_base["high_exact_acc_pct"],
        "low_exact_lift_pp": scores["low_exact_acc_pct"] - exact_base["low_exact_acc_pct"],
        "high_adjacent_lift_pp": scores["high_adjacent_acc_pct"] - adj_base["high_adjacent_acc_pct"],
        "low_adjacent_lift_pp": scores["low_adjacent_acc_pct"] - adj_base["low_adjacent_acc_pct"],
        "high_mae_lift_pct": exact_base["high_mae_pct"] - scores["high_mae_pct"],
        "low_mae_lift_pct": exact_base["low_mae_pct"] - scores["low_mae_pct"],
        "combined_mae_lift_pct": exact_base["combined_mae_pct"] - scores["combined_mae_pct"],
        "baseline_exact_high_acc_pct": exact_base["high_exact_acc_pct"],
        "baseline_exact_low_acc_pct": exact_base["low_exact_acc_pct"],
        "baseline_exact_combined_acc_pct": exact_base["combined_exact_acc_pct"],
        "baseline_adjacent_high_acc_pct": adj_base["high_adjacent_acc_pct"],
        "baseline_adjacent_low_acc_pct": adj_base["low_adjacent_acc_pct"],
        "baseline_adjacent_combined_acc_pct": adj_base["combined_adjacent_acc_pct"],
        "baseline_both_exact_acc_pct": exact_base["both_exact_acc_pct"],
        "baseline_both_adjacent_acc_pct": adj_base["both_adjacent_acc_pct"],
        "baseline_high_mae_pct": exact_base["high_mae_pct"],
        "baseline_low_mae_pct": exact_base["low_mae_pct"],
        "baseline_combined_mae_pct": exact_base["combined_mae_pct"],
        **penalty,
        **pred_diag,
    }
    high_component = (
        metrics["high_exact_lift_pp"] * HIGH_EXACT_WEIGHT
        + metrics["high_adjacent_lift_pp"] * HIGH_ADJACENT_WEIGHT
        + metrics["high_mae_lift_pct"] * HIGH_MAE_WEIGHT
    )
    low_component = (
        metrics["low_exact_lift_pp"] * LOW_EXACT_WEIGHT
        + metrics["low_adjacent_lift_pp"] * LOW_ADJACENT_WEIGHT
        + metrics["low_mae_lift_pct"] * LOW_MAE_WEIGHT
    )
    both_component = (
        metrics["both_exact_lift_pp"] * BOTH_EXACT_WEIGHT
        + metrics["both_adjacent_lift_pp"] * BOTH_ADJACENT_WEIGHT
        + metrics["combined_mae_lift_pct"] * COMBINED_MAE_WEIGHT
    )
    metrics["high_component_score"] = high_component
    metrics["low_component_score"] = low_component
    metrics["both_component_score"] = both_component
    metrics["head_component_gap_abs"] = abs(high_component - low_component)
    metrics["head_imbalance_penalty"] = abs(high_component - low_component) * HEAD_IMBALANCE_PENALTY
    metrics["fitness"] = predictor_fitness(metrics)
    return metrics


def mutate_rule(rule: Any, rng: random.Random, qspec: dict[str, dict[str, list[float]]], target: str) -> Any:
    r = clone_rule(rule)
    r.target = target
    action = rng.choice(["replace", "feature", "shift_band", "resize_band", "bin", "weight", "softness"])
    if action == "replace" and qspec:
        return random_rule_for_target(target, rng, qspec)
    if action == "feature" and qspec:
        r.feature = rng.choice(list(qspec.keys()))
    elif action == "shift_band":
        delta = rng.gauss(0.0, L.MUTATION_STRENGTH * 0.18)
        r.q_low += delta
        r.q_high += delta
    elif action == "resize_band":
        lo, hi = L.normalize_band(r.q_low, r.q_high)
        mid = (lo + hi) / 2.0
        width = L.clamp((hi - lo) * rng.uniform(0.70, 1.35), L.MIN_BAND_WIDTH_Q, L.MAX_BAND_WIDTH_Q)
        r.q_low = mid - width / 2.0
        r.q_high = mid + width / 2.0
    elif action == "bin":
        r.bin = int(max(0, min(L.BIN_COUNT - 1, r.bin + rng.choice([-2, -1, 1, 2]))))
    elif action == "weight":
        r.weight = float(max(0.1, min(5.0, r.weight + rng.gauss(0.0, L.MUTATION_STRENGTH))))
    elif action == "softness":
        r.softness = float(L.clamp(r.softness + rng.gauss(0.0, 0.18), L.MIN_SOFTNESS, L.MAX_SOFTNESS))
    r.q_low, r.q_high = L.normalize_band(r.q_low, r.q_high)
    r.softness = float(L.clamp(r.softness, L.MIN_SOFTNESS, L.MAX_SOFTNESS))
    r.target = target
    return r


def mutate(ind: DualHeadPredictorIndividual, rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any] | None = None) -> DualHeadPredictorIndividual:
    child = clone_individual(ind)
    child.fitness = -1e9
    child.metrics = None
    child.signature = None
    if baseline_spec is not None:
        child.baseline_spec = dict(baseline_spec)
        child.default_high_bin = safe_int(baseline_spec.get("exact_high_bin"), child.default_high_bin)
        child.default_low_bin = safe_int(baseline_spec.get("exact_low_bin"), child.default_low_bin)
    child.high_rules = repair_head_rules(child.high_rules, "HIGH", HIGH_RULE_COUNT, rng, qspec)
    child.low_rules = repair_head_rules(child.low_rules, "LOW", LOW_RULE_COUNT, rng, qspec)
    for i, rule in enumerate(child.high_rules):
        if rng.random() <= L.MUTATION_RATE:
            child.high_rules[i] = mutate_rule(rule, rng, qspec, "HIGH")
    for i, rule in enumerate(child.low_rules):
        if rng.random() <= L.MUTATION_RATE:
            child.low_rules[i] = mutate_rule(rule, rng, qspec, "LOW")
    return child


def crossover(a: DualHeadPredictorIndividual, b: DualHeadPredictorIndividual, rng: random.Random, baseline_spec: dict[str, Any]) -> DualHeadPredictorIndividual:
    ah = repair_head_rules(a.high_rules, "HIGH", HIGH_RULE_COUNT, rng, {})
    bh = repair_head_rules(b.high_rules, "HIGH", HIGH_RULE_COUNT, rng, {})
    al = repair_head_rules(a.low_rules, "LOW", LOW_RULE_COUNT, rng, {})
    bl = repair_head_rules(b.low_rules, "LOW", LOW_RULE_COUNT, rng, {})
    high_rules = [clone_rule(ra if rng.random() < 0.5 else rb) for ra, rb in zip(ah, bh)]
    low_rules = [clone_rule(ra if rng.random() < 0.5 else rb) for ra, rb in zip(al, bl)]
    for r in high_rules:
        r.target = "HIGH"
    for r in low_rules:
        r.target = "LOW"
    return DualHeadPredictorIndividual(
        high_rules,
        low_rules,
        safe_int(baseline_spec.get("exact_high_bin")),
        safe_int(baseline_spec.get("exact_low_bin")),
        dict(baseline_spec),
    )


def score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows:
        return []
    high_adj_r = L.percentile_ranks([safe_float(r.get("high_adjacent_lift_pp")) for r in rows])
    low_adj_r = L.percentile_ranks([safe_float(r.get("low_adjacent_lift_pp")) for r in rows])
    high_mae_r = L.percentile_ranks([safe_float(r.get("high_mae_lift_pct")) for r in rows])
    low_mae_r = L.percentile_ranks([safe_float(r.get("low_mae_lift_pct")) for r in rows])
    both_adj_r = L.percentile_ranks([safe_float(r.get("both_adjacent_lift_pp")) for r in rows])
    fitness_r = L.percentile_ranks([safe_float(r.get("fitness")) for r in rows])
    penalty_r = L.percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(
            0.0,
            min(
                1.0,
                high_adj_r[i] * 0.18
                + low_adj_r[i] * 0.18
                + high_mae_r[i] * 0.16
                + low_mae_r[i] * 0.16
                + both_adj_r[i] * 0.16
                + fitness_r[i] * 0.11
                + penalty_r[i] * 0.05,
            ),
        ) * 100.0
        r = dict(row)
        r["member_score"] = round(score, 6)
        r["member_score_components"] = {
            "high_adjacent_lift_percentile": round(high_adj_r[i], 6),
            "low_adjacent_lift_percentile": round(low_adj_r[i], 6),
            "high_mae_lift_percentile": round(high_mae_r[i], 6),
            "low_mae_lift_percentile": round(low_mae_r[i], 6),
            "both_adjacent_lift_percentile": round(both_adj_r[i], 6),
            "fitness_percentile": round(fitness_r[i], 6),
            "low_penalty_percentile": round(penalty_r[i], 6),
        }
        out.append(r)
    return out


def dual_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    max_high_share = safe_float(metrics.get("max_pred_share_high_pct"))
    max_low_share = safe_float(metrics.get("max_pred_share_low_pct"))
    checks = [
        ("sample_count", safe_int(metrics.get("sample_count")), 100, ">="),
        ("member_score", safe_float(metrics.get("member_score")), MIN_MEMBER_SCORE, ">="),
        ("high_exact_lift_pp", safe_float(metrics.get("high_exact_lift_pp")), MIN_HIGH_EXACT_LIFT, ">="),
        ("low_exact_lift_pp", safe_float(metrics.get("low_exact_lift_pp")), MIN_LOW_EXACT_LIFT, ">="),
        ("high_adjacent_lift_pp", safe_float(metrics.get("high_adjacent_lift_pp")), MIN_HIGH_ADJACENT_LIFT, ">="),
        ("low_adjacent_lift_pp", safe_float(metrics.get("low_adjacent_lift_pp")), MIN_LOW_ADJACENT_LIFT, ">="),
        ("both_exact_lift_pp", safe_float(metrics.get("both_exact_lift_pp")), MIN_BOTH_EXACT_LIFT, ">="),
        ("both_adjacent_lift_pp", safe_float(metrics.get("both_adjacent_lift_pp")), MIN_BOTH_ADJACENT_LIFT, ">="),
        ("high_mae_lift_pct", safe_float(metrics.get("high_mae_lift_pct")), MIN_HIGH_MAE_LIFT, ">="),
        ("low_mae_lift_pct", safe_float(metrics.get("low_mae_lift_pct")), MIN_LOW_MAE_LIFT, ">="),
        ("combined_mae_lift_pct", safe_float(metrics.get("combined_mae_lift_pct")), MIN_COMBINED_MAE_LIFT, ">="),
        ("total_penalty", safe_float(metrics.get("total_penalty")), MAX_TOTAL_PENALTY, "<="),
        ("max_pred_share_high_pct", max_high_share, MAX_HIGH_PRED_SHARE, "<="),
        ("max_pred_share_low_pct", max_low_share, MAX_LOW_PRED_SHARE, "<="),
    ]
    out = []
    for metric, value, threshold, rule in checks:
        failed = (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold)
        if failed:
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def evaluate_mixed_population(
    *,
    ticker: str,
    alive_sigs: set[str],
    representative_by_sig: Mapping[str, dict[str, Any]],
    origins_by_sig: Mapping[str, list[dict[str, Any]]],
    df,
    period: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for rank, sig in enumerate(sorted(alive_sigs), 1):
        entry = representative_by_sig[sig]
        ind = entry["individual"]
        metrics = evaluate_predictor(ind, df, entry["features"], entry["qspec"])
        origin_labels = sorted({str(origin["train_label"]) for origin in origins_by_sig[sig]})
        raw.append(
            {
                "ticker": ticker,
                "signature": sig,
                "rank_is": rank,
                "period_label": period["label"],
                "period_kind": period["kind"],
                "period_order": period["order"],
                "period_start": period["start"],
                "period_end": period["end"],
                "origin_count": len(origins_by_sig[sig]),
                "origin_train_labels": origin_labels,
                "representative_train_label": entry["origin"]["train_label"],
                "representative_train_fitness": safe_float(entry["origin"].get("train_fitness")),
                **metrics,
            }
        )
    scored = score_period_candidates(raw)
    for row in scored:
        reasons = dual_fail_reasons(row, str(period["kind"]))
        row["fail_reasons"] = reasons
        row["passed_gate"] = not reasons
        row["status"] = "evaluated"
    return scored


def dual_head_params() -> dict[str, Any]:
    return {
        "high_rule_count": HIGH_RULE_COUNT,
        "low_rule_count": LOW_RULE_COUNT,
        "head_weights": {"high": HIGH_HEAD_WEIGHT, "low": LOW_HEAD_WEIGHT, "both": BOTH_HEAD_WEIGHT},
        "score_weights": {
            "high_exact": HIGH_EXACT_WEIGHT,
            "high_adjacent": HIGH_ADJACENT_WEIGHT,
            "high_mae": HIGH_MAE_WEIGHT,
            "low_exact": LOW_EXACT_WEIGHT,
            "low_adjacent": LOW_ADJACENT_WEIGHT,
            "low_mae": LOW_MAE_WEIGHT,
            "both_exact": BOTH_EXACT_WEIGHT,
            "both_adjacent": BOTH_ADJACENT_WEIGHT,
            "combined_mae": COMBINED_MAE_WEIGHT,
            "head_imbalance_penalty": HEAD_IMBALANCE_PENALTY,
        },
        "gate": {
            "min_member_score": MIN_MEMBER_SCORE,
            "min_high_exact_lift": MIN_HIGH_EXACT_LIFT,
            "min_low_exact_lift": MIN_LOW_EXACT_LIFT,
            "min_high_adjacent_lift": MIN_HIGH_ADJACENT_LIFT,
            "min_low_adjacent_lift": MIN_LOW_ADJACENT_LIFT,
            "min_both_exact_lift": MIN_BOTH_EXACT_LIFT,
            "min_both_adjacent_lift": MIN_BOTH_ADJACENT_LIFT,
            "min_high_mae_lift": MIN_HIGH_MAE_LIFT,
            "min_low_mae_lift": MIN_LOW_MAE_LIFT,
            "min_combined_mae_lift": MIN_COMBINED_MAE_LIFT,
            "max_total_penalty": MAX_TOTAL_PENALTY,
            "max_high_pred_share": MAX_HIGH_PRED_SHARE,
            "max_low_pred_share": MAX_LOW_PRED_SHARE,
        },
    }


def install_dual_head_target(args: argparse.Namespace) -> None:
    global HIGH_RULE_COUNT, LOW_RULE_COUNT
    global HIGH_HEAD_WEIGHT, LOW_HEAD_WEIGHT, BOTH_HEAD_WEIGHT
    global HIGH_EXACT_WEIGHT, HIGH_ADJACENT_WEIGHT, HIGH_MAE_WEIGHT
    global LOW_EXACT_WEIGHT, LOW_ADJACENT_WEIGHT, LOW_MAE_WEIGHT
    global BOTH_EXACT_WEIGHT, BOTH_ADJACENT_WEIGHT, COMBINED_MAE_WEIGHT, HEAD_IMBALANCE_PENALTY
    global MIN_MEMBER_SCORE, MIN_HIGH_EXACT_LIFT, MIN_LOW_EXACT_LIFT, MIN_HIGH_ADJACENT_LIFT, MIN_LOW_ADJACENT_LIFT
    global MIN_BOTH_EXACT_LIFT, MIN_BOTH_ADJACENT_LIFT, MIN_HIGH_MAE_LIFT, MIN_LOW_MAE_LIFT, MIN_COMBINED_MAE_LIFT
    global MAX_TOTAL_PENALTY, MAX_HIGH_PRED_SHARE, MAX_LOW_PRED_SHARE

    HIGH_RULE_COUNT = max(1, int(args.high_rule_count))
    LOW_RULE_COUNT = max(1, int(args.low_rule_count))
    HIGH_HEAD_WEIGHT = float(args.high_head_weight)
    LOW_HEAD_WEIGHT = float(args.low_head_weight)
    BOTH_HEAD_WEIGHT = float(args.both_head_weight)
    HIGH_EXACT_WEIGHT = float(args.high_exact_weight)
    HIGH_ADJACENT_WEIGHT = float(args.high_adjacent_weight)
    HIGH_MAE_WEIGHT = float(args.high_mae_weight)
    LOW_EXACT_WEIGHT = float(args.low_exact_weight)
    LOW_ADJACENT_WEIGHT = float(args.low_adjacent_weight)
    LOW_MAE_WEIGHT = float(args.low_mae_weight)
    BOTH_EXACT_WEIGHT = float(args.both_exact_weight)
    BOTH_ADJACENT_WEIGHT = float(args.both_adjacent_weight)
    COMBINED_MAE_WEIGHT = float(args.combined_mae_weight)
    HEAD_IMBALANCE_PENALTY = float(args.head_imbalance_penalty)

    MIN_MEMBER_SCORE = float(args.min_member_score)
    MIN_HIGH_EXACT_LIFT = float(args.min_high_exact_lift)
    MIN_LOW_EXACT_LIFT = float(args.min_low_exact_lift)
    MIN_HIGH_ADJACENT_LIFT = float(args.min_high_adjacent_lift)
    MIN_LOW_ADJACENT_LIFT = float(args.min_low_adjacent_lift)
    MIN_BOTH_EXACT_LIFT = float(args.min_both_exact_lift)
    MIN_BOTH_ADJACENT_LIFT = float(args.min_both_adjacent_lift)
    MIN_HIGH_MAE_LIFT = float(args.min_high_mae_lift)
    MIN_LOW_MAE_LIFT = float(args.min_low_mae_lift)
    MIN_COMBINED_MAE_LIFT = float(args.min_combined_mae_lift)
    MAX_TOTAL_PENALTY = float(args.max_total_penalty)
    MAX_HIGH_PRED_SHARE = float(args.max_high_pred_share)
    MAX_LOW_PRED_SHARE = float(args.max_low_pred_share)

    # legacy GA 루프가 참조하는 module-level 함수들을 dual-head 버전으로 교체한다.
    L.make_baseline_spec = make_dual_baseline_spec
    L.clone_individual = clone_individual
    L.individual_to_dict = individual_to_dict
    L.predictor_signature = predictor_signature
    L.random_individual = random_individual
    L.predict = predict
    L.evaluate_predictor = evaluate_predictor
    L.mutate_rule = mutate_rule
    L.mutate = mutate
    L.crossover = crossover


def train_one_split(
    *,
    ticker: str,
    split_idx: int,
    split: Mapping[str, str],
    seed_base: int,
    data,
    all_features: list[str],
) -> dict[str, Any]:
    started = time.time()
    rng = random.Random(seed_base + split_idx)
    train_df = period_frame_checked(data, split["train_start"], split["train_end"], split["label"])
    qspec = L.make_quantile_spec(train_df, all_features)
    usable_features = [f for f in all_features if f in qspec]
    baseline_spec = L.make_baseline_spec(train_df)
    init_pop = L.prepare_population_for_split(None, rng, qspec, baseline_spec)
    split_meta = {
        "label": split["label"],
        "train_start": split["train_start"],
        "train_end": split["train_end"],
        "stage": split_idx,
    }
    pop, history = L.run_ga_on_split(init_pop, train_df, usable_features, qspec, split_meta, seed_base + split_idx)
    rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for ind in pop:
        ind.metrics = evaluate_predictor(ind, train_df, usable_features, qspec)
        ind.fitness = safe_float(ind.metrics.get("fitness"))
        ind.signature = predictor_signature(ind)
    pop_sorted = sorted(pop, key=lambda ind: safe_float(getattr(ind, "fitness", 0.0)), reverse=True)
    for rank, ind in enumerate(pop_sorted, 1):
        sig = ind.signature or predictor_signature(ind)
        row = {
            "ticker": ticker,
            "target_mode": TARGET_MODE,
            "train_label": split["label"],
            "train_start": split["train_start"],
            "train_end": split["train_end"],
            "origin_rank": rank,
            "signature": sig,
            "train_fitness": safe_float(getattr(ind, "fitness", 0.0)),
            "train_metrics": ind.metrics,
            "predictor": individual_to_dict(ind),
        }
        rows.append(row)
        entries.append(
            {
                "signature": sig,
                "individual": ind,
                "qspec": qspec,
                "features": usable_features,
                "baseline_spec": baseline_spec,
                "origin": {k: row[k] for k in ["train_label", "train_start", "train_end", "origin_rank", "train_fitness"]},
            }
        )
    for h in history:
        h.update(
            {
                "train_label": split["label"],
                "train_start": split["train_start"],
                "train_end": split["train_end"],
                "generations_run": len(history),
                "early_stop_triggered": len(history) < L.GENERATIONS,
                "train_elapsed_sec": time.time() - started,
                "target_mode": TARGET_MODE,
            }
        )
    return {
        "split": dict(split),
        "rows": rows,
        "entries": entries,
        "history": history,
        "elapsed_sec": time.time() - started,
        "generations_run": len(history),
        "early_stop": len(history) < L.GENERATIONS,
        "baseline_spec": baseline_spec,
        "feature_count": len(usable_features),
    }


def build_representatives(train_results: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    representative_by_sig: dict[str, dict[str, Any]] = {}
    origins_by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in train_results:
        for entry in result["entries"]:
            sig = str(entry["signature"])
            origins_by_sig[sig].append(dict(entry["origin"]))
            current = representative_by_sig.get(sig)
            if current is None or safe_float(entry["origin"].get("train_fitness")) > safe_float(current["origin"].get("train_fitness")):
                representative_by_sig[sig] = entry
    return representative_by_sig, origins_by_sig


def period_frame_checked(data, start: str, end: str, label: str):
    df = L.period_frame(data, start, end)
    if df.empty:
        raise ValueError(f"empty period: {label} {start}~{end}")
    return df


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: argparse.Namespace) -> dict[str, Any]:
    install_dual_head_target(args)
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = L.build_dataset(ticker)
    required_cols = {"high_bin", "low_bin", "high_pct_label", "low_mag_pct_label"}
    missing = sorted(required_cols - set(data.columns))
    if missing:
        raise ValueError(f"missing required high/low label columns: {missing}")
    all_features = L.feature_columns(data)

    train_results: list[dict[str, Any]] = []
    for idx, split in enumerate(TRAIN_SPLITS, 1):
        print(f"TRAIN_START label={split['label']}", flush=True)
        result = train_one_split(ticker=ticker, split_idx=idx, split=split, seed_base=seed_base, data=data, all_features=all_features)
        print(
            f"TRAIN_DONE label={split['label']} rows={len(result['rows'])} generations={result['generations_run']} early_stop={result['early_stop']} elapsed={result['elapsed_sec']:.1f}",
            flush=True,
        )
        train_results.append(result)

    predictor_rows: list[dict[str, Any]] = []
    ga_history_rows: list[dict[str, Any]] = []
    for result in train_results:
        predictor_rows.extend(result["rows"])
        ga_history_rows.extend(result["history"])

    representative_by_sig, origins_by_sig = build_representatives(train_results)
    unique_sigs = set(representative_by_sig)
    alive = set(unique_sigs)
    period_metric_rows: list[dict[str, Any]] = []
    early_cut_rows: list[dict[str, Any]] = []
    survivor_rows_by_stage: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    first_fail_by_sig: dict[str, dict[str, Any]] = {}
    evaluated_periods_by_sig: dict[str, list[str]] = defaultdict(list)
    metrics_by_sig_period: dict[tuple[str, str], dict[str, Any]] = {}
    actual_eval_count = 0
    max_eval_count = len(unique_sigs) * len(PERIODS_TEMPLATE)

    for period in PERIODS_TEMPLATE:
        reached = set(alive)
        print(f"EVAL_START period={period['label']} reached={len(reached)}", flush=True)
        pdf = period_frame_checked(data, str(period["start"]), str(period["end"]), str(period["label"]))
        scored = evaluate_mixed_population(
            ticker=ticker,
            alive_sigs=reached,
            representative_by_sig=representative_by_sig,
            origins_by_sig=origins_by_sig,
            df=pdf,
            period=period,
        )
        actual_eval_count += len(scored)
        passed_sigs = {str(row["signature"]) for row in scored if row.get("passed_gate")}
        for row in scored:
            sig = str(row["signature"])
            evaluated_periods_by_sig[sig].append(str(period["label"]))
            metrics_by_sig_period[(sig, str(period["label"]))] = dict(row)
            period_metric_rows.append(dict(row))
            if not row.get("passed_gate") and sig not in first_fail_by_sig:
                first_fail_by_sig[sig] = {
                    "signature": sig,
                    "failed_period_label": period["label"],
                    "failed_period_order": period["order"],
                    "failed_period_kind": period["kind"],
                    "fail_reasons": row.get("fail_reasons") or [],
                }
        top_rows = sorted(scored, key=lambda r: safe_float(r.get("fitness")), reverse=True)[:20]
        for rank, row in enumerate([r for r in top_rows if r.get("passed_gate")], 1):
            survivor_rows_by_stage.append({"stage_period_label": period["label"], "stage_rank": rank, **dict(row)})
        alive = passed_sigs
        print(f"EVAL_DONE period={period['label']} pass={len(alive)}", flush=True)
        trace.append(
            {
                "period_label": period["label"],
                "period_kind": period["kind"],
                "period_order": period["order"],
                "reached": len(reached),
                "passed": len(alive),
                "failed": len(reached) - len(alive),
                "ga_ran": False,
                "new_individuals_created": 0,
            }
        )
        if not alive:
            break

    for sig in sorted(unique_sigs):
        reached_periods = evaluated_periods_by_sig.get(sig, [])
        skipped = [period["label"] for period in PERIODS_TEMPLATE if period["label"] not in reached_periods]
        failed = first_fail_by_sig.get(sig)
        early_cut_rows.append(
            {
                "ticker": ticker,
                "signature": sig,
                "origin_count": len(origins_by_sig[sig]),
                "origin_train_labels": sorted({origin["train_label"] for origin in origins_by_sig[sig]}),
                "representative_train_label": representative_by_sig[sig]["origin"]["train_label"],
                "evaluated_period_count": len(reached_periods),
                "evaluated_periods": reached_periods,
                "skipped_period_count": len(skipped),
                "skipped_periods": skipped,
                "survived_all_5": sig in alive,
                "failed_period_label": failed.get("failed_period_label") if failed else None,
                "failed_period_order": failed.get("failed_period_order") if failed else None,
                "failed_period_kind": failed.get("failed_period_kind") if failed else None,
                "fail_reasons": failed.get("fail_reasons") if failed else [],
            }
        )
        if failed:
            for period in PERIODS_TEMPLATE:
                if period["label"] in skipped:
                    period_metric_rows.append(
                        {
                            "ticker": ticker,
                            "signature": sig,
                            "period_label": period["label"],
                            "period_kind": period["kind"],
                            "period_order": period["order"],
                            "period_start": period["start"],
                            "period_end": period["end"],
                            "status": "skipped_after_early_cut",
                            "passed_gate": False,
                            "fail_reasons": [],
                            "origin_count": len(origins_by_sig[sig]),
                            "origin_train_labels": sorted({origin["train_label"] for origin in origins_by_sig[sig]}),
                        }
                    )

    final_survivor_rows = []
    for sig in sorted(alive):
        entry = representative_by_sig[sig]
        final_survivor_rows.append(
            {
                "ticker": ticker,
                "target_mode": TARGET_MODE,
                "signature": sig,
                "origin_count": len(origins_by_sig[sig]),
                "origin_train_labels": sorted({origin["train_label"] for origin in origins_by_sig[sig]}),
                "origins": origins_by_sig[sig],
                "representative_train_label": entry["origin"]["train_label"],
                "representative_train_fitness": safe_float(entry["origin"].get("train_fitness")),
                "predictor": individual_to_dict(entry["individual"]),
                "periods": [metrics_by_sig_period.get((sig, period["label"]), {}) for period in PERIODS_TEMPLATE],
            }
        )

    distributions = {}
    for period in PERIODS_TEMPLATE:
        pdf = period_frame_checked(data, str(period["start"]), str(period["end"]), str(period["label"]))
        distributions[period["label"]] = {
            "start": period["start"],
            "end": period["end"],
            "kind": period["kind"],
            "high_bin": L.distribution(pdf["high_bin"].to_numpy(dtype=int)),
            "low_bin": L.distribution(pdf["low_bin"].to_numpy(dtype=int)),
            "high_pct_mean": float(np.nanmean(pdf["high_pct_label"].to_numpy(dtype=float))) if len(pdf) else 0.0,
            "low_mag_pct_mean": float(np.nanmean(pdf["low_mag_pct_label"].to_numpy(dtype=float))) if len(pdf) else 0.0,
        }
    train_baselines = {result["split"]["label"]: result["baseline_spec"] for result in train_results}
    source_counts = Counter(m.get("source", "unknown") for m in feature_meta)

    write_jsonl(out_dir / "predictors_all.jsonl", predictor_rows)
    write_jsonl(out_dir / "ga_history.jsonl", ga_history_rows)
    write_jsonl(out_dir / "period_metrics_all.jsonl", period_metric_rows)
    write_jsonl(out_dir / "early_cut_log.jsonl", early_cut_rows)
    write_jsonl(out_dir / "stage_survivors.jsonl", survivor_rows_by_stage)
    write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)

    fail_counts = Counter(str(row.get("failed_period_label") or "SURVIVED") for row in early_cut_rows)
    target_desc = {
        "mode": TARGET_MODE,
        "prediction_heads": {
            "high": "high_rules -> predicted_high_bin",
            "low": "low_rules -> predicted_low_bin",
        },
        "objective": "separate HIGH/LOW bin exact/adjacent accuracy and pct MAE improvement, plus same-day both-head agreement",
        "dual_head_params": dual_head_params(),
    }
    config = {
        "ticker": ticker,
        "runner": "scripts/research/run_range_predictor_stage2_v3.py",
        "legacy_feature_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}",
        "mode": "original_stage2_train123_independent_ga_then_early_cut_dual_hilo",
        "stage2_original_reference": "scripts/research/run_stage2.py: TRAIN_SPLITS independent GA; PERIODS_TEMPLATE early-cut stress -> train_3 -> train_2 -> train_1 -> oos",
        "train_splits": list(TRAIN_SPLITS),
        "evaluation_periods": list(PERIODS_TEMPLATE),
        "early_cut_order": [period["label"] for period in PERIODS_TEMPLATE],
        "ga": {
            "population_per_train_split": L.POPULATION,
            "train_split_count": len(TRAIN_SPLITS),
            "expected_candidate_rows": L.POPULATION * len(TRAIN_SPLITS),
            "generations": L.GENERATIONS,
            "patience": L.PATIENCE,
            "elite_ratio": L.ELITE_RATIO,
            "mutation_rate": L.MUTATION_RATE,
            "rule_count_legacy": L.RULE_COUNT,
            "high_rule_count": HIGH_RULE_COUNT,
            "low_rule_count": LOW_RULE_COUNT,
            "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO,
            "seed_base": seed_base,
            "train_splits_independent": True,
            "post_train_re_evolution": False,
            "post_train_new_individuals": 0,
            "min_band_width_q": L.MIN_BAND_WIDTH_Q,
            "max_band_width_q": L.MAX_BAND_WIDTH_Q,
            "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS],
        },
        "target": target_desc,
        "train_baselines": train_baselines,
        "lookahead_report": {
            "pass": True,
            "feature_quantile_spec": "each candidate uses its representative origin train split qspec; no eval-period refit",
            "label_reference": "D-day high_bin/low_bin and high_pct_label/low_mag_pct_label are labels only, not features",
            "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap",
            "flow_features": "optional D-1 orderbook/flow columns if cache provides them",
            "market_features": "ETF D0 gap or D-1 confirmed values only",
            "news_features": "market_history rows joined from D-1 date only",
            "excluded": ["D0 high/low/close as features", "future trading results as features", "eval-period qspec refit"],
        },
        "feature_sources": dict(source_counts),
        "bin_labels": L.BIN_LABELS,
        "default_bin_centers_pct": L.DEFAULT_BIN_CENTERS,
        "distributions": distributions,
    }
    write_json(out_dir / "config.json", config)
    actual_eval_ratio = float(actual_eval_count / max_eval_count) if max_eval_count else 0.0
    summary = {
        "ticker": ticker,
        "mode": "original_stage2_train123_independent_ga_then_early_cut_dual_hilo",
        "target": target_desc,
        "generated_candidate_rows": len(predictor_rows),
        "unique_signatures": len(unique_sigs),
        "survivor_count": len(final_survivor_rows),
        "survivor_signatures": [row["signature"] for row in final_survivor_rows],
        "stage_trace": trace,
        "fail_counts_by_first_failed_period": dict(fail_counts),
        "actual_period_evaluations": actual_eval_count,
        "max_period_evaluations": max_eval_count,
        "actual_eval_ratio": actual_eval_ratio,
        "period_eval_saved_ratio": 1.0 - actual_eval_ratio,
        "ga_generations_run_by_train": {result["split"]["label"]: result["generations_run"] for result in train_results},
        "ga_early_stop_triggered_by_train": {result["split"]["label"]: result["early_stop"] for result in train_results},
        "elapsed_sec": time.time() - started,
        "outputs": {
            "predictors_all": str(out_dir / "predictors_all.jsonl"),
            "ga_history": str(out_dir / "ga_history.jsonl"),
            "period_metrics_all": str(out_dir / "period_metrics_all.jsonl"),
            "early_cut_log": str(out_dir / "early_cut_log.jsonl"),
            "stage_survivors": str(out_dir / "stage_survivors.jsonl"),
            "final_survivors": str(out_dir / "final_survivors.jsonl"),
            "config": str(out_dir / "config.json"),
            "summary": str(out_dir / "summary.json"),
        },
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(L.json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Original Stage2 independent train1/train2/train3 GA + dual-head HIGH/LOW bin prediction")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=None, help="accepted for compatibility; original Stage2 does not pre-limit train split populations")
    p.add_argument("--parallel", action="store_true", help="accepted for interface parity; not used")

    # legacy large-range 인자는 호환용으로만 받는다. dual-head 모드에서는 쓰지 않는다.
    p.add_argument("--range-quantile", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("--signal-range-bin-sum-threshold", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--wilson-z", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("--min-signal-rate", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("--max-signal-rate", type=float, default=None, help=argparse.SUPPRESS)
    p.add_argument("--min-signal-count-train", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--min-signal-count-final", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--min-hit-count-train", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--min-hit-count-final", type=int, default=None, help=argparse.SUPPRESS)

    p.add_argument("--high-rule-count", type=int, default=HIGH_RULE_COUNT)
    p.add_argument("--low-rule-count", type=int, default=LOW_RULE_COUNT)
    p.add_argument("--high-head-weight", type=float, default=HIGH_HEAD_WEIGHT)
    p.add_argument("--low-head-weight", type=float, default=LOW_HEAD_WEIGHT)
    p.add_argument("--both-head-weight", type=float, default=BOTH_HEAD_WEIGHT)
    p.add_argument("--high-exact-weight", type=float, default=HIGH_EXACT_WEIGHT)
    p.add_argument("--high-adjacent-weight", type=float, default=HIGH_ADJACENT_WEIGHT)
    p.add_argument("--high-mae-weight", type=float, default=HIGH_MAE_WEIGHT)
    p.add_argument("--low-exact-weight", type=float, default=LOW_EXACT_WEIGHT)
    p.add_argument("--low-adjacent-weight", type=float, default=LOW_ADJACENT_WEIGHT)
    p.add_argument("--low-mae-weight", type=float, default=LOW_MAE_WEIGHT)
    p.add_argument("--both-exact-weight", type=float, default=BOTH_EXACT_WEIGHT)
    p.add_argument("--both-adjacent-weight", type=float, default=BOTH_ADJACENT_WEIGHT)
    p.add_argument("--combined-mae-weight", type=float, default=COMBINED_MAE_WEIGHT)
    p.add_argument("--head-imbalance-penalty", type=float, default=HEAD_IMBALANCE_PENALTY)

    p.add_argument("--min-member-score", type=float, default=MIN_MEMBER_SCORE)
    p.add_argument("--min-high-exact-lift", type=float, default=MIN_HIGH_EXACT_LIFT)
    p.add_argument("--min-low-exact-lift", type=float, default=MIN_LOW_EXACT_LIFT)
    p.add_argument("--min-high-adjacent-lift", type=float, default=MIN_HIGH_ADJACENT_LIFT)
    p.add_argument("--min-low-adjacent-lift", type=float, default=MIN_LOW_ADJACENT_LIFT)
    p.add_argument("--min-both-exact-lift", type=float, default=MIN_BOTH_EXACT_LIFT)
    p.add_argument("--min-both-adjacent-lift", type=float, default=MIN_BOTH_ADJACENT_LIFT)
    p.add_argument("--min-high-mae-lift", type=float, default=MIN_HIGH_MAE_LIFT)
    p.add_argument("--min-low-mae-lift", type=float, default=MIN_LOW_MAE_LIFT)
    p.add_argument("--min-combined-mae-lift", type=float, default=MIN_COMBINED_MAE_LIFT)
    p.add_argument("--max-total-penalty", type=float, default=MAX_TOTAL_PENALTY)
    p.add_argument("--max-high-pred-share", type=float, default=MAX_HIGH_PRED_SHARE)
    p.add_argument("--max-low-pred-share", type=float, default=MAX_LOW_PRED_SHARE)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else L.default_seed_base(ticker)
    run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
