"""
Broker 추상 인터페이스
- Paper / KIS(실전) 양쪽에서 구현
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
    shares: int
    price: float                  # 지정가일 때 가격 (시장가는 0)
    status: OrderStatus
    filled_shares: int = 0
    filled_avg_price: float = 0.0
    commission: float = 0.0
    submitted_at: str = ""
    filled_at: str = ""
    message: str = ""             # 오류/거부 사유 등

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"] = self.side.value if isinstance(self.side, OrderSide) else self.side
        d["order_type"] = self.order_type.value if isinstance(self.order_type, OrderType) else self.order_type
        d["status"] = self.status.value if isinstance(self.status, OrderStatus) else self.status
        return d


@dataclass
class Holding:
    ticker: str
    shares: int
    avg_cost: float            # 평단가
    current_price: float       # 현재가 (조회 시점)
    market_value: float        # 평가금액 = shares × current_price
    unrealized_pnl: float      # 평가손익 (수수료 제외)
    unrealized_pnl_pct: float  # 평가손익률

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Balance:
    cash_krw: float            # 가용 현금
    total_value_krw: float     # 총 자산 (현금 + 평가금액)
    invested_krw: float        # 매수 원금 합계
    holdings: List[Holding] = field(default_factory=list)
    fetched_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["holdings"] = [h.to_dict() if hasattr(h, "to_dict") else h for h in self.holdings]
        return d


class BrokerError(Exception):
    """Broker 호출 중 발생한 오류"""
    pass


class Broker(ABC):
    """모든 Broker 구현체가 따라야 하는 인터페이스"""

    # 식별
    @property
    @abstractmethod
    def mode(self) -> str:
        """'paper' | 'live'"""
        ...

    # 잔고
    @abstractmethod
    def get_balance(self) -> Balance:
        """예수금 + 보유 종목 + 평가손익"""
        ...

    @abstractmethod
    def get_holdings(self) -> List[Holding]:
        """보유 종목만 (현재가/평가손익 포함)"""
        ...

    # 시세
    @abstractmethod
    def get_current_price(self, ticker: str) -> Optional[float]:
        """현재가 조회 (None이면 조회 실패)"""
        ...

    # 시장 상태
    @abstractmethod
    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        """장 개장 여부 (ticker 주면 해당 종목 거래소 기준)"""
        ...

    # 주문
    @abstractmethod
    def place_buy(
        self,
        ticker: str,
        shares: int,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        """매수 주문 실행"""
        ...

    @abstractmethod
    def place_sell(
        self,
        ticker: str,
        shares: int,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
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

    # 부가
    def health_check(self) -> bool:
        """연결 정상 여부 (기본 구현: get_balance 시도)"""
        try:
            self.get_balance()
            return True
        except Exception:
            return False


if __name__ == "__main__":
    # 데이터클래스 직렬화 테스트
    o = Order(
        order_id="TEST001", ticker="379800", side=OrderSide.BUY,
        order_type=OrderType.MARKET, shares=1, price=0.0,
        status=OrderStatus.FILLED, filled_shares=1, filled_avg_price=25615.0,
        commission=4.0, submitted_at=datetime.now().isoformat(),
        filled_at=datetime.now().isoformat(),
    )
    print("Order dict:", o.to_dict())

    h = Holding(ticker="379800", shares=1, avg_cost=25615, current_price=25800,
                market_value=25800, unrealized_pnl=185, unrealized_pnl_pct=0.722)
    print("Holding dict:", h.to_dict())

    b = Balance(cash_krw=1000000, total_value_krw=1025800, invested_krw=25615,
                holdings=[h], fetched_at=datetime.now().isoformat())
    print("Balance dict:", b.to_dict())
