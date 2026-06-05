"""Telegram polling single-owner guard.

The Telegram Bot API update stream must have exactly one consumer.  This
module wraps the existing :class:`TelegramBot` with an atomic process lock so
run_bot.py and run_live.py cannot silently split approval commands.

The lock stores only hashes/metadata.  The Telegram token itself is never
written to disk or logs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .bot import TelegramBot as BaseTelegramBot

log = logging.getLogger("telegram.polling_lock")

DEFAULT_POLLING_LOCK_PATH = Path("data/_system/telegram_polling.lock")


def token_fingerprint(token: str) -> str:
    """Return a non-reversible identifier; never persist the token itself."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def is_process_alive(pid: int) -> bool:
    try:
        if int(pid) <= 0:
            return False
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OSError):
        return False


def _read_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _read_process_start_ticks(pid: int) -> str:
    """Read Linux /proc stat field 22 without trusting the process name."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        close_paren = raw.rfind(")")
        if close_paren < 0:
            return ""
        # The remaining fields start at field 3; starttime is field 22.
        remaining = raw[close_paren + 2 :].split()
        return remaining[19] if len(remaining) > 19 else ""
    except Exception:
        return ""


def _read_process_cmd_fingerprint(pid: int) -> str:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        return hashlib.sha256(raw).hexdigest() if raw else ""
    except Exception:
        return ""


def process_identity(pid: int) -> dict[str, str]:
    """Return PID-reuse-resistant process metadata containing hashes only."""
    boot_id = _read_boot_id()
    start_ticks = _read_process_start_ticks(pid)
    cmd_fingerprint = _read_process_cmd_fingerprint(pid)
    if not start_ticks:
        return {}
    identity_source = f"{boot_id}|{start_ticks}|{cmd_fingerprint}"
    return {
        "process_identity": hashlib.sha256(identity_source.encode("utf-8")).hexdigest(),
        "process_start_ticks": start_ticks,
        "process_cmd_fingerprint": cmd_fingerprint,
        "boot_id_fingerprint": hashlib.sha256(boot_id.encode("utf-8")).hexdigest() if boot_id else "",
    }


class TelegramPollingLock:
    """Atomic single-owner lock for Telegram getUpdates polling."""

    def __init__(
        self,
        *,
        token: str,
        owner: str,
        path: Path | str = DEFAULT_POLLING_LOCK_PATH,
        pid: Optional[int] = None,
    ):
        self.path = Path(path)
        self.pid = int(pid if pid is not None else os.getpid())
        self.owner = str(owner or "telegram_bot")
        self._token_fingerprint = token_fingerprint(token)
        self._owned = False
        self._identity = process_identity(self.pid)
        if not self._identity:
            raise RuntimeError(f"Telegram polling process identity unavailable: pid={self.pid}")

    @property
    def owned(self) -> bool:
        return self._owned

    def _payload(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "owner": self.owner,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "token_fingerprint": self._token_fingerprint,
            **self._identity,
        }

    def _read_existing(self) -> tuple[str, dict[str, Any]]:
        raw = self.path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(
                f"Telegram polling lock is unreadable; manual inspection required: {self.path}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Telegram polling lock is invalid; manual inspection required: {self.path}")
        return raw, payload

    def _remove_dead_owner_lock(self, expected_raw: str) -> bool:
        """Remove only the exact dead-owner payload that was inspected."""
        try:
            if self.path.read_text(encoding="utf-8") != expected_raw:
                return False
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except Exception as exc:
            raise RuntimeError(f"Cannot remove stale Telegram polling lock: {self.path}") from exc

    def acquire(self) -> None:
        if self._owned:
            raise RuntimeError("Telegram polling lock already owned by this instance")
        self.path.parent.mkdir(parents=True, exist_ok=True)

        for _ in range(5):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raw, existing = self._read_existing()
                try:
                    existing_pid = int(existing.get("pid", 0))
                except Exception as exc:
                    raise RuntimeError(
                        f"Telegram polling lock PID invalid; manual inspection required: {self.path}"
                    ) from exc

                if is_process_alive(existing_pid):
                    current_identity = process_identity(existing_pid)
                    stored_identity = str(existing.get("process_identity") or "")
                    if not current_identity or not stored_identity:
                        raise RuntimeError(
                            f"Telegram polling lock owner identity cannot be verified; fail-closed: "
                            f"pid={existing_pid} owner={existing.get('owner', '?')}"
                        )
                    if current_identity.get("process_identity") != stored_identity:
                        # PID is alive but no longer identifies the process that created the lock.
                        # Do not steal/delete automatically: require explicit operator review.
                        raise RuntimeError(
                            f"Telegram polling lock PID reuse suspected; fail-closed, manual cleanup required: "
                            f"pid={existing_pid} owner={existing.get('owner', '?')}"
                        )
                    raise RuntimeError(
                        f"Telegram polling already owned: pid={existing_pid} owner={existing.get('owner', '?')}"
                    )

                if self._remove_dead_owner_lock(raw):
                    log.warning(
                        "dead Telegram polling owner lock removed: pid=%s owner=%s",
                        existing_pid,
                        existing.get("owner", "?"),
                    )
                continue
            except Exception:
                raise
            else:
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(self._payload(), handle, ensure_ascii=False, indent=2)
                        handle.flush()
                        os.fsync(handle.fileno())
                    self._owned = True
                    log.info("Telegram polling lock acquired: pid=%s owner=%s", self.pid, self.owner)
                    return
                except Exception:
                    try:
                        self.path.unlink()
                    except Exception:
                        pass
                    raise

        raise RuntimeError(f"Telegram polling lock acquisition race exceeded retry limit: {self.path}")

    def release(self) -> None:
        if not self._owned:
            return
        try:
            _, existing = self._read_existing()
            matches = (
                int(existing.get("pid", 0)) == self.pid
                and str(existing.get("owner") or "") == self.owner
                and str(existing.get("token_fingerprint") or "") == self._token_fingerprint
                and str(existing.get("process_identity") or "") == self._identity.get("process_identity")
            )
            if not matches:
                log.error("Telegram polling lock ownership changed; refusing to remove: %s", self.path)
                return
            self.path.unlink()
            log.info("Telegram polling lock released: pid=%s owner=%s", self.pid, self.owner)
        except FileNotFoundError:
            pass
        finally:
            self._owned = False


class TelegramBot(BaseTelegramBot):
    """Existing TelegramBot with mandatory single-owner polling lock."""

    def __init__(
        self,
        *args,
        polling_owner: Optional[str] = None,
        polling_lock_path: Path | str = DEFAULT_POLLING_LOCK_PATH,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        inferred_owner = Path(sys.argv[0]).stem if sys.argv else "telegram_bot"
        self._polling_lock = TelegramPollingLock(
            token=self.token,
            owner=polling_owner or inferred_owner,
            path=polling_lock_path,
        )

    def start_polling(self, blocking: bool = True) -> None:
        if self._running or self._polling_lock.owned:
            raise RuntimeError("Telegram polling already started by this instance")
        self._polling_lock.acquire()
        self._running = True
        if blocking:
            try:
                self._poll_loop()
            finally:
                self._running = False
                self._polling_lock.release()
            return

        def guarded_poll_loop() -> None:
            try:
                self._poll_loop()
            finally:
                self._running = False
                self._polling_lock.release()

        try:
            self._thread = threading.Thread(target=guarded_poll_loop, daemon=True)
            self._thread.start()
        except Exception:
            self._running = False
            self._polling_lock.release()
            raise

    def stop(self) -> None:
        self._running = False
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, float(self.poll_interval) + 1.0))
        if thread is None or not thread.is_alive():
            self._polling_lock.release()
