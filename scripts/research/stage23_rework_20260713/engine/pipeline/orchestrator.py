"""Single-ticker pipeline orchestrator.

Current scope: screening -> rolling validation -> optional full training.
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from engine.core.feature_lag import FEATURE_LAG_METADATA
from engine.core.metadata import build_metadata
from engine.pipeline.full_training import (
    full_training_gate_from_rolling,
    run_full_training as run_full_training_stage,
    save_full_training_artifacts,
    summarize_full_training_result,
)
from engine.pipeline.rolling_validation import run_rolling_validation
from engine.pipeline.screening import run_screening

PIPELINE_ROOT = Path("data/_system/pipeline/v1/runs")

ScreeningFn = Callable[..., dict[str, Any]]
RollingFn = Callable[..., dict[str, Any]]
FullTrainingFn = Callable[..., dict[str, Any]]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if str(k) != "_context"}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return _json_safe({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
    return str(value)


def ticker_dir(run_id: str, ticker: str) -> Path:
    safe_ticker = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(ticker).upper())
    return PIPELINE_ROOT / str(run_id) / safe_ticker


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _stock_score_summary(rolling: dict[str, Any] | None) -> dict[str, Any]:
    if not rolling:
        return {}
    score = rolling.get("stock_score", {}) or {}
    return {
        "stock_score": score.get("stock_score"),
        "consistency_score": score.get("consistency_score"),
        "quality_score": score.get("quality_score"),
        "liquidity_weight": score.get("liquidity_weight"),
        "excluded": score.get("excluded"),
        "exclude_reason": score.get("exclude_reason"),
        "pass_count": (score.get("raw_metrics", {}) or {}).get("pass_count"),
    }


def _screening_summary(screening: dict[str, Any] | None) -> dict[str, Any]:
    if not screening:
        return {}
    data = screening.get("data", {}) or {}
    sentiment = screening.get("sentiment", {}) or {}
    viability = screening.get("viability", {}) or {}
    return {
        "passed": screening.get("passed"),
        "status": screening.get("status"),
        "reason_code": screening.get("reason_code"),
        "adv_usd_252d": screening.get("adv_usd_252d"),
        "liquidity_weight": screening.get("liquidity_weight"),
        "rows": data.get("rows"),
        "split_count": data.get("split_count"),
        "sentiment_days": sentiment.get("sentiment_days"),
        "viability_executed": viability.get("executed"),
        "viability_trade_count": viability.get("trade_count"),
        "viability_expectancy_pct": viability.get("expectancy_pct"),
    }


def _skipped_full_training(gate: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        reason = "DISABLED"
    else:
        reason = gate.get("reason_code") or "BELOW_CUTOFF"
    return {
        "executed": False,
        "status": "SKIPPED",
        "reason_code": reason,
        "gate": gate,
    }


def process_ticker(
    ticker: str,
    run_id: str,
    run_full_training: bool = False,
    screening_fn: ScreeningFn | None = None,
    rolling_fn: RollingFn | None = None,
    full_training_fn: FullTrainingFn | None = None,
) -> dict[str, Any]:
    """Process one ticker through screening -> rolling -> optional full training.

    One-ticker exceptions are caught and returned as status=ERROR so batch
    execution can continue.
    """
    started = time.time()
    ticker = str(ticker or "").upper().strip()
    out_dir = ticker_dir(run_id, ticker)
    outputs: dict[str, str] = {}
    screening: dict[str, Any] | None = None
    rolling: dict[str, Any] | None = None
    full_training = None

    try:
        screen = screening_fn or run_screening
        roll = rolling_fn or run_rolling_validation
        full_train = full_training_fn or run_full_training_stage

        screening = screen(ticker, include_context=True)
        context = screening.pop("_context", None)
        screening_path = out_dir / "screening.json"
        write_json(screening_path, screening)
        outputs["screening"] = str(screening_path)

        if not screening.get("passed"):
            final_status = "ERROR" if screening.get("status") == "ERROR" else "SCREENED_OUT"
            result = {
                "ticker": ticker,
                "run_id": run_id,
                "final_stage": "screening",
                "final_status": final_status,
                "passed": False,
                "reason_code": screening.get("reason_code") or "SCREENED_OUT",
                "screening": _screening_summary(screening),
                "rolling": None,
                "full_training": None,
                "outputs": outputs,
                "elapsed_sec": time.time() - started,
            }
        else:
            rolling = roll(ticker, context=context)
            rolling_path = out_dir / "rolling_validation.json"
            write_json(rolling_path, rolling)
            outputs["rolling_validation"] = str(rolling_path)
            rolling_summary = _stock_score_summary(rolling)
            gate = full_training_gate_from_rolling(rolling)

            final_stage = "rolling"
            final_status = "ROLLING_DONE"
            if run_full_training and gate.get("should_run"):
                full_training_result = full_train(ticker, context=context, run_id=run_id)
                ft_paths = save_full_training_artifacts(full_training_result, out_dir)
                outputs.update(ft_paths)
                full_training = summarize_full_training_result(full_training_result, ft_paths)
                final_stage = "full_training"
                final_status = "FULL_TRAINING_DONE"
            else:
                full_training = _skipped_full_training(gate, enabled=run_full_training)

            result = {
                "ticker": ticker,
                "run_id": run_id,
                "final_stage": final_stage,
                "final_status": final_status,
                "passed": True,
                "reason_code": "",
                "screening": _screening_summary(screening),
                "rolling": rolling_summary,
                "full_training": full_training,
                "outputs": outputs,
                "elapsed_sec": time.time() - started,
            }

        result["_meta"] = build_metadata(
            source="pipeline_v1.process_ticker",
            ticker=ticker,
            fitness_mode="swing",
            data_start=(screening.get("data", {}) or {}).get("data_start") if screening else "",
            data_end=(screening.get("data", {}) or {}).get("data_end") if screening else "",
            validation={
                "final_stage": result.get("final_stage"),
                "final_status": result.get("final_status"),
                "reason_code": result.get("reason_code"),
                "screening": result.get("screening"),
                "rolling": result.get("rolling"),
                "full_training": result.get("full_training"),
            },
            feature_lag=FEATURE_LAG_METADATA,
            run_id=run_id,
        )
        final_path = out_dir / "final.json"
        write_json(final_path, result)
        result["outputs"]["final"] = str(final_path)
        return result
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        result = {
            "ticker": ticker,
            "run_id": run_id,
            "final_stage": "error",
            "final_status": "ERROR",
            "passed": False,
            "reason_code": "ERROR",
            "screening": _screening_summary(screening),
            "rolling": _stock_score_summary(rolling),
            "full_training": full_training,
            "outputs": outputs,
            "error": error,
            "elapsed_sec": time.time() - started,
        }
        result["_meta"] = build_metadata(
            source="pipeline_v1.process_ticker",
            ticker=ticker,
            fitness_mode="swing",
            validation={"final_status": "ERROR", "error": error},
            feature_lag=FEATURE_LAG_METADATA,
            run_id=run_id,
        )
        error_path = out_dir / "error.json"
        write_json(error_path, result)
        result["outputs"]["error"] = str(error_path)
        return result


__all__ = ["PIPELINE_ROOT", "process_ticker", "ticker_dir", "write_json"]
