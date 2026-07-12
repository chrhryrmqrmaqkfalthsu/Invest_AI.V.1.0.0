# 라이브 S2 후보선정 경로 전반 전수 조사

- 조사 시점: 2026-07-12
- 대상: `S2AutoTrader`, `live_candidate_slots.py`, `build_elite_shadow_report()`, Event OFF, v3·BOIL BLOCK
- 코드 변경: **0**
- 최종 판정: **S2에 오늘 작업 적용됨**

## 1. S2의 정체와 범위

코드에서 라이브 “S2”는 `engine/live/s2_auto_trader.py::S2AutoTrader`다. 2026-07-08 commit `75faf9a5871fe92fa43cb842f79481b0b6ac4825`에서 dry-run·next-open 자동매매 컨트롤러로 추가됐다.

관련 구성:

- 설정: `engine/live/s2_auto_config.py`
- 실행기: `scripts/run_s2_auto_live.py`
- 상태: `data/_system/live_auto_state.json`
- 이벤트: `data/_system/live_auto_events.jsonl`
- 주문 intent: `data/_system/live_auto_order_intents.jsonl`
- 공유 후보 state: `data/_system/live_slots_state.json`

S2라는 이름과 달리 후보는 Stage2 전용이 아니다. 현재 구현은 Stage2와 Stage3가 합쳐진 공유 `candidate_pool`을 소비한다.

## 2. S2 후보선정 경로

S2는 후보를 자체 생성하지 않는다.

실제 흐름:

1. `live_candidate_slots.py` daemon이 `build_elite_shadow_report(stage2_limit=60, stage3_limit=80)`를 호출한다.
2. elite report가 Stage2와 Stage3 후보를 수집한다.
3. v3·BOIL upstream gate와 denylist를 적용한다.
4. 각 후보를 `evaluate_candidate()`로 재평가하고 `should_buy=true`만 남긴다.
5. 우선순위와 final score로 정렬해 `data/_system/live_slots_state.json::candidate_pool`에 기록한다.
6. `S2AutoTrader.candidate_pool()`은 이 저장된 pool을 그대로 읽고 보유 후보를 제외한다.
7. 주문 직전 `_candidate_full_payload()`가 다시 `build_elite_shadow_report()`를 호출해 candidate_id를 exact match한다.
8. `_validate_candidate_signal()`이 다시 `evaluate_candidate()`를 실행하고 `should_buy=true`를 요구한다.

근거:

- `data/_system/ops/live_candidate_slots.py:378`
- `engine/live/s2_auto_trader.py:290-329`

즉 S2는 공유 pool을 재사용하며, 주문 직전 같은 elite report와 signal evaluator를 한 번 더 통과한다.

## 3. 오늘 작업 적용 여부

### direct Event OFF

현재 effective 설정:

- `live.direct_event_enabled = false`

`evaluate_candidate()`는 `_event_flags(ctx)`를 호출하고, 이는 `live_event_flags(ctx)`를 통해 전역 설정을 읽는다. OFF이면 `event_flags=None`을 `evaluate_signal()`에 넘긴다.

S2 주문 직전 signal 재검증도 동일 `evaluate_candidate()`를 사용한다.

따라서 Event OFF는 다음 모두에 적용된다.

- `live_candidate_slots`의 pool 생성
- S2 full candidate lookup 이후 signal 재검증
- S2 주문 계획의 `should_buy` 판단

판정: **적용됨**.

주의: OFF는 direct `event_flags`만 제거하며 `MarketContext.score`에 이미 포함된 macro score는 유지한다. 이는 코드상 의도된 부분 적용이다.

근거:

- `engine/live/event_policy.py:1-6,53-79`
- `engine/live/elite_shadow_trader.py:376-419`

### v3·BOIL BLOCK

현재 effective 설정:

- `live.upstream_gate_enforcement = BLOCK`

Stage2와 Stage3 collector 모두 raw 후보에 `apply_upstream_gate_shadow()`를 호출한다. enforcement가 BLOCK이면 PASS만 반환하고 FAIL/HOLD는 제거한다.

S2는:

- BLOCK이 적용된 shared candidate pool을 읽고
- full lookup에서도 다시 BLOCK이 적용된 elite report를 호출한다.

따라서 S2가 v3·BOIL을 우회하는 경로는 발견되지 않았다.

판정: **적용됨**.

근거:

- `engine/live/elite_shadow_report.py:283-309,312-378`
- `engine/live/upstream_candidate_gate.py:92-164`
- `engine/live/s2_auto_trader.py:300-329`

## 4. S2와 candidate_pool 정합성

조사 시점 shared candidate pool과 `S2AutoTrader.candidate_pool()`은 candidate_id와 순서가 정확히 같았다.

현재 10개:

1. `stage3:ADMA:42437a3ee595`
2. `stage3:CRS:8695c9ce3320`
3. `stage3:ALGT:aec5dd5b1dc1`
4. `stage3:AEIS:6e26f08a7c6d`
5. `stage3:ARKW:296c057b4ef7`
6. `stage3:CBRL:677767a0b6a9`
7. `stage3:BTU:ecfcb41f1be2`
8. `stage3:BB:f1bdfe7f8ad9`
9. `stage3:BN:d264957fe5f6`
10. `stage3:ACMR:44c1e02681c4`

분포:

- Stage2: 0
- Stage3: 10

따라서 “S2 auto”는 이름과 달리 현재 실제로 Stage3 후보 10개를 소비한다.

제거 대상 5개:

- BTBT
- BMI
- BCS
- BNTX
- CRK

이들은 다음 세 곳 모두에 존재하지 않았다.

- shared candidate pool
- S2 candidate pool
- 현재 BLOCK 적용 elite report

즉 S2에 살아 있는 우회 후보는 없었다.

## 5. 현재 실행 상태

2026-07-12 process/cron 확인 결과:

- `live_candidate_slots.py daemon`: 실행 중
- S2 auto daemon: 실행 중 아님
- S2 auto cron: 없음
- candidate slots guard: 매분 및 reboot 실행

현재 S2 설정:

- `master_enabled=false`
- `auto_buy_enabled=false`
- `auto_exit_enabled=false`
- `real_orders_enabled=true`
- `dry_run=false`
- `operator_armed=false`

`real_orders_enabled=true`만으로는 주문할 수 없다. master, auto-buy, direct-order env, operator arm, kill switch 등 모든 gate를 통과해야 한다.

현재는 master와 auto-buy가 꺼져 있고 S2 daemon도 없으므로 S2발 신규 BUY 가능성은 차단돼 있다.

## 6. 주문 경로

모든 gate가 명시적으로 활성화되면 S2는 실제 주문을 낼 수 있는 코드다.

경로:

1. candidate pool 최상위 선택
2. full payload 재조회
3. current signal 재검증
4. capital/risk/capacity 계산
5. `real_order_gate()`
6. `SafetyLayer.check_order()`
7. order intent 기록
8. `broker.place_buy(... OrderType.MARKET ...)`
9. fill 시 `BuyReconciliationService`로 position 등록

근거:

- `engine/live/s2_auto_trader.py:342-475`

그러나 보존 상태상:

- `real_order_count=0`
- `orders_today=0`
- S2 order intent 파일 없음
- `REAL_BUY_SUBMITTED` 이벤트 없음

## 7. CE 거래 경위

현재 CE candidate `stage3:CE:998b0b638c66`은 denylist에 있다.

S2 보존 기록에서는 다음이 발견되지 않았다.

- CE S2 order intent
- CE `REAL_BUY_SUBMITTED`
- `km-s2-auto` CE client order id

그리고 S2 state의 누적 실주문 수는 0이다.

따라서 CE 거래가 S2 경로였다는 증거는 없으며, 보존된 S2 기록 기준으로는 S2발 거래가 아니다. 다만 CE 거래를 발생시킨 다른 dashboard/broker 경로의 완전한 provenance는 이 조사 범위에서 저장 근거를 찾지 못해 `NOT_STORED`로 둔다.

## 최종 판정

### **S2가 오늘 작업 적용됨**

- Event OFF: 적용됨
- v3 BLOCK: 적용됨
- BOIL BLOCK: 적용됨
- shared candidate pool과 S2 pool: 동일
- 제거 대상 5개: S2에도 없음
- S2 우회 경로: `NOT_FOUND`
- 현재 S2 실주문: 차단 상태

단, 명명상 주의가 필요하다. 현재 `S2AutoTrader`는 Stage2 전용 trader가 아니라 Stage2+Stage3 elite report에서 만든 공유 pool을 소비하는 자동주문 컨트롤러이며, 조사 시점 pool은 전부 `stage3:` 후보다.

세부 산출물:

- `s2_identity_scope.csv`
- `today_policy_application.csv`
- `candidate_pool_consistency.csv`
- `execution_state.csv`
