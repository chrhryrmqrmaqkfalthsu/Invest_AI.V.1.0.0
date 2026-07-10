# 익절/손절선 기준 가격 확인 readout

범위: 코드·설정·주문·기존 상태 파일 변경 없음. `BuyReconciliationService.reconcile()` → `PositionManager.register_entry()` → `ExitPolicy.initialize_position_state()` 경로를 추적하고, 로컬 실거래 API와 기존 상태 파일에서 현재 Alpaca 실계좌 6개 포지션을 읽기 전용으로 대조했다.

분석 대상 실계좌 포지션:

```text
ADPT, ALGT, ANET, BB, BCS, CDE
```

## 1. 최종 판정

판정은 가격 기준과 전체 입력 기준을 나누어야 정확하다.

```text
register_entry 가격 기준:
BASED_ON_FILL_PRICE

stop/target 전체 입력 기준:
MIXED
  가격 anchor = broker 실제 체결가
  ATR = 주문 전 candidate/preflight 스냅샷
  ATR multiplier = 주문 당시 selected_rulebook + entry market context

현재 실계좌 6개 실제 등록선:
UNKNOWN_NOT_REGISTERED
  stop_price = null
  target_price = null
  take_profit_enabled = false
  broker open exit order = 0
```

즉 정상 reconciliation 경로가 완료되면 stop/target은 **신호가가 아니라 실제 체결가**를 기준으로 계산된다. 다만 현재 실계좌 6개는 그 reconciliation이 완료되지 않아 PositionManager stop/target이 등록돼 있지 않다.

## 2. 실제 코드 경로

### 2.1 `BuyReconciliationService.reconcile()`

파일:

```text
engine/live/buy_reconciliation.py
```

핵심 코드:

```text
lines 67-74
filled_price = float(order.filled_avg_price or 0.0)
filled_price <= 0이면 reconciliation 실패

lines 103-109
position_manager.register_entry(
    ticker,
    filled_price,
    filled_shares,
    metadata.rulebook,
    metadata.atr,
    ...
)
```

`candidate.price`, `first_signal_price`, 주문 제출 전 quote를 stop/target 기준가로 넘기지 않는다. broker가 반환한 `order.filled_avg_price`만 등록 기준가로 전달한다.

판정:

```text
BASED_ON_FILL_PRICE
```

### 2.2 `PositionManager.register_entry()`

파일:

```text
engine/live/position_manager.py
```

핵심 코드:

```text
lines 233-267
register_entry(entry_price, ..., atr_value)
initialize_position_state(
    entry_price=entry_price,
    atr_value=atr_value,
    ...
)

lines 268-286
stop = state.stop_price
target = state.target_price
PositionEntry.entry_price = entry_price
PositionEntry.stop_price = stop
PositionEntry.target_price = target
```

여기서 `entry_price`는 바로 앞 reconciliation에서 받은 broker fill price다.

### 2.3 실제 stop/target 계산식

파일:

```text
engine/core/exit_policy.py
```

핵심 코드:

```text
entry = broker filled_avg_price
atr = preflight atr_value

stop_price   = entry - atr * effective_stop_loss_atr
target_price = entry + atr * effective_take_profit_atr
```

정확한 위치:

```text
initialize_position_state(): lines 225-263
stop_price: line 252
target_price: line 253
```

따라서 가격 anchor는 fill price다.

## 3. ATR과 multiplier 기준

### 3.1 ATR은 체결가에서 다시 계산하지 않는다

ATR은 주문 전 preflight에서 확보한다.

```text
BuyReconciliationService.preflight()
provider.get_last_atr(ticker)
```

real-dashboard direct 경로에서는 candidate snapshot의 `atr/preflight_atr`를 metadata에 넣는다.

```text
engine/live/real_dashboard_holding_days_patch.py
lines 217-245
preflight_atr = candidate.preflight_atr or candidate.atr
```

즉 다음 조합이다.

```text
가격 anchor: 체결 후 실제 fill
ATR absolute distance: 주문 전 candidate 평가 스냅샷
```

이 때문에 전체 입력 판정은 `MIXED`다. 그러나 “신호가냐 체결가냐”라는 핵심 가격 기준 질문의 답은 명확히 `BASED_ON_FILL_PRICE`다.

### 3.2 effective ATR multiplier

`resolve_exit_params()`:

```text
market_score < 40:
  stop_loss_atr_bear 사용
그 외:
  stop_loss_atr 사용

market_score >= 70:
  take_profit_atr_bull 사용
그 외:
  take_profit_atr 사용

VIX > 25:
  trailing_atr_volatile 사용
그 외:
  trailing_atr 사용
```

현재 6개 entry market score는 77.6 또는 83.1이므로, hypothetical target 계산에는 모두 `take_profit_atr_bull`이 effective multiplier로 적용된다.

## 4. 기대수익률과 target의 관계

rulebook의 다음 값은 서로 다르다.

```text
expectancy_pct / avg_return_pct
  과거 거래 성과 통계
  target 계산식에 직접 사용하지 않음

take_profit_atr / take_profit_atr_bull
  ATR 배수
  target = fill + ATR * multiplier에 사용
```

따라서 “기대수익률 8%니까 target=entry×1.08”이 현재 코드의 실제 공식은 아니다. 현재 코드는 ATR 기반이다.

## 5. live ExitPolicy에서 target 활성 여부

파일:

```text
engine/core/exit_policy.py
engine/live/exit_policy_adapter.py
```

`ExitExecutionConfig.take_profit_enabled` 기본값은 `False`다. `_live_execution_config()`도 이 값을 True로 설정하지 않는다.

현재 `/api/real/positions`에서도 6개 모두:

```text
exit_strategy = S2 no-TP REAL
take_profit_enabled = false
```

즉 PositionEntry에 target이 정상 생성되더라도, 현재 S2 no-TP ExitPolicy에서는 target hit가 자동 익절 권한으로 활성화되지 않는다. 현재 6개는 더 나아가 PositionEntry 자체가 없어 target 값도 null이다.

## 6. 현재 실계좌 6개 실측

읽기 전용 소스:

```text
GET http://127.0.0.1:8001/api/real/positions
GET http://127.0.0.1:8001/api/real/open_orders
GET http://127.0.0.1:8001/api/real/alpaca_exit_orders?ticker=...
data/_system/real_dashboard_manual_buy_intent.json
data/_system/pending_orders.json
data/_system/positions.json
```

### 6.1 실제 상태

| ticker | first signal | broker avg fill | fill vs signal | actual stop | actual target | TP enabled | reconciliation |
|---|---:|---:|---:|---|---|---|---|
| ADPT | 21.4600 | 21.9000 | +2.05% | null | null | false | pending_order_tracked |
| ALGT | 110.0000 | 114.7824 | +4.35% | null | null | false | pending_order_tracked |
| ANET | 164.5000 | 184.9360 | +12.42% | null | null | false | pending_order_tracked |
| BB | 11.0400 | 11.4999 | +4.17% | null | null | false | pending_order_tracked |
| BCS | 27.6625 | 27.3167 | -1.25% | null | null | false | pending_order_tracked |
| CDE | 16.0000 | 16.1268 | +0.79% | null | null | false | pending_order_tracked |

실측 결론:

```text
6개 모두 stop/target이 등록되어 있지 않다.
따라서 실제 등록값을 역산해 signal/fill 어느 쪽인지 판별하는 것은 불가능하다.
현재 실제 값 판정은 UNKNOWN_NOT_REGISTERED다.
```

### 6.2 broker exit order 확인

현재 API 대조 결과:

```text
/api/real/open_orders: 0건
/api/real/orders: 0건
각 ticker /api/real/alpaca_exit_orders: orders={}, open_orders=[]
```

즉 현재 6개에는 로컬 stop/target뿐 아니라 broker OCO/exit order도 확인되지 않았다.

### 6.3 PositionManager 등록 여부

`data/_system/positions.json`에는 현재 6개가 없다. 별도 종목 MPLX, CAKE, WPM만 있다.

각 direct buy intent에는 다음 상태가 남아 있다.

```text
position_registered = false
reconciliation_deferred = true
reconciliation_status = pending_order_tracked
```

`pending_orders.json`의 해당 BUY records도 local 기준으로 아직:

```text
state = OPEN
filled_shares = 0
filled_avg_price = 0
last_polled_at = ""
```

반면 Alpaca holdings에는 실제 avg_entry_price와 보유 수량이 존재한다. 따라서 local pending-order reconciliation이 실제 broker fill을 반영하지 못한 상태로 확인된다.

## 7. 정상 reconciliation이 됐다고 가정한 비교

아래 값은 현재 등록값이 아니라, 저장된 ATR/rulebook/context에 현행 공식을 적용한 **진단용 hypothetical 값**이다.

| ticker | ATR | effective SL×ATR | effective TP×ATR | signal 기준 stop | fill 기준 stop | signal 기준 target | fill 기준 target | signal-target의 fill 대비 잔여 | fill-target의 fill 대비 잔여 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ADPT | 1.1675 | 1.5670 | 5.6986 | 19.6306 | 20.0706 | 28.1128 | 28.5528 | +28.37% | +30.38% |
| ALGT | 6.3867 | 1.1914 | 5.5180 | 102.3912 | 107.1736 | 145.2421 | 150.0245 | +26.54% | +30.70% |
| ANET | 10.1493 | 2.9304 | 2.9234 | 134.7584 | 155.1944 | 194.1702 | 214.6062 | +4.99% | +16.04% |
| BB | 0.9089 | 2.7438 | 6.0000 | 8.5462 | 9.0061 | 16.4935 | 16.9534 | +43.42% | +47.42% |
| BCS | 0.6898 | 2.9721 | 4.3627 | 25.6123 | 25.2665 | 30.6720 | 30.3262 | +12.28% | +11.02% |
| CDE | 1.0979 | 1.2667 | 3.4736 | 14.6094 | 14.7362 | 19.8136 | 19.9404 | +22.86% | +23.65% |

### ANET

ANET이 추격 폭이 가장 크다.

```text
signal: 164.50
fill: 184.936
chase: +12.42%
```

신호가 기준 target을 잘못 사용했다면:

```text
target = 194.1702
fill 대비 남은 상승 여력 = +4.99%
```

정상 fill 기준 target이면:

```text
target = 214.6062
fill 대비 남은 상승 여력 = +16.04%
```

따라서 신호가 anchor를 사용했다면 익절 구조가 크게 압축됐을 것이다. 그러나 정상 reconciliation 코드 자체는 fill anchor를 사용하므로 그런 압축은 발생하지 않는다.

다만 현재 ANET 실제 포지션에는 target이 null이고 `take_profit_enabled=false`다. 따라서 현재 실계좌에서는 어느 target도 자동 익절선으로 작동하지 않는다.

## 8. 요청 시나리오: 신호가 100, 기대수익 8%, 실제 체결 110

이 시나리오는 ATR이 아니라 단순 percentage target으로 비교한다.

### 신호가 기준

```text
signal price = 100
target = 100 × 1.08 = 108
actual fill = 110
```

체결 순간:

```text
fill은 target 108보다 이미 1.85% 높다.
(target / fill - 1) = -1.82%
```

즉 신호가 기준 target이라면 체결 시점에 이미 익절선을 넘었다. 이 구조에서는 즉시 익절 판정, 잘못된 target hit, 또는 이미 지나간 목표가라는 모순이 발생할 수 있다.

### 체결가 기준

```text
actual fill = 110
target = 110 × 1.08 = 118.8
```

체결 후 필요한 상승:

```text
+8.0%
```

신호가 100 대비 최종 target은:

```text
+18.8%
```

### 현재 코드에 대응

현재 core registration의 가격 anchor는 fill이므로, percentage 방식으로 비유하면 target은 108이 아니라 118.8 쪽이다.

실제 코드에서는 percentage 8% 대신 다음을 쓴다.

```text
target = 110 + ATR × effective_take_profit_atr
```

## 9. 추격 매수 시 익절 구조 판정

### 정상 reconciliation 경로

```text
체결가 기준으로 stop/target을 재설정하므로
추격 때문에 target이 체결 순간 이미 아래에 놓이는 구조는 아니다.
```

다만 ATR은 주문 전 스냅샷이다. 급격한 가격 gap으로 ATR 절대값이 체결가 대비 작아지면 target/stop 퍼센트 폭은 신호 당시보다 상대적으로 좁아질 수 있다. 이것은 signal-price anchor 오류가 아니라 ATR snapshot과 fill price의 시점 차이 문제다.

### 현재 실계좌 6개

```text
stop/target 미등록
S2 no-TP
broker exit order 없음
```

따라서 현재 가장 큰 문제는 “target이 신호가 기준으로 잘못 계산됐다”가 아니라 **target/stop registration 자체가 완료되지 않은 것**이다.

## 10. 최종 결론

```text
1. BuyReconciliationService는 broker filled_avg_price를 register_entry에 전달한다.
2. register_entry는 fill price ± ATR×multiplier로 stop/target을 만든다.
3. first_signal_price는 계산에 사용되지 않는다.
4. 가격 anchor 판정은 BASED_ON_FILL_PRICE다.
5. ATR은 주문 전 candidate/preflight snapshot이어서 전체 입력은 MIXED다.
6. 현재 실계좌 6개는 reconciliation 미완료로 stop/target이 모두 null이다.
7. 현재 6개는 take_profit_enabled=false이며 broker exit order도 없다.
8. ANET 같은 추격 진입도 정상 등록됐다면 target은 fill 기준으로 재산정되지만, 현재는 실제 target이 등록되지 않았다.
```

이번 작업은 읽기 전용 추적만 수행했으며 reconciliation, stop/target 등록, OCO 생성, 주문 제출 또는 설정 변경은 하지 않았다.
