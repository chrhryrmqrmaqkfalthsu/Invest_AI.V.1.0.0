# 신호 발생 시점 vs 진입 체결 타이밍 정합성 — READ-ONLY

범위:

```text
backtest 기준: data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv
backtest 생성 코드: data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py
live 기준: data/_system/ops/live_candidate_slots.py, engine/live/real_dashboard_api.py, data/_system/live_slots_state.json
확인 방식: 기존 코드/CSV/state 파일 read-only inspection
코드 수정/학습/서버 실행: 없음
```

최종 판정:

```text
ENTRY_TIMING_CONSISTENT
```

주의:

```text
이 판정은 요청서의 기준, 즉 “지속 신호를 첫날만 보느냐 / 매일 다시 후보로 보느냐” 기준이다.
체결 시계 자체는 동일하지 않다:
- backtest: 신호일 D의 평가 후 다음 OHLCV row, 보통 D+1 거래일 Open 체결로 계산
- live: 후보가 떠 있는 동안 사용자가 누르면 현재가/market order 시점에 제출
```

---

## 1. oos_reproduce_frozen 진입 체결 규칙

코드 경로:

```text
data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py:359-365
  for i in range(WARMUP, len(df) - 1):
      sig_date = df.index[i]
      entry_idx = i + 1
      entry_date = df.index[entry_idx]

run_ohlc_freeze_rebuild.py:385-393
  sub = df.iloc[: i + 1]
  sig = evaluate_signal(...)
  if not sig.should_buy: continue
  meta['buy_signals'] += 1

run_ohlc_freeze_rebuild.py:394-401
  entry_open = df.iloc[entry_idx].Open or Close
  shares = POSITION_BUDGET / entry_open
  simulate_exit(... entry_idx ... entry_price_override=entry_open ...)

run_ohlc_freeze_rebuild.py:421-423
  record_from_trade(... signal_idx=i, entry_idx=entry_idx ...)
  out.append(row)
```

확인 결과:

```text
신호 발생 tick/day: df row i
체결 계산 tick/day: df row i+1
체결 가격: entry_idx row의 Open 우선, 없으면 Close
일반 해석: daily OHLCV 기준 다음 거래일 Open 진입
```

실제 CSV delta:

| signal_date → entry_date calendar delta | rows |
|---:|---:|
| 1일 | 34201 |
| 2일 | 426 |
| 3일 | 8034 |
| 4일 | 1311 |

해석:

```text
코드는 항상 다음 OHLCV row(i+1)를 쓴다.
calendar delta가 2~4일인 건 주말/휴일 등 거래일 간격 때문이다.
```

---

## 2. backtest에서 신호 지속 시 재진입/중복진입 처리

코드상 상태:

```text
run_candidate() 내부에는 open_positions / already_open / first_signal_only gate가 없다.
각 i마다 should_buy=True이면 simulate_exit()를 독립 호출한다.
disable_add_buy=True는 해당 단일 simulate_exit 내부의 add-buy를 끄는 옵션일 뿐, 다음 날짜의 별도 신규 trade 생성을 막지 않는다.
```

검색/코드 확인:

```text
build_signal_history / judge_buy_gate / consecutive_buy_days / first_signal_at gate: NOT_FOUND in oos_reproduce_frozen path
open position 중복 차단: NOT_FOUND in run_candidate()
```

CSV 집계:

| 항목 | 값 |
|---|---:|
| total trades | 43972 |
| IS trades | 31057 |
| OOS trades | 12915 |
| candidates with multiple trades | 93 |
| same candidate + same entry_date duplicates | 0 |
| same candidate near-consecutive entry pairs, calendar gap 1~4일 | 37067 |

예시:

```text
stage2:AGI:202bb1a936de
signal 2021-05-10 → entry 2021-05-11
signal 2021-05-11 → entry 2021-05-12
signal 2021-05-12 → entry 2021-05-13
signal 2021-05-13 → entry 2021-05-14
signal 2021-05-14 → entry 2021-05-17
```

CE 예시:

```text
CE trades: 628
CE signal→entry delta counts: {1: 492, 2: 6, 3: 114, 4: 16}
```

CE 초반 연속 예시:

| split | signal_date | entry_date | exit_date | exit_reason |
|---|---|---|---|---|
| IS | 2021-03-24 | 2021-03-25 | 2021-04-09 | time_out |
| IS | 2021-03-25 | 2021-03-26 | 2021-04-12 | time_out |
| IS | 2021-03-26 | 2021-03-29 | 2021-04-13 | time_out |
| IS | 2021-03-29 | 2021-03-30 | 2021-04-14 | time_out |
| IS | 2021-03-30 | 2021-03-31 | 2021-04-15 | time_out |

CE OOS 최근 예시:

| split | signal_date | entry_date | exit_date | exit_reason |
|---|---|---|---|---|
| OOS | 2026-04-29 | 2026-04-30 | 2026-05-06 | trailing |
| OOS | 2026-04-30 | 2026-05-01 | 2026-05-07 | stop_loss |
| OOS | 2026-05-04 | 2026-05-05 | 2026-05-07 | stop_loss |
| OOS | 2026-05-05 | 2026-05-06 | 2026-05-19 | stop_loss |
| OOS | 2026-05-06 | 2026-05-07 | 2026-05-19 | stop_loss |

결론:

```text
oos_reproduce_frozen은 첫 신호일만 진입하는 방식이 아니다.
신호가 여러 거래일 지속되면 각 신호일마다 다음 거래일 Open 기준 독립 진입/거래를 만든다.
```

---

## 3. live_candidate_slots에서 N일 지속 신호가 매수 대상으로 남는지

코드 경로:

```text
data/_system/ops/live_candidate_slots.py:403
  ev = evaluate_candidate(candidate, ctx=ctx)

data/_system/ops/live_candidate_slots.py:414-418
  if not ev.get('should_buy'): continue
  pool.append(public_candidate_row(candidate, ev, gate, spy))

data/_system/ops/live_candidate_slots.py:419-450
  first_seen_signals에 first_signal_at / last_seen_at 저장
  row['first_signal_at'] = rec.get('first_signal_at')
  row['last_seen_at'] = rec.get('last_seen_at') or now_iso
```

판단:

```text
first_signal_at이 오래됐다는 이유로 후보를 제외하는 gate: NOT_FOUND
last_seen_at이 계속 갱신되는 동안 should_buy=True이면 candidate_pool/slots에 유지됨
```

real dashboard payload:

```text
engine/live/real_dashboard_api.py:833-890
  _candidate_slot_to_dashboard()
  -> first_signal_at / last_seen_at 그대로 노출
  -> manual_buy_enabled=True
  -> action_label='실전 매수'
```

실전 매수 버튼 경로:

```text
engine/live/real_dashboard_api.py:2162-2167
  markSlotBuy(cid, notional, slot)
  -> POST /api/real/live_slot_buy
```

현재 live_slots_state 확인 시점:

```text
state_updated_at: 2026-07-10 00:01:30 KST
filled_slots: 8
filled_slots older_than_24h by first_signal_at: 8
candidate_pool count: 27
candidate_pool older_than_24h: 25
candidate_pool max first_signal age: 40.65 hours
```

현재 슬롯 예시:

| slot | ticker | candidate_id | first_signal_at KST | last_seen_at KST | first_signal age hours | status |
|---:|---|---|---|---|---:|---|
| 1 | BMI | stage3:BMI:07d4ee0f7841 | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 2 | BMA | stage3:BMA:0c978464f9dd | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 3 | BTBT | stage3:BTBT:363898884d44 | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 4 | ADMA | stage3:ADMA:42437a3ee595 | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 5 | CE | stage3:CE:998b0b638c66 | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 6 | BCS | stage3:BCS:5e7da5a74b01 | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 7 | ALGT | stage2:ALGT:402f72d48c3c | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |
| 8 | ALGT | stage3:ALGT:aec5dd5b1dc1 | 2026-07-08 07:22:21 | 2026-07-10 00:01:30 | 40.65 | FILLED |

결론:

```text
live_candidate_slots는 first_signal_at이 하루 이상 지난 후보도, 현재 tick에서 should_buy=True이면 계속 후보 슬롯에 올린다.
해당 row는 dashboard-real에서 manual_buy_enabled=True payload로 표시된다.
```

---

## 4. 진입 타이밍 정합성 판정

요청서의 판정 기준:

```text
ENTRY_TIMING_CONSISTENT:
  backtest도 지속 신호를 매일 후보로 보고 live도 동일

ENTRY_TIMING_DRIFT:
  backtest는 첫 신호일 위주 진입인데 live는 지속 신호도 계속 매수
```

확인 결과:

| 항목 | oos_reproduce_frozen | live_candidate_slots |
|---|---|---|
| 첫 신호일만 진입 | NO | NO |
| 지속 신호를 계속 후보/진입 대상으로 봄 | YES | YES |
| first_signal_at/연속일 gate | NOT_FOUND | NOT_FOUND |
| should_buy 현재 평가 기준 | YES | YES |
| 체결 시계 | 다음 OHLCV row Open | 사용자 클릭 시 live market/current price |

최종:

```text
ENTRY_TIMING_CONSISTENT
```

단서:

```text
지속 신호 처리 관점은 일치한다.
체결 가격/체결 시각 관점은 backtest D+1 Open vs live 즉시 market order로 동일하지 않다.
그러나 요청서의 DRIFT 조건인 “backtest는 첫 신호일 위주인데 live는 지속 신호도 계속 매수”는 확인되지 않았다.
```
