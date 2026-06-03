# ExitPolicy 통합 설계

작성일: 2026-06-04  
상태: Phase 1 확정 설계 + 공통 모듈 생성 기준

---

## 1. 목표

백테스트(`engine/strategies/exit_simulator.py`)와 라이브(`engine/live/position_manager.py`)의 청산 판정 로직을 단일 ExitPolicy로 통합한다.

핵심 원칙은 다음과 같다.

```text
검증=라이브
청산 판정은 하나의 함수에서 수행
백테스트는 봉마다 호출
라이브는 현재가 스냅샷마다 호출
체결 모델은 판정과 분리
```

---

## 2. 확정 정책

### 2.1 hybrid 우선순위

확정 우선순위:

```text
stop_loss → trailing_stop → take_profit → max_holding
```

보수적 기준으로 통일한다. 같은 봉 안에서 손절과 익절이 동시에 충족되면 실제 선후를 알 수 없으므로 손절을 먼저 인정한다. trailing과 take_profit이 동시에 충족되면 trailing을 먼저 인정한다. hybrid는 고정 익절과 추세 방어를 함께 쓰는 전략이므로, 이미 trailing stop이 올라온 상태에서는 이익 보존 조건을 fixed target보다 우선한다.

---

### 2.2 trailing 발동 지연

확정값:

```text
trailing_activation_bars = 2
```

백테스트와 라이브 모두 같은 config를 사용한다. 진입 직후 잡음으로 trailing stop이 너무 빨리 발동하는 것을 막기 위해, 진입 후 2개 거래 bar가 지난 뒤부터 trailing을 활성화한다.

---

### 2.3 max_holding_days

확정 기준:

```text
거래일(trading-day) 기준
```

calendar day 기준은 주말과 휴장을 포함해 의도보다 빠른 청산을 만들 수 있다. 백테스트는 일봉 row 수 기준으로 계산하고, 라이브는 포지션 상태에 `holding_trading_days` 또는 거래일 카운터를 저장해 같은 기준을 맞춘다.

---

### 2.4 동적 exit

동적 exit는 라이브에도 반영한다.

```text
bear: market_score < 40  → stop_loss_atr_bear
bull: market_score >= 70 → take_profit_atr_bull
volatile: vix_level > 25 → trailing_atr_volatile
```

기존 백테스트 쪽 동적 ATR 개념을 표준화하되, 계산 위치는 공통 ExitPolicy로 이동한다.

---

### 2.5 체결 모델

청산 판정과 체결 가격을 분리한다.

```text
trigger_price: 조건이 충족된 기준 가격
fill_price_base: 기본 슬리피지 반영 추정 체결가
fill_price_stress: 높은 슬리피지 반영 추정 체결가
```

백테스트 산출물에는 base/stress를 둘 다 저장한다. 라이브는 실제 주문 체결가를 얻을 수 있으면 별도 `actual_fill_price`로 저장하고, 없으면 base/stress 추정치를 함께 남긴다.

---

### 2.6 long-only

1차 운영은 long-only다.

```text
direction=long만 허용
short/inverse는 추후 별도 검증 후 추가
```

공통 ExitPolicy Phase 1은 short/inverse 분기를 구현하지 않는다. short 방향이 들어오면 명시적으로 거부한다.

---

### 2.7 추가매수

추가매수는 자동화하되 SafetyLayer를 반드시 통과해야 한다.

```text
AddBuyPolicy는 청산 판정과 분리
자동 추가매수 주문은 SafetyLayer 필수 통과
```

ExitPolicy는 청산 판정의 단일 책임을 유지한다. 추가매수 상태 갱신 함수는 제공하되, 실제 추가매수 발동 판단과 주문 실행은 별도 AddBuyPolicy/SafetyLayer에서 처리한다.

---

### 2.8 라이브 포지션 저장 확장

라이브 포지션에는 다음을 저장해야 한다.

```text
rulebook_snapshot
member_hash
```

이유:

1. 동적 exit와 추가매수에는 원본 Rulebook 파라미터가 필요하다.
2. 라이브 보유 개체가 어떤 검증/학습 개체와 같은지 추적하려면 `member_hash`가 필요하다.
3. 나중에 Rulebook 파일이 바뀌어도 이미 진입한 포지션은 진입 당시 기준으로 관리해야 한다.

---

## 3. 공통 모듈 구조

모듈 위치:

```text
engine/core/exit_policy.py
```

핵심 함수:

```python
def evaluate_exit(
    position: PositionState,
    price: PriceSnapshot,
    rulebook,
    market_context: MarketContext | None = None,
    execution_config: ExitExecutionConfig | None = None,
) -> ExitDecision:
    ...
```

백테스트는 각 일봉마다 `PriceSnapshot(open/high/low/close/next_open)`을 만들어 호출한다. 라이브는 현재가만 있는 경우 `PriceSnapshot(current_price=...)`를 만들어 호출한다. 같은 함수가 입력 형태에 따라 OHLC intraday touch 방식 또는 current price polling 방식으로 분기한다.

---

## 4. 데이터 클래스

공통 모듈의 1차 데이터 구조:

```text
MarketContext
ExitExecutionConfig
PositionState
PriceSnapshot
ExitDecision
```

`PositionState`는 라이브 저장 구조와 백테스트 내부 상태를 모두 표현할 수 있어야 한다. 특히 다음 필드를 포함한다.

```text
avg_cost
atr_at_entry
stop_price
target_price
trailing_stop
trailing_distance
highest_price
holding_trading_days
rulebook_snapshot
member_hash
```

---

## 5. 마이그레이션 계획

### Phase 1. 공통 모듈 생성

```text
engine/core/exit_policy.py 추가
docs/EXIT_POLICY.md 추가
tests/test_exit_policy.py 추가
기존 exit_simulator.py / position_manager.py는 미변경
```

목표는 신규 모듈 자체의 단위 검증이다.

### Phase 2. 백테스트 교체

```text
exit_simulator.py의 직접 조건문을 evaluate_exit 호출로 교체
기존 결과와 새 결과를 비교
의도된 차이와 버그 후보를 분리
```

백테스트부터 교체하는 이유는 같은 OHLC 입력으로 반복 검증이 가능하기 때문이다.

### Phase 3. 라이브 shadow mode

```text
position_manager.py는 기존 로직으로 실제 주문 유지
동시에 evaluate_exit 결과를 병렬 계산해 로그만 남김
```

차이 분류:

```text
SAME
INTENTIONAL_DYNAMIC_EXIT
INTENTIONAL_HYBRID_PRIORITY
INTENTIONAL_TRADING_DAY_TIMEOUT
INTENTIONAL_SLIPPAGE
INTENTIONAL_LONG_ONLY
BUG_CANDIDATE
```

### Phase 4. 라이브 교체

```text
position_manager.py의 실제 청산 판단을 evaluate_exit로 교체
trade_log에 trigger_price, fill_price_base, fill_price_stress, actual_fill_price 저장
```

---

## 6. 검증 계획

단위 테스트는 다음 정책을 고정한다.

```text
fixed long stop만 터짐 → stop_loss
fixed long target만 터짐 → take_profit
trailing만 터짐 → trailing
아무 조건 없이 max_holding 도달 → time_out
hybrid stop·target 동시 충족 → stop_loss
hybrid trailing·target 동시 충족 → trailing
보통장(market_score=50, vix=18) → 고정 ATR과 동일
trailing_activation_bars=2 → 진입 직후 trailing 미발동
base/stress fill → stress가 base보다 더 보수적
```

---

## 7. 주의사항

Phase 1은 신규 파일만 추가한다. 기존 청산 로직에는 아직 영향이 없다.

다음 단계부터 기존 결과가 달라질 수 있는 항목은 반드시 의도된 변경으로 분리해 검증한다.

```text
hybrid 우선순위
동적 exit 라이브 반영
거래일 기준 max_holding
base/stress 슬리피지
long-only 강제
자동 추가매수
```
