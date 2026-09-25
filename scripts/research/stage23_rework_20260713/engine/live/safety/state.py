"""
SafetyState - SafetyLayer가 사용하는 일일/진입 안전 상태 저장소
- 매일 자정 자동 리셋 (일일 카운터만 0으로)
- data/_system/safety_state.json 에 영속화
- 봇 재시작/날짜 변경 뒤에도 종목별 매수 쿨다운 상태 복원
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

STATE_PATH = Path.home() / "kingmaker" / "data" / "_system" / "safety_state.json"


@dataclass
class SafetyState:
    date: str = ""                          # YYYY-MM-DD
    orders_today: int = 0                   # 오늘 제출된 주문 횟수
    invested_krw_today: float = 0.0         # 오늘 실제 체결 매수 누적 금액
    realized_pnl_today: float = 0.0         # 오늘 실현손익 (- 면 손실)
    consecutive_losses: int = 0             # 연속 손실 카운터
    cooldown_until: str = ""                # ISO datetime; "" 이면 쿨다운 없음
    first_order_approved: bool = False      # 오늘 첫 주문 승인 여부
    kill_until: str = ""                    # 일일 손실 한도 도달 시 그날 끝까지 차단
    # BQ-2a: 일일 리셋과 무관하게 유지되는 종목별 FILLED BUY 시각.
    last_buy_at_by_ticker: dict[str, str] = field(default_factory=dict)
    last_add_buy_at_by_ticker: dict[str, str] = field(default_factory=dict)
    # BN-1: 주문 제출/체결 기록을 분리하고 재조회 시 중복 반영하지 않는다.
    submitted_order_ids: dict[str, str] = field(default_factory=dict)
    settled_order_ids: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _today_str() -> str:
    return date.today().isoformat()


def load() -> SafetyState:
    """상태 로드. 파일 없거나 날짜 바뀌었으면 일일 카운터만 새로 만듦."""
    today = _today_str()
    if not STATE_PATH.exists():
        return SafetyState(date=today)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return SafetyState(date=today)

    state = SafetyState(**{k: v for k, v in data.items() if k in SafetyState.__dataclass_fields__})
    for field_name in (
        "last_buy_at_by_ticker",
        "last_add_buy_at_by_ticker",
        "submitted_order_ids",
        "settled_order_ids",
    ):
        if not isinstance(getattr(state, field_name), dict):
            setattr(state, field_name, {})

    # 날짜가 바뀌었으면 일일 카운터만 리셋. 쿨다운/연속손실/종목별 BUY 시각과
    # BN-1 idempotency map은 재시작 후 중복 정산 방지를 위해 유지한다.
    if state.date != today:
        new_state = SafetyState(
            date=today,
            consecutive_losses=state.consecutive_losses,
            cooldown_until=state.cooldown_until,
            last_buy_at_by_ticker=dict(state.last_buy_at_by_ticker),
            last_add_buy_at_by_ticker=dict(state.last_add_buy_at_by_ticker),
            submitted_order_ids=dict(state.submitted_order_ids),
            settled_order_ids=dict(state.settled_order_ids),
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
    print("[1] reset")
    reset_for_test()
    s = load()
    print(f"  ✅ date={s.date}, orders={s.orders_today}")

    print("[2] modify + save")
    s.orders_today = 2
    s.invested_krw_today = 15000.0
    s.consecutive_losses = 1
    s.last_buy_at_by_ticker["AAPL"] = "2026-06-05T10:00:00+09:00"
    s.submitted_order_ids["B1"] = "2026-06-05T10:00:00+09:00"
    save(s)

    print("[3] reload")
    s2 = load()
    assert s2.orders_today == 2 and s2.invested_krw_today == 15000.0
    assert "AAPL" in s2.last_buy_at_by_ticker
    assert "B1" in s2.submitted_order_ids
    print(f"  ✅ orders={s2.orders_today}, invested={s2.invested_krw_today}")

    print("[4] date rollover simulation")
    s2.date = "1999-01-01"
    save(s2)
    s3 = load()
    assert s3.orders_today == 0
    assert s3.consecutive_losses == 1
    assert "AAPL" in s3.last_buy_at_by_ticker
    assert "B1" in s3.submitted_order_ids
    print(f"  ✅ orders reset to {s3.orders_today}, cooldown/idempotency maps preserved")

    reset_for_test()
    print("✅ 모든 테스트 통과")
