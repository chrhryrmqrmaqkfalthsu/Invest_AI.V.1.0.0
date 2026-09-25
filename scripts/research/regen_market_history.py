#!/usr/bin/env python3
"""Safely regenerate the 7-year market history cache.

Safety contract:
- Fetch into memory using the existing market scoring helpers.
- Write only to data/_system/market_history.csv.regen_tmp first.
- Re-read the temporary CSV and evaluate all eight validation gates.
- Preserve the current target before replacement.
- Replace the target atomically with os.replace() only after every gate passes.
- Re-read and validate the final target.

The live engine/market/context.py file is imported but never modified, and
build_market_history()/get_market_history() are never called.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.market.context import (  # noqa: E402
    _fetch_index,
    _safe_float,
    _safe_pct_change,
    _score_from_trends,
)

SYSTEM_DIR = ROOT / "data/_system"
TARGET_PATH = SYSTEM_DIR / "market_history.csv"
TMP_PATH = SYSTEM_DIR / "market_history.csv.regen_tmp"
EMPTY_BACKUP_PATH = SYSTEM_DIR / "market_history.csv.empty_20260712_bak"
BEFORE_6Y_PATH = SYSTEM_DIR / "market_history.csv.before_6y"
REPORT_PATH = SYSTEM_DIR / "regen_validation_report.md"

PERIOD = "7y"
MIN_ROWS = 1700
MAX_START_DATE = pd.Timestamp("2019-08-31")
MIN_END_DATE = pd.Timestamp("2026-07-01")
SLEEP_SECONDS = 1.0

EXPECTED_COLUMNS = [
    "date",
    "score",
    "regime",
    "kospi_60d",
    "sp500_60d",
    "vix",
    "sector_tech",
    "sector_finance",
    "sector_energy",
    "sector_healthcare",
    "sector_consumer",
    "sector_industrials",
]
SECTOR_SYMBOLS = {
    "tech": "XLK",
    "finance": "XLF",
    "energy": "XLE",
    "healthcare": "XLV",
    "consumer": "XLY",
    "industrials": "XLI",
}
FETCH_SYMBOLS = ["^GSPC", "^VIX", *SECTOR_SYMBOLS.values()]

# Gate 8 defines "mostly small" before execution so the criterion cannot be
# relaxed after seeing the overlap result. The first ~60 rows of the old
# two-year build can legitimately differ because that build lacked a full
# 60-session lookback. The remaining majority should closely reproduce.
OVERLAP_MIN_DATES = 450
OVERLAP_SMALL_DIFF = 1.0
OVERLAP_MIN_SMALL_SHARE = 0.80
OVERLAP_MAX_MEDIAN_ABS_DIFF = 0.50
OVERLAP_MAX_MEAN_ABS_DIFF = 5.00


@dataclass(frozen=True)
class GateResult:
    number: int
    name: str
    passed: bool
    detail: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    index = pd.to_datetime(result.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert(None)
    result.index = pd.DatetimeIndex(index).normalize()
    result = result[~result.index.isna()]
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result


def fetch_all() -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    frames: dict[str, pd.DataFrame] = {}
    profiles: list[dict[str, Any]] = []
    for position, symbol in enumerate(FETCH_SYMBOLS):
        frame = _fetch_index(symbol, period=PERIOD)
        if frame is None or frame.empty:
            profiles.append(
                {
                    "symbol": symbol,
                    "success": False,
                    "rows": 0,
                    "first_date": None,
                    "last_date": None,
                }
            )
        else:
            frame = normalize_frame(frame)
            frames[symbol] = frame
            profiles.append(
                {
                    "symbol": symbol,
                    "success": True,
                    "rows": int(len(frame)),
                    "first_date": frame.index.min().strftime("%Y-%m-%d"),
                    "last_date": frame.index.max().strftime("%Y-%m-%d"),
                }
            )
        print(profiles[-1], flush=True)
        if position < len(FETCH_SYMBOLS) - 1:
            time.sleep(SLEEP_SECONDS)
    return frames, profiles


def build_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    missing = [symbol for symbol in ["^GSPC", "^VIX"] if symbol not in frames]
    if missing:
        raise RuntimeError(f"required index fetch failed: {missing}")

    sp500 = frames["^GSPC"]
    vix = frames["^VIX"]
    sectors = {
        name: frames.get(symbol)
        for name, symbol in SECTOR_SYMBOLS.items()
    }

    records: list[dict[str, Any]] = []
    for raw_date in sp500.index:
        current_date = pd.Timestamp(raw_date).normalize()
        sp500_slice = sp500.loc[:current_date, "Close"]
        if current_date > vix.index[-1]:
            vix_slice = vix["Close"]
        else:
            vix_slice = vix.loc[:current_date, "Close"]
        if len(vix_slice) == 0:
            continue

        sp500_60d = _safe_pct_change(sp500_slice, 60)
        vix_level = _safe_float(vix_slice.iloc[-1], 18.0)
        score, regime = _score_from_trends(sp500_60d, vix_level)
        record: dict[str, Any] = {
            "date": current_date,
            "score": score,
            "regime": regime,
            "kospi_60d": 0.0,
            "sp500_60d": sp500_60d,
            "vix": vix_level,
        }

        for sector_name, sector_frame in sectors.items():
            column = f"sector_{sector_name}"
            if sector_frame is None:
                record[column] = 50.0
                continue
            try:
                sector_slice = sector_frame.loc[:current_date, "Close"]
                if len(sector_slice) < 60:
                    record[column] = 50.0
                    continue
                trend = _safe_pct_change(sector_slice, 60)
                record[column] = float(max(0.0, min(100.0, 50.0 + trend * 5.0)))
            except Exception:
                record[column] = 50.0
        records.append(record)

    frame = pd.DataFrame(records, columns=EXPECTED_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    return frame


def overlap_metrics(candidate: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    before = pd.read_csv(BEFORE_6Y_PATH)
    before["date"] = pd.to_datetime(before["date"], errors="coerce").dt.normalize()
    compare = candidate[["date", "score"]].merge(
        before[["date", "score"]],
        on="date",
        how="inner",
        suffixes=("_regen", "_before_6y"),
        validate="one_to_one",
    )
    compare["abs_diff"] = (
        compare["score_regen"].astype(float)
        - compare["score_before_6y"].astype(float)
    ).abs()
    if compare.empty:
        metrics = {
            "overlap_dates": 0,
            "mean_abs_diff": math.inf,
            "median_abs_diff": math.inf,
            "p90_abs_diff": math.inf,
            "max_abs_diff": math.inf,
            "small_diff_share": 0.0,
        }
    else:
        metrics = {
            "overlap_dates": int(len(compare)),
            "mean_abs_diff": float(compare["abs_diff"].mean()),
            "median_abs_diff": float(compare["abs_diff"].median()),
            "p90_abs_diff": float(compare["abs_diff"].quantile(0.90)),
            "max_abs_diff": float(compare["abs_diff"].max()),
            "small_diff_share": float(
                (compare["abs_diff"] <= OVERLAP_SMALL_DIFF).mean()
            ),
        }
    return metrics, compare


def validate_candidate(frame: pd.DataFrame) -> tuple[list[GateResult], dict[str, Any], pd.DataFrame]:
    gates: list[GateResult] = []

    gate1 = not frame.empty
    gates.append(GateResult(1, "non_empty", gate1, f"rows={len(frame)}"))

    gate2 = len(frame) >= MIN_ROWS
    gates.append(GateResult(2, "minimum_rows", gate2, f"rows={len(frame)}, required>={MIN_ROWS}"))

    dates = pd.to_datetime(frame.get("date"), errors="coerce")
    first_date = dates.min() if len(dates) else pd.NaT
    last_date = dates.max() if len(dates) else pd.NaT
    gate3 = bool(
        pd.notna(first_date)
        and pd.notna(last_date)
        and first_date <= MAX_START_DATE
        and last_date >= MIN_END_DATE
    )
    gates.append(
        GateResult(
            3,
            "seven_year_date_coverage",
            gate3,
            f"first={first_date}, last={last_date}, required_first<={MAX_START_DATE.date()}, required_last>={MIN_END_DATE.date()}",
        )
    )

    actual_columns = frame.columns.tolist()
    gate4 = actual_columns == EXPECTED_COLUMNS
    gates.append(
        GateResult(
            4,
            "exact_schema_and_order",
            gate4,
            f"columns={actual_columns}",
        )
    )

    if "score" in frame.columns:
        score = pd.to_numeric(frame["score"], errors="coerce")
        score_nulls = int(score.isna().sum())
        out_of_range = int((~score.between(0.0, 100.0, inclusive="both") & score.notna()).sum())
    else:
        score_nulls = len(frame)
        out_of_range = len(frame)
    gate5 = score_nulls == 0 and out_of_range == 0
    gates.append(
        GateResult(
            5,
            "score_complete_and_bounded",
            gate5,
            f"nulls={score_nulls}, out_of_range={out_of_range}",
        )
    )

    duplicate_dates = int(dates.duplicated().sum()) if len(dates) else 0
    monotonic = bool(dates.is_monotonic_increasing) if len(dates) else False
    date_nulls = int(dates.isna().sum()) if len(dates) else 0
    gate6 = duplicate_dates == 0 and date_nulls == 0 and monotonic
    gates.append(
        GateResult(
            6,
            "unique_sorted_dates",
            gate6,
            f"duplicates={duplicate_dates}, date_nulls={date_nulls}, monotonic={monotonic}",
        )
    )

    sector_details: list[str] = []
    sector_gate_flags: list[bool] = []
    for sector_name in SECTOR_SYMBOLS:
        column = f"sector_{sector_name}"
        if column not in frame.columns:
            sector_gate_flags.append(False)
            sector_details.append(f"{column}:missing")
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        unique_count = int(values.nunique(dropna=True))
        all_neutral = bool(values.notna().all() and (values == 50.0).all())
        sector_gate_flags.append(unique_count > 1 and not all_neutral)
        sector_details.append(
            f"{column}:unique={unique_count},all_50={all_neutral}"
        )
    gate7 = all(sector_gate_flags)
    gates.append(
        GateResult(7, "sector_series_not_all_neutral", gate7, "; ".join(sector_details))
    )

    metrics, compare = overlap_metrics(frame)
    gate8 = bool(
        metrics["overlap_dates"] >= OVERLAP_MIN_DATES
        and metrics["small_diff_share"] >= OVERLAP_MIN_SMALL_SHARE
        and metrics["median_abs_diff"] <= OVERLAP_MAX_MEDIAN_ABS_DIFF
        and metrics["mean_abs_diff"] <= OVERLAP_MAX_MEAN_ABS_DIFF
    )
    gates.append(
        GateResult(
            8,
            "before_6y_score_reproducibility",
            gate8,
            (
                f"overlap={metrics['overlap_dates']}, "
                f"mean_abs_diff={metrics['mean_abs_diff']:.6f}, "
                f"median_abs_diff={metrics['median_abs_diff']:.6f}, "
                f"p90_abs_diff={metrics['p90_abs_diff']:.6f}, "
                f"max_abs_diff={metrics['max_abs_diff']:.6f}, "
                f"share_abs_diff<={OVERLAP_SMALL_DIFF}={metrics['small_diff_share']:.4%}; "
                f"required overlap>={OVERLAP_MIN_DATES}, share>={OVERLAP_MIN_SMALL_SHARE:.0%}, "
                f"median<={OVERLAP_MAX_MEDIAN_ABS_DIFF}, mean<={OVERLAP_MAX_MEAN_ABS_DIFF}"
            ),
        )
    )
    return gates, metrics, compare


def markdown_table(rows: list[list[str]], headers: list[str]) -> list[str]:
    output = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    output.extend("| " + " | ".join(row) + " |" for row in rows)
    return output


def write_report(
    *,
    fetch_profiles: list[dict[str, Any]],
    gates: list[GateResult],
    overlap: dict[str, Any],
    compare: pd.DataFrame,
    replacement_status: str,
    target_sha_before: str,
    final_info: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    lines = [
        "# market_history 재생성 검증 리포트",
        "",
        f"- replacement_status: `{replacement_status}`",
        f"- target_sha_before: `{target_sha_before}`",
        f"- tmp_path: `{TMP_PATH.relative_to(ROOT)}`",
        f"- target_path: `{TARGET_PATH.relative_to(ROOT)}`",
        "",
        "## Fetch 결과",
        "",
    ]
    fetch_rows = [
        [
            str(item["symbol"]),
            "PASS" if item["success"] else "FAIL",
            str(item["rows"]),
            str(item["first_date"]),
            str(item["last_date"]),
        ]
        for item in fetch_profiles
    ]
    lines.extend(markdown_table(fetch_rows, ["symbol", "success", "rows", "first", "last"]))
    lines.extend(["", "## 8개 게이트", ""])
    gate_rows = [
        [str(gate.number), gate.name, "PASS" if gate.passed else "FAIL", gate.detail.replace("|", "\\|")]
        for gate in gates
    ]
    lines.extend(markdown_table(gate_rows, ["gate", "name", "result", "detail"]))
    lines.extend(
        [
            "",
            "## .before_6y 겹침 요약",
            "",
            f"- overlap_dates: `{overlap.get('overlap_dates')}`",
            f"- mean_abs_diff: `{overlap.get('mean_abs_diff')}`",
            f"- median_abs_diff: `{overlap.get('median_abs_diff')}`",
            f"- p90_abs_diff: `{overlap.get('p90_abs_diff')}`",
            f"- max_abs_diff: `{overlap.get('max_abs_diff')}`",
            f"- share_abs_diff_le_{OVERLAP_SMALL_DIFF}: `{overlap.get('small_diff_share')}`",
            "",
            "## 겹치는 구간 score 대조 일부",
            "",
        ]
    )
    if compare.empty:
        lines.append("겹치는 날짜 없음.")
    else:
        sample = compare.nlargest(10, "abs_diff")
        compare_rows = [
            [
                pd.Timestamp(row.date).strftime("%Y-%m-%d"),
                f"{float(row.score_regen):.6f}",
                f"{float(row.score_before_6y):.6f}",
                f"{float(row.abs_diff):.6f}",
            ]
            for row in sample.itertuples(index=False)
        ]
        lines.extend(markdown_table(compare_rows, ["date", "regen", "before_6y", "abs_diff"]))
    if final_info is not None:
        lines.extend(
            [
                "",
                "## 최종 파일",
                "",
                f"- rows: `{final_info['rows']}`",
                f"- first_date: `{final_info['first_date']}`",
                f"- last_date: `{final_info['last_date']}`",
                f"- sha256: `{final_info['sha256']}`",
                f"- empty_backup: `{EMPTY_BACKUP_PATH.relative_to(ROOT)}`",
                f"- empty_backup_sha256: `{final_info['empty_backup_sha256']}`",
            ]
        )
    if error:
        lines.extend(["", "## 오류", "", f"```text\n{error}\n```"])
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_final_file() -> dict[str, Any]:
    final = pd.read_csv(TARGET_PATH)
    final["date"] = pd.to_datetime(final["date"], errors="coerce").dt.normalize()
    gates, _, _ = validate_candidate(final)
    failed = [gate for gate in gates if not gate.passed]
    if failed:
        details = "; ".join(f"gate{gate.number}:{gate.detail}" for gate in failed)
        raise RuntimeError(f"final file validation failed: {details}")
    return {
        "rows": int(len(final)),
        "first_date": final["date"].min().strftime("%Y-%m-%d"),
        "last_date": final["date"].max().strftime("%Y-%m-%d"),
        "sha256": sha256(TARGET_PATH),
        "empty_backup_sha256": sha256(EMPTY_BACKUP_PATH),
    }


def main() -> int:
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    if not TARGET_PATH.exists():
        raise RuntimeError(f"target missing: {TARGET_PATH}")
    if not BEFORE_6Y_PATH.exists():
        raise RuntimeError(f"comparison source missing: {BEFORE_6Y_PATH}")
    if TMP_PATH.exists():
        raise RuntimeError(f"temporary path already exists: {TMP_PATH}")
    if EMPTY_BACKUP_PATH.exists():
        raise RuntimeError(f"empty backup path already exists: {EMPTY_BACKUP_PATH}")

    target_sha_before = sha256(TARGET_PATH)
    target_stat_before = TARGET_PATH.stat()
    fetch_profiles: list[dict[str, Any]] = []
    gates: list[GateResult] = []
    overlap: dict[str, Any] = {}
    compare = pd.DataFrame()

    try:
        frames, fetch_profiles = fetch_all()
        candidate = build_frame(frames)
        candidate.to_csv(TMP_PATH, index=False, date_format="%Y-%m-%d")
        with TMP_PATH.open("rb") as handle:
            os.fsync(handle.fileno())

        reread = pd.read_csv(TMP_PATH)
        reread["date"] = pd.to_datetime(reread["date"], errors="coerce").dt.normalize()
        gates, overlap, compare = validate_candidate(reread)
        all_passed = all(gate.passed for gate in gates)
        write_report(
            fetch_profiles=fetch_profiles,
            gates=gates,
            overlap=overlap,
            compare=compare,
            replacement_status="VALIDATED_NOT_REPLACED" if all_passed else "VALIDATION_FAILED_TARGET_UNCHANGED",
            target_sha_before=target_sha_before,
        )
        if not all_passed:
            if sha256(TARGET_PATH) != target_sha_before:
                raise RuntimeError("target changed despite validation failure")
            print("VALIDATION_FAILED", flush=True)
            return 2

        # Preserve the exact pre-replacement target before touching its path.
        shutil.copy2(TARGET_PATH, EMPTY_BACKUP_PATH)
        if sha256(EMPTY_BACKUP_PATH) != target_sha_before:
            raise RuntimeError("pre-replacement backup hash mismatch")

        # Ensure no process changed the target between validation and replace.
        current_stat = TARGET_PATH.stat()
        if (
            sha256(TARGET_PATH) != target_sha_before
            or current_stat.st_mtime_ns != target_stat_before.st_mtime_ns
            or current_stat.st_size != target_stat_before.st_size
        ):
            raise RuntimeError("target changed before atomic replacement")

        os.replace(TMP_PATH, TARGET_PATH)
        directory_fd = os.open(SYSTEM_DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        try:
            final_info = validate_final_file()
        except Exception:
            rollback_tmp = SYSTEM_DIR / "market_history.csv.rollback_tmp"
            if rollback_tmp.exists():
                rollback_tmp.unlink()
            shutil.copy2(EMPTY_BACKUP_PATH, rollback_tmp)
            os.replace(rollback_tmp, TARGET_PATH)
            raise

        write_report(
            fetch_profiles=fetch_profiles,
            gates=gates,
            overlap=overlap,
            compare=compare,
            replacement_status="ATOMIC_REPLACE_SUCCESS",
            target_sha_before=target_sha_before,
            final_info=final_info,
        )
        print(
            {
                "replacement": "SUCCESS",
                "rows": final_info["rows"],
                "first_date": final_info["first_date"],
                "last_date": final_info["last_date"],
                "sha256": final_info["sha256"],
            },
            flush=True,
        )
        return 0
    except Exception as exc:
        write_report(
            fetch_profiles=fetch_profiles,
            gates=gates,
            overlap=overlap,
            compare=compare,
            replacement_status="ERROR_TARGET_UNCHANGED_OR_ROLLED_BACK",
            target_sha_before=target_sha_before,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
