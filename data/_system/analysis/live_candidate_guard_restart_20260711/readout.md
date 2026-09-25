# guard 개체의 실거래 투입 및 스위치 적재 확인 후 재시작

## 최종 결과

- guard가 띄우는 `live_candidate_slots.py daemon`이 현재 실거래 후보 pool의 실제 생성 경로임을 재확인했다.
- 기존 daemon은 `e5cd786` 이전에 시작돼 direct Event 단일 스위치를 메모리에 적재하지 않은 상태였다.
- 상태 파일 정합성, tick lock 부재, Alpaca open order 0건을 확인한 뒤 daemon을 재시작했다.
- 재시작 후 새 PID가 최신 코드를 적재했고, 실제 Stage3 후보 평가에서 `mode=elite_shared` shadow row가 생성됐다.
- 운영 코드·설정 변경은 0건이다.

## 1. guard → 실거래 경로

### guard

`scripts/live_candidate_slots_guard.sh`는 실행 중인 daemon이 없으면 다음 프로세스를 기동한다.

```text
venv/bin/python data/_system/ops/live_candidate_slots.py daemon --interval 60
```

### 후보 계산

`data/_system/ops/live_candidate_slots.py::refresh_slots()`는:

```text
build_elite_shadow_report()
→ elite_shadow_trader.evaluate_candidate(candidate, ctx)
→ live_slots_state.json 원자 저장
```

경로로 후보를 계산한다.

### 실거래 연결

`engine/live/real_dashboard_holding_days_patch.py`의 실거래 후보 lookup은 isolated candidate가 없을 때 `live_slots_state.json`에서 같은 candidate ID를 찾아:

```text
candidate_source=live_slots_state_fallback
real_candidate_fallback=true
```

로 반환한다.

Dashboard real-buy 경로는 이 후보 snapshot을 사용해 Alpaca live order를 제출한다. 즉 guard daemon이 만든 후보는 dashboard fallback을 통해 실거래 주문 후보로 투입된다.

## 2. 스위치 적재 여부

기존 daemon:

```text
PID 337946
started 2026-07-10 13:30:03 UTC
```

스위치 구현 commit:

```text
e5cd786
2026-07-11 12:09:28 UTC
```

`live_candidate_slots.py`는 process 시작 시 top-level로 `elite_shadow_trader.evaluate_candidate`를 import한다. 따라서 기존 PID는 구현 전 함수 객체를 메모리에 보유해 스위치 미적재 상태였다.

판정:

```text
SWITCH_NOT_LOADED_BEFORE_RESTART
```

## 3. 재시작 전 안전성 검사

### 상태 파일

`data/_system/live_slots_state.json`:

- JSON parse 정상
- version 정상
- slots/pool 구조 정상
- 임시 파일 없음
- `live_slots_tick.lock` 없음

### 미완료 주문

Alpaca live API:

```text
/api/real/open_orders
count=0
```

수동 buy/sell intent의 pending 계열 상태도 0건이었다.

내부 `pending_orders.json`에는 6건이 `OPEN/pending_new`로 남아 있었지만, 각 broker order ID를 Alpaca 최근 주문 이력과 대조한 결과 6건 모두 이미 `filled`였다. 따라서 실제 미완료 broker order는 없고 내부 reconciliation 잔존 레코드로 판정했다.

### selector의 주문 부작용

`live_candidate_slots.py`는 broker submit/order API를 호출하지 않는다. 후보 계산과 state 저장만 수행한다.

안전 판정:

```text
SAFE_TO_RESTART
```

## 4. 재시작 결과

기존 PID에 SIGTERM을 보냈으나 Yahoo 연결 정리 중 kernel D-state에 머물러 정상 종료가 완료되지 않았다. tick lock과 주문은 없었고 상태 파일도 정상이라 SIGKILL 후 최신 daemon을 별도 기동했다.

새 daemon:

```text
PID 452686
started 2026-07-11 12:30:49 UTC
```

Guard 재실행 결과 새 PID를 정상 running 상태로 인식했다.

상태 파일은 재시작 후에도 JSON 정합성을 유지했고 slot/pool 구조가 보존됐다.

판정:

```text
SWITCH_LOADED_AFTER_RESTART
```

## 5. 실제 elite_shared shadow row

2026-07-11은 주말이라 daemon 정규 refresh는 gate에 의해 skip된다. 따라서 주문을 발생시키지 않는 동일 evaluator를 사용해 현재 live pool의 실제 후보:

```text
stage3:BTBT:363898884d44
```

를 평가했다.

평가 결과:

```text
ok=true
score_on=13.92611381099677
score_off=0.0
threshold=1.911258135445086
pass_on=true
pass_off=false
event_component=14.919841836985065
market_score_on=71.5
market_score_off=71.5
market_adjustment_on=1.1044649520068255
market_adjustment_off=1.1044649520068255
invariant_ok=true
```

생성된 shadow row:

```text
mode=elite_shared
path=engine.live.elite_shadow_trader.evaluate_candidate
candidate_id=stage3:BTBT:363898884d44
```

즉 최신 elite evaluator가 실제 후보에서 단일 스위치와 OFF shadow 계산을 정상 수행하는 것이 확인됐다.

이 row는 실제 후보 ID와 실제 live evaluator 경로를 사용하므로 이전 synthetic test row와 달리 2단계 판단 자료로 사용할 수 있다. 다만 한 건뿐이므로 OFF 전환 판단에는 여러 정규장 refresh cycle 축적이 여전히 필요하다.

## 최종 판정

| 항목 | 결과 |
|---|---|
| guard 후보의 실거래 연결 | 확인됨 |
| 재시작 전 스위치 적재 | 미적재 |
| 재시작 안전성 | 충족 |
| 새 daemon 최신 코드 적재 | 확인됨 |
| 실제 `elite_shared` row | 확인됨 |
| market_score 보존 | 확인됨 |
| shadow invariant | 통과 |
| 운영 코드 변경 | 0건 |

## 주의 사항

내부 `pending_orders.json`의 6개 filled 주문이 여전히 `OPEN`으로 남아 있는 reconciliation 불일치는 별도 운영 정리 대상이다. 이번 daemon 재시작에는 영향을 주지 않았지만, 주문 상태 관리 관점에서는 후속 점검이 필요하다.
