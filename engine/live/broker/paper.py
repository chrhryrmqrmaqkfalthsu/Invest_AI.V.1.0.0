"""
PaperBroker — 모의 매매 (실제 시장가격 + 가상 잔고)
- 시세는 pykrx/yfinance에서 실시간 조회 (Adapter 활용)
- 주문은 즉시 체결로 가정, 슬리피지 적용
- 상태는 data/_system/paper_state.json에 영구 저장
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

from engine.core.logger import get_logger
from engine.core import config as config_mod
from engine.adapters.factory import get_adapter
from engine.live.broker.base import (
    Broker, Order, Holding, Balance,
    OrderSide, OrderType, OrderStatus, BrokerError,
)

log = get_logger("paper_broker")

DEFAULT_INITIAL_CASH = 1_000_000.0   # 100만원
COMMISSION_RATE = 0.00015            # 0.015% (KIS 모의/실전 동일 수준)
SLIPPAGE_RATE = 0.0002               # 0.02% 슬리피지


def _state_path() -> Path:
    return config_mod.PROJECT_ROOT / "data" / "_system" / "paper_state.json"


class PaperBroker(Broker):
    def __init__(self, initial_cash: float = DEFAULT_INITIAL_CASH):
        self._state = self._load_state(initial_cash)

    @property
    def mode(self) -> str:
        return "paper"

    # ---------- State ----------
    def _load_state(self, initial_cash: float) -> dict:
        p = _state_path()
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    s = json.load(f)
                log.info(f"paper state loaded: cash={s.get('cash',0):,.0f}, "
                         f"holdings={len(s.get('holdings',{}))}")
                return s
            except Exception as e:
                log.warning(f"paper state load failed: {e}, initializing")
        s = {
            "cash": initial_cash,
            "holdings": {},       # ticker -> {"shares", "avg_cost"}
            "orders": [],         # 모든 주문 기록
            "created_at": datetime.now().isoformat(),
        }
        self._save_state(s)
        log.info(f"paper state initialized: cash={initial_cash:,.0f}")
        return s

    def _save_state(self, s: Optional[dict] = None) -> None:
        s = s or self._state
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        tmp.replace(p)

    # ---------- 시세 ----------
    def get_current_price(self, ticker: str) -> Optional[float]:
        try:
            adapter = get_adapter(ticker)
            price = adapter.current_price()
            if price is None or price <= 0:
                return None
            return float(price)
        except Exception as e:
            log.warning(f"get_current_price({ticker}) failed: {e}")
            return None

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        # ticker 주면 해당 종목 거래소 기준, 없으면 한국 ETF 기준 (KRX)
        try:
            tk = ticker or "379800"  # KRX 대표
            adapter = get_adapter(tk)
            return bool(adapter.is_market_open())
        except Exception:
            return False

    # ---------- 잔고/보유 ----------
    def get_holdings(self) -> List[Holding]:
        out: List[Holding] = []
        for tk, pos in self._state.get("holdings", {}).items():
            shares = int(pos.get("shares", 0))
            avg_cost = float(pos.get("avg_cost", 0))
            if shares <= 0:
                continue
            cur = self.get_current_price(tk) or avg_cost
            mv = shares * cur
            cost = shares * avg_cost
            pnl = mv - cost
            pnl_pct = (pnl / cost * 100.0) if cost > 0 else 0.0
            out.append(Holding(
                ticker=tk, shares=shares, avg_cost=avg_cost,
                current_price=cur, market_value=mv,
                unrealized_pnl=pnl, unrealized_pnl_pct=pnl_pct,
            ))
        return out

    def get_balance(self) -> Balance:
        holdings = self.get_holdings()
        invested = sum(h.shares * h.avg_cost for h in holdings)
        market_val = sum(h.market_value for h in holdings)
        cash = float(self._state.get("cash", 0))
        return Balance(
            cash_krw=cash,
            total_value_krw=cash + market_val,
            invested_krw=invested,
            holdings=holdings,
            fetched_at=datetime.now().isoformat(),
        )

    # ---------- 주문 ----------
    def _make_order(self, side: OrderSide, ticker: str, shares: int,
                    order_type: OrderType, price: float) -> Order:
        return Order(
            order_id=f"P-{uuid.uuid4().hex[:10]}",
            ticker=ticker, side=side, order_type=order_type,
            shares=shares, price=price,
            status=OrderStatus.PENDING,
            submitted_at=datetime.now().isoformat(),
        )

    def place_buy(self, ticker: str, shares: int,
                  order_type: OrderType = OrderType.MARKET,
                  price: float = 0.0) -> Order:
        if shares <= 0:
            return self._reject(ticker, OrderSide.BUY, shares, order_type, price,
                                "shares must be > 0")
        cur = self.get_current_price(ticker)
        if cur is None:
            return self._reject(ticker, OrderSide.BUY, shares, order_type, price,
                                "current price unavailable")
        # 체결가: 시장가는 슬리피지 가산, 지정가는 그대로
        fill_price = cur * (1 + SLIPPAGE_RATE) if order_type == OrderType.MARKET else price
        notional = fill_price * shares
        commission = notional * COMMISSION_RATE
        total_cost = notional + commission

        cash = float(self._state.get("cash", 0))
        if total_cost > cash + 1e-6:
            return self._reject(ticker, OrderSide.BUY, shares, order_type, price,
                                f"insufficient cash: need {total_cost:,.0f}, have {cash:,.0f}")

        # 잔고 차감 + 포지션 누적
        self._state["cash"] = cash - total_cost
        holdings = self._state.setdefault("holdings", {})
        pos = holdings.get(ticker, {"shares": 0, "avg_cost": 0.0})
        prev_shares = int(pos.get("shares", 0))
        prev_avg = float(pos.get("avg_cost", 0))
        new_shares = prev_shares + shares
        # 평단가 = (기존비용 + 신규비용) / 신규수량
        new_avg = ((prev_shares * prev_avg) + (shares * fill_price)) / new_shares
        holdings[ticker] = {"shares": new_shares, "avg_cost": new_avg}

        order = self._make_order(OrderSide.BUY, ticker, shares, order_type, price)
        order.status = OrderStatus.FILLED
        order.filled_shares = shares
        order.filled_avg_price = fill_price
        order.commission = commission
        order.filled_at = datetime.now().isoformat()

        self._state.setdefault("orders", []).append(order.to_dict())
        self._save_state()
        log.info(f"[PAPER] BUY {ticker} {shares}주 @ {fill_price:,.0f} "
                 f"(수수료 {commission:.0f}, 잔고 {self._state['cash']:,.0f})")
        return order

    def place_sell(self, ticker: str, shares: int,
                   order_type: OrderType = OrderType.MARKET,
                   price: float = 0.0) -> Order:
        if shares <= 0:
            return self._reject(ticker, OrderSide.SELL, shares, order_type, price,
                                "shares must be > 0")
        holdings = self._state.setdefault("holdings", {})
        pos = holdings.get(ticker)
        if not pos or int(pos.get("shares", 0)) < shares:
            held = int(pos.get("shares", 0)) if pos else 0
            return self._reject(ticker, OrderSide.SELL, shares, order_type, price,
                                f"insufficient position: need {shares}, have {held}")
        cur = self.get_current_price(ticker)
        if cur is None:
            return self._reject(ticker, OrderSide.SELL, shares, order_type, price,
                                "current price unavailable")
        fill_price = cur * (1 - SLIPPAGE_RATE) if order_type == OrderType.MARKET else price
        notional = fill_price * shares
        commission = notional * COMMISSION_RATE
        proceeds = notional - commission

        # 잔고 + 포지션 반영
        self._state["cash"] = float(self._state.get("cash", 0)) + proceeds
        new_shares = int(pos["shares"]) - shares
        if new_shares <= 0:
            holdings.pop(ticker, None)
        else:
            pos["shares"] = new_shares
            holdings[ticker] = pos

        order = self._make_order(OrderSide.SELL, ticker, shares, order_type, price)
        order.status = OrderStatus.FILLED
        order.filled_shares = shares
        order.filled_avg_price = fill_price
        order.commission = commission
        order.filled_at = datetime.now().isoformat()

        self._state.setdefault("orders", []).append(order.to_dict())
        self._save_state()
        log.info(f"[PAPER] SELL {ticker} {shares}주 @ {fill_price:,.0f} "
                 f"(수수료 {commission:.0f}, 잔고 {self._state['cash']:,.0f})")
        return order

    def _reject(self, ticker: str, side: OrderSide, shares: int,
                order_type: OrderType, price: float, msg: str) -> Order:
        order = self._make_order(side, ticker, shares, order_type, price)
        order.status = OrderStatus.REJECTED
        order.message = msg
        self._state.setdefault("orders", []).append(order.to_dict())
        self._save_state()
        log.warning(f"[PAPER] REJECT {side.value.upper()} {ticker} {shares}주: {msg}")
        return order

    def cancel_order(self, order_id: str) -> bool:
        # paper는 즉시 체결이라 사실상 취소 불가. 미체결 케이스만 처리.
        for o in self._state.get("orders", []):
            if o.get("order_id") == order_id and o.get("status") == OrderStatus.PENDING.value:
                o["status"] = OrderStatus.CANCELLED.value
                self._save_state()
                return True
        return False

    def get_order(self, order_id: str) -> Optional[Order]:
        for o in self._state.get("orders", []):
            if o.get("order_id") == order_id:
                return Order(
                    order_id=o["order_id"], ticker=o["ticker"],
                    side=OrderSide(o["side"]), order_type=OrderType(o["order_type"]),
                    shares=o["shares"], price=o["price"],
                    status=OrderStatus(o["status"]),
                    filled_shares=o.get("filled_shares", 0),
                    filled_avg_price=o.get("filled_avg_price", 0.0),
                    commission=o.get("commission", 0.0),
                    submitted_at=o.get("submitted_at", ""),
                    filled_at=o.get("filled_at", ""),
                    message=o.get("message", ""),
                )
        return None

    # ---------- 유틸 ----------
    def reset(self, initial_cash: float = DEFAULT_INITIAL_CASH) -> None:
        """페이퍼 상태 초기화 (테스트용)"""
        self._state = {
            "cash": initial_cash, "holdings": {}, "orders": [],
            "created_at": datetime.now().isoformat(),
        }
        self._save_state()
        log.info(f"paper state reset: cash={initial_cash:,.0f}")


if __name__ == "__main__":
    print("=== PaperBroker 단위 테스트 ===")
    b = PaperBroker(initial_cash=1_000_000)
    b.reset(1_000_000)

    print("\n[1] 초기 잔고")
    bal = b.get_balance()
    print(f"  현금: {bal.cash_krw:,.0f} / 총자산: {bal.total_value_krw:,.0f} / 보유: {len(bal.holdings)}개")

    print("\n[2] 시세 조회 (379800)")
    price = b.get_current_price("379800")
    print(f"  현재가: {price}")

    print(f"\n[3] 매수 1주 @ 시장가")
    o1 = b.place_buy("379800", 1)
    print(f"  status: {o1.status.value}, fill: {o1.filled_avg_price:,.0f}, cmm: {o1.commission:.2f}")
    if o1.status == OrderStatus.REJECTED:
        print(f"  reason: {o1.message}")

    print("\n[4] 잔고 + 보유 확인")
    bal = b.get_balance()
    print(f"  현금: {bal.cash_krw:,.0f} / 총자산: {bal.total_value_krw:,.0f}")
    for h in bal.holdings:
        print(f"  {h.ticker}: {h.shares}주 @ {h.avg_cost:,.0f}, 평가 {h.market_value:,.0f}, "
              f"손익 {h.unrealized_pnl:+,.0f} ({h.unrealized_pnl_pct:+.2f}%)")

    print("\n[5] 추가매수 1주 (평단가 갱신)")
    o2 = b.place_buy("379800", 1)
    print(f"  status: {o2.status.value}, fill: {o2.filled_avg_price:,.0f}")
    bal = b.get_balance()
    for h in bal.holdings:
        print(f"  {h.ticker}: {h.shares}주 @ 평단 {h.avg_cost:,.0f}")

    print("\n[6] 매도 1주 (부분 청산)")
    o3 = b.place_sell("379800", 1)
    print(f"  status: {o3.status.value}, fill: {o3.filled_avg_price:,.0f}, cmm: {o3.commission:.2f}")
    bal = b.get_balance()
    print(f"  현금: {bal.cash_krw:,.0f}")
    for h in bal.holdings:
        print(f"  {h.ticker}: {h.shares}주 (평단 {h.avg_cost:,.0f})")

    print("\n[7] 잔량 매도 (전량 청산)")
    o4 = b.place_sell("379800", 1)
    print(f"  status: {o4.status.value}, fill: {o4.filled_avg_price:,.0f}")
    bal = b.get_balance()
    print(f"  현금: {bal.cash_krw:,.0f} / 보유: {len(bal.holdings)}개")

    print("\n[8] 과다 매도 시도 (거부 검증)")
    o5 = b.place_sell("379800", 100)
    print(f"  status: {o5.status.value}, msg: {o5.message}")

    print("\n[9] 잔고 부족 매수 시도 (거부 검증)")
    o6 = b.place_buy("379800", 100)
    print(f"  status: {o6.status.value}, msg: {o6.message}")

    print(f"\n✅ 누적 주문 수: {len(b._state['orders'])}건")
    print(f"   체결: {sum(1 for o in b._state['orders'] if o['status']=='filled')}건")
    print(f"   거부: {sum(1 for o in b._state['orders'] if o['status']=='rejected')}건")

