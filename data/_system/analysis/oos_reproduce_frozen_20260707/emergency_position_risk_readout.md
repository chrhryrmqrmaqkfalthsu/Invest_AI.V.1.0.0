# 긴급 확인 — 현재 포지션 stop 위험도 / runner 구성 / 방어 경로 (READ-ONLY)

목적:

```text
현재 6개 포지션의 현재가 vs 계산 stop_price 거리 확인
live runner 실행 시 자동청산만 도는지, 자동매수 위험이 있는지 확인
runner 없이 Alpaca stop order를 걸 수 있는 수동 방어 경로 확인
현재 시장 개장 여부 확인
```

엄수 사항:

```text
read-only
주문 제출 없음
프로세스 실행 없음
설정/코드/상태 파일 수정 없음
```

산출물:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/emergency_position_risk_readout.md
data/_system/analysis/oos_reproduce_frozen_20260707/emergency_position_risk_audit.csv
```

백업:

```text
backup/pre_emergency_risk_readout_20260709_182705.tar.gz
```

---

## 1. 현재 시장 상태

Alpaca clock read-only 조회:

```text
is_open: True
timestamp_utc: 2026-07-09T18:26:14.013054+00:00
timestamp_kst: 2026-07-10T03:26:14.013054+09:00
timestamp_ny: 2026-07-09T14:26:14.013054-04:00
next_close_utc: 2026-07-09T20:00:00+00:00
next_close_kst: 2026-07-10T05:00:00+09:00
next_close_ny: 2026-07-09T16:00:00-04:00
next_open_kst: 2026-07-10T22:30:00+09:00
```

판정:

```text
MARKET_OPEN_NOW
```

의미:

```text
현재 정규장 중이다. 방어 주문이 필요하면 장중 처리 가능한 상태다.
다만 아래 stop 거리 기준으로는 즉시 stop 근접 포지션은 없다.
```

---

## 2. 현재 보유 / open orders

실계좌 API:

```text
broker_mode: alpaca_live
direct_orders_enabled: True
holdings_count: 6
cash: 48.70
total_value: 626.31
open_orders_count: 0
```

보유:

```text
ADPT
ALGT
ANET
BB
BCS
CDE
```

주의:

```text
open_orders_count=0 이므로 현재 Alpaca에 걸려 있는 예약/stop/exit 주문은 확인되지 않았다.
```

---

## 3. 즉시 위험도 순위 — 현재가 vs active stop

계산 기준:

```text
pending_orders.json의 entry metadata selected_rulebook + ATR 사용
entry_price는 Alpaca position 평균단가 사용
ExitPolicy initialize_position_state로 stop/target/trailing 계산
hybrid/trailing은 현재 방어 기준 active_stop = max(stop_price, trailing_stop)
fixed는 active_stop = stop_price
```

위험도 등급:

```text
BREACHED: current <= active_stop
VERY_CLOSE_<=1%: active stop까지 1% 이하
CLOSE_<=3%: 3% 이하
WATCH_<=5%: 5% 이하
OK: 5% 초과
```

| rank | ticker | current | active_stop | gap $ | gap % | pnl % | strategy | stop | trailing_stop | target | danger |
|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 1 | ALGT | 113.3100 | 107.1736 | 6.1364 | 5.42% | -1.28% | hybrid | 107.1736 | 95.6223 | 150.0245 | OK |
| 2 | BCS | 27.3076 | 25.4868 | 1.8208 | 6.67% | -0.03% | trailing | 25.2665 | 25.4868 | 30.3262 | OK |
| 3 | ADPT | 21.8700 | 20.0706 | 1.7994 | 8.23% | -0.14% | hybrid | 20.0706 | 18.9645 | 28.5528 | OK |
| 4 | CDE | 16.1074 | 14.7362 | 1.3712 | 8.51% | -0.12% | trailing | 14.7362 | 12.9593 | 19.9404 | OK |
| 5 | ANET | 184.8350 | 167.6876 | 17.1474 | 9.28% | -0.05% | hybrid | 155.1944 | 167.6876 | 214.6062 | OK |
| 6 | BB | 11.4350 | 9.0061 | 2.4289 | 21.24% | -0.56% | fixed | 9.0061 | 10.0411 | 16.9534 | OK |

판정:

```text
NO_STOP_BREACHED
NO_POSITION_WITHIN_1_PERCENT_OF_STOP
NO_POSITION_WITHIN_3_PERCENT_OF_STOP
NO_POSITION_WITHIN_5_PERCENT_OF_STOP
NEAREST_TO_STOP=ALGT gap=5.42%
```

긴급도:

```text
장중이므로 방어 경로 점검은 긴급성이 있다.
하지만 현재가 기준 stop 근접 위험은 낮음~보통이다.
```

---

## 4. live runner를 띄우면 같이 도는 것

대상:

```text
scripts/run_live.py
engine/live/runner.py
engine/live/central_control.py
engine/live/s2_auto_trader.py
engine/live/s2_auto_config.py
```

### 4.1 기본 run_live 동작

`scripts/run_live.py` 기본값:

```text
--central-control default=off
--buy-timing-mode next_open only
--market-tick default=60 sec
--offmarket-tick default=3600 sec
```

스케줄:

```text
scripts/run_live.py:450-453
  scheduler.add_once_job(runner.startup_check, delay_sec=2)
  scheduler.add_market_hours_job(make_holding_news_tick_market_job(runner), interval_sec=args.market_tick, job_id='tick_market')
  scheduler.add_interval_job(runner.tick_offmarket, interval_sec=args.offmarket_tick)
```

`Runner.tick_market()`에서 실제로 실행되는 것:

```text
engine/live/runner.py:286-319
  1. _poll_pending_orders(context='tick_market.pre_exit')
  2. pending 주문이 남아 있으면 자동청산 1 tick 보류
  3. pending 주문이 없으면 position_manager.check_exits(...)
  4. _process_chart_exit_plans()
  5. _process_manual_sell_intents()
  6. _process_pending_approvals()
  7. _poll_pending_orders(context='tick_market.pre_signal')
  8. for ticker in symbols: _process_ticker(ticker)
```

따라서 live runner는 “자동청산 감시만” 도는 프로세스가 아니다. pending 정산, chart/manual sell, 기존 ticker signal 평가도 같이 돈다.

### 4.2 자동매수 위험

중요 guard:

```text
scripts/run_live.py:225-248
  install_legacy_buy_guard(runner)
  side == BUY and not central_control buy reason이면 즉시 차단
  preflight/safety/broker submission 전에 return
```

로그 문구:

```text
구 개별 ticker BUY 경로 fail-safe 차단 설치 완료; SELL/청산/central BUY는 유지
```

central-control:

```text
scripts/run_live.py:361-365
  central-control ON일 때 broker_mode가 paper/alpaca_paper가 아니면 sys.exit(4)
```

즉 Alpaca live 모드에서 `--central-control on`은 코드상 허용되지 않는다.

S2 auto trader:

```text
engine/live/s2_auto_config.py default_config:
  master_enabled=False
  auto_buy_enabled=False
  auto_exit_enabled=False
  real_orders_enabled=False
  dry_run=True
```

현재 실제 config:

```text
data/_system/live_auto_config.json
master_enabled=False
auto_buy_enabled=False
auto_exit_enabled=False
real_orders_enabled=True
dry_run=False
entry_timing=next_open
portfolio_K=1
```

S2 auto 실행 조건:

```text
engine/live/s2_auto_trader.py:350-356
  master/auto_buy가 꺼져 있으면 BLOCKED
```

판정:

```text
RUN_LIVE_DEFAULT_NOT_EXIT_ONLY
LEGACY_SIGNAL_BUY_GUARDED_OFF
CENTRAL_CONTROL_BUY_NOT_ALLOWED_IN_ALPACA_LIVE
S2_AUTO_NOT_PART_OF_RUN_LIVE_AND_MASTER_DISABLED
```

운영 해석:

```text
run_live를 기본 central-control off로 띄우면 자동매수는 legacy BUY guard로 차단되는 설계다.
하지만 프로세스가 자동청산만 단독 실행하는 것은 아니며, ticker signal 평가와 SELL 경로도 함께 돈다.
또 실계좌 run_live는 EXIT_LIVE_POLICY=1 없이는 startup guard에서 중단된다.
```

실계좌 startup guard:

```text
engine/live/exit_policy_guard.py:53-63
  Alpaca live broker에서 EXIT_LIVE_POLICY=1 또는 ALLOW_LEGACY_EXIT_LIVE=1 없으면 RuntimeError
```

---

## 5. runner 없이 Alpaca stop order를 거는 경로

경로 존재:

```text
engine/live/real_dashboard_alpaca_exit_orders_patch.py
engine/live/real_dashboard_alpaca_exit_oco_fix.py
```

API route:

```text
GET    /api/real/alpaca_exit_orders?ticker=...
POST   /api/real/alpaca_exit_order
DELETE /api/real/alpaca_exit_order/{ticker}
```

POST route 동작:

```text
/api/real/alpaca_exit_order
body:
  ticker
  take_profit_price optional
  stop_loss_price optional
  shares optional
  replace_existing default True
  time_in_force
```

코드상 제출 주문:

```text
stop_loss only -> Alpaca StopOrderRequest SELL
take_profit only -> LimitOrderRequest SELL
take_profit + stop_loss -> OCO, 단 fractional이면 whole-share OCO + fractional watch 처리
```

제약:

```text
engine/live/real_dashboard_alpaca_exit_orders_patch.py:177-190
  stop_loss_price는 현재가보다 낮아야 함
  take_profit_price는 현재가보다 높아야 함
  take_profit_price > stop_loss_price 필요

engine/live/real_dashboard_alpaca_exit_oco_fix.py:40-54
  OCO는 fractional shares 불가
  TP+SL OCO에서 fractional이면 whole-share만 OCO, fractional remainder는 별도 local watch
  1주 미만이면 OCO 불가. 익절만 또는 손절만 simple DAY 예약 사용 필요

engine/live/real_dashboard_alpaca_exit_oco_fix.py:74-107
  stop-only simple order는 StopOrderRequest, TimeInForce.DAY, fractional 허용 경로
```

현재 status read-only GET:

```text
ADPT open exit orders: 0
ALGT open exit orders: 0
ANET open exit orders: 0
BB open exit orders: 0
BCS open exit orders: 0
CDE open exit orders: 0
```

판정:

```text
MANUAL_DEFENSE_STOP_ROUTE_EXISTS_WITHOUT_RUNNER
NO_RESERVED_EXIT_ORDERS_CURRENTLY_OPEN
```

운영 해석:

```text
runner 없이도 dashboard API의 /api/real/alpaca_exit_order 경로로 Alpaca LIVE stop sell 예약 주문을 걸 수 있다.
특히 현재 보유 중 일부가 소수점 수량이므로, TP+SL OCO보다 stop-only simple DAY 주문이 가장 직접적인 수동 방어 경로다.
```

이번 점검에서는 POST를 호출하지 않았다.

---

## 6. 즉시 요약

```text
시장: 열림, 장중
가장 stop에 가까운 포지션: ALGT, active stop까지 5.42% 여유
3% 이내 위험 포지션: 0개
open exit/stop orders: 0개
run_live 기본 실행: exit-only 아님. pending 정산/자동청산/수동SELL/chartSELL/ticker signal 평가가 함께 돈다.
run_live 자동매수: legacy BUY guard로 차단 설계. central-control live buy는 paper/alpaca_paper만 허용.
runner 없이 방어: /api/real/alpaca_exit_order로 Alpaca stop sell 가능. 단 POST는 실제 주문이므로 이번 점검에서는 실행하지 않음.
```

---

## 7. 최종 판정

```text
MARKET_OPEN_NOW
NO_IMMEDIATE_STOP_PROXIMITY_RISK
NEAREST_STOP_ALGT_5_42_PERCENT_ABOVE_ACTIVE_STOP
RUN_LIVE_IS_NOT_EXIT_ONLY
RUN_LIVE_LEGACY_BUY_GUARD_PRESENT
CENTRAL_CONTROL_BUY_BLOCKED_FOR_ALPACA_LIVE
S2_AUTO_MASTER_DISABLED
MANUAL_ALPACA_STOP_ORDER_ROUTE_EXISTS_WITHOUT_RUNNER
NO_EXISTING_ALPACA_RESERVED_EXIT_ORDERS
```
