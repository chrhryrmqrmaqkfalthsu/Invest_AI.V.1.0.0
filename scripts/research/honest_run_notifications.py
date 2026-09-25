"""Telegram notification helpers for long honest research runs.

This module reuses the existing TelegramNotifier environment configuration.
Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID or transient network failures must
never stop a research run.

Progress updates are handled as one editable Telegram message:
    start() sends a normal start message and creates/loads one progress message.
    progress() edits that same progress message with throttling.
    complete() edits it one last time and sends one normal completion message.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

try:
    from engine.live.telegram.notifier import TelegramNotifier
except Exception:  # pragma: no cover - import safety for isolated tooling
    TelegramNotifier = None  # type: ignore[assignment]

STATE_ROOT = Path("data/_system/research/_telegram_progress_state")


def _safe_slug(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_")
    return value[:160] or "run"


class HonestRunNotifier:
    def __init__(
        self,
        *,
        run_id: str,
        stage: str,
        batch_index: str = "",
        total: int = 0,
        notify_every: int = 100,
        notify_pct: float = 10.0,
        min_edit_interval_sec: float = 3.0,
        state_path: str | Path | None = None,
    ) -> None:
        self.run_id = str(run_id or "")
        self.stage = str(stage or "")
        self.batch_index = str(batch_index or "")
        self.total = max(0, int(total or 0))
        # Progress is edited in-place. Keep update cadence low enough for Telegram
        # but frequent enough for long unattended runs. Even if a caller passes a
        # larger interval, cap editable progress to 50 completed tickers.
        self.notify_every = max(1, min(int(notify_every or 50), 50))
        self.notify_pct = max(0.1, float(notify_pct or 10.0))
        self.min_edit_interval_sec = max(0.0, float(min_edit_interval_sec or 3.0))
        self.started_at = time.time()
        self._last_edited_done = 0
        self._last_edited_pct_bucket = 0
        self._last_edit_at = 0.0
        self._progress_message_id = 0
        self._last_progress_text = ""
        self.state_path = Path(state_path) if state_path else self._default_state_path()
        self._notifier = None
        if TelegramNotifier is not None:
            try:
                self._notifier = TelegramNotifier(default_rate_limit_seconds=0)
            except Exception:
                self._notifier = None
        self._load_state()

    @property
    def enabled(self) -> bool:
        return bool(self._notifier and getattr(self._notifier, "enabled", False))

    def _default_state_path(self) -> Path:
        name = f"{_safe_slug(self.run_id)}__{_safe_slug(self.stage)}__{_safe_slug(self.batch_index or 'default')}.json"
        return STATE_ROOT / name

    def _load_state(self) -> None:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._progress_message_id = int(data.get("progress_message_id") or 0)
                self._last_progress_text = str(data.get("last_progress_text") or "")
        except Exception:
            self._progress_message_id = 0
            self._last_progress_text = ""

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "run_id": self.run_id,
                "stage": self.stage,
                "batch_index": self.batch_index,
                "progress_message_id": int(self._progress_message_id or 0),
                "last_progress_text": self._last_progress_text,
                "updated_at": time.time(),
            }
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception:
            return

    def _send(self, title: str, lines: list[str], *, level: str = "INFO", event_key: str = "") -> None:
        if not self.enabled:
            return
        try:
            message = "\n".join(str(x) for x in lines if str(x).strip())
            self._notifier.send_system_alert(
                title=title,
                message=message[:3500],
                level=level,
                event_key=event_key or f"honest_run:{self.stage}:{self.run_id}:{title}",
            )
        except Exception:
            return

    def _format_progress_text(self, *, done: int, passed: int = 0, errors: int = 0, selected: int = 0, final: bool = False) -> str:
        done = max(0, int(done or 0))
        pct = (done / self.total * 100.0) if self.total else 0.0
        elapsed = time.time() - self.started_at
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (self.total - done) / rate if rate > 0 and self.total >= done else 0.0
        label = "완료" if final else "진행"
        primary_name = "통과" if passed or not selected else "선별"
        primary_value = int(passed if passed else selected)
        batch = f" b{self.batch_index}" if self.batch_index and self.batch_index != "stage0" else ""
        return (
            f"정직 RUN {self.stage}{batch} ▸ {done}/{self.total} ({pct:.1f}%) | "
            f"{primary_name} {primary_value} | 실패 {int(errors or 0)} | "
            f"ETA {eta/60:.0f}분 | {label}"
        )[:3900]

    def _ensure_progress_message(self, *, done: int = 0, passed: int = 0, errors: int = 0, selected: int = 0) -> None:
        if not self.enabled:
            return
        if self._progress_message_id:
            return
        try:
            text = self._format_progress_text(done=done, passed=passed, errors=errors, selected=selected)
            message_id = int(self._notifier.send_progress(text) or 0)
            if message_id:
                self._progress_message_id = message_id
                self._last_progress_text = text
                self._last_edit_at = time.time()
                self._save_state()
        except Exception:
            return

    def _edit_progress(self, text: str, *, force: bool = False) -> None:
        if not self.enabled:
            return
        if not self._progress_message_id:
            self._ensure_progress_message()
        if not self._progress_message_id:
            return
        if not force and text == self._last_progress_text:
            return
        try:
            ok = bool(self._notifier.edit_message(int(self._progress_message_id), text))
            if ok:
                self._last_progress_text = text
                self._last_edit_at = time.time()
                self._save_state()
        except Exception:
            return

    def start(self, *, total: int | None = None, batch_index: str | None = None, extra: Mapping[str, Any] | None = None) -> None:
        if total is not None:
            self.total = max(0, int(total or 0))
        if batch_index is not None:
            self.batch_index = str(batch_index or "")
        lines = [
            f"run_id: {self.run_id}",
            f"stage: {self.stage}",
            f"batch_index: {self.batch_index or '-'}",
            f"total_tickers: {self.total}",
        ]
        if extra:
            lines.extend(f"{k}: {v}" for k, v in extra.items())
        self._send("정직 RUN 시작", lines, level="INFO", event_key=f"honest_start:{self.stage}:{self.run_id}:{self.batch_index}")
        # One editable progress message for this run/stage/batch. Subsequent
        # progress calls use editMessageText and do not create new messages.
        self._ensure_progress_message(done=0, passed=0, errors=0, selected=0)

    def progress(self, *, done: int, passed: int = 0, errors: int = 0, selected: int = 0, force: bool = False) -> None:
        """Edit the single progress message with throttling.

        No new progress messages are sent here. Small smoke runs still only
        create the placeholder at start and a final edit at completion.
        """
        done = max(0, int(done or 0))
        pct = (done / self.total * 100.0) if self.total else 0.0
        pct_bucket = int(pct // self.notify_pct) if self.notify_pct > 0 else 0
        now = time.time()
        count_threshold = done > 0 and (done - self._last_edited_done) >= self.notify_every
        pct_threshold = self.total >= self.notify_every and pct_bucket >= 1 and pct_bucket > self._last_edited_pct_bucket
        time_threshold = self._last_edit_at > 0 and (now - self._last_edit_at) >= self.min_edit_interval_sec and done > self._last_edited_done
        should_edit = force or count_threshold or pct_threshold or time_threshold
        if not should_edit:
            return
        text = self._format_progress_text(done=done, passed=passed, errors=errors, selected=selected)
        self._edit_progress(text, force=force)
        self._last_edited_done = done
        self._last_edited_pct_bucket = max(self._last_edited_pct_bucket, pct_bucket)

    def error(self, *, ticker: str = "", error: Any = "", context: str = "") -> None:
        lines = [
            f"run_id: {self.run_id}",
            f"stage: {self.stage}",
            f"batch_index: {self.batch_index or '-'}",
            f"ticker: {ticker or '-'}",
            f"context: {context or '-'}",
            f"error: {str(error)[:900]}",
        ]
        self._send("정직 RUN 에러", lines, level="CRITICAL", event_key=f"honest_error:{self.stage}:{self.run_id}:{self.batch_index}:{ticker}:{context}:{str(error)[:80]}")

    def complete(self, *, total: int, passed: int = 0, selected: int = 0, errors: int = 0, elapsed_sec: float | None = None, honesty_ok: bool = True, extra: Mapping[str, Any] | None = None) -> None:
        elapsed = float(elapsed_sec if elapsed_sec is not None else time.time() - self.started_at)
        self.total = max(self.total, int(total or 0))
        final_text = self._format_progress_text(done=int(total or 0), passed=passed, selected=selected, errors=errors, final=True)
        self._edit_progress(final_text, force=True)
        lines = [
            f"run_id: {self.run_id}",
            f"stage: {self.stage}",
            f"batch_index: {self.batch_index or '-'}",
            f"total: {total}",
            f"passed/selected: {passed if passed else selected}",
            f"errors: {errors}",
            f"elapsed_hr: {elapsed/3600:.2f}",
            f"honesty_flags_ok: {bool(honesty_ok)}",
        ]
        if extra:
            lines.extend(f"{k}: {v}" for k, v in extra.items())
        level = "INFO" if errors == 0 and honesty_ok else "WARN"
        self._send("정직 RUN 완료", lines, level=level, event_key=f"honest_complete:{self.stage}:{self.run_id}:{self.batch_index}")
