"""
BrokerFactory - 환경설정에 따라 PaperBroker / KisBroker 자동 선택
사용법:
    from engine.live.broker.factory import make_broker
    broker = make_broker()  # .env의 KIS_MODE 보고 자동 결정
    broker = make_broker(force_mode="paper")  # 강제로 paper
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from dotenv import dotenv_values

from .base import Broker
from .paper import PaperBroker
from .kis import KisBroker

ENV_PATH = Path.home() / "kingmaker" / ".env"


def make_broker(
    force_mode: Optional[str] = None,
    dry_run: bool = False,
    paper_initial_cash: float = 1_000_000,
) -> Broker:
    """
    force_mode: "paper" / "real" / "vts" / "live" 중 하나. None이면 .env에서 읽음.
    dry_run:    KisBroker에만 적용 (실제 KIS 호출 안 함)
    paper_initial_cash: PaperBroker 초기 현금 (기본 100만 원)
    """
    env = dotenv_values(str(ENV_PATH))
    kis_mode = (force_mode or env.get("KIS_MODE", "paper")).strip().lower()

    if kis_mode == "paper":
        return PaperBroker(initial_cash=paper_initial_cash)
    elif kis_mode in ("real", "live", "vts"):
        if force_mode:
            os.environ["KIS_MODE"] = force_mode
        return KisBroker(dry_run=dry_run)
    else:
        raise ValueError(
            f"알 수 없는 mode: {kis_mode!r}. paper / real / vts / live 중 하나여야 함."
        )


if __name__ == "__main__":
    print("=" * 50)
    print("BrokerFactory 검증")
    print("=" * 50)

    print("\n[1] force_mode='paper'")
    b1 = make_broker(force_mode="paper")
    print(f"  type: {type(b1).__name__}, mode: {b1.mode}")
    assert type(b1).__name__ == "PaperBroker"
    print("  ✅ PaperBroker 반환")

    print("\n[2] .env 기본 (dry_run=True)")
    b2 = make_broker(dry_run=True)
    print(f"  type: {type(b2).__name__}, mode: {b2.mode}, "
          f"kis_mode: {getattr(b2, 'kis_mode', '-')}, dry_run: {getattr(b2, 'dry_run', '-')}")

    print("\n[3] 잘못된 mode (foobar)")
    try:
        make_broker(force_mode="foobar")
        print("  ❌ 예외 안 났음")
    except ValueError as e:
        print(f"  ✅ ValueError: {str(e)[:60]}")

    print("\n" + "=" * 50)
    print("✅ BrokerFactory 검증 완료")
    print("=" * 50)
