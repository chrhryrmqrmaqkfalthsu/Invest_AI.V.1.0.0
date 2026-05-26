"""
PositionManager - 보유 종목 자동 청산 매니저.

책임:
  1. 매수 체결 직후 진입 정보 등록 (entry_price, stop, target, max_holding_days)
  2. 매분 tick에서 현재가 vs stop/target 비교
  3. 청산 조건 충족시 자동 매도 + 텔레그램 알림
  4. 청산 결과를 trade_log.csv에 기록 (학습 피드백용)

청산 전략 (Rulebook.exit_strategy):
  - 'fixed'    : 진입가 기준 고정 손절/익절
  - 'trailing' : 최고가 추적 트레일링 스톱
  - 'hybrid'   : 트레일링 + 익절(고정) 병행
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from engine.live.broker.base import Broker, OrderStatus, OrderType
from engine.strategies.rulebook import Rulebook

log = logging.getLogger("position_manager")

POSITIONS_PATH = Path("data/_system/positions.json")
TRADE_LOG_PATH = Path("data/_system/trade_log.csv")
KST = ZoneInfo("Asia/Seoul")


@dataclass
class PositionEntry:
    """단일 포지션의 청산 메타데이터"""
    ticker: str
    entry_date: str
    entry_price: float
    shares: int
    atr_at_entry: float
    stop_price: float
    target_price: float
    trailing_distance: float
    trailing_stop: float
    highest_price: float
    exit_strategy: str
    max_holding_days: int
    rulebook_direction: str
    # 대시보드용 (default로 기존 JSON 호환)
    win_rate_at_entry: float = 0.0
    signal_score_at_entry: float = 0.0
    signal_threshold_at_entry: float = 0.0
    total_invested_krw: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PositionEntry":
        return cls(**d)


class PositionManager:
    """보유 종목 자동 청산 매니저."""

    def __init__(self):
        self._positions: Dict[str, PositionEntry] = {}
        self._load()
        log.info(f"PositionManager 초기화: 추적 중 {len(self._positions)}건")

    def _load(self) -> None:
        if not POSITIONS_PATH.exists():
            return
        try:
            with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._positions = {
                t: PositionEntry.from_dict(d) for t, d in data.items()
            }
            log.info(f"positions.json 로드: {len(self._positions)}건")
        except Exception as e:
            log.error(f"positions.json 로드 실패: {e}")
            self._positions = {}

    def _save(self) -> None:
        POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {t: p.to_dict() for t, p in self._positions.items()},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            log.error(f"positions.json 저장 실패: {e}")

    def register_entry(
        self,
        ticker: str,
        entry_price: float,
        shares: int,
        rulebook: Rulebook,
        atr_value: float,
    ) -> PositionEntry:
        """매수 체결 후 호출. ATR 기반 stop/target 계산."""
        stop = entry_price - rulebook.stop_loss_atr * atr_value
        target = entry_price + rulebook.take_profit_atr * atr_value
        trail_dist = rulebook.trailing_atr * atr_value
        trailing = entry_price - trail_dist

        entry = PositionEntry(
            ticker=ticker,
            entry_date=datetime.now(KST).isoformat(),
            entry_price=entry_price,
            shares=shares,
            atr_at_entry=atr_value,
            stop_price=stop,
            target_price=target,
            trailing_distance=trail_dist,
            trailing_stop=trailing,
            highest_price=entry_price,
            exit_strategy=rulebook.exit_strategy,
            max_holding_days=int(rulebook.max_holding_days),
            rulebook_direction=rulebook.direction,
        )
        # 대시보드용 메타
        entry.win_rate_at_entry = float(getattr(rulebook, "win_rate", 0.0) or 0.0)
        entry.total_invested_krw = float(entry_price * shares)
        self._positions[ticker] = entry
        self._save()
        log.info(
            f"[ENTRY] {ticker} 등록: entry={entry_price:,.0f} "
            f"stop={stop:,.0f}({(stop/entry_price-1)*100:+.2f}%) "
            f"target={target:,.0f}({(target/entry_price-1)*100:+.2f}%) "
            f"strategy={rulebook.direction}/{rulebook.exit_strategy}"
        )
        return entry

    def add_to_position(
        self,
        ticker: str,
        add_price: float,
        add_shares: int,
        rulebook,
        atr_value: float,
    ) -> Optional[PositionEntry]:
        """추가 매수 시 평균가/stop/target/trailing 재계산.

        - 새 평균가 기준으로 stop/target 재산정
        - trailing_stop은 기존값과 새 계산값 중 큰 쪽 (보수적)
        - entry_date / win_rate_at_entry 등 진입 메타는 유지
        """
        pos = self._positions.get(ticker)
        if pos is None:
            log.warning(f"{ticker} add_to_position: 기존 포지션 없음 → register_entry로 위임")
            return self.register_entry(ticker, add_price, add_shares, rulebook, atr_value)

        old_shares = pos.shares
        old_invested = pos.entry_price * old_shares
        new_shares = old_shares + add_shares
        new_invested = old_invested + add_price * add_shares
        new_avg = new_invested / new_shares if new_shares > 0 else add_price

        stop = new_avg - rulebook.stop_loss_atr * atr_value
        target = new_avg + rulebook.take_profit_atr * atr_value
        trail_dist = rulebook.trailing_atr * atr_value
        new_trailing = new_avg - trail_dist

        pos.shares = new_shares
        pos.entry_price = new_avg
        pos.atr_at_entry = atr_value
        pos.stop_price = stop
        pos.target_price = target
        pos.trailing_distance = trail_dist
        pos.trailing_stop = max(pos.trailing_stop, new_trailing)
        pos.highest_price = max(pos.highest_price, add_price)
        pos.total_invested_krw = float(new_invested)
        self._save()

        log.info(
            f"[ADD-BUY] {ticker} +{add_shares}주 @ {add_price:,.0f} → "
            f"총 {new_shares}주 평균 {new_avg:,.0f}, "
            f"stop={stop:,.0f} target={target:,.0f} trail={pos.trailing_stop:,.0f}"
        )
        return pos

    def unregister(self, ticker: str) -> None:
        if ticker in self._positions:
            del self._positions[ticker]
            self._save()

    def get(self, ticker: str) -> Optional[PositionEntry]:
        return self._positions.get(ticker)

    def all(self) -> List[PositionEntry]:
        return list(self._positions.values())

    def check_exits(self, broker: Broker, notifier=None) -> List[dict]:
        """모든 보유 종목 청산 체크. 트리거된 종목은 매도 발사."""
        exited = []
        for ticker, pos in list(self._positions.items()):
            try:
                exit_info = self._check_one(ticker, pos, broker, notifier)
                if exit_info:
                    exited.append(exit_info)
            except Exception as e:
                log.error(f"{ticker} 청산 체크 실패: {e}")
        return exited

    def _check_one(
        self, ticker: str, pos: PositionEntry, broker: Broker, notifier=None,
    ) -> Optional[dict]:
        price = broker.get_current_price(ticker)
        if price is None:
            log.warning(f"{ticker} 현재가 조회 실패, 청산 체크 skip")
            return None

        holdings = {h.ticker: h for h in broker.get_holdings()}
        if ticker not in holdings or holdings[ticker].shares <= 0:
            log.info(f"{ticker} broker에 보유 없음 → unregister")
            self.unregister(ticker)
            return None

        actual_shares = holdings[ticker].shares

        # 최고가 갱신 + 트레일링 스톱 끌어올리기
        if price > pos.highest_price:
            pos.highest_price = price
            new_trailing = price - pos.trailing_distance
            if new_trailing > pos.trailing_stop:
                pos.trailing_stop = new_trailing
                self._save()

        entry_dt = datetime.fromisoformat(pos.entry_date)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=KST)
        holding_days = (datetime.now(KST) - entry_dt).days

        exit_reason = None
        strategy = pos.exit_strategy

        if strategy == "fixed":
            if price <= pos.stop_price:
                exit_reason = "stop_loss"
            elif price >= pos.target_price:
                exit_reason = "take_profit"
            elif holding_days >= pos.max_holding_days:
                exit_reason = "time_out"
        elif strategy == "trailing":
            if price <= pos.trailing_stop:
                exit_reason = "trailing"
            elif holding_days >= pos.max_holding_days:
                exit_reason = "time_out"
        elif strategy == "hybrid":
            if price >= pos.target_price:
                exit_reason = "take_profit"
            elif price <= pos.trailing_stop:
                exit_reason = "trailing"
            elif price <= pos.stop_price:
                exit_reason = "stop_loss"
            elif holding_days >= pos.max_holding_days:
                exit_reason = "time_out"
        else:
            log.warning(f"{ticker} unknown exit_strategy: {strategy}")

        if exit_reason is None:
            return None

        log.info(
            f"[EXIT-TRIGGER] {ticker} {exit_reason}: "
            f"price={price:,.0f}, entry={pos.entry_price:,.0f}, "
            f"PnL={(price/pos.entry_price-1)*100:+.2f}%, hold={holding_days}일"
        )

        try:
            order = broker.place_sell(ticker, actual_shares, OrderType.MARKET)
        except Exception as e:
            log.error(f"{ticker} 매도 발사 실패: {e}")
            if notifier:
                try:
                    notifier.send_error(f"{ticker} 자동 매도 실패: {e}")
                except Exception:
                    pass
            return None

        if order.status != OrderStatus.FILLED:
            log.warning(f"{ticker} 매도 미체결: {order.status.value}")

        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
        pnl_krw = (price - pos.entry_price) * actual_shares
        trade_record = {
            "exited_at": datetime.now(KST).isoformat(),
            "ticker": ticker,
            "direction": pos.rulebook_direction,
            "entry_date": pos.entry_date,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "shares": actual_shares,
            "exit_reason": exit_reason,
            "holding_days": holding_days,
            "highest_price": pos.highest_price,
            "pnl_pct": round(pnl_pct, 3),
            "pnl_krw": round(pnl_krw, 0),
            "exit_strategy": pos.exit_strategy,
        }
        self._append_trade_log(trade_record)

        if notifier:
            try:
                emoji = {
                    "take_profit": "🟢",
                    "stop_loss": "🔴",
                    "trailing": "🟡",
                    "time_out": "⏰",
                }.get(exit_reason, "⚪")
                msg = (
                    f"{emoji} 자동 청산: {ticker}\n"
                    f"사유: {exit_reason}\n"
                    f"진입 {pos.entry_price:,.0f} → 청산 {price:,.0f}\n"
                    f"손익: {pnl_krw:+,.0f}원 ({pnl_pct:+.2f}%)\n"
                    f"보유: {holding_days}일\n"
                    f"최고가: {pos.highest_price:,.0f}"
                )
                notifier.send(msg)
            except Exception as e:
                log.warning(f"청산 알림 실패: {e}")

        self.unregister(ticker)
        return trade_record

    def _append_trade_log(self, record: dict) -> None:
        TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_exists = TRADE_LOG_PATH.exists()
        try:
            with open(TRADE_LOG_PATH, "a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(record.keys()))
                if not file_exists:
                    writer.writeheader()
                writer.writerow(record)
        except Exception as e:
            log.error(f"trade_log 기록 실패: {e}")


# ==========================================================
# 단위 테스트
# ==========================================================
if __name__ == "__main__":
    import logging as _lg
    import os
    _lg.basicConfig(
        level=_lg.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    print("=" * 60)
    print("PositionManager 단위 테스트")
    print("=" * 60)

    test_pos_path = Path("/tmp/test_positions.json")
    test_log_path = Path("/tmp/test_trade_log.csv")
    if test_pos_path.exists(): test_pos_path.unlink()
    if test_log_path.exists(): test_log_path.unlink()

    import engine.live.position_manager as pm_mod
    pm_mod.POSITIONS_PATH = test_pos_path
    pm_mod.TRADE_LOG_PATH = test_log_path

    from engine.strategies.rulebook import Rulebook
    rb = Rulebook(
        ticker="379800", direction="long",
        stop_loss_atr=2.0, take_profit_atr=3.0,
        trailing_atr=1.5, max_holding_days=20,
        exit_strategy="hybrid",
    )

    from engine.live.broker.paper import PaperBroker
    paper_state = Path("/tmp/test_pm_paper.json")
    if paper_state.exists(): paper_state.unlink()
    os.environ["PAPER_STATE_PATH"] = str(paper_state)
    broker = PaperBroker(initial_cash=1_000_000)

    class FakeNotifier:
        def __init__(self): self.sent = []
        def send(self, msg):
            self.sent.append(msg)
            print(f"  📱 알림: {msg.splitlines()[0]}")
        def send_error(self, msg): self.sent.append(msg)

    notifier = FakeNotifier()
    pm = pm_mod.PositionManager()

    print("\n[1] 진입 등록: 379800 @ 13,000원, ATR=200원")
    pm.register_entry("379800", 13000, 1, rb, 200)
    p = pm.get("379800")
    print(f"  stop={p.stop_price:.0f}, target={p.target_price:.0f}, trailing={p.trailing_stop:.0f}")
    assert p.stop_price == 12600
    assert p.target_price == 13600
    assert p.trailing_stop == 12700
    broker.place_buy("379800", 1, OrderType.MARKET)

    print("\n[2] 현재가 13,200원 (보유 지속 기대)")
    broker.get_current_price = lambda t: 13200.0
    exited = pm.check_exits(broker, notifier)
    assert exited == []
    print(f"  ✅ 청산 안 됨")

    print("\n[3] 현재가 13,700원 (익절 트리거)")
    broker.get_current_price = lambda t: 13700.0
    exited = pm.check_exits(broker, notifier)
    assert len(exited) == 1
    assert exited[0]["exit_reason"] == "take_profit"
    print(f"  ✅ 익절: pnl_pct={exited[0]['pnl_pct']}")
    assert pm.get("379800") is None

    print("\n[4] 새 진입 → 손절 트리거 (fixed 전략)")
    rb_fixed = Rulebook(
        ticker="379800", direction="long",
        stop_loss_atr=2.0, take_profit_atr=3.0,
        trailing_atr=1.5, max_holding_days=20,
        exit_strategy="fixed",
    )
    if paper_state.exists(): paper_state.unlink()
    broker2 = PaperBroker(initial_cash=1_000_000)
    broker2.place_buy("379800", 1, OrderType.MARKET)
    pm.register_entry("379800", 13000, 1, rb_fixed, 200)
    broker2.get_current_price = lambda t: 12500.0
    exited = pm.check_exits(broker2, notifier)
    assert len(exited) == 1
    assert exited[0]["exit_reason"] == "stop_loss"
    print(f"  ✅ 손절: pnl_pct={exited[0]['pnl_pct']}")

    print("\n[5] 트레일링: 가격이 오르면 stop도 따라 올라감")
    if paper_state.exists(): paper_state.unlink()
    broker3 = PaperBroker(initial_cash=1_000_000)
    broker3.place_buy("379800", 1, OrderType.MARKET)
    rb_trail = Rulebook(
        ticker="379800", direction="long",
        stop_loss_atr=2.0, take_profit_atr=10.0,
        trailing_atr=1.5, max_holding_days=20,
        exit_strategy="trailing",
    )
    pm.register_entry("379800", 13000, 1, rb_trail, 200)
    broker3.get_current_price = lambda t: 14000.0
    pm.check_exits(broker3, notifier)
    p = pm.get("379800")
    assert p is not None
    assert p.highest_price == 14000
    expected_trail = 14000 - 1.5 * 200
    print(f"  highest=14000 → trailing_stop={p.trailing_stop:.0f} (기대 {expected_trail:.0f})")
    assert abs(p.trailing_stop - expected_trail) < 0.01
    broker3.get_current_price = lambda t: p.trailing_stop - 10
    exited = pm.check_exits(broker3, notifier)
    assert len(exited) == 1
    assert exited[0]["exit_reason"] == "trailing"
    print(f"  ✅ 트레일링 청산: pnl_pct={exited[0]['pnl_pct']:+.2f}")

    print("\n[6] trade_log.csv 확인")
    assert test_log_path.exists()
    with open(test_log_path) as f:
        rows = list(csv.DictReader(f))
    print(f"  기록된 청산: {len(rows)}건")
    assert len(rows) == 3
    for r in rows:
        print(f"    - {r['ticker']} {r['exit_reason']:12s} pnl={float(r['pnl_pct']):>+8.2f}%")

    test_pos_path.unlink(missing_ok=True)
    test_log_path.unlink(missing_ok=True)
    if paper_state.exists(): paper_state.unlink()

    print("\n" + "=" * 60)
    print("✅ PositionManager 검증 완료")
    print("=" * 60)
