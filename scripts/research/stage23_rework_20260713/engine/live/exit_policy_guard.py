"""BS-1c live ExitPolicy 운영 가드.

실계좌에서는 EXIT_LIVE_POLICY 미설정 또는 정책 실패 시 조용히 legacy 청산으로
복귀하지 않는다. KIS는 broker.mode만으로 real/vts 구분이 안 되므로 kis_mode를
함께 본다. Alpaca는 alpaca_live만 실계좌로 본다.
"""
from __future__ import annotations

import os
from typing import Any


def env_enabled(name: str) -> bool:
    return str(os.environ.get(name, "0")).strip().lower() in {"1", "true", "yes", "on"}


def exit_live_policy_enabled() -> bool:
    return env_enabled("EXIT_LIVE_POLICY")


def legacy_live_override_enabled() -> bool:
    return env_enabled("ALLOW_LEGACY_EXIT_LIVE")


def broker_mode(broker: Any) -> str:
    try:
        return str(getattr(broker, "mode", "") or "").strip().lower()
    except Exception:
        return ""


def broker_kis_mode(broker: Any) -> str:
    try:
        return str(getattr(broker, "kis_mode", "") or "").strip().lower()
    except Exception:
        return ""


def is_strict_live_broker(broker: Any) -> bool:
    """실제 자본이 나갈 수 있는 live broker만 True.

    - Alpaca: alpaca_live는 strict, alpaca_paper는 제외
    - KIS: 외부 mode는 live로 동일하므로 kis_mode real/live만 strict, vts 제외
    """
    mode = broker_mode(broker)
    if mode == "alpaca_live":
        return True
    if mode == "live":
        return broker_kis_mode(broker) in {"real", "live"}
    return False


def validate_startup_exit_policy(broker: Any) -> None:
    if not is_strict_live_broker(broker):
        return
    if exit_live_policy_enabled():
        return
    if legacy_live_override_enabled():
        return
    raise RuntimeError(
        "실계좌 모드에서는 EXIT_LIVE_POLICY=1이 필요합니다. "
        "legacy 청산을 명시적으로 허용하려면 ALLOW_LEGACY_EXIT_LIVE=1을 설정하십시오."
    )


def should_block_legacy_fallback(broker: Any) -> bool:
    """실계좌에서 silent legacy fallback을 차단해야 하는지 반환."""
    return is_strict_live_broker(broker) and not legacy_live_override_enabled()
