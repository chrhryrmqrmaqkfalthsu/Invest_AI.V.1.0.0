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
from engine.core.data_loader import get_current_price_with_source
from engine.adapters.factory import get_adapter
from engine.live.broker.base import (
    Broker, Order, Holding, Balance,
    OrderSide, OrderType, OrderStatus, BrokerError,
)

log = get_logger("paper_broker")

DEFAULT_INITIAL_CASH = 1_000_000.0   # 100만원
COMMISSION_RATE = 0.00015            # 0.015% (KIS 모의/실전 동일 수준)
SLIPPAGE_RATE = 0.0002               # 0.02% 슬리피지
PRICE_SANITY_LO = 0.80
PRICE_SANITY_HI = 1.20
SHARE_ROUND_DIGITS = 6
SHARE_EPS = 10 ** (-SHARE_ROUND_DIGITS)


def _state_path() -> Path:
    return config_mod.PROJECT_ROOT / "data" / "_system" / "paper_state.json"


def _audit_log_path() -> Path:
    return config_mod.PROJECT_ROOT / "data" / "_system" / "paper_trade_audit.jsonl"


def _to_shares(value) -> float:
    """기존 int 저장값과 신규 float 저장값을 모두 안전하게 float 수량으로 읽는다."""
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _normalize_shares(value: float) -> float:
    """소수점 주문 반복으로 생기는 미세 잔량을 6자리 기준으로 정리한다."""
    v = round(float(value), SHARE_ROUND_DIGITS)
    return 0.0 if abs(v) <= SHARE_EPS else v


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
        """기존 Broker 인터페이스 호환용: 가격 숫자만 반환."""
        q = get_current_price_with_source(ticker)
        if not q:
            return None
        price = q.get("price")
        if price is None or float(price) <= 0:
            return None
        return float(price)

    def _get_current_price_quote(self, ticker: str) -> Optional[dict]:
        """주문 감사용 현재가 + 소스 + 가격 sanity 정보를 반환한다."""
        q = get_current_price_with_source(ticker)
        if not q:
            return None
        price = q.get("price")
        if price is None or float(price) <= 0:
            return None
        quote_price = float(price)
        norm = q.get("normalized") or {}

        audit = {
            "audit_schema_version": 1,
            "quote_price": quote_price,
            "quote_source": q.get("source"),
            "quote_date": q.get("quote_date"),
            "quote_pykrx_error": q.get("pykrx_error"),
            "normalized_raw_ticker": norm.get("raw"),
            "normalized_krx_ticker": norm.get("krx"),
            "normalized_yf_ticker": norm.get("yf"),
            "normalized_is_kr": norm.get("is_kr"),
            "adapter_type": None,
            "history_last_close": None,
            "history_last_date": None,
            "price_sanity_ratio": None,
            "price_sanity_ok": None,
            "price_sanity_range": [PRICE_SANITY_LO, PRICE_SANITY_HI],
        }

        try:
            adapter = get_adapter(ticker)
            audit["adapter_type"] = type(adapter).__name__
            hist = adapter.load_history(years=1)
            if hist is not None and not hist.empty and "Close" in hist.columns:
                last = hist.iloc[-1]
                last_close = float(last["Close"])
                if last_close > 0:
                    audit["history_last_close"] = last_close
                    try:
                        audit["history_last_date"] = hist.index[-1].strftime("%Y-%m-%d")
                    except Exception:
                        audit["history_last_date"] = str(hist.index[-1])
                    ratio = quote_price / last_close
                    audit["price_sanity_ratio"] = ratio
                    audit["price_sanity_ok"] = bool(PRICE_SANITY_LO <= ratio <= PRICE_SANITY_HI)
        except Exception as e:
            audit["history_error"] = f"{type(e).__name__}: {e}"

        if audit.get("price_sanity_ok") is False:
            log.warning(
                f"[PAPER][PRICE_SANITY] {ticker} quote={quote_price:,.4f} "
                f"history_last={audit.get('history_last_close')}@{audit.get('history_last_date')} "
                f"ratio={audit.get('price_sanity_ratio'):.4f} source={audit.get('quote_source')} "
                f"yf={audit.get('normalized_yf_ticker')} krx={audit.get('normalized_krx_ticker')}"
            )
        return audit

    def _append_audit_log(self, record: dict) -> None:
        """append-only 감사 로그. 실패해도 주문 자체는 막지 않는다."""
        try:
            p = _audit_log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            out = dict(record)
            out.setdefault("audit_logged_at", datetime.now().isoformat())
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            log.warning(f"paper audit log append failed: {e}")

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
            shares = _normalize_shares(_to_shares(pos.get("shares", 0)))
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
    def _make_order(self, side: OrderSide, ticker: str, shares: float,
                    order_type: OrderType, price: float) -> Order:
        return Order(
            order_id=f"P-{uuid.uuid4().hex[:10]}",
            ticker=ticker, side=side, order_type=order_type,
            shares=shares, price=price,
            status=OrderStatus.PENDING,
            submitted_at=datetime.now().isoformat(),
        )

    def _store_order(self, order: Order, audit: Optional[dict] = None) -> dict:
        d = order.to_dict()
        if audit:
            d.update(audit)
        self._state.setdefault("orders", []).append(d)
        self._append_audit_log(d)
        return d

    def place_buy(self, ticker: str, shares: float,
                  order_type: OrderType = OrderType.MARKET,
                  price: float = 0.0,
                  client_order_id: str = "") -> Order:
        shares = _normalize_shares(shares)
        if shares <= 0:
            return self._reject(ticker, OrderSide.BUY, shares, order_type, price,
                                "shares must be > 0")
        quote = self._get_current_price_quote(ticker)
        if not quote:
            return self._reject(ticker, OrderSide.BUY, shares, order_type, price,
                                "current price unavailable")
        cur = float(quote["quote_price"])
        # 체결가: 시장가는 슬리피지 가산, 지정가는 그대로
        fill_price = cur * (1 + SLIPPAGE_RATE) if order_type == OrderType.MARKET else price
        notional = fill_price * shares
        commission = notional * COMMISSION_RATE
        total_cost = notional + commission

        cash = float(self._state.get("cash", 0))
        if total_cost > cash + 1e-6:
            return self._reject(ticker, OrderSide.BUY, shares, order_type, price,
                                f"insufficient cash: need {total_cost:,.2f}, have {cash:,.2f}")

        # 잔고 차감 + 포지션 누적
        self._state["cash"] = cash - total_cost
        holdings = self._state.setdefault("holdings", {})
        pos = holdings.get(ticker, {"shares": 0.0, "avg_cost": 0.0})
        prev_shares = _normalize_shares(_to_shares(pos.get("shares", 0)))
        prev_avg = float(pos.get("avg_cost", 0))
        new_shares = _normalize_shares(prev_shares + shares)
        # 평단가 = (기존비용 + 신규비용) / 신규수량
        new_avg = ((prev_shares * prev_avg) + (shares * fill_price)) / new_shares
        holdings[ticker] = {"shares": new_shares, "avg_cost": new_avg}

        order = self._make_order(OrderSide.BUY, ticker, shares, order_type, price)
        order.status = OrderStatus.FILLED
        order.filled_shares = shares
        order.filled_avg_price = fill_price
        order.commission = commission
        order.filled_at = datetime.now().isoformat()

        quote.update({
            "side": OrderSide.BUY.value,
            "fill_price": fill_price,
            "slippage_rate": SLIPPAGE_RATE if order_type == OrderType.MARKET else 0.0,
            "notional": notional,
            "cash_before": cash,
            "cash_after": self._state["cash"],
        })
        self._store_order(order, quote)
        self._save_state()
        log.info(f"[PAPER] BUY {ticker} {shares:g}주 @ {fill_price:,.4f} "
                 f"(수수료 {commission:.4f}, 잔고 {self._state['cash']:,.2f})")
        return order

    def place_sell(self, ticker: str, shares: float,
                   order_type: OrderType = OrderType.MARKET,
                   price: float = 0.0,
                   client_order_id: str = "") -> Order:
        shares = _normalize_shares(shares)
        if shares <= 0:
            return self._reject(ticker, OrderSide.SELL, shares, order_type, price,
                                "shares must be > 0")
        holdings = self._state.setdefault("holdings", {})
        pos = holdings.get(ticker)
        held_shares = _normalize_shares(_to_shares(pos.get("shares", 0))) if pos else 0.0
        if not pos or held_shares + SHARE_EPS < shares:
            return self._reject(ticker, OrderSide.SELL, shares, order_type, price,
                                f"insufficient position: need {shares:g}, have {held_shares:g}")
        quote = self._get_current_price_quote(ticker)
        if not quote:
            return self._reject(ticker, OrderSide.SELL, shares, order_type, price,
                                "current price unavailable")
        cur = float(quote["quote_price"])
        fill_price = cur * (1 - SLIPPAGE_RATE) if order_type == OrderType.MARKET else price
        notional = fill_price * shares
        commission = notional * COMMISSION_RATE
        proceeds = notional - commission

        # 잔고 + 포지션 반영
        cash_before = float(self._state.get("cash", 0))
        self._state["cash"] = cash_before + proceeds
        new_shares = _normalize_shares(held_shares - shares)
        if new_shares <= SHARE_EPS:
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

        quote.update({
            "side": OrderSide.SELL.value,
            "fill_price": fill_price,
            "slippage_rate": SLIPPAGE_RATE if order_type == OrderType.MARKET else 0.0,
            "notional": notional,
            "cash_before": cash_before,
            "cash_after": self._state["cash"],
        })
        self._store_order(order, quote)
        self._save_state()
        log.info(f"[PAPER] SELL {ticker} {shares:g}주 @ {fill_price:,.4f} "
                 f"(수수료 {commission:.4f}, 잔고 {self._state['cash']:,.2f})")
        return order

    def _reject(self, ticker: str, side: OrderSide, shares: float,
                order_type: OrderType, price: float, msg: str) -> Order:
        order = self._make_order(side, ticker, shares, order_type, price)
        order.status = OrderStatus.REJECTED
        order.message = msg
        self._store_order(order)
        self._save_state()
        log.warning(f"[PAPER] REJECT {side.value.upper()} {ticker} {shares:g}주: {msg}")
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
                    filled_shares=o.get("filled_shares", 0.0),
                    filled_avg_price=o.get("filled_avg_price", 0.0),
                    commission=o.get("commission", 0.0),
                    submitted_at=o.get("submitted_at", ""),
                    filled_at=o.get("filled_at", ""),
                    message=o.get("message", ""),
                )
        return None

    def get_open_orders(self) -> List[Order]:
        out: List[Order] = []
        for o in self._state.get("orders", []):
            try:
                status = OrderStatus(o.get("status"))
            except Exception:
                continue
            if status not in {OrderStatus.PENDING, OrderStatus.PARTIAL}:
                continue
            out.append(Order(
                order_id=o["order_id"], ticker=o["ticker"],
                side=OrderSide(o["side"]), order_type=OrderType(o["order_type"]),
                shares=o["shares"], price=o["price"],
                status=status, filled_shares=o.get("filled_shares", 0.0),
                filled_avg_price=o.get("filled_avg_price", 0.0),
                commission=o.get("commission", 0.0),
                submitted_at=o.get("submitted_at", ""), filled_at=o.get("filled_at", ""),
                message=o.get("message", ""),
            ))
        return out

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

    print(f"\n[3] 매수 0.5주 @ 시장가")
    o1 = b.place_buy("379800", 0.5)
    print(f"  status: {o1.status.value}, fill: {o1.filled_avg_price:,.4f}, cmm: {o1.commission:.4f}")
    if o1.status == OrderStatus.REJECTED:
        print(f"  reason: {o1.message}")

    print("\n[4] 잔고 + 보유 확인")
    bal = b.get_balance()
    print(f"  현금: {bal.cash_krw:,.2f} / 총자산: {bal.total_value_krw:,.2f}")
    for h in bal.holdings:
        print(f"  {h.ticker}: {h.shares:g}주 @ {h.avg_cost:,.4f}, 평가 {h.market_value:,.2f}, "
              f"손익 {h.unrealized_pnl:+,.2f} ({h.unrealized_pnl_pct:+.2f}%)")

    print("\n[5] 추가매수 0.3주 (평단가 갱신)")
    o2 = b.place_buy("379800", 0.3)
    print(f"  status: {o2.status.value}, fill: {o2.filled_avg_price:,.4f}")
    bal = b.get_balance()
    for h in bal.holdings:
        print(f"  {h.ticker}: {h.shares:g}주 @ 평단 {h.avg_cost:,.4f}")

    print("\n[6] 매도 0.8주 (전량 청산)")
    o3 = b.place_sell("379800", 0.8)
    print(f"  status: {o3.status.value}, fill: {o3.filled_avg_price:,.4f}, cmm: {o3.commission:.4f}")
    bal = b.get_balance()
    print(f"  현금: {bal.cash_krw:,.2f} / 보유: {len(bal.holdings)}개")

    print("\n[7] 과다 매도 시도 (거부 검증)")
    o4 = b.place_sell("379800", 0.1)
    print(f"  status: {o4.status.value}, msg: {o4.message}")

    print("\n[8] 잔고 부족 매수 시도 (거부 검증)")
    o5 = b.place_buy("379800", 100)
    print(f"  status: {o5.status.value}, msg: {o5.message}")

    print(f"\n✅ 누적 주문 수: {len(b._state['orders'])}건")
    print(f"   체결: {sum(1 for o in b._state['orders'] if o['status']=='filled')}건")
    print(f"   거부: {sum(1 for o in b._state['orders'] if o['status']=='rejected')}건")

