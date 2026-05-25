"""
MarketClock - 시장별 영업시간/영업일 판단 인터페이스
- Scheduler가 이걸 보고 "지금 일해야 하는지" 판단
- 코인 추가 시 CryptoMarket 클래스 하나 추가하면 끝

설계 원칙:
  Scheduler는 시간 판단을 직접 안 함. 항상 MarketClock에 위임.
  MarketClock 구현체만 갈아끼면 KRX/코인/미국 어디든 작동.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time, timedelta
from typing import Optional, Set
from zoneinfo import ZoneInfo


class MarketClock(ABC):
    """모든 시장의 영업시간 판단 인터페이스"""

    name: str = "abstract"
    timezone: ZoneInfo = ZoneInfo("UTC")

    @abstractmethod
    def is_open(self, dt: Optional[datetime] = None) -> bool:
        """해당 시각에 정규장이 열려있는가"""
        ...

    @abstractmethod
    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        """해당 날짜가 영업일인가 (주말/공휴일 제외)"""
        ...

    def now(self) -> datetime:
        """이 시장 기준 현재 시각 (timezone-aware)"""
        return datetime.now(self.timezone)

    def next_open(self, dt: Optional[datetime] = None) -> Optional[datetime]:
        """다음 개장 시각 (운영 정보용, 필수 아님)"""
        return None


# ==================================================
# KRX (한국거래소)
# ==================================================
class KrxMarketClock(MarketClock):
    """
    KRX 정규장: 평일 09:00 ~ 15:30 (Asia/Seoul)
    공휴일은 별도 캘린더 (간단 버전: 주말만 차단, 공휴일은 데이터 갱신 실패로 자연 처리)
    """
    name = "KRX"
    timezone = ZoneInfo("Asia/Seoul")

    OPEN_TIME  = time(9, 0)
    CLOSE_TIME = time(15, 30)

    # 운영 중 추가 가능 (수동 또는 별도 로더로 갱신)
    holidays: Set[str] = set()  # "YYYY-MM-DD" 형식

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        dt = self._to_local(dt)
        if not self.is_business_day(dt):
            return False
        return self.OPEN_TIME <= dt.time() <= self.CLOSE_TIME

    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        dt = self._to_local(dt)
        if dt.weekday() >= 5:  # 5=토, 6=일
            return False
        if dt.strftime("%Y-%m-%d") in self.holidays:
            return False
        return True

    def next_open(self, dt: Optional[datetime] = None) -> Optional[datetime]:
        dt = self._to_local(dt)
        candidate = dt.replace(hour=9, minute=0, second=0, microsecond=0)
        if dt.time() >= self.OPEN_TIME:
            candidate += timedelta(days=1)
        # 영업일 찾을 때까지 점프
        for _ in range(10):
            if self.is_business_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        return None

    def _to_local(self, dt: Optional[datetime]) -> datetime:
        if dt is None:
            return self.now()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.timezone)
        return dt.astimezone(self.timezone)


# ==================================================
# Crypto (예: 바이낸스 — 24/7)
# ==================================================
class CryptoMarketClock(MarketClock):
    """24/7 시장. 정기 점검 시간 등은 추후 추가."""
    name = "Crypto"
    timezone = ZoneInfo("UTC")

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        return True

    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        return True


# ==================================================
# US (예시 — 정확한 구현은 추후)
# ==================================================
class UsMarketClock(MarketClock):
    """
    US 정규장: 평일 09:30 ~ 16:00 (America/New_York)
    한국시간으로는 23:30 ~ 06:00 (서머타임은 ZoneInfo가 처리)
    프리/애프터 마켓은 추후 별도 클래스로.
    """
    name = "US"
    timezone = ZoneInfo("America/New_York")

    OPEN_TIME  = time(9, 30)
    CLOSE_TIME = time(16, 0)

    holidays: Set[str] = set()

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        dt = self._to_local(dt)
        if not self.is_business_day(dt):
            return False
        return self.OPEN_TIME <= dt.time() <= self.CLOSE_TIME

    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        dt = self._to_local(dt)
        if dt.weekday() >= 5:
            return False
        if dt.strftime("%Y-%m-%d") in self.holidays:
            return False
        return True

    def _to_local(self, dt: Optional[datetime]) -> datetime:
        if dt is None:
            return self.now()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.timezone)
        return dt.astimezone(self.timezone)


# ==================================================
# 단위 테스트
# ==================================================
if __name__ == "__main__":
    from datetime import datetime as dt

    print("=" * 60)
    print("MarketClock 검증")
    print("=" * 60)

    krx = KrxMarketClock()
    crypto = CryptoMarketClock()
    us = UsMarketClock()

    # 테스트 시각들 (모두 Asia/Seoul)
    tz_seoul = ZoneInfo("Asia/Seoul")
    cases = [
        ("2026-05-25 10:00", "월요일 10:00",  krx, True,  True),
        ("2026-05-25 15:31", "월요일 15:31",  krx, False, True),
        ("2026-05-25 08:59", "월요일 08:59",  krx, False, True),
        ("2026-05-30 10:00", "토요일 10:00",  krx, False, False),
        ("2026-05-25 10:00", "코인 평일 10시", crypto, True, True),
        ("2026-05-31 03:00", "코인 일요일 새벽", crypto, True, True),
    ]

    print(f"\n{'시각':<25} {'시장':<8} {'예상open':<10} {'예상영업일':<12} 결과")
    for ts, desc, market, exp_open, exp_biz in cases:
        d = dt.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=tz_seoul)
        got_open = market.is_open(d)
        got_biz = market.is_business_day(d)
        ok = (got_open == exp_open) and (got_biz == exp_biz)
        mark = "✅" if ok else "❌"
        print(f"  {desc:<22} {market.name:<8} open={got_open}/{exp_open} "
              f"biz={got_biz}/{exp_biz}  {mark}")

    # next_open
    print("\n[next_open] 토요일 14:00 → 다음 KRX 개장:")
    sat = dt(2026, 5, 30, 14, 0, tzinfo=tz_seoul)
    nxt = krx.next_open(sat)
    print(f"  {nxt}  (월요일 09:00이어야 정상)")
    assert nxt.weekday() == 0 and nxt.hour == 9
    print("  ✅ 정상")

    # 공휴일 등록 테스트
    print("\n[공휴일] 2026-05-25를 공휴일로 등록 후 재검증:")
    krx.holidays.add("2026-05-25")
    d = dt(2026, 5, 25, 10, 0, tzinfo=tz_seoul)
    print(f"  is_open={krx.is_open(d)} (False여야 정상)")
    assert krx.is_open(d) is False
    print("  ✅ 정상")
    krx.holidays.clear()

    print("\n" + "=" * 60)
    print("✅ MarketClock 검증 완료")
    print("=" * 60)
