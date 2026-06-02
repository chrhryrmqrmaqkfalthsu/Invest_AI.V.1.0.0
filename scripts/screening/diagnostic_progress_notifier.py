#!/usr/bin/env python3
"""bulk swing diagnostic 진행상황 텔레그램 알림 워처.

- bulk_swing_diagnostic.py 본체를 수정하지 않고 _progress.json / _summary.csv / results/*.json만 읽는다.
- data/symbols, ga_population_dump, _progress.json에는 절대 쓰지 않는다.
- 기본 2시간마다 텔레그램으로 진행률을 보낸다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.telegram.notifier import TelegramNotifier  # noqa: E402

DEFAULT_PROGRESS_FILE = ROOT / "data" / "_system" / "bulk_diagnostic" / "swing" / "_progress.json"
DEFAULT_INTERVAL_SEC = 7200


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}분"
    hours = minutes / 60.0
    if hours < 48:
        return f"{hours:.1f}시간"
    days = hours / 24.0
    return f"{days:.1f}일"


def _read_summary_counts(base_dir: Path) -> tuple[Counter, list[float]]:
    """_summary.csv 우선, 없으면 results/*.json에서 상태/elapsed 집계."""
    counts: Counter = Counter()
    elapsed: list[float] = []
    summary_path = base_dir / "_summary.csv"
    if summary_path.exists():
        try:
            with summary_path.open("r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    status = (row.get("status") or "UNKNOWN").strip() or "UNKNOWN"
                    counts[status] += 1
                    e = _to_float(row.get("elapsed_sec"), 0.0)
                    if e > 0:
                        elapsed.append(e)
            return counts, elapsed
        except Exception:
            counts.clear()
            elapsed.clear()

    result_dir = base_dir / "results"
    if result_dir.exists():
        for path in result_dir.glob("*.json"):
            data = _load_json(path, {})
            status = str(data.get("status") or "UNKNOWN")
            counts[status] += 1
            e = _to_float(data.get("elapsed_sec"), 0.0)
            if e > 0:
                elapsed.append(e)
    return counts, elapsed


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _read_pid(path: Path) -> int | None:
    try:
        if not path.exists():
            return None
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _find_related_processes() -> list[str]:
    """full_screening/bulk diagnostic 관련 프로세스 표시용. 실패해도 빈 리스트."""
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,etime,stat,pcpu,pmem,cmd"],
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    rows: list[str] = []
    for line in out.splitlines():
        if "diagnostic_progress_notifier.py" in line:
            continue
        if "bulk_swing_diagnostic.py" in line or "run_full_screening.sh" in line:
            rows.append(" ".join(line.split()))
    return rows[:8]


def _is_full_screening_alive(base_dir: Path) -> tuple[bool, list[str]]:
    pid_paths = [
        ROOT / "data" / "_system" / "full_screening.pid",
        ROOT / "data" / "_system" / "bulk_swing_diagnostic.pid",
    ]
    alive = False
    notes: list[str] = []
    for p in pid_paths:
        pid = _read_pid(p)
        if pid is None:
            continue
        ok = _pid_alive(pid)
        notes.append(f"{p.name}:{pid}:{'alive' if ok else 'dead'}")
        alive = alive or ok

    related = _find_related_processes()
    if related:
        alive = True
        notes.extend(related[:3])
    return alive, notes


def _latest_failed(failed: dict[str, Any], limit: int = 5) -> list[str]:
    if not isinstance(failed, dict) or not failed:
        return []
    items = list(failed.items())[-limit:]
    out = []
    for ticker, reason in items:
        txt = str(reason)
        if len(txt) > 120:
            txt = txt[:117] + "..."
        out.append(f"{ticker}: {txt}")
    return out


def build_message(progress_file: Path, prev_finished: int | None = None) -> tuple[str, int, bool]:
    progress = _load_json(progress_file, {})
    base_dir = progress_file.parent

    if not progress:
        msg = (
            "⚠️ bulk diagnostic 진행 파일 없음\n"
            f"progress_file: {progress_file}\n"
            f"시각: {_now_iso()}"
        )
        return msg, 0, False

    completed = progress.get("completed") or {}
    failed = progress.get("failed") or {}
    running = progress.get("running") or []
    completed_n = len(completed) if isinstance(completed, dict) else 0
    failed_n = len(failed) if isinstance(failed, dict) else 0
    running_n = len(running) if isinstance(running, list) else 0
    finished_n = completed_n + failed_n

    total = int(progress.get("limit") or 0)
    if total <= 0:
        total = max(finished_n + running_n, 0)
    pct = (finished_n / total * 100.0) if total else 0.0
    new_done = 0 if prev_finished is None else max(0, finished_n - prev_finished)

    counts, elapsed_list = _read_summary_counts(base_dir)
    avg_elapsed = (sum(elapsed_list) / len(elapsed_list)) if elapsed_list else None
    parallel = int(progress.get("parallel") or 1)
    remaining = max(0, total - finished_n)
    eta = None
    if avg_elapsed and parallel > 0:
        eta = remaining * avg_elapsed / parallel

    alive, proc_notes = _is_full_screening_alive(base_dir)
    complete = bool(total and finished_n >= total and running_n == 0)
    interrupted = (not alive) and (not complete) and bool(total)

    status_line = "✅ 완료" if complete else ("⚠️ 작업 중단됨" if interrupted else "🏃 진행 중")
    count_line = (
        f"완료 {finished_n:,} / {total:,} ({pct:.1f}%)\n"
        f"  성공 {completed_n:,}, 실패 {failed_n:,}, 실행중 {running_n:,}, 신규완료 +{new_done:,}"
    )

    status_counts = " / ".join(
        f"{k}:{counts.get(k, 0)}" for k in ["POS", "UNCERTAIN", "NEG", "ERROR"]
    )
    if not status_counts.strip():
        status_counts = "집계 없음"

    timing = (
        f"평균/종목: {_fmt_duration(avg_elapsed)}\n"
        f"예상 잔여: {_fmt_duration(eta)}"
    )

    failed_lines = _latest_failed(failed)
    failed_text = "없음" if not failed_lines else "\n".join(f"  - {x}" for x in failed_lines)
    proc_text = "없음" if not proc_notes else "\n".join(f"  - {x}" for x in proc_notes[:5])

    msg = (
        "📡 bulk swing diagnostic 진행 알림\n"
        f"상태: {status_line}\n"
        f"시각: {_now_iso()}\n\n"
        f"{count_line}\n"
        f"상태 카운트: {status_counts}\n\n"
        f"{timing}\n\n"
        f"최근 실패/타임아웃:\n{failed_text}\n\n"
        f"프로세스:\n{proc_text}\n"
        f"progress updated_at: {progress.get('updated_at', '?')}"
    )
    return msg[:3900], finished_n, complete


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="bulk diagnostic progress Telegram notifier")
    p.add_argument("--interval-sec", type=int, default=DEFAULT_INTERVAL_SEC)
    p.add_argument("--progress-file", default=str(DEFAULT_PROGRESS_FILE))
    p.add_argument("--once", action="store_true", help="1회만 전송하고 종료")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    progress_file = Path(args.progress_file)
    if not progress_file.is_absolute():
        progress_file = ROOT / progress_file

    notifier = TelegramNotifier()
    prev_finished: int | None = None

    if args.once:
        msg, _, _ = build_message(progress_file, prev_finished=None)
        ok = notifier.send(msg)
        print(f"telegram_sent={ok}")
        print(msg)
        return 0 if ok else 1

    if args.interval_sec <= 0:
        raise SystemExit("--interval-sec must be positive")

    while True:
        time.sleep(args.interval_sec)
        msg, finished, complete = build_message(progress_file, prev_finished=prev_finished)
        prev_finished = finished
        ok = notifier.send(msg)
        print(f"{_now_iso()} telegram_sent={ok} finished={finished} complete={complete}", flush=True)
        if complete:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
