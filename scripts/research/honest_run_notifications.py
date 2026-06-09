"""Telegram notification helpers for long honest research runs.

This module reuses the existing TelegramNotifier environment configuration.
Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID or transient network failures must
never stop a research run.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

try:
    from engine.live.telegram.notifier import TelegramNotifier
except Exception:  # pragma: no cover - import safety for isolated tooling
    TelegramNotifier = None  # type: ignore[assignment]


class HonestRunNotifier:
    def __init__(self, *, run_id: str, stage: str, batch_index: str = "", total: int = 0, notify_every: int = 100, notify_pct: float = 5.0) -> None:
        self.run_id = str(run_id or "")
        self.stage = str(stage or "")
        self.batch_index = str(batch_index or "")
        self.total = max(0, int(total or 0))
        self.notify_every = max(1, int(notify_every or 100))
        self.notify_pct = max(0.1, float(notify_pct or 5.0))
        self.started_at = time.time()
        self._last_sent_done = 0
        self._last_sent_pct_bucket = -1
        self._notifier = None
        if TelegramNotifier is not None:
            try:
                self._notifier = TelegramNotifier(default_rate_limit_seconds=0)
            except Exception:
                self._notifier = None

    @property
    def enabled(self) -> bool:
        return bool(self._notifier and getattr(self._notifier, "enabled", False))

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

    def progress(self, *, done: int, passed: int = 0, errors: int = 0, selected: int = 0, force: bool = False) -> None:
        done = max(0, int(done or 0))
        pct = (done / self.total * 100.0) if self.total else 0.0
        pct_bucket = int(pct // self.notify_pct) if self.notify_pct > 0 else 0
        should_send = force or done == self.total or (done > 0 and done % self.notify_every == 0) or pct_bucket > self._last_sent_pct_bucket
        if not should_send or done == self._last_sent_done:
            return
        self._last_sent_done = done
        self._last_sent_pct_bucket = pct_bucket
        elapsed = time.time() - self.started_at
        rate = done / elapsed if elapsed > 0 else 0.0
        eta = (self.total - done) / rate if rate > 0 and self.total >= done else 0.0
        lines = [
            f"run_id: {self.run_id}",
            f"stage: {self.stage}",
            f"batch_index: {self.batch_index or '-'}",
            f"progress: {done}/{self.total} ({pct:.1f}%)",
            f"passed/selected: {passed if passed else selected}",
            f"errors: {errors}",
            f"elapsed_min: {elapsed/60:.1f}",
            f"eta_min: {eta/60:.1f}",
        ]
        self._send("정직 RUN 진행률", lines, level="INFO", event_key=f"honest_progress:{self.stage}:{self.run_id}:{self.batch_index}:{done}")

    def error(self, *, ticker: str = "", error: Any = "", context: str = "") -> None:
        lines = [
            f"run_id: {self.run_id}",
            f"stage: {self.stage}",
            f"batch_index: {self.batch_index or '-'}",
            f"ticker: {ticker or '-'}",
            f"context: {context or '-'}",
            f"error: {str(error)[:900]}",
        ]
        self._send("정직 RUN 에러", lines, level="CRITICAL", event_key=f"honest_error:{self.stage}:{self.run_id}:{self.batch_index}:{ticker}:{str(error)[:80]}")

    def complete(self, *, total: int, passed: int = 0, selected: int = 0, errors: int = 0, elapsed_sec: float | None = None, honesty_ok: bool = True, extra: Mapping[str, Any] | None = None) -> None:
        elapsed = float(elapsed_sec if elapsed_sec is not None else time.time() - self.started_at)
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
