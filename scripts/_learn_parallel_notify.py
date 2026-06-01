#!/usr/bin/env python3
"""병렬 개별주 학습 실행/감시 + 텔레그램 진행/완료 알림.

용도
----
1) 새 병렬 학습 실행:
   venv/bin/python scripts/_learn_parallel_notify.py AAPL MSFT NVDA JPM KO XOM \
     --title "TEST 제거 + spread fitness 기준선" \
     --fitness-mode spread

2) 이미 실행 중인 병렬 학습 감시만 붙이기:
   venv/bin/python scripts/_learn_parallel_notify.py AAPL MSFT NVDA JPM KO XOM \
     --watch-existing --job-dir data/_system/clean_spread_trainonly \
     --title "TEST 제거 + spread fitness 기준선"
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.telegram.notifier import TelegramNotifier

GEN_RE = re.compile(r"Gen\s+(\d+): best=([+-]?\d+(?:\.\d+)?), avg=([+-]?\d+(?:\.\d+)?)")
TRAIN_RE = re.compile(
    r"\[TRAIN\].*fitness=([+-]?\d+(?:\.\d+)?), trades=(\d+), "
    r"win=([+-]?\d+(?:\.\d+)?)%, expectancy=([+-]?\d+(?:\.\d+)?)%"
)
TEST_RE = re.compile(
    r"\[TEST\].*fitness=([+-]?\d+(?:\.\d+)?), trades=(\d+), "
    r"win=([+-]?\d+(?:\.\d+)?)%, expectancy=([+-]?\d+(?:\.\d+)?)%"
)
LEARN_ONE_RE = re.compile(r"\[(TRAIN|TEST)\]\s+fit=([+-]?\d+(?:\.\d+)?)\s+거래=(\d+)\s+승률=([+-]?\d+(?:\.\d+)?)%\s+exp=([+-]?\d+(?:\.\d+)?)%")
ERROR_RE = re.compile(r"Traceback|ERROR|Exception|오류|실패", re.IGNORECASE)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(errors="ignore")


def _parse_done(done_path: Path) -> dict[str, str]:
    done: dict[str, str] = {}
    text = _read_text(done_path)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "DONE":
            done[parts[1]] = parts[2]
    return done


def _latest_status(ticker: str, log_path: Path, done: dict[str, str], generations: int) -> dict:
    text = _read_text(log_path)
    gens = GEN_RE.findall(text)
    train_matches = TRAIN_RE.findall(text)
    test_matches = TEST_RE.findall(text)

    # _learn_one.py 최종 출력 형식도 지원
    learn_one_matches = LEARN_ONE_RE.findall(text)
    if learn_one_matches:
        train_lo = [m for m in learn_one_matches if m[0] == "TRAIN"]
        test_lo = [m for m in learn_one_matches if m[0] == "TEST"]
        if train_lo:
            m = train_lo[-1]
            train_matches.append((m[1], m[2], m[3], m[4]))
        if test_lo:
            m = test_lo[-1]
            test_matches.append((m[1], m[2], m[3], m[4]))

    has_error = bool(ERROR_RE.search(text))
    if ticker in done:
        return {
            "ticker": ticker,
            "state": "done" if done[ticker] == "0" and not has_error else "error",
            "rc": done[ticker],
            "gen": generations,
            "pct": 100,
            "best": None,
            "avg": None,
            "train": train_matches[-1] if train_matches else None,
            "test": test_matches[-1] if test_matches else None,
            "error": has_error,
        }

    if gens:
        gen, best, avg = gens[-1]
        gen_i = int(gen)
        return {
            "ticker": ticker,
            "state": "running",
            "rc": None,
            "gen": gen_i,
            "pct": min(99, int(gen_i / max(1, generations) * 100)),
            "best": float(best),
            "avg": float(avg),
            "train": train_matches[-1] if train_matches else None,
            "test": test_matches[-1] if test_matches else None,
            "error": has_error,
        }

    return {
        "ticker": ticker,
        "state": "starting" if not has_error else "error",
        "rc": None,
        "gen": 0,
        "pct": 0,
        "best": None,
        "avg": None,
        "train": train_matches[-1] if train_matches else None,
        "test": test_matches[-1] if test_matches else None,
        "error": has_error,
    }


def _format_metric(m: Optional[tuple]) -> str:
    if not m:
        return "결과 없음"
    fit, trades, win, exp = m
    return f"fit {float(fit):+.2f}, 거래 {int(trades)}회, exp {float(exp):+.2f}%"


def _build_message(
    title: str,
    tickers: list[str],
    job_dir: Path,
    generations: int,
    started_at: datetime,
    fitness_mode: str,
    final: bool = False,
) -> str:
    done = _parse_done(job_dir / "done.log")
    logs_dir = job_dir / "logs"
    rows = [_latest_status(t, logs_dir / f"{t}.log", done, generations) for t in tickers]
    elapsed = int((datetime.now() - started_at).total_seconds())
    done_n = sum(1 for r in rows if r["state"] == "done")
    err_n = sum(1 for r in rows if r["state"] == "error")
    running_n = len(rows) - done_n - err_n

    header_icon = "✅" if final and err_n == 0 else ("⚠️" if err_n else "🧬")
    lines = [
        f"{header_icon} *병렬 개별주 학습 {'완료' if final else '진행'}*",
        f"실험: {title}",
        f"fitness_mode: `{fitness_mode}`",
        f"경과: {elapsed // 3600}h {(elapsed % 3600) // 60}m {elapsed % 60}s",
        f"상태: 완료 {done_n}/{len(rows)}, 진행 {running_n}, 오류 {err_n}",
        "",
    ]

    for r in rows:
        t = r["ticker"]
        if r["state"] == "done":
            lines.append(f"✅ *{t}* 완료")
            lines.append(f"  TRAIN: {_format_metric(r['train'])}")
            lines.append(f"  TEST : {_format_metric(r['test'])}")
        elif r["state"] == "error":
            lines.append(f"❌ *{t}* 오류/비정상 종료 rc={r.get('rc')}")
            if r.get("train") or r.get("test"):
                lines.append(f"  TRAIN: {_format_metric(r['train'])}")
                lines.append(f"  TEST : {_format_metric(r['test'])}")
        elif r["state"] == "running":
            lines.append(
                f"⏳ *{t}* Gen {r['gen']}/{generations} ({r['pct']}%) "
                f"best {r['best']:.2f}, avg {r['avg']:.2f}"
            )
        else:
            lines.append(f"⏳ *{t}* 초기화/평가 중")

    lines.append("")
    if final:
        lines.append("📌 다음 단계: 새 dump 기준으로 거래수/expectancy 표 비교")
    else:
        lines.append("🔒 GA selection: TRAIN only / TEST: 완료 후 검증 전용")
    return "\n".join(lines)


def _backup_existing_dumps(tickers: list[str], backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    for ticker in tickers:
        src = ROOT / "data" / "_system" / f"ga_population_dump_{ticker}.json"
        if src.exists():
            shutil.copy2(src, backup_dir / src.name)


def _copy_final_dumps(tickers: list[str], dump_dir: Path) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    for ticker in tickers:
        src = ROOT / "data" / "_system" / f"ga_population_dump_{ticker}.json"
        if src.exists():
            shutil.copy2(src, dump_dir / src.name)


def _launch_jobs(tickers: list[str], job_dir: Path, parallel: int, fitness_mode: str) -> list[subprocess.Popen]:
    logs_dir = job_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["FITNESS_MODE"] = fitness_mode
    procs: list[subprocess.Popen] = []
    for ticker in tickers:
        log_f = (logs_dir / f"{ticker}.log").open("w")
        p = subprocess.Popen(
            [str(ROOT / "venv" / "bin" / "python"), "scripts/_learn_one.py", ticker],
            cwd=str(ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        procs.append(p)
        if len(procs) >= parallel:
            break
    return procs


def _run_queue(tickers: list[str], job_dir: Path, parallel: int, fitness_mode: str = "spread") -> None:
    """간단한 병렬 큐 실행. done.log를 기존 xargs 형식으로 남긴다."""
    pending = list(tickers)
    running: dict[str, tuple[subprocess.Popen, object]] = {}
    logs_dir = job_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    done_path = job_dir / "done.log"
    done_path.write_text("")
    env = os.environ.copy()
    env["FITNESS_MODE"] = fitness_mode

    while pending or running:
        while pending and len(running) < parallel:
            ticker = pending.pop(0)
            log_f = (logs_dir / f"{ticker}.log").open("w")
            proc = subprocess.Popen(
                [str(ROOT / "venv" / "bin" / "python"), "scripts/_learn_one.py", ticker],
                cwd=str(ROOT),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            running[ticker] = (proc, log_f)
        time.sleep(2)
        for ticker, (proc, log_f) in list(running.items()):
            rc = proc.poll()
            if rc is not None:
                log_f.close()
                with done_path.open("a") as f:
                    f.write(f"DONE {ticker} {rc}\n")
                del running[ticker]


def main() -> int:
    ap = argparse.ArgumentParser(description="병렬 학습 텔레그램 진행/완료 알림")
    ap.add_argument("tickers", nargs="+", help="학습/감시할 티커 목록")
    ap.add_argument("--title", default="병렬 개별주 학습", help="텔레그램에 표시할 실험 이름")
    ap.add_argument("--job-dir", default="data/_system/parallel_learn_notify/latest", help="로그/done 저장 디렉터리")
    ap.add_argument("--watch-existing", action="store_true", help="이미 실행 중인 로그만 감시")
    ap.add_argument("--parallel", type=int, default=6, help="동시 실행 개수")
    ap.add_argument("--interval", type=int, default=90, help="텔레그램 갱신 간격 초")
    ap.add_argument("--generations", type=int, default=50, help="진행률 계산용 GA 세대 수")
    ap.add_argument("--fitness-mode", default=os.environ.get("FITNESS_MODE", "spread"), help="자식 학습 프로세스에 전달할 FITNESS_MODE")
    ap.add_argument("--backup-dumps", action="store_true", help="시작 전 기존 dump 백업")
    args = ap.parse_args()

    os.chdir(ROOT)
    fitness_mode = (args.fitness_mode or "spread").strip().lower()
    os.environ["FITNESS_MODE"] = fitness_mode
    job_dir = (ROOT / args.job_dir).resolve() if not Path(args.job_dir).is_absolute() else Path(args.job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "logs").mkdir(parents=True, exist_ok=True)
    (job_dir / "tickers.txt").write_text("\n".join(args.tickers) + "\n")
    (job_dir / "fitness_mode.txt").write_text(fitness_mode + "\n")
    started_at = datetime.now()
    notifier = TelegramNotifier()

    if args.backup_dumps and not args.watch_existing:
        backup_dir = job_dir / f"backup_before_{started_at.strftime('%Y%m%d_%H%M%S')}"
        _backup_existing_dumps(args.tickers, backup_dir)
        (job_dir / "backup_path.txt").write_text(str(backup_dir) + "\n")

    msg_id = notifier.send_progress(_build_message(args.title, args.tickers, job_dir, args.generations, started_at, fitness_mode))

    runner: Optional[subprocess.Popen] = None
    if not args.watch_existing:
        # 같은 프로세스에서 큐 실행하면 모니터 루프가 막히므로 하위 프로세스로 자기 자신의 큐 함수 대신 bash-free 파이썬 명령을 띄운다.
        runner_code = (
            "import os, sys; "
            "from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT)!r}); "
            "from scripts._learn_parallel_notify import _run_queue, _copy_final_dumps; "
            f"tickers={args.tickers!r}; job_dir=Path({str(job_dir)!r}); "
            f"fitness_mode={fitness_mode!r}; os.environ['FITNESS_MODE']=fitness_mode; "
            f"_run_queue(tickers, job_dir, {args.parallel}, fitness_mode); "
            "_copy_final_dumps(tickers, job_dir/'dumps')"
        )
        env = os.environ.copy()
        env["FITNESS_MODE"] = fitness_mode
        runner = subprocess.Popen([str(ROOT / "venv" / "bin" / "python"), "-c", runner_code], cwd=str(ROOT), env=env)

    try:
        while True:
            done = _parse_done(job_dir / "done.log")
            all_done = all(t in done for t in args.tickers)
            if all_done:
                _copy_final_dumps(args.tickers, job_dir / "dumps")
                notifier.edit_message(
                    msg_id,
                    _build_message(args.title, args.tickers, job_dir, args.generations, started_at, fitness_mode, final=True),
                    parse_mode="Markdown",
                )
                break
            notifier.edit_message(
                msg_id,
                _build_message(args.title, args.tickers, job_dir, args.generations, started_at, fitness_mode),
                parse_mode="Markdown",
            )
            time.sleep(max(10, args.interval))
    except KeyboardInterrupt:
        notifier.send("⚠️ 병렬 학습 모니터가 중단됐습니다. 학습 프로세스는 별도로 확인하세요.")
        return 130
    finally:
        if runner is not None and runner.poll() is None and args.watch_existing:
            runner.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
