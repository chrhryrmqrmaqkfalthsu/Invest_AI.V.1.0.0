# S2 자동매매 구현 — 2단계 완료 보고

- 생성일: 2026-07-08
- 구현 범위: `s2_auto_design_20260708` 설계 범위
- 실행 상태: **dry_run only**
- 실제 Alpaca 주문: **0건**

## 1. 구현 요약

추가/수정 파일:

```text
engine/live/s2_auto_config.py          신규
engine/live/s2_auto_trader.py          신규
scripts/run_s2_auto_live.py            신규
engine/core/exit_policy.py             수정
data/_system/live_auto_config.json     신규 초기 설정
```

생성/검증 상태 파일:

```text
data/_system/live_auto_state.json
data/_system/live_auto_events.jsonl
```

`live_candidate_slots.py`의 후보 선정/정렬 로직은 건드리지 않았다.

## 2. 기본 스위치

초기 설정은 모두 fail-closed다.

| 설정 | 초기값 | 의미 |
|---|---:|---|
| `master_enabled` | false | 전체 S2 자동매매 kill switch. false면 tick 차단. |
| `auto_buy_enabled` | false | 자동매수 실행 허용 여부. |
| `auto_exit_enabled` | false | 자동청산 실행 허용 여부. |
| `real_orders_enabled` | false | 실주문 제출 허용 여부. |
| `dry_run` | true | true면 주문 제출 불가. |
| `entry_timing` | next_open | 백테스트 T+1 시가 진입 가정에 맞춘 기본값. |
| `portfolio_K` | 20 | 검증된 K=20 기본값. |
| `display_slots` | 8 | 화면 표시 슬롯 수. K와 분리. |
| `s2_take_profit_enabled` | false | S2 no-TP. target hit가 익절 트리거가 되지 않음. |
| `total_capital_mode` | fixed_from_account_at_start | 세션 시작 시 가용현금 고정. |
| `capital_source` | available_cash | 마진 제외 현금 기준. |

초기 config:

```text
data/_system/live_auto_config.json
```

`total_capital_usd`는 하드코딩하지 않고 `null`로 초기화했다. 세션 시작 시 계좌에서 읽은 available cash가 state에 고정된다.

## 3. 매수 → 포지션 등록 경로

구현된 경로:

```text
S2AutoTrader.tick()
→ live_auto_config.json 확인
→ kill switch / dry_run / real order gate 확인
→ live_candidate_slots candidate_pool 읽기
→ build_elite_shadow_report에서 full candidate/rulebook 재구성
→ evaluate_candidate 재확인
→ next_open order plan 생성
→ real order gate 통과 시 broker.place_buy
→ filled order면 BuyReconciliationService.reconcile
→ PositionManager.register_entry
```

단, 현재 초기값에서는 다음 때문에 주문 제출이 불가능하다.

```text
master_enabled=false
real_orders_enabled=false
dry_run=true
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS env=false
operator_armed=false
```

## 4. 실주문 게이트

실제 주문이 나가려면 전부 true여야 한다.

```text
master_enabled == true
auto_buy_enabled == true
real_orders_enabled == true
dry_run == false
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS == 1
operator_armed == true
armed_until_utc > now
confirmation_phrase 일치
kill switch clear
SafetyLayer.check_order 통과
```

현재 상태:

```text
real_orders_enabled=false
dry_run=true
direct_order_env_enabled=false
operator_armed=false
```

따라서 현재 구현 상태에서는 실주문이 나갈 수 없다.

## 5. 자본 분배 구현

요구사항 반영:

```text
total_capital_mode = fixed_from_account_at_start
capital_source = available_cash
```

세션 시작 시 Alpaca 계좌에서 available cash를 읽는다.

검증 결과:

```text
broker_mode = alpaca_live
available cash = 650.37 USD
total_value = 650.37 USD
holdings_count = 0
portfolio_K = 20
position_notional = 650.37 / 20 = 32.5185 USD
```

계산된 dry-run 주문:

```text
ticker = BMI
price = 147.08
position_notional = 32.5185 USD
shares = 32.5185 / 147.08 = 0.221094
entry_timing = next_open
execution_session = 2026-07-09
orders_submitted = 0
```

기존 포지션 재균등화는 하지 않는다. 신규 진입만 목표 슬롯 금액으로 진입한다.

## 6. 계좌 조회 실패 안전장치

검증 명령:

```text
scripts/run_s2_auto_live.py plan-dry-run --ignore-switches --simulate-balance-failure --force-capital-refresh
```

결과:

```text
ok = false
status = BLOCKED
reason = simulated_balance_failure
orders_submitted = 0
real_order_attempted = false
```

즉 계좌 available cash 조회 실패 시 주문 생성이 차단된다.

## 7. next_open 타이밍

자동매수 기본값은 `next_open`이다.

```text
entry_timing = next_open
allow_intraday_immediate = false
```

dry-run plan에도 다음처럼 기록된다.

```text
entry_timing = next_open
execution_session = 2026-07-09
```

intraday 즉시매수는 구현 경로에서 기본 차단한다.

## 8. S2 청산 no-TP 구현

`engine/core/exit_policy.py`에 다음 필드를 추가했다.

```python
ExitExecutionConfig.take_profit_enabled: bool = False
```

핵심 변경:

```python
raw_target_hit = high >= position.target_price
target_hit = bool(take_profit_enabled and raw_target_hit)
```

따라서 `take_profit_enabled=false`이면 target price를 넘어도 `take_profit` 청산이 발생하지 않는다. 다만 diagnostics에는 `raw_target_hit=true`, `target_hit=false`가 남는다.

명시 검증:

```text
fixed strategy, high > target_price

take_profit_enabled=false:
  should_exit=false
  reason=null
  raw_target_hit=true
  target_hit=false

take_profit_enabled=true:
  should_exit=true
  reason=take_profit
  raw_target_hit=true
  target_hit=true
```

S2 no-TP에서 허용되는 청산 트리거:

```text
stop_loss
trailing
sell_omen
time_out
breakeven_stop
```

제외 트리거:

```text
take_profit
```

## 9. kill switch 확인

현재 `master_enabled=false`가 전체 kill switch 역할을 한다.

일반 tick 결과:

```text
status = BLOCKED
reason = master_enabled_false
orders_submitted = 0
real_order_attempted = false
```

## 10. dry-run 실제 주문 미발생 증거

검증 결과:

```text
orders_submitted = 0
real_order_attempted = false
would_submit_order = false
real_order_count = 0
```

주문 제출 함수 `broker.place_buy()`는 `real_order_gate()`를 모두 통과해야만 호출된다. 현재는 `real_orders_enabled=false`, `dry_run=true`, env false, operator arm false이므로 호출되지 않는다.

## 11. 실거래 활성화 절차

현재 상태에서는 실주문이 나가지 않는다. 실주문을 활성화하려면 다음 순서를 모두 거쳐야 한다.

```text
1. dry-run으로 하루 이상 관찰
2. positions.json ↔ broker holdings reconciliation 확인
3. live_auto_config.json에서 master_enabled=true
4. auto_buy_enabled=true
5. auto_exit_enabled=true
6. dry_run=false
7. real_orders_enabled=true
8. operator_armed=true, armed_until_utc 설정
9. confirmation_phrase 확인
10. 환경변수 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1 설정
11. scripts/run_s2_auto_live.py tick 또는 daemon 실행
```

하나라도 빠지면 실주문은 차단된다.

## 12. 주의 사항

이번 단계에서 구현한 것은 dry-run 안전 완성이다.

남은 주의점:

```text
1. shared ExitPolicy의 no-TP core는 구현됐지만, legacy fallback은 S2AutoTrader 경로에서 쓰지 않는 설계다.
2. 현재 positions.json에는 기존 3개 추적 포지션이 있고, Alpaca holdings는 0개로 확인됐었다. 실거래 전 reconciliation 필요.
3. next_open execution_session은 보수적 placeholder이며, 실제 주문 실행 전 exchange calendar 기반으로 더 엄밀화하는 것이 좋다.
4. real_orders_enabled=false 상태에서만 검증했다.
```

## 13. 검증 명령

```text
./venv/bin/python -m py_compile engine/core/exit_policy.py engine/live/s2_auto_config.py engine/live/s2_auto_trader.py scripts/run_s2_auto_live.py
./venv/bin/python scripts/run_s2_auto_live.py status
./venv/bin/python scripts/run_s2_auto_live.py probe-capital --force-refresh
./venv/bin/python scripts/run_s2_auto_live.py plan-dry-run --ignore-switches
./venv/bin/python scripts/run_s2_auto_live.py plan-dry-run --ignore-switches --simulate-balance-failure --force-capital-refresh
./venv/bin/python scripts/run_s2_auto_live.py tick
```

## 산출물

```text
engine/live/s2_auto_config.py
engine/live/s2_auto_trader.py
scripts/run_s2_auto_live.py
engine/core/exit_policy.py
data/_system/live_auto_config.json
data/_system/live_auto_state.json
data/_system/live_auto_events.jsonl
data/_system/analysis/s2_auto_implementation_20260708/readout.md
data/_system/analysis/s2_auto_implementation_20260708/validation_results.json
```
