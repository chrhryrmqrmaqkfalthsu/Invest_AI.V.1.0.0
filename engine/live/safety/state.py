"""
SafetyState - SafetyLayer가 사용하는 일일 상태 저장소
- 매일 자정 자동 리셋 (날짜 바뀌면 카운터 0으로)
- data/_system/safety_state.json 에 영속화
- 봇 재시작해도 그날 사용량/쿨다운 상태 복원
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

STATE_PATH = Path.home() / "kingmaker" / "data" / "_system" / "safety_state.json"


@dataclass
class SafetyState:
    date: str = ""                          # YYYY-MM-DD
    orders_today: int = 0                   # 오늘 주문 횟수
    invested_krw_today: float = 0.0         # 오늘 매수 누적 금액
    realized_pnl_today: float = 0.0         # 오늘 실현손익 (- 면 손실)
    consecutive_losses: int = 0             # 연속 손실 카운트
    cooldown_until: str = ""                # ISO datetime; "" 이면 쿨다운 없음
    first_order_approved: bool = False      # 오늘 첫 주문 승인 여부
    kill_until: str = ""                    # 일일 손실 한도 도달 시 그날 끝까지 차단

    def to_dict(self) -> dict:
        return asdict(self)


def _today_str() -> str:
    return date.today().isoformat()


def load() -> SafetyState:
    """상태 로드. 파일 없거나 날짜 바뀌었으면 새로 만듦."""
    today = _today_str()
    if not STATE_PATH.exists():
        return SafetyState(date=today)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return SafetyState(date=today)

    state = SafetyState(**{k: v for k, v in data.items() if k in SafetyState.__dataclass_fields__})

    # 날짜가 바뀌었으면 일일 카운터만 리셋. 쿨다운/연속손실은 유지.
    if state.date != today:
        new_state = SafetyState(
            date=today,
            consecutive_losses=state.consecutive_losses,  # 어제까지 누적 유지
            cooldown_until=state.cooldown_until,           # 쿨다운은 시각 기반이라 유지
        )
        save(new_state)
        return new_state

    return state


def save(state: SafetyState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_for_test() -> None:
    """테스트용: 상태 파일 삭제"""
    STATE_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    # 단위 테스트
    print("[1] reset")
    reset_for_test()
    s = load()
    print(f"  ✅ date={s.date}, orders={s.orders_today}")

    print("[2] modify + save")
    s.orders_today = 2
    s.invested_krw_today = 15000.0
    s.consecutive_losses = 1
    save(s)

    print("[3] reload")
    s2 = load()
    assert s2.orders_today == 2 and s2.invested_krw_today == 15000.0
    print(f"  ✅ orders={s2.orders_today}, invested={s2.invested_krw_today}")

    print("[4] date rollover simulation")
    s2.date = "1999-01-01"
    save(s2)
    s3 = load()
    assert s3.orders_today == 0
    assert s3.consecutive_losses == 1  # 연속손실은 유지돼야 함
    print(f"  ✅ orders reset to {s3.orders_today}, "
          f"consecutive_losses preserved as {s3.consecutive_losses}")

    reset_for_test()
    print("✅ 모든 테스트 통과")
