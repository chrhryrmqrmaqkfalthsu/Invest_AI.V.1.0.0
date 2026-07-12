from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
REPLAY_DIR = ROOT / "data/_system/analysis/entry_filter_2d3pct_replay_20260712"
LOG_DIR = ROOT / "data/_system/analysis/entry_filter_2d3pct_20260712"
UNIVERSE_PATH = REPLAY_DIR / "replay_signal_universe.csv"
LOG_DATASET_PATH = LOG_DIR / "signal_dataset.csv"
PID = 494330
PRE_HEAD = "c00e80cbaf2a10e4713fcb1e20feee19e3b8e566"
BACKUP_TAG = "backup_continuous_signal_inclusion_check_20260712_pre"


def rel(path: Path | str) -> str:
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT.resolve()))
    except Exception:
        return str(p)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            encoded: dict[str, Any] = {}
            for key in fields:
                value = row.get(key)
                if isinstance(value, (dict, list, tuple, set)):
                    encoded[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
                elif isinstance(value, pd.Timestamp):
                    encoded[key] = value.strftime("%Y-%m-%d")
                else:
                    encoded[key] = value
            writer.writerow(encoded)


def parse_date(value: Any) -> pd.Timestamp | None:
    text = str(value or "")[:10]
    if not text or text.lower() == "nan":
        return None
    try:
        return pd.Timestamp(text).normalize()
    except Exception:
        return None


def live_paths() -> list[Path]:
    paths: list[Path] = []
    for base in [ROOT / "engine/live", ROOT / "scripts/live", ROOT / "data/_system/ops"]:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if ".bak." in path.name:
                continue
            paths.append(path)
    for path in [ROOT / "scripts/run_live.py", ROOT / "api_server.py", ROOT / "data/_system/live_auto_config.json"]:
        if path.exists():
            paths.append(path)
    return sorted(set(paths))


def live_hashes() -> dict[str, str]:
    return {rel(path): sha256(path) for path in live_paths()}


def process_snapshot(pid: int = PID) -> dict[str, str]:
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "pid=,lstart=,cmd="],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    text = proc.stdout.strip()
    return {"pid": str(pid), "running": str(bool(text)).lower(), "identity": text}


def load_current_candidates() -> list[dict[str, Any]]:
    from engine.live.elite_shadow_report import build_elite_shadow_report

    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    rows = list(report.get("candidates") or [])
    rows.sort(key=lambda row: str(row.get("candidate_id") or ""))
    return rows


def load_session_maps(candidates: list[dict[str, Any]]) -> dict[str, dict[pd.Timestamp, int]]:
    from engine.central.signal_collector import CacheOnlyDataProvider

    provider = CacheOnlyDataProvider(
        cache_roots=[ROOT / "data/_system/research", ROOT / "exp_batch_stage123_2009_20260616_full"],
        recompute_indicators=True,
    )
    out: dict[str, dict[pd.Timestamp, int]] = {}
    for ticker in sorted({str(row.get("ticker") or "").upper() for row in candidates}):
        df = provider.load_price_df(ticker)
        index = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))
        index = index[~index.isna()].normalize().drop_duplicates().sort_values()
        out[ticker] = {pd.Timestamp(day): int(i) for i, day in enumerate(index)}
    return out


def validate_csv(path: Path) -> dict[str, Any]:
    parse_errors = 0
    row_count = 0
    column_count = 0
    error = ""
    try:
        with path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.reader(fp, strict=True)
            header = next(reader)
            column_count = len(header)
            for row in reader:
                row_count += 1
                if len(row) != column_count:
                    parse_errors += 1
    except Exception as exc:
        parse_errors += 1
        error = repr(exc)
    return {
        "path": rel(path),
        "row_count": row_count,
        "column_count": column_count,
        "parse_error_count": parse_errors,
        "status": "PASS" if parse_errors == 0 else "FAIL",
        "error": error,
    }
