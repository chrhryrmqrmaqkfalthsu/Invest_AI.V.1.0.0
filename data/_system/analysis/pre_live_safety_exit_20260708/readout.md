# Pre-Live Safety & Exit Audit — 실주문/청산 경로 확인

- 생성일: 2026-07-08
- 범위: `/dashboard-real`, `live_candidate_slots.py`, `real_dashboard_api.py`, `PositionManager`, live exit policy code
- 제약: 코드·데이터·상태파일·환경변수 수정 없음. read-only 조사 후 산출물만 생성.

## 최종 판정

```text
Part A — 실주문 경로: ORDER_RISK
Part B — 청산 경로: EXIT_PATH_UNCLEAR
```

세부 해석:

```text
현재 direct_orders_enabled=false라 지금 당장 후보 슬롯 버튼으로 실제 주문은 나가지 않는다.
하지만 /api/real/manual_buy_intent는 환경변수 하나가 켜지면 Alpaca live 시장가 주문을 낼 수 있다.

매수 후 청산은 더 큰 문제다.
라이브 후보 슬롯 또는 real dashboard direct order로 산 포지션이 PositionManager에 자동 등록되어 S2 no-TP 청산을 받는 경로가 확인되지 않는다.
또한 PositionManager/ExitPolicy의 자동청산 코드에는 hybrid/fixed take_profit 트리거가 아직 살아 있다.
```

따라서 현재 상태에서 실제 매수를 시작하면 다음이 된다.

```text
진입 후보 선정은 검증 로직에 가까움.
하지만 주문 경계는 env 하나로 열릴 수 있음.
청산은 S2와 연결되지 않았거나, 연결하더라도 take_profit 잔존으로 S2 no-TP와 불일치.
```

## Part A — 실주문 경로 안전 확인

### A 판정: ORDER_RISK

현재 상태만 보면 locked다.

```text
/api/real/connection:
- direct_orders_enabled: false
- account_check: passed
- broker_mode: alpaca_live
- cash: 650.37
- holdings_count: 0
```

하지만 구조적으로는 `ORDER_RISK`다. 이유는 `/api/real/manual_buy_intent`가 환경변수 하나로 실제 주문 경로가 되기 때문이다.

### A-1. 후보 슬롯 버튼 경로

후보 슬롯 카드/상세의 `매수 후보 선택`은 다음 경로다.

```text
UI handleBuy()
→ POST /api/real/live_slot_buy
→ _mark_real_slot_manual_buy(req)
```

`_mark_real_slot_manual_buy()`가 하는 일:

```text
1. live_slots_state.json 읽음
2. 선택 후보 확인
3. held_exclusions[candidate_id] = event 기록
4. manual_buy_events append
5. slots rebuild
6. live_slots_events.jsonl append
```

하지 않는 일:

```text
broker.place_buy 호출 없음
Alpaca 주문 없음
PositionManager.register_entry 없음
```

즉 후보 슬롯의 “매수 후보 선택”은 실제 주문이 아니라 후보 제외/상태 기록이다.

### A-2. 실제 매수 주문 가능 경로

실제 주문 가능 경로는 별도다.

```text
POST /api/real/manual_buy_intent
→ _create_real_buy_intent(req)
```

주문이 실제 Alpaca로 나가는 조건은 전부 다음과 같다.

```text
1. candidate_id가 real_dashboard_buy_candidates.json 안에 존재
2. candidate status가 pending 또는 manual_requested
3. manual_buy_enabled가 false가 아님
4. notional이 양수
5. KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS 값이 1/true/yes/on 중 하나
6. _get_real_broker() 성공
7. broker.get_current_price(ticker) 또는 candidate price가 양수
8. broker.place_buy(ticker, shares, OrderType.MARKET) 성공
```

핵심 게이트:

```python
DIRECT_ORDER_ENV = "KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS"

def _direct_orders_enabled() -> bool:
    return str(os.environ.get(DIRECT_ORDER_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}
```

실제 주문 코드:

```text
if _direct_orders_enabled():
    broker = _get_real_broker()
    price = broker.get_current_price(ticker) or candidate.price
    shares = notional / price
    broker.place_buy(ticker, shares, order_type=OrderType.MARKET, price=0.0)
```

### A-3. 실주문 최단 경로

서버 기준 최단 경로는 다음이다.

```text
환경변수 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1
+ POST /api/real/manual_buy_intent {candidate_id, notional}
→ Alpaca live MARKET BUY
```

브라우저 UI에는 confirm이 있지만, API 직접 호출에는 별도 confirmation token이나 2단계 승인 토큰이 없다.

### A-4. 주문 한도/cap 확인

`_create_real_buy_intent()` 내부 수량 결정:

```text
notional = req.notional 또는 candidate.notional
notional은 양수만 확인
shares = notional / current_price
order_type = MARKET
```

확인된 한계:

```text
max_notional cap 없음
max_shares cap 없음
cash 대비 cap 없음
candidate별 cap 없음
slippage cap 없음
```

따라서 direct order env를 켜면, 사용자가 입력한 notional이 사실상 그대로 시장가 주문 수량이 된다.

### A-5. 매도 주문 경로

매도도 동일하다.

```text
POST /api/real/manual_sell_intent
→ _create_real_sell_intent(req)
→ if _direct_orders_enabled(): broker.place_sell(... MARKET)
```

매도는 실제 보유 수량을 넘지 못하게 확인하지만, direct env ON이면 live 시장가 매도가 가능하다.

## Part B — 청산 경로 S2 일치 여부

### B 판정: EXIT_PATH_UNCLEAR

현재 라이브 후보 슬롯 기반 수동매수 후 청산 담당 주체가 명확하지 않다.

확인된 사실:

```text
1. live_candidate_slots.py는 청산을 전혀 하지 않는다.
2. /api/real/live_slot_buy는 청산 시스템에 포지션을 등록하지 않는다.
3. /api/real/manual_buy_intent의 direct order 경로도 PositionManager.register_entry를 호출하지 않는다.
4. 현재 실행 중인 프로세스에는 run_live.py/Runner가 없다.
5. 자동청산 일반 경로는 Runner.tick_market → PositionManager.check_exits다.
```

따라서 후보 슬롯으로 수동 매수를 시작하면, 청산은 다음 중 하나다.

```text
- 사람 수동 청산
- 브로커 앱/다른 매매 화면에서 재량 청산
- 별도 미확인 시스템
```

이는 “진입만 검증 규칙, 청산은 재량” 상태라서 S2 검증과 정합성이 없다.

## B-1. 자동청산 일반 경로

자동청산을 실제로 수행하는 일반 live 경로는 다음이다.

```text
scripts/run_live.py
→ Runner.tick_market()
→ PositionManager.check_exits()
→ PositionManager._check_one()
→ exit_reason 발생 시 broker.place_sell(... MARKET)
```

하지만 현재 후보 슬롯/real dashboard direct buy가 이 `PositionManager`에 포지션을 등록하는 경로는 확인되지 않았다.

BUY 체결 후 PositionManager 등록은 `BuyReconciliationService.reconcile()`에 있다.

```text
BuyReconciliationService.reconcile()
→ position_manager.register_entry(...)
```

하지만 `/api/real/manual_buy_intent` direct order 함수 `_create_real_buy_intent()`는 이 reconciliation을 호출하지 않는다.

## B-2. S2와 live exit code 대조

검증된 S2 기준 확인:

```text
per_trade_entry_quality_regime.csv:
- s2_exit_reason take_profit count = 0
- analysis_exit_reason take_profit count = 0
- 원 exit_reason에는 take_profit 4107건 존재
```

즉 S2/analysis는 take_profit 제거 버전으로 확인된다.

반면 live 자동청산 코드에는 take_profit이 존재한다.

### live legacy path

`PositionManager._legacy_exit_reason()`:

```text
fixed:
  stop_loss
  take_profit
  time_out

trailing:
  trailing
  time_out

hybrid:
  take_profit
  trailing
  stop_loss
  time_out
```

### shared ExitPolicy path

`engine/core/exit_policy.py:evaluate_exit()`:

```text
fixed:
  stop_loss
  breakeven_stop
  sell_omen
  take_profit
  time_out

trailing:
  breakeven_stop
  sell_omen
  trailing
  time_out

hybrid:
  stop_loss
  breakeven_stop
  sell_omen
  trailing
  take_profit
  time_out
```

즉 자동청산을 붙이더라도, hybrid/fixed 포지션은 S2 no-TP와 맞지 않는다.

## B-3. take_profit 잔존 여부

판정: **잔존함**

정확히 “고정 3% 익절”이 live 핵심 exit path에서 직접 확인된 것은 아니다. 하지만 더 중요한 점은 `take_profit` 트리거 자체가 살아 있다는 것이다.

잔존 형태:

```text
take_profit_atr
→ target_price = entry_price + ATR * take_profit_atr
→ price/high >= target_price
→ exit_reason = take_profit
```

관련 위치:

```text
engine/live/position_manager.py
- register_entry/add_to_position에서 target_price 계산
- _legacy_exit_reason에서 fixed/hybrid take_profit

engine/core/exit_policy.py
- initialize_position_state에서 target_price 계산
- evaluate_exit에서 fixed/hybrid target_hit -> take_profit

engine/live/elite_shadow_trader.py / elite_strategy_sim.py
- virtual/shadow/sim exit에도 fixed/hybrid take_profit 존재
```

검색 결과:

```text
DemoRuleBook의 0.03은 stop_loss_pct 기본값이며 3% 익절이 아님.
elite_exit_policy_lab에는 +3% profit_lock 실험 정책이 있으나 후보 슬롯 실청산 경로는 아님.
```

결론:

```text
문제는 고정 3% 익절보다 더 넓다.
S2 no-TP와 맞추려면 live 실청산에서 fixed/hybrid target_hit/take_profit 전체가 비활성화되어야 한다.
```

## B-4. 현재 8개 후보의 exit_strategy 위험

현재 슬롯의 exit 설정:

| slot | ticker | exit_strategy | take_profit_atr | stop_loss_atr | trailing_atr | max_days | S2 no-TP 호환 |
|---:|---|---|---:|---:|---:|---:|---|
| 1 | BMI | trailing | 3.2564 | 3.4814 | 2.0745 | 18 | 비교적 안전: trailing path는 take_profit trigger 없음 |
| 2 | BMA | trailing | 3.6742 | 3.4060 | 2.1357 | 24 | 비교적 안전: trailing path는 take_profit trigger 없음 |
| 3 | BTBT | hybrid | 2.5591 | 1.7073 | 3.0000 | 17 | 위험: hybrid take_profit trigger 있음 |
| 4 | CE | hybrid | 3.7074 | 2.9489 | 2.5318 | 10 | 위험: hybrid take_profit trigger 있음 |
| 5 | ADMA | trailing | 4.2725 | 3.5000 | 2.5077 | 8 | 비교적 안전: trailing path는 take_profit trigger 없음 |
| 6 | ALGT | hybrid | 3.1443 | 3.5000 | 1.0000 | 11 | 위험: hybrid take_profit trigger 있음 |
| 7 | CAMT | hybrid | 4.1535 | 2.9813 | 1.0000 | 18 | 위험: hybrid take_profit trigger 있음 |
| 8 | ALGT | hybrid | 3.7144 | 1.1914 | 3.0000 | 18 | 위험: hybrid take_profit trigger 있음 |

현재 8개 중 5개가 `hybrid`라 자동 PositionManager 청산에 등록될 경우 take_profit 영향권이다.

## B-5. S2 트리거 대조표

| 트리거 | S2 검증 | live PositionManager/ExitPolicy | 일치 여부 |
|---|---|---|---|
| stop_loss | 있음 | 있음 | MATCH |
| trailing | 있음 | 있음 | MATCH |
| sell_omen | 있음 | 있음, rulebook sell_omen_enabled/threshold 사용 | MATCH |
| timeout/time_out | 있음 | 있음, max_holding_days 사용 | MATCH |
| breakeven_stop | 있음 | 있음 | MATCH |
| take_profit | S2에는 없음 | fixed/hybrid에 있음 | **MISMATCH** |

## B-6. 현재 positions.json과 실계좌 보유 불일치

read-only 확인 결과:

```text
positions.json: 3개 추적 포지션 존재
/api/real/connection: holdings_count = 0
```

positions.json 예시:

```text
MPLX, CAKE, WPM
```

의미:

```text
현재 PositionManager 상태와 Alpaca live 실제 보유가 일치하지 않는다.
이 상태로 live runner 자동청산을 켜면 보유 없음 unregister 등 혼선이 날 수 있다.
```

## 실거래 시작 전 필수 조치 제안

수정은 하지 않았고, 조치 제안만 정리한다.

### 주문 경로

```text
1. KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS는 계속 OFF 유지
2. /api/real/manual_buy_intent 직접 주문을 쓰려면 서버측 max_notional cap 추가 검증 후 사용
3. 후보 슬롯 버튼과 실제 주문 버튼을 UI/권한/문구상 완전히 분리
4. API 직접 호출 방지를 위한 confirmation token 또는 별도 operator approval 도입 검토
```

### 청산 경로

```text
1. 후보 슬롯 매수분이 PositionManager에 등록되는지 명확히 해야 함
2. 등록되지 않는다면 청산은 사람 수동임을 명시해야 함
3. S2 자동청산을 원하면 take_profit 제거/no-TP 모드가 live exit에 반영되어야 함
4. hybrid/fixed target_hit -> take_profit 비활성화 후 별도 dry-run 검증 필요
5. positions.json과 실제 broker holdings reconciliation 필요
```

## 최종 결론

```text
현재 실제 주문:
- 후보 슬롯 버튼: 안전하게 잠김, 주문 없음
- manual_buy_intent: 현재는 direct_orders_enabled=false라 주문 없음
- 그러나 env 하나로 live 시장가 주문 가능 → ORDER_RISK

현재 청산:
- 후보 슬롯 수동매수 후 자동 S2 청산 경로 확인 안 됨 → EXIT_PATH_UNCLEAR
- live 자동청산 코드에는 take_profit 잔존 → S2 no-TP와 EXIT_MISMATCH 위험
```

따라서 지금 매수를 시작하려면 최소한 다음 중 하나를 먼저 선택해야 한다.

```text
A안: 완전 수동 매수/수동 청산으로 인정하고 소액 테스트만 한다.
B안: S2 no-TP 자동청산 경로를 구현/검증한 뒤 매수한다.
```

검증 정합성 기준으로는 **B안 전에는 실거래 자동화 투입은 보류**가 맞다.

## 산출물

```text
data/_system/analysis/pre_live_safety_exit_20260708/readout.md
data/_system/analysis/pre_live_safety_exit_20260708/findings.csv
```
