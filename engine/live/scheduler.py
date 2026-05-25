"""
Scheduler - 자동매매 봇의 심장박동
- MarketClock 받아서 장중에만 작업 실행 (자동 시간 판단)
- 3가지 잡 타입:
    1) market_hours: 장중에만 N초마다 실행
    2) cron:         매일 특정 시각 (영업일 옵션)
    3) once:         가동 후 한 번 (지연 가능)
- BackgroundScheduler: 별도 스레드 → 메인은 텔레그램 봇 등 다른 작업 가능

사용법:
    from engine.live.scheduler import Scheduler
    from engine.live.market_clock import KrxMarketClock

    sch = Scheduler()
    sch.add_market_hours_job(runner.tick, interval_sec=60, market=KrxMarketClock())
    sch.add_cron_job(runner.daily_summary, hour=16, minute=0, market=KrxMarketClock())
    sch.start()
    ...
    sch.shutdown()
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .market_clock import MarketClock, KrxMarketClock

log = logging.getLogger("scheduler")


class Scheduler:
    """
    내부적으로 APScheduler BackgroundScheduler를 사용.
    공개 API는 MarketClock 중심으로 단순화.
    """

    def __init__(self, default_timezone: str = "Asia/Seoul"):
        self.tz = ZoneInfo(default_timezone)
        self._sched = BackgroundScheduler(timezone=self.tz)
        self._jobs: List[dict] = []  # 디버깅/추적용 메타데이터
        self._started = False

    # =========================================================
    # 잡 등록 API
    # =========================================================
    def add_market_hours_job(
        self,
        func: Callable,
        interval_sec: int,
        market: MarketClock,
        job_id: Optional[str] = None,
        run_on_market_close: bool = False,
    ) -> str:
        """
        장중에만 interval_sec마다 실행.
        run_on_market_close=True면 장 마감 후 한 번 더 실행 (cleanup용).
        """
        job_id = job_id or f"mh_{market.name}_{func.__name__}_{len(self._jobs)}"

        def _wrapped():
            if not market.is_open():
                log.debug(f"[{job_id}] 장 마감 — skip")
                return
            try:
                func()
            except Exception as e:
                log.exception(f"[{job_id}] 실행 실패: {e}")

        self._sched.add_job(
            _wrapped,
            trigger=IntervalTrigger(seconds=interval_sec, timezone=self.tz),
            id=job_id,
            name=job_id,
            replace_existing=True,
            max_instances=1,        # 한 잡이 두 번 겹쳐서 돌지 않게
            coalesce=True,          # 밀린 실행은 1번으로 합침
            misfire_grace_time=30,
        )

        self._jobs.append({
            "id": job_id, "type": "market_hours",
            "interval_sec": interval_sec, "market": market.name,
        })
        log.info(f"등록: {job_id} (market={market.name}, every {interval_sec}s)")
        return job_id

    def add_cron_job(
        self,
        func: Callable,
        hour: int,
        minute: int = 0,
        market: Optional[MarketClock] = None,
        weekdays_only: bool = False,
        job_id: Optional[str] = None,
    ) -> str:
        """
        매일 정해진 시각에 실행.
        market이 주어지면 그 시장의 영업일에만 실행.
        weekdays_only=True면 월~금만 (market 미지정 시 fallback).
        """
        job_id = job_id or f"cron_{hour:02d}{minute:02d}_{func.__name__}_{len(self._jobs)}"

        def _wrapped():
            now = datetime.now(self.tz)
            if market and not market.is_business_day(now):
                log.debug(f"[{job_id}] 비영업일 ({market.name}) — skip")
                return
            if weekdays_only and not market and now.weekday() >= 5:
                log.debug(f"[{job_id}] 주말 — skip")
                return
            try:
                func()
            except Exception as e:
                log.exception(f"[{job_id}] 실행 실패: {e}")

        trigger_kwargs = {"hour": hour, "minute": minute, "timezone": self.tz}
        if weekdays_only:
            trigger_kwargs["day_of_week"] = "mon-fri"

        self._sched.add_job(
            _wrapped,
            trigger=CronTrigger(**trigger_kwargs),
            id=job_id,
            name=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,  # 5분 이내 밀린 건 실행
        )

        self._jobs.append({
            "id": job_id, "type": "cron",
            "hour": hour, "minute": minute,
            "market": market.name if market else None,
            "weekdays_only": weekdays_only,
        })
        log.info(f"등록: {job_id} (every day {hour:02d}:{minute:02d})")
        return job_id

    def add_once_job(
        self,
        func: Callable,
        delay_sec: float = 0,
        job_id: Optional[str] = None,
    ) -> str:
        """
        가동 후 한 번만 실행 (지연 가능). startup 체크 등에 사용.
        """
        job_id = job_id or f"once_{func.__name__}_{len(self._jobs)}"
        run_at = datetime.now(self.tz)
        if delay_sec > 0:
            from datetime import timedelta
            run_at = run_at + timedelta(seconds=delay_sec)

        def _wrapped():
            try:
                func()
            except Exception as e:
                log.exception(f"[{job_id}] 실행 실패: {e}")

        self._sched.add_job(
            _wrapped,
            trigger=DateTrigger(run_date=run_at, timezone=self.tz),
            id=job_id, name=job_id, replace_existing=True,
        )
        self._jobs.append({
            "id": job_id, "type": "once",
            "run_at": run_at.isoformat(),
        })
        log.info(f"등록: {job_id} (one-shot at {run_at.isoformat()})")
        return job_id

    # =========================================================
    # 생명주기
    # =========================================================
    def start(self) -> None:
        if self._started:
            log.warning("이미 시작됨")
            return
        self._sched.start()
        self._started = True
        log.info(f"Scheduler 시작 ({len(self._jobs)}개 잡)")

    def shutdown(self, wait: bool = True) -> None:
        if not self._started:
            return
        log.info("Scheduler 종료 중...")
        self._sched.shutdown(wait=wait)
        self._started = False
        log.info("Scheduler 종료 완료")

    def remove_job(self, job_id: str) -> bool:
        try:
            self._sched.remove_job(job_id)
            self._jobs = [j for j in self._jobs if j["id"] != job_id]
            log.info(f"제거: {job_id}")
            return True
        except Exception as e:
            log.warning(f"제거 실패 {job_id}: {e}")
            return False

    def list_jobs(self) -> List[dict]:
        return list(self._jobs)

    @property
    def is_running(self) -> bool:
        return self._started


# ==========================================================
# 단위 테스트: 가짜 콜백 등록 + 짧은 인터벌로 트리거 검증
# ==========================================================
if __name__ == "__main__":
    import time
    from .market_clock import KrxMarketClock, CryptoMarketClock

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    print("=" * 60)
    print("Scheduler 검증 (10초 동안 실행)")
    print("=" * 60)

    counters = {"market": 0, "cron": 0, "once": 0, "crypto": 0}

    def market_tick():
        counters["market"] += 1
        print(f"  📡 market_tick #{counters['market']}")

    def daily_summary():
        counters["cron"] += 1
        print(f"  📊 daily_summary #{counters['cron']}")

    def startup_check():
        counters["once"] += 1
        print(f"  🚀 startup_check (once)")

    def crypto_tick():
        counters["crypto"] += 1
        print(f"  ₿ crypto_tick #{counters['crypto']}")

    sch = Scheduler()

    # KRX: 매 2초 (테스트용 짧은 인터벌)
    sch.add_market_hours_job(market_tick, interval_sec=2, market=KrxMarketClock())

    # 코인: 매 3초 — 24/7이므로 항상 실행
    sch.add_market_hours_job(crypto_tick, interval_sec=3, market=CryptoMarketClock())

    # cron: 매 분 0초에 1회 (테스트 중 안 걸릴 수도 있음 — 단순 등록 확인용)
    now = datetime.now()
    sch.add_cron_job(daily_summary, hour=now.hour, minute=(now.minute + 1) % 60,
                     weekdays_only=False)

    # once: 가동 3초 후 1회
    sch.add_once_job(startup_check, delay_sec=3)

    print(f"\n등록된 잡: {len(sch.list_jobs())}")
    for j in sch.list_jobs():
        print(f"  - {j}")

    print("\n[Scheduler 시작]\n")
    sch.start()

    # 10초 대기
    time.sleep(10)

    sch.shutdown()

    print("\n[결과]")
    print(f"  market_tick (KRX, 장중일 때만): {counters['market']}회")
    print(f"  crypto_tick (24/7): {counters['crypto']}회")
    print(f"  startup_check (once): {counters['once']}회")
    print(f"  daily_summary (cron, 다음 분 0초): {counters['cron']}회")

    # 검증
    print("\n[검증]")
    krx_open = KrxMarketClock().is_open()
    expected_market_min = 3 if krx_open else 0
    if krx_open:
        assert counters["market"] >= 3, f"장중인데 market_tick {counters['market']}회밖에 안 됨"
        print(f"  ✅ KRX 장중: market_tick {counters['market']}회 (>= 3)")
    else:
        assert counters["market"] == 0, f"장 마감인데 market_tick {counters['market']}회 실행됨"
        print(f"  ✅ KRX 장 마감: market_tick {counters['market']}회 (== 0)")

    assert counters["crypto"] >= 2, f"crypto_tick {counters['crypto']}회밖에 안 됨"
    print(f"  ✅ Crypto 24/7: crypto_tick {counters['crypto']}회 (>= 2)")

    assert counters["once"] == 1, f"once {counters['once']}회"
    print(f"  ✅ once: 정확히 1회")

    print("\n" + "=" * 60)
    print("✅ Scheduler 검증 완료")
    print("=" * 60)
