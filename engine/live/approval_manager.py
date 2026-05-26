"""ApprovalManager - 강한 BUY 시그널 추가 매수 승인 매니저.

기능:
1. 매수 직후 강한 시그널이면 텔레그램 추가 매수 알림 발송
2. 강도별 차등 한도 옵션 제공 (weak/medium/strong)
3. 사용자 60초 이내 승인 → 즉시 추가 매수
4. 60초 초과 승인 → 재평가 후 진행/취소
5. 보유 중인데도 강한 시그널 유지 → 1시간마다 추가 매수 의사 재확인

영속화: data/_system/approvals.json
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

log = logging.getLogger("approval_manager")
APPROVALS_PATH = Path("data/_system/approvals.json")
KST = ZoneInfo("Asia/Seoul")

# 재평가 / 재알림 임계값
REEVAL_AFTER_SEC = 60                    # 60초 이내면 즉시 진행
RECONFIRM_INTERVAL_SEC = 3600            # 1시간마다 보유 종목 재알림


class SignalStrength(str, Enum):
    WEAK = "weak"          # threshold * 1.2
    MEDIUM = "medium"      # threshold * 1.5
    STRONG = "strong"      # threshold * 2.0


# 강도별 한도 옵션 (KRW)
STRENGTH_LIMITS: Dict[SignalStrength, list[int]] = {
    SignalStrength.WEAK:   [20_000, 30_000],
    SignalStrength.MEDIUM: [30_000, 50_000, 100_000],
    SignalStrength.STRONG: [50_000, 100_000, 200_000, 500_000],
}

STRENGTH_EMOJI = {
    SignalStrength.WEAK: "🟡",
    SignalStrength.MEDIUM: "🟠",
    SignalStrength.STRONG: "🔴",
}


def classify_strength(
    score: float,
    threshold: float,
    win_rate: float,
    regime: str,
    sector_score: float,
) -> Optional[SignalStrength]:
    """시그널 강도 분류. None이면 강한 시그널 아님(일반 매수만)."""
    if threshold <= 0:
        return None
    ratio = score / threshold

    if ratio >= 2.0 and win_rate >= 0.75 and regime == "bull" and sector_score >= 70:
        return SignalStrength.STRONG
    if ratio >= 1.5 and win_rate >= 0.70 and regime == "bull":
        return SignalStrength.MEDIUM
    if ratio >= 1.2 and win_rate >= 0.60:
        return SignalStrength.WEAK
    return None


@dataclass
class ApprovalRequest:
    request_id: str
    ticker: str
    created_at: str                          # ISO KST
    strength: str                            # SignalStrength.value
    current_price: float

    # 시그널 분석
    signal_score: float
    signal_threshold: float
    signal_reasons: list                     # ["정배열(+0.18)", ...]

    # 룰북 기대치
    win_rate: float
    fitness: float
    target_price: float
    stop_price: float
    trailing_stop: float
    max_holding_days: int

    # 시장 컨텍스트
    market_score: float
    market_regime: str
    sector_score: float
    buy_multiplier: float

    # 상태
    options_krw: list                         # [30000, 50000, 100000]
    status: str = "pending"                   # pending / approved / rejected / expired / reevaluating
    approved_krw: int = 0
    approved_at: str = ""
    reconfirm_count: int = 0                  # 1시간 재알림 횟수

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalRequest":
        return cls(**d)


# ==========================================================
# ApprovalManager
# ==========================================================
class ApprovalManager:
    """추가 매수 승인 요청 매니저."""

    def __init__(self):
        self._requests: Dict[str, ApprovalRequest] = {}      # request_id → request
        self._by_ticker: Dict[str, str] = {}                 # ticker → latest request_id (pending)
        self._reconfirm_last: Dict[str, float] = {}          # ticker → last reconfirm epoch
        self._load()
        log.info(f"ApprovalManager 초기화: pending {len(self._requests)}건")

    # ---------- 영속화 ----------
    def _load(self) -> None:
        if not APPROVALS_PATH.exists():
            return
        try:
            data = json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))
            for rid, rd in data.get("requests", {}).items():
                req = ApprovalRequest.from_dict(rd)
                self._requests[rid] = req
                if req.status == "pending":
                    self._by_ticker[req.ticker] = rid
            self._reconfirm_last = data.get("reconfirm_last", {})
            log.info(f"approvals.json 로드: {len(self._requests)}건")
        except Exception as e:
            log.error(f"approvals.json 로드 실패: {e}")

    def _save(self) -> None:
        APPROVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {
                "requests": {rid: r.to_dict() for rid, r in self._requests.items()},
                "reconfirm_last": self._reconfirm_last,
            }
            APPROVALS_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            log.error(f"approvals.json 저장 실패: {e}")

    # ---------- 등록 ----------
    def create_request(
        self,
        ticker: str,
        strength: SignalStrength,
        current_price: float,
        signal_score: float,
        signal_threshold: float,
        signal_reasons: list,
        win_rate: float,
        fitness: float,
        target_price: float,
        stop_price: float,
        trailing_stop: float,
        max_holding_days: int,
        market_score: float,
        market_regime: str,
        sector_score: float,
        buy_multiplier: float,
    ) -> ApprovalRequest:
        """새 승인 요청 생성. 같은 ticker에 기존 pending 있으면 expire 처리."""
        # 같은 ticker 기존 pending → 만료
        if ticker in self._by_ticker:
            old_rid = self._by_ticker[ticker]
            old_req = self._requests.get(old_rid)
            if old_req and old_req.status == "pending":
                old_req.status = "expired"
                log.info(f"{ticker} 이전 pending 요청 {old_rid[:8]} 만료")

        rid = uuid.uuid4().hex[:12]
        req = ApprovalRequest(
            request_id=rid,
            ticker=ticker,
            created_at=datetime.now(KST).isoformat(),
            strength=strength.value,
            current_price=current_price,
            signal_score=signal_score,
            signal_threshold=signal_threshold,
            signal_reasons=signal_reasons,
            win_rate=win_rate,
            fitness=fitness,
            target_price=target_price,
            stop_price=stop_price,
            trailing_stop=trailing_stop,
            max_holding_days=max_holding_days,
            market_score=market_score,
            market_regime=market_regime,
            sector_score=sector_score,
            buy_multiplier=buy_multiplier,
            options_krw=list(STRENGTH_LIMITS[strength]),
        )
        self._requests[rid] = req
        self._by_ticker[ticker] = rid
        self._save()
        log.info(
            f"[APPROVAL-CREATE] {ticker} {strength.value} "
            f"score={signal_score:.2f}/{signal_threshold:.2f} "
            f"options={req.options_krw}"
        )
        return req

    # ---------- 조회 ----------
    def get_latest_pending(self, ticker: str) -> Optional[ApprovalRequest]:
        rid = self._by_ticker.get(ticker)
        if not rid:
            return None
        req = self._requests.get(rid)
        if req and req.status == "pending":
            return req
        return None

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        return self._requests.get(request_id)

    def all_pending(self) -> list[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == "pending"]

    # ---------- 승인 / 거부 / 만료 ----------
    def approve(self, request_id: str, approved_krw: int) -> tuple[bool, str, Optional[ApprovalRequest]]:
        """승인 처리. 60초 초과면 (False, "재평가 필요", req) 반환 → 호출자가 재평가."""
        req = self._requests.get(request_id)
        if not req:
            return False, "요청을 찾을 수 없음", None
        if req.status != "pending":
            return False, f"이미 처리됨 (status={req.status})", req

        created = datetime.fromisoformat(req.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=KST)
        elapsed = (datetime.now(KST) - created).total_seconds()

        if elapsed > REEVAL_AFTER_SEC:
            req.status = "reevaluating"
            self._save()
            log.info(f"[APPROVAL-REEVAL] {req.ticker} elapsed={elapsed:.0f}s → 재평가 필요")
            return False, f"요청 후 {elapsed:.0f}초 경과 → 재평가 필요", req

        req.status = "approved"
        req.approved_krw = approved_krw
        req.approved_at = datetime.now(KST).isoformat()
        if req.ticker in self._by_ticker and self._by_ticker[req.ticker] == request_id:
            del self._by_ticker[req.ticker]
        self._save()
        log.info(f"[APPROVAL-APPROVE] {req.ticker} {approved_krw:,}원 (elapsed={elapsed:.0f}s)")
        return True, "승인 완료", req

    def confirm_after_reeval(
        self, request_id: str, approved_krw: int, new_signal_ok: bool
    ) -> tuple[bool, str, Optional[ApprovalRequest]]:
        """재평가 후 재확인. new_signal_ok=True면 진행, False면 거부."""
        req = self._requests.get(request_id)
        if not req:
            return False, "요청을 찾을 수 없음", None
        if not new_signal_ok:
            req.status = "rejected"
            if req.ticker in self._by_ticker and self._by_ticker[req.ticker] == request_id:
                del self._by_ticker[req.ticker]
            self._save()
            log.info(f"[APPROVAL-REEVAL-FAIL] {req.ticker} 시그널 약화로 자동 거부")
            return False, "재평가 결과 시그널 약화 → 거부됨", req
        req.status = "approved"
        req.approved_krw = approved_krw
        req.approved_at = datetime.now(KST).isoformat()
        if req.ticker in self._by_ticker and self._by_ticker[req.ticker] == request_id:
            del self._by_ticker[req.ticker]
        self._save()
        log.info(f"[APPROVAL-REEVAL-OK] {req.ticker} {approved_krw:,}원 재평가 통과")
        return True, "재평가 통과 → 승인", req

    def reject(self, request_id: str) -> tuple[bool, str]:
        req = self._requests.get(request_id)
        if not req:
            return False, "요청을 찾을 수 없음"
        if req.status != "pending" and req.status != "reevaluating":
            return False, f"이미 처리됨 (status={req.status})"
        req.status = "rejected"
        if req.ticker in self._by_ticker and self._by_ticker[req.ticker] == request_id:
            del self._by_ticker[req.ticker]
        self._save()
        log.info(f"[APPROVAL-REJECT] {req.ticker}")
        return True, "거부 처리됨"

    # ---------- 1시간 재알림 ----------
    def should_reconfirm(self, ticker: str) -> bool:
        """이미 보유 중이고 강한 시그널 유지 → 1시간마다 한 번씩 True."""
        last = self._reconfirm_last.get(ticker, 0.0)
        return (time.time() - last) >= RECONFIRM_INTERVAL_SEC

    def mark_reconfirmed(self, ticker: str) -> None:
        self._reconfirm_last[ticker] = time.time()
        self._save()


# ==========================================================
# 단위 테스트
# ==========================================================
if __name__ == "__main__":
    import logging as _lg
    _lg.basicConfig(level=_lg.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    print("=" * 60)
    print("ApprovalManager 단위 테스트")
    print("=" * 60)

    # 격리
    test_path = Path("/tmp/test_approvals.json")
    if test_path.exists():
        test_path.unlink()
    import engine.live.approval_manager as am_mod
    am_mod.APPROVALS_PATH = test_path

    # ---------- [1] classify_strength ----------
    print("\n[1] classify_strength")
    s1 = classify_strength(score=8.0, threshold=3.78, win_rate=0.81, regime="bull", sector_score=85)
    s2 = classify_strength(score=6.0, threshold=3.78, win_rate=0.75, regime="bull", sector_score=60)
    s3 = classify_strength(score=4.7, threshold=3.78, win_rate=0.65, regime="neutral", sector_score=50)
    s4 = classify_strength(score=3.0, threshold=3.78, win_rate=0.81, regime="bull", sector_score=85)
    print(f"  score=8.0/3.78 win=0.81 bull sec=85 → {s1}")
    print(f"  score=6.0/3.78 win=0.75 bull sec=60 → {s2}")
    print(f"  score=4.7/3.78 win=0.65 neutral    → {s3}")
    print(f"  score=3.0/3.78 win=0.81 bull       → {s4} (None = 강하지 않음)")
    assert s1 == SignalStrength.STRONG
    assert s2 == SignalStrength.MEDIUM
    assert s3 == SignalStrength.WEAK
    assert s4 is None
    print("  ✅ 강도 분류 4/4 통과")

    # ---------- [2] create_request ----------
    print("\n[2] create_request")
    mgr = am_mod.ApprovalManager()
    req = mgr.create_request(
        ticker="379800",
        strength=SignalStrength.MEDIUM,
        current_price=13500.0,
        signal_score=5.8,
        signal_threshold=3.78,
        signal_reasons=["정배열(+0.18)", "MACD골든(+1.21)", "BB근접(+1.25)"],
        win_rate=0.81,
        fitness=60.68,
        target_price=14200.0,
        stop_price=12800.0,
        trailing_stop=13000.0,
        max_holding_days=13,
        market_score=88.9,
        market_regime="bull",
        sector_score=80.0,
        buy_multiplier=1.33,
    )
    print(f"  request_id={req.request_id} strength={req.strength}")
    print(f"  options={req.options_krw}")
    assert req.status == "pending"
    assert req.options_krw == [30_000, 50_000, 100_000]
    latest = mgr.get_latest_pending("379800")
    assert latest is not None and latest.request_id == req.request_id
    print("  ✅ 요청 생성 OK")

    # ---------- [3] 60초 이내 승인 ----------
    print("\n[3] 60초 이내 승인 (즉시 통과)")
    ok, msg, r = mgr.approve(req.request_id, 50_000)
    print(f"  result={ok} msg='{msg}' approved_krw={r.approved_krw if r else 0}")
    assert ok and r.status == "approved" and r.approved_krw == 50_000
    print("  ✅ 즉시 승인 통과")

    # ---------- [4] 같은 ticker 중복 요청 → 이전 만료 ----------
    print("\n[4] 같은 ticker 중복 → 이전 자동 expire")
    req2 = mgr.create_request(
        ticker="379800", strength=SignalStrength.WEAK, current_price=13550,
        signal_score=4.7, signal_threshold=3.78, signal_reasons=["정배열"],
        win_rate=0.65, fitness=55, target_price=14000, stop_price=12900,
        trailing_stop=13100, max_holding_days=10,
        market_score=80, market_regime="bull", sector_score=55, buy_multiplier=1.1,
    )
    req3 = mgr.create_request(
        ticker="379800", strength=SignalStrength.STRONG, current_price=13600,
        signal_score=9.0, signal_threshold=3.78, signal_reasons=["전부 강함"],
        win_rate=0.81, fitness=60, target_price=14500, stop_price=12700,
        trailing_stop=13200, max_holding_days=13,
        market_score=88, market_regime="bull", sector_score=85, buy_multiplier=1.4,
    )
    assert mgr.get_request(req2.request_id).status == "expired"
    assert mgr.get_request(req3.request_id).status == "pending"
    print(f"  req2 → {mgr.get_request(req2.request_id).status}")
    print(f"  req3 → {mgr.get_request(req3.request_id).status}")
    print("  ✅ 중복 요청 처리 OK")

    # ---------- [5] 60초 초과 → 재평가 필요 ----------
    print("\n[5] 60초 초과 시뮬레이션 → reevaluating")
    # created_at을 인위적으로 70초 전으로 조작
    old_time = (datetime.now(KST) - __import__("datetime").timedelta(seconds=70)).isoformat()
    req3.created_at = old_time
    mgr._save()
    ok, msg, r = mgr.approve(req3.request_id, 50_000)
    print(f"  result={ok} msg='{msg}'")
    assert not ok and r.status == "reevaluating"
    print("  ✅ 재평가 라우팅 통과")

    # ---------- [6] 재평가 후 확정 (시그널 유지) ----------
    print("\n[6] 재평가 OK → 진행")
    ok, msg, r = mgr.confirm_after_reeval(req3.request_id, 100_000, new_signal_ok=True)
    print(f"  result={ok} msg='{msg}' approved={r.approved_krw}")
    assert ok and r.status == "approved" and r.approved_krw == 100_000
    print("  ✅ 재평가 통과 시 승인")

    # ---------- [7] 재평가 후 거부 (시그널 약화) ----------
    print("\n[7] 재평가 시그널 약화 → 거부")
    req4 = mgr.create_request(
        ticker="360750", strength=SignalStrength.MEDIUM, current_price=18200,
        signal_score=5.5, signal_threshold=3.65, signal_reasons=["정배열"],
        win_rate=0.79, fitness=59, target_price=19000, stop_price=17500,
        trailing_stop=17800, max_holding_days=14,
        market_score=85, market_regime="bull", sector_score=72, buy_multiplier=1.3,
    )
    req4.created_at = (datetime.now(KST) - __import__("datetime").timedelta(seconds=80)).isoformat()
    mgr._save()
    ok, _, _ = mgr.approve(req4.request_id, 50_000)
    assert not ok
    ok2, msg2, r2 = mgr.confirm_after_reeval(req4.request_id, 50_000, new_signal_ok=False)
    print(f"  result={ok2} msg='{msg2}' status={r2.status}")
    assert not ok2 and r2.status == "rejected"
    print("  ✅ 재평가 실패 시 거부")

    # ---------- [8] 1시간 재알림 로직 ----------
    print("\n[8] should_reconfirm / mark_reconfirmed")
    assert mgr.should_reconfirm("379800") is True
    mgr.mark_reconfirmed("379800")
    assert mgr.should_reconfirm("379800") is False
    # 강제로 옛날로 되돌리기
    mgr._reconfirm_last["379800"] = time.time() - 4000
    assert mgr.should_reconfirm("379800") is True
    print("  ✅ 1시간 재알림 로직 OK")

    # ---------- [9] 영속화 ----------
    print("\n[9] 영속화 검증 (재로딩)")
    mgr2 = am_mod.ApprovalManager()
    assert mgr2.get_request(req.request_id).status == "approved"
    assert mgr2.get_request(req3.request_id).status == "approved"
    assert mgr2.get_request(req4.request_id).status == "rejected"
    print(f"  로드된 총 요청: {len(mgr2._requests)}")
    print("  ✅ 영속화 OK")

    # cleanup
    test_path.unlink(missing_ok=True)
    print("\n" + "=" * 60)
    print("✅ ApprovalManager 검증 완료 (9/9)")
    print("=" * 60)
