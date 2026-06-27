"""
Broker 추상 인터페이스
- Paper / KIS(실전) / Alpaca 양쪽에서 구현
- 주문/잔고/시세 조회의 공통 API
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, List


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"     # 시장가
    LIMIT = "limit"       # 지정가


class OrderStatus(str, Enum):
    PENDING = "pending"       # 미체결
    FILLED = "filled"         # 전량 체결
    PARTIAL = "partial"       # 일부 체결
    CANCELLED = "cancelled"   # 취소됨
    REJECTED = "rejected"     # 거부됨
    FAILED = "failed"         # 오류


@dataclass
class Order:
    order_id: str
    ticker: str
    side: OrderSide
    order_type: OrderType
    shares: float
    price: float                  # 지정가일 때 가격 (시장가는 0)
    status: OrderStatus
    filled_shares: float = 0.0
    filled_avg_price: float = 0.0
    commission: float = 0.0
    submitted_at: str = ""
    filled_at: str = ""
    message: str = ""             # 오류/거부 사유 등
    raw_status: str = ""           # BN-1: 브로커 원본 상태 보존
    client_order_id: str = ""      # BN-2 deterministic client_order_id 복구
    replaced_by: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"] = self.side.value if isinstance(self.side, OrderSide) else self.side
        d["order_type"] = self.order_type.value if isinstance(self.order_type, OrderType) else self.order_type
        d["status"] = self.status.value if isinstance(self.status, OrderStatus) else self.status
        return d


@dataclass
class Holding:
    ticker: str
    shares: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Balance:
    # Legacy field names are kept for compatibility. In the US/Alpaca path these
    # values are USD notional, not KRW. Prefer the *_usd aliases in new code.
    cash_krw: float
    total_value_krw: float
    invested_krw: float
    holdings: List[Holding] = field(default_factory=list)
    fetched_at: str = ""

    @property
    def cash_usd(self) -> float:
        return self.cash_krw

    @property
    def total_value_usd(self) -> float:
        return self.total_value_krw

    @property
    def invested_usd(self) -> float:
        return self.invested_krw

    def to_dict(self) -> dict:
        d = asdict(self)
        d["holdings"] = [h.to_dict() if hasattr(h, "to_dict") else h for h in self.holdings]
        return d


class BrokerError(Exception):
    """Broker 호출 중 발생한 오류"""
    pass


class Broker(ABC):
    """모든 Broker 구현체가 따라야 하는 인터페이스"""

    @property
    @abstractmethod
    def mode(self) -> str:
        """'paper' | 'live' | 'alpaca_paper' | 'alpaca_live'"""
        ...

    @abstractmethod
    def get_balance(self) -> Balance:
        """예수금 + 보유 종목 + 평가손익"""
        ...

    @abstractmethod
    def get_holdings(self) -> List[Holding]:
        """보유 종목만 (현재가/평가손익 포함)"""
        ...

    @abstractmethod
    def get_current_price(self, ticker: str) -> Optional[float]:
        """현재가 조회 (None이면 조회 실패)"""
        ...

    @abstractmethod
    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        """장 개장 여부 (ticker 주면 해당 종목 거래소 기준)"""
        ...

    @abstractmethod
    def place_buy(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        client_order_id: str = "",
    ) -> Order:
        """매수 주문 실행"""
        ...

    @abstractmethod
    def place_sell(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        client_order_id: str = "",
    ) -> Order:
        """매도 주문 실행"""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""
        ...

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        """주문 상태 조회"""
        ...

    def get_open_orders(self) -> List[Order]:
        """브로커 미체결 주문 전체 조회.

        실브로커 구현체는 반드시 override해야 한다. 기본 구현은 즉시체결형
        모의 브로커 호환을 위해 빈 목록을 반환한다.
        """
        return []

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Order]:
        """BN-2: client_order_id 기반 복구 조회. 미지원 브로커는 None."""
        return None

    def health_check(self) -> bool:
        """연결 정상 여부 (기본 구현: get_balance 시도)"""
        try:
            self.get_balance()
            return True
        except Exception:
            return False


if __name__ == "__main__":
    o = Order(
        order_id="TEST001", ticker="379800", side=OrderSide.BUY,
        order_type=OrderType.MARKET, shares=0.5, price=0.0,
        status=OrderStatus.FILLED, filled_shares=0.5, filled_avg_price=25615.0,
        commission=4.0, submitted_at=datetime.now().isoformat(),
        filled_at=datetime.now().isoformat(), raw_status="filled",
    )
    print("Order dict:", o.to_dict())

    h = Holding(ticker="379800", shares=0.5, avg_cost=25615, current_price=25800,
                market_value=12900, unrealized_pnl=92.5, unrealized_pnl_pct=0.722)
    print("Holding dict:", h.to_dict())

    b = Balance(cash_krw=1000000, total_value_krw=1012900, invested_krw=12807.5,
                holdings=[h], fetched_at=datetime.now().isoformat())
    print("Balance dict:", b.to_dict())
