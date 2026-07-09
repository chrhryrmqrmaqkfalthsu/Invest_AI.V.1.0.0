# 전체 유니버스 청산 배관 작동 확인 — READ-ONLY

목적:

```text
자동청산(register_entry로 등록되는 룰북 청산)이 전체 후보 유니버스에 대해 제대로 붙는지,
현재 보유 포지션 포함 확인한다.
```

엄수 사항:

```text
read-only 점검
코드 수정 없음
positions/pending/order state 수정 없음
주문 제출 없음
학습/재학습 없음
```

산출물:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/universe_exit_wiring_audit_readout.md
data/_system/analysis/oos_reproduce_frozen_20260707/universe_exit_wiring_audit.csv
```

백업:

```text
backup/pre_exit_wiring_universe_audit_20260709_182021.tar.gz
```

---

## 1. 최종 판정

```text
CANDIDATE_26_EXIT_LEVEL_CALCULATION_OK
CURRENT_HOLDINGS_POSITION_MANAGER_REGISTRATION_MISSING
PENDING_ENTRY_METADATA_PRESENT_BUT_NOT_RECONCILED
POSITION_MANAGER_MONITOR_PATH_EXISTS_IN_CODE
POSITION_MANAGER_MONITOR_LOOP_NOT_RUNNING_AS_PROCESS
AUTO_EXIT_NOT_ACTIVE_FOR_CURRENT_REAL_HOLDINGS
```

핵심 결론:

```text
정규 후보 26개는 전부 full rulebook/ATR로 stop/target/trailing/holding 계산이 정상이다.
하지만 현재 실계좌 보유 6개는 positions.json에 등록되어 있지 않다.
따라서 현재 상태 그대로는 PositionManager.check_exits()의 자동청산 감시 대상이 아니다.
```

---

## 2. 현재 실계좌/로컬 상태

실계좌 API:

```text
broker_mode: alpaca_live
direct_orders_enabled: True
holdings_count: 6
cash: 48.70
total_value: 626.95
open_orders_count: 0
```

현재 실보유:

```text
ADPT
ALGT
ANET
BB
BCS
CDE
```

요청서에는 현재 보유 ALGT/BCS라고 되어 있었지만, 점검 시점에는 추가 매수로 실보유가 6개였다. 따라서 ALGT/BCS를 포함한 전체 현재 실보유 6개를 모두 대조했다.

로컬 positions.json:

```text
path: data/_system/positions.json
count: 3
registered tickers: MPLX, CAKE, WPM
```

중요:

```text
positions.json에는 현재 실보유 ADPT/ALGT/ANET/BB/BCS/CDE가 없다.
반대로 positions.json의 MPLX/CAKE/WPM은 현재 실계좌 보유가 아니다.
```

---

## 3. 현재 보유 6개 청산선 등록 대조

각 보유는 `pending_orders.json` metadata에 full selected_rulebook/ATR가 존재해서 “계산 가능한 기대 청산선”은 만들 수 있었다. 그러나 `positions.json`에 등록된 actual 값이 없어 모두 `NOT_REGISTERED_IN_POSITIONS_JSON`이다.

| ticker | shares | entry_price | rulebook_source | rulebook_len | ATR | actual registered? | expected_stop | expected_target | expected_trailing_stop | expected_trailing_distance | expected_max_days | expected_strategy | status |
|---|---:|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|---|
| ADPT | 4.564126 | 21.9000 | pending_metadata | 88 | 1.167450 | NO | 20.070584 | 28.552775 | 18.964464 | 2.935536 | 11 | hybrid | NOT_REGISTERED_IN_POSITIONS_JSON |
| ALGT | 1.311074 | 114.7824 | pending_metadata | 88 | 6.386689 | NO | 107.173582 | 150.024458 | 95.622333 | 19.160067 | 18 | hybrid | NOT_REGISTERED_IN_POSITIONS_JSON |
| ANET | 0.162162 | 184.9360 | pending_metadata | 88 | 10.149329 | NO | 155.194351 | 214.606154 | 167.687629 | 17.248371 | 19 | hybrid | NOT_REGISTERED_IN_POSITIONS_JSON |
| BB | 8.695652 | 11.4999 | pending_metadata | 88 | 0.908912 | NO | 9.006063 | 16.953372 | 10.041121 | 1.458779 | 18 | fixed | NOT_REGISTERED_IN_POSITIONS_JSON |
| BCS | 3.663004 | 27.3167 | pending_metadata | 88 | 0.689817 | NO | 25.266462 | 30.326195 | 25.486774 | 1.829926 | 16 | trailing | NOT_REGISTERED_IN_POSITIONS_JSON |
| CDE | 6.197707 | 16.1268 | pending_metadata | 88 | 1.097870 | NO | 14.736163 | 19.940390 | 12.959318 | 3.167482 | 11 | trailing | NOT_REGISTERED_IN_POSITIONS_JSON |

판정:

```text
HELD_EXIT_LEVELS_CALCULABLE_FROM_PENDING_METADATA=6/6
HELD_POSITIONS_REGISTERED_IN_POSITIONS_JSON=0/6
```

ALGT/BCS 요청 항목의 결론:

```text
ALGT: positions.json 미등록. 기대 청산선 계산 가능하지만 실제 PositionManager 등록값 없음.
BCS: positions.json 미등록. 기대 청산선 계산 가능하지만 실제 PositionManager 등록값 없음.
```

---

## 4. 왜 register_entry가 아직 안 붙었는지

최근 직접 매수 intent:

```text
ALGT, BCS, ADPT, BB, CDE, ANET 모두:
  broker_order_status: pending
  position_registered: false
  pending_order_tracked: true
  reconciliation_status: pending_order_tracked
  entry_metadata_rulebook_len: 88
```

pending_orders.json:

```text
orders_count: 6
ALGT/BCS/ADPT/BB/CDE/ANET 모두 state=OPEN, purpose=entry, metadata_rulebook_len=88
```

실계좌 API:

```text
동일 ticker들이 실제 positions에는 이미 존재한다.
open_orders_count=0
```

해석:

```text
직접 매수 직후 order.status가 pending으로 반환되어 PendingOrderManager.track_order(metadata=...)까지는 성공했다.
하지만 이후 pending poll/reconciliation이 실행되어 FILLED를 확인하고 PositionManager.register_entry()로 옮겨가는 단계가 아직 실행되지 않았다.
```

---

## 5. 정규 후보 26개 청산선 계산 검증

정규 후보 파일:

```text
data/_system/real_dashboard_buy_candidates.json
candidates: 26
source_section: candidate_pool
```

검증 방식:

```text
각 후보의 selected_rulebook + ATR + candidate price로 initialize_position_state() 실행
stop_price, target_price, trailing_stop, trailing_distance, max_holding_days, exit_strategy 계산
numeric/positive/finite 여부 점검
```

집계:

```text
candidate_count: 26
VALID: 26
VALID_WITH_WARNINGS: 0
INVALID: 0
```

후보별 요약:

| candidate_id | ticker | entry_price | ATR | stop | target | trailing_stop | trailing_distance | max_days | strategy | status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| stage2:ALGT:402f72d48c3c | ALGT | 113.839996 | 6.386689 | 91.486585 | 149.498020 | 107.453307 | 6.386689 | 11 | hybrid | VALID |
| stage2:CEF:fe84c0ad85d8 | CEF | 41.359901 | 1.241899 | 37.013254 | 48.811297 | 38.129635 | 3.230267 | 12 | trailing | VALID |
| stage2:CMC:4f6ee2739add | CMC | 60.930000 | 2.488531 | 53.404035 | 64.882875 | 55.944747 | 4.985253 | 17 | hybrid | VALID |
| stage2:FIX:cab7d458767d | FIX | 1787.130005 | 111.173493 | 1451.940614 | 1953.890244 | 1573.820962 | 213.309043 | 10 | trailing | VALID |
| stage3:AAP:71dcdeb19ec0 | AAP | 55.174999 | 2.790122 | 51.652686 | 61.933937 | 47.756811 | 7.418188 | 19 | hybrid | VALID |
| stage3:ACMR:44c1e02681c4 | ACMR | 106.584999 | 9.914664 | 72.511106 | 126.776718 | 76.841006 | 29.743993 | 7 | hybrid | VALID |
| stage3:ADMA:42437a3ee595 | ADMA | 9.195000 | 0.363276 | 7.923533 | 10.923554 | 8.284025 | 0.910975 | 8 | trailing | VALID |
| stage3:ADPT:78c31f1ca209 | ADPT | 21.820000 | 1.167450 | 19.990584 | 28.472774 | 18.884464 | 2.935536 | 11 | hybrid | VALID |
| stage3:AEIS:6e26f08a7c6d | AEIS | 314.959991 | 23.336506 | 264.049935 | 376.150052 | 259.087374 | 55.872617 | 16 | fixed | VALID |
| stage3:ALGT:aec5dd5b1dc1 | ALGT | 113.839996 | 6.386689 | 106.231178 | 149.082054 | 94.679929 | 19.160067 | 18 | hybrid | VALID |
| stage3:ANET:fe220620802b | ANET | 184.740005 | 10.149329 | 154.998357 | 214.410160 | 167.491634 | 17.248371 | 19 | hybrid | VALID |
| stage3:ARKW:296c057b4ef7 | ARKW | 148.350006 | 4.461239 | 132.914753 | 161.441971 | 134.983061 | 13.366945 | 24 | trailing | VALID |
| stage3:BB:f1bdfe7f8ad9 | BB | 11.477200 | 0.908912 | 8.983363 | 16.930671 | 10.018420 | 1.458779 | 18 | fixed | VALID |
| stage3:BCS:5e7da5a74b01 | BCS | 27.320000 | 0.689817 | 25.269762 | 30.329494 | 25.490074 | 1.829926 | 16 | trailing | VALID |
| stage3:BKSY:f1bcc8efea02 | BKSY | 25.350000 | 3.065319 | 20.864535 | 43.741917 | 17.013583 | 8.336417 | 10 | fixed | VALID |
| stage3:BMA:0c978464f9dd | BMA | 89.775002 | 4.561096 | 74.239680 | 110.007907 | 80.034042 | 9.740960 | 24 | trailing | VALID |
| stage3:BMI:07d4ee0f7841 | BMI | 146.080002 | 5.353018 | 127.443817 | 164.006085 | 134.975082 | 11.104920 | 18 | trailing | VALID |
| stage3:BOIL:9044dc2c67a3 | BOIL | 23.525000 | 1.531586 | 21.029280 | 26.908000 | 19.212476 | 4.312524 | 13 | hybrid | VALID |
| stage3:BTBT:363898884d44 | BTBT | 1.690000 | 0.180108 | 1.382499 | 2.770649 | 1.149675 | 0.540325 | 17 | hybrid | VALID |
| stage3:BWXT:f195725cb792 | BWXT | 188.699997 | 8.182731 | 169.016857 | 225.329054 | 165.557190 | 23.142807 | 19 | trailing | VALID |
| stage3:CAPR:a51d615a0ff1 | CAPR | 22.059999 | 1.770522 | 15.863172 | 28.066971 | 17.320917 | 4.739083 | 8 | hybrid | VALID |
| stage3:CBRL:677767a0b6a9 | CBRL | 49.535000 | 2.742368 | 44.696228 | 62.957485 | 44.290633 | 5.244367 | 17 | fixed | VALID |
| stage3:CDE:ceb9fe0512dc | CDE | 16.145000 | 1.097870 | 14.754364 | 19.958591 | 12.977519 | 3.167482 | 11 | trailing | VALID |
| stage3:CE:998b0b638c66 | CE | 46.540001 | 2.247860 | 39.911308 | 56.935635 | 40.848799 | 5.691202 | 10 | hybrid | VALID |
| stage3:CIEN:2ed675d30868 | CIEN | 467.515015 | 34.755824 | 348.399492 | 586.627860 | 383.339175 | 84.175839 | 20 | trailing | VALID |
| stage3:CRS:8695c9ce3320 | CRS | 601.184998 | 23.387070 | 526.211136 | 710.607843 | 545.213578 | 55.971420 | 23 | trailing | VALID |

판정:

```text
CANDIDATE_EXIT_LEVELS_ALL_VALID
```

---

## 6. PositionManager 감시 경로 확인

등록 경로:

```text
engine/live/position_manager.py:233-304
  register_entry(ticker, entry_price, shares, rulebook, atr_value, entry_market_context)
  -> initialize_position_state(...)
  -> stop_price / target_price / trailing_distance / trailing_stop / max_holding_days / exit_strategy 계산
  -> self._positions[ticker] = entry
  -> self._save()
```

감시 경로:

```text
engine/live/position_manager.py:412-421
  check_exits() loops over self._positions.items()

engine/live/position_manager.py:610-690
  _check_one()
  -> broker.get_current_price(ticker)
  -> broker.get_holdings()
  -> broker에 보유 없으면 unregister
  -> ExitPolicy 또는 legacy 조건 평가

engine/live/position_manager.py:706-760
  exit_reason 발생 시 broker.place_sell(... MARKET ...)
  미체결이면 pending_manager.track_order(order, purpose='exit')
```

감시 주기/호출 위치:

```text
engine/live/runner.py:286-294
  tick_market()
  -> _poll_pending_orders(context='tick_market.pre_exit')
  -> pending 주문이 있으면 자동청산 1 tick 보류
  -> pending 주문이 없으면 position_manager.check_exits(...)

engine/live/central_control.py:275-291
  CentralControl.tick_market()
  -> runner._poll_pending_orders(...)
  -> pending 주문이 없으면 runner.position_manager.check_exits(...)
```

pending reconciliation 경로:

```text
engine/live/runner.py:1095-1103
  _poll_pending_orders()
  -> pending_order_manager.poll_all()
  -> _finalize_pending_order(record, order)
  -> mark_finalized(record.order_id)

engine/live/runner.py:1119-1123
  buy fill이면 _get_buy_reconciler().reconcile(order, purpose, preflight)
  -> PositionManager.register_entry(...)
```

트리거 조건:

```text
legacy fixed:
  price <= stop_price -> stop_loss
  price >= target_price -> take_profit
  holding_days >= max_holding_days -> time_out

legacy trailing:
  price <= trailing_stop -> trailing
  holding_days >= max_holding_days -> time_out

legacy hybrid:
  price >= target_price -> take_profit
  price <= trailing_stop -> trailing
  holding_days >= max_holding_days -> time_out

ExitPolicy mode:
  EXIT_LIVE_POLICY=1 and pos.rulebook_snapshot exists and direction long이면 shared ExitPolicy 평가 사용
  아니면 legacy fallback 또는 guard 정책 적용
```

---

## 7. 현재 감시 프로세스 상태

프로세스 확인:

```text
running uvicorn api_server_candidate_only:app --port 8001
running gpt_tool uvicorn server --port 8000
no run_live process
no engine.live.runner process
no central_control process
no s2_auto_trader process
```

crontab 확인:

```text
KINGMAKER_DASHBOARD_GUARD
KINGMAKER_LIVE_CANDIDATE_SLOTS_GUARD
sentiment history cron
```

확인되지 않은 것:

```text
PositionManager.check_exits를 주기적으로 호출하는 live runner cron/systemd/process가 현재 확인되지 않는다.
```

판정:

```text
MONITOR_CODE_PATH_EXISTS
MONITOR_LOOP_CURRENTLY_NOT_RUNNING
```

---

## 8. 현재 상태의 리스크

1. 실보유 6개가 `positions.json`에 없다.

```text
PositionManager.check_exits()는 self._positions, 즉 positions.json 로드 결과만 순회한다.
따라서 ADPT/ALGT/ANET/BB/BCS/CDE는 현재 자동청산 감시 대상이 아니다.
```

2. pending order records는 남아 있지만 이를 poll/reconcile할 runner가 보이지 않는다.

```text
pending_orders.json에는 full rulebook metadata가 보존되어 있다.
하지만 runner._poll_pending_orders()가 실행되지 않으면 FILLED 확인과 register_entry가 진행되지 않는다.
```

3. positions.json에는 현재 실보유가 아닌 과거 MPLX/CAKE/WPM이 남아 있다.

```text
만약 Runner가 check_exits를 실행하면 broker에 해당 보유가 없어 unregister할 수는 있다.
하지만 그 다음에도 pending poll이 실행되어 현재 6개를 register해야 실효 감시가 시작된다.
```

---

## 9. 최종 결론

```text
후보 26개 룰북 청산선 계산: PASS
현재 보유 ALGT/BCS 포함 6개 pending metadata 룰북 보존: PASS
현재 보유 6개 positions.json 등록: FAIL
현재 자동청산 감시 프로세스 실행 확인: FAIL
```

따라서 현재 판정은:

```text
AUTO_EXIT_NOT_WORKING_FOR_CURRENT_HOLDINGS_AS_DEPLOYED
```

정확한 의미:

```text
룰북과 청산선 계산 로직은 정상이다.
직접매수 pending metadata도 full 룰북을 보존하고 있다.
그러나 filled pending order를 PositionManager.register_entry로 최종화하는 runner poll이 현재 실행 중이지 않아,
현재 실보유 포지션이 positions.json에 등록되지 않았다.
등록되지 않았으므로 PositionManager.check_exits 자동청산도 현재 실보유에는 작동하지 않는다.
```
