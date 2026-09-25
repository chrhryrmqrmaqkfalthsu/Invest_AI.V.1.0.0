# CE 동적 게이트 백테스트 가능성 점검

- 판정: **PARTIAL**
- 분석 방식: 기존 로그·스냅샷·원본 history read-only
- 설계·구현 변경: 없음
- 운영 구현: `false`

## 1. 핵심 결론

CE의 `ratio = final_score / threshold`와 realized core Top2 집중도를 동일 진입 건의 성과와 함께 검증할 수 있는 durable 데이터는 Stage2에만 존재한다.

- Stage2: 1,162/1,162개 후보, 71,271건의 역사적 진입이 full CE 백테스트 가능
- Stage3: 0/15,909개 후보, 0건 — canonical `exit_trades.jsonl`에 entry score/components가 없음
- CE FAIL 7개: full 현재 스냅샷은 7/7, 역사적 full 진입 스냅샷은 0/7

따라서 CE 임계 `ratio<1.25`, `Top2>=90%`를 Stage2 범위에서 retrospective 검증하는 것은 가능하지만, Stage3와 CE 7개에 일반화해 데이터로 확정하는 것은 불가능하다.

## 2. 주요 소스 커버리지

| 소스 | 레코드 | 후보 | score+threshold | core 분해 | Top2 가능 | 동일 진입 성과 | 용도 |
|---|---:|---:|---:|---:|---:|---:|---|
| CANONICAL_STAGE2_HISTORY | 72,690 | 1,162 | 72,690 | 72,690 | 71,271 | YES | BACKTESTABLE_STAGE2_ONLY |
| CANONICAL_STAGE3_HISTORY | 975,118 | 15,909 | 0 | 0 | 0 | NO | NOT_USABLE_NO_ENTRY_SIGNAL_SNAPSHOT |
| LIVE93_THREE_SYMPTOM_SCAN | 93 | 93 | 93 | 93 | 86 | NO | DIAGNOSTIC_POINT_SNAPSHOT_ONLY |
| REAL_DASHBOARD_BUY_CANDIDATES | 18 | 18 | 18 | 18 | 18 | NO | DIAGNOSTIC_POINT_SNAPSHOT_ONLY |
| LIVE_SLOTS_STATE_CANDIDATE_POOL | 18 | 18 | 18 | 0 | 0 | NO | RATIO_ONLY_NO_COMPONENTS |
| ELITE_SHADOW_CLOSED_TRADES | 150 | 32 | 150 | 0 | 0 | NO | RATIO_OUTCOME_ONLY_NO_COMPONENTS |
| ELITE_STRATEGY_SIM_TRADES | 2 | 2 | 2 | 0 | 0 | NO | RATIO_OUTCOME_ONLY_NO_COMPONENTS |
| DAILY_SIGNAL_REPLAY | 20 | 0 | 20 | 20 | 20 | NO | LIMITED_PROBE_NOT_SYSTEM_COVERAGE |
| SCHEDULED_OPEN_BUY_QUEUE | 8 | 8 | 8 | 0 | 0 | NO | RATIO_ONLY_NO_COMPONENTS |
| CENTRAL_BUY_CANDIDATES | 2 | 2 | 2 | 0 | 0 | NO | RATIO_ONLY_NO_COMPONENTS |
| FROZEN_OOS_TRADES_93 | 43,972 | 93 | 0 | 0 | 0 | NO | OUTCOME_ONLY_NO_ENTRY_SNAPSHOT |

## 3. 스냅샷 품질

### Stage2 canonical trades

`entry_signal_score`, `entry_signal_threshold`, `entry_signal_components`, 시장보정·시장/섹터/VIX 컨텍스트와 `pnl_pct`가 동일 trade row에 저장된다. core MA/MACD/RSI/BB/Volume을 분해해 Top2를 재계산할 수 있다.

### Stage3 canonical exit trades

성과·진입일·entry rule hash는 있지만 `entry_signal_score`, `entry_signal_threshold`, `entry_signal_components`가 없다. 결과는 있으나 CE 원인을 붙일 수 없다.

### live93·real dashboard

현재 시점 score·threshold·component 분해가 있어 CE 상태 계산은 가능하다. 그러나 후보당 한 시점이고 동일 진입 이후 성과가 연결된 반복 표본이 아니므로 임계 검증용 백테스트 데이터가 아니다.

### elite shadow·strategy sim

진입 ratio와 결과가 연결되지만 component dict가 저장되지 않아 realized Top2를 계산할 수 없다. ratio 단독 임계 분석만 가능하다.

### daily signal replay

full component를 포함하지만 좁은 연구 subset이며 시스템 전체 후보·CE7 커버리지가 없다. 시스템 임계 도출 근거로는 부족하다.

## 4. CE 7개

| 후보 | live93 full 현재값 | 역사적 full CE 진입 | shadow ratio+성과 | 판정 |
|---|---:|---:|---:|---|
| stage3:ANET:fe220620802b | YES | 0 | 7 | POINT_SNAPSHOT_PLUS_RATIO_OUTCOME_NO_COMPONENTS |
| stage3:BB:f1bdfe7f8ad9 | YES | 0 | 4 | POINT_SNAPSHOT_PLUS_RATIO_OUTCOME_NO_COMPONENTS |
| stage3:BOIL:9044dc2c67a3 | YES | 0 | 0 | POINT_SNAPSHOT_ONLY |
| stage3:BTE:4ba9af200f79 | YES | 0 | 0 | POINT_SNAPSHOT_ONLY |
| stage3:CDE:ceb9fe0512dc | YES | 0 | 10 | POINT_SNAPSHOT_PLUS_RATIO_OUTCOME_NO_COMPONENTS |
| stage3:CE:998b0b638c66 | YES | 0 | 1 | POINT_SNAPSHOT_PLUS_RATIO_OUTCOME_NO_COMPONENTS |
| stage3:CWK:2970595abcd4 | YES | 0 | 0 | POINT_SNAPSHOT_ONLY |

v3 동적 전용 ANET·BB·CDE·CE도 역사적 full CE 진입 스냅샷은 0건이다. 일부는 shadow ratio+성과가 있지만 component가 없어 Top2 검증은 불가능하다.

## 5. 최종 판정

**PARTIAL**

- Stage2 historical entry 범위: `BACKTESTABLE`
- Stage3 및 CE7 임계 검증: `INSUFFICIENT_SNAPSHOT`
- 시스템 전체 CE 임계 확정: 불가

현재 자료만으로 `1.25`와 `90%`를 시스템 전체에 확정하면 Stage3에서는 임의 임계 의존 위험이 크다. Stage2에서 후보 임계 조합을 탐색할 수는 있지만 Stage3 external validation 없이 운영 BLOCK 근거로 일반화하면 안 된다.

## 6. 관측 로깅 대안 평가

관측 전용 로깅은 현실적이며 난이도가 낮다. `evaluate_signal`은 이미 score·raw_score·threshold·market_adjustment·components를 반환하고, shadow/queue 계층은 score와 threshold를 이미 저장한다. component와 context, outcome join key를 append-only로 추가하면 된다.

권장 방식은 차단 없는 observation-only 축적이다. 구현은 이번 작업에서 수행하지 않았다.

## 7. 산출물

- `ce_dynamic_snapshot_source_coverage.csv`
- `ce_dynamic_snapshot_candidate_coverage.csv.gz`
- `ce_dynamic_ce7_snapshot_coverage.csv`
- `ce_dynamic_snapshot_quality.csv`
- `ce_dynamic_backtestability_summary.json`

## 8. 추가 인벤토리 — 상태·런타임·텍스트 로그

- `elite_shadow_state.json#open_positions`: 16개 open entry의 score/threshold/ratio는 있으나 technical component는 없다. `entry_concentration.components`는 배분 품질 점수이며 CE의 MA/MACD/RSI/BB/Volume 기여도가 아니다.
- `live_slots_events.jsonl`: 2,849개 운영 이벤트 중 구조화된 score+threshold+component 진입 스냅샷은 0건이다.
- `evaluate_signal` 반환 객체 자체에는 필요한 component가 모두 있지만, 호출자가 이를 저장하지 않으면 과거 검증 자료로 남지 않는다.
- `elite_shadow_report`와 `elite_signal_history`는 런타임/온디맨드 계산 경로이며 durable full CE 로그가 아니다. 특히 signal history는 component를 출력하지 않고 현재 시장 컨텍스트를 과거 날짜에 고정 재생한다.
- `data/logs/*.log`, `logs/*.log`에서는 구조화된 CE 진입 스냅샷을 찾지 못했다.
