#!/usr/bin/env python3
"""단일 실행용 AAP 상세 qualify launcher.

상세 runner의 신규/빈 output gate를 보존하기 위해 stdout/stderr 로그는 먼저
/tmp에 기록하고, 실행 종료 뒤 output 디렉터리의 run.log로 복사한다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
RUNNER_PATH = HERE.with_name("run_stage3_aap_detail.py")


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_stage3_aap_detail_runner_launch", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch one AAP detailed qualify run")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", type=int, default=2026071301)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = _load_runner()
    runner._apply_detail_config()
    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"output directory must be new or empty: {out_dir}")

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    temp_path: Path | None = None
    exit_code = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="stage3_aap_detail_",
            suffix=".log",
            delete=False,
        ) as log_handle:
            temp_path = Path(log_handle.name)
            sys.stdout = _Tee(original_stdout, log_handle)
            sys.stderr = _Tee(original_stderr, log_handle)
            try:
                runner.run("AAP", out_dir, int(args.seed_base))
            except Exception as exc:
                failure = {
                    "event": "stage3_aap_detail_failed",
                    "ticker": "AAP",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                print(json.dumps(failure, ensure_ascii=False), flush=True)
                out_dir.mkdir(parents=True, exist_ok=True)
                runner.light._write_json(out_dir / "failure.json", failure)
                exit_code = 2
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        if temp_path is not None and temp_path.exists():
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run.log").write_text(temp_path.read_text(encoding="utf-8"), encoding="utf-8")
            temp_path.unlink(missing_ok=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
