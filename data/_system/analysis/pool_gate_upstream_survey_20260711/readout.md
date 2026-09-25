# 룰북 pool 생성 경로 조사 — 게이트 상류 이동 준비

## 최종 판정

`pool 필터 삽입 지점 확정 가능`

권장 지점은 `engine/live/elite_shadow_report.py`의 Stage2·Stage3 후보 생성 함수 내부에서 **candidate row 생성 후, elite_score 정렬·ticker dedup 이전**이다.

이 지점은 원본 학습 artifact를 건드리지 않으면서 모든 report 소비 경로를 공통으로 정리하고, FAIL 최상위 룰북 대신 같은 ticker의 차순위 PASS 룰북을 승계할 수 있다.

## 1. pool 생성 경로

### 학습·백테스트 artifact

Stage2·Stage3 단일 ticker 학습 스크립트가 다음을 생성한다.

```text
Stage2:
exp_batch_stage123_2009_20260616_full/tickers/<TICKER>/stage2/survivors.jsonl

Stage3:
exp_batch_stage123_2009_20260616_full/tickers/<TICKER>/stage3/final_rulebooks.jsonl
```

`scripts/research/run_stage23_batch.py`는 기존 Stage2/Stage3 스크립트를 subprocess로 실행하고 완료 artifact를 검증한다.

Stage2 survivor는 `build_stage2_central_index_rows()`를 통해:

```text
rulebook_hash
source_file
source_row_index
metrics
artifact_paths
```

와 함께 batch root의 `central_index.jsonl`에 append된다.

Stage3는 `final_rulebooks.jsonl`, `validation_results.jsonl`, `stage3_profile_catalog.jsonl` 등을 생성한다.

### elite 후보 report

`engine/live/elite_shadow_report.py`가 현재 live candidate의 실질적인 룰북 pool builder다.

Stage2:

```text
central_index.jsonl
→ eligible stage2 row
→ source_file/source_row_index로 survivors.jsonl 룰북 복원
→ OOS/fitness/trade/DD 필터
→ anti-pattern 필터
→ elite_score 정렬
→ ticker별 1개 dedup
→ 최대 60개
```

Stage3:

```text
*/stage3/final_rulebooks.jsonl
→ metrics 필터
→ anti-pattern 필터
→ elite_score 정렬
→ ticker별 1개 dedup
→ 최대 80개
```

최종:

```text
stage2 + stage3
→ candidate denylist
→ bucket/elite_score 정렬
→ report.candidates
```

### live candidate pool

`data/_system/ops/live_candidate_slots.py::refresh_slots()`가:

```text
build_elite_shadow_report(stage2_limit=60, stage3_limit=80)
→ KEEP gate
→ held exclusion
→ v3·BOIL hook
→ evaluate_candidate()
→ should_buy=true만 append
→ live_slots_state.json::candidate_pool 저장
```

한다.

`live_slots_state.json::candidate_pool`은 룰북 원본 pool이 아니라 라이브 평가를 통과한 파생 신호 pool이다.

## 2. pool 생성 주기

Guard 실행:

```text
live_candidate_slots.py daemon --interval 60
```

정규장:

- 매 loop마다 `build_elite_shadow_report()` 재호출
- artifact를 다시 읽음
- signal 재평가
- candidate_pool 재작성

장외:

- `outside_regular_hours_cached_pool`
- 기존 candidate_pool 재사용
- artifact 재조립 없음

따라서 pool은 1회 생성 후 고정이 아니라 정규장 60초 주기로 재생성된다.

## 3. 룰북 식별자

Candidate ID 규칙:

```text
stage2:<ticker>:<rulebook_hash[:12]>
stage3:<ticker>:<rulebook_hash[:12]>
```

현재 18개 모두:

- live state candidate ID
- elite report candidate ID
- v3 catalog candidate ID

가 exact match한다.

대조 결과:

```text
candidate_pool 18
elite report exact match 18/18
v3 catalog exact join 18/18
```

최종 BOIL exclusive target은 현재 18개 중 0개이므로 exact match row가 0건이다. 이는 join schema 불일치가 아니라 현재 pool에 BOIL-exclusive FAIL 룰북이 없기 때문이다.

키 schema는 동일하다.

```text
candidate_id = stage:ticker:rulebook_hash_short
```

상세 매핑은 `rulebook_id_mapping.csv`에 기록했다.

## 4. 권장 삽입 지점

### 권장 A — Stage별 ticker dedup 이전

위치:

```text
collect_stage2_elite()
  rows.append(candidate)
  [v3·BOIL filter]
  rows.sort(...)
  ticker dedup

collect_stage3_elite()
  rows.append(candidate)
  [v3·BOIL filter]
  rows.sort(...)
  ticker dedup
```

장점:

- FAIL 룰북이 ticker 대표 자리를 차지하지 않음
- 같은 ticker에 차순위 PASS 룰북이 있으면 자동 승계
- 모든 `build_elite_shadow_report()` 소비 경로가 동일 pool 사용
- 원본 artifact 보존
- 다음 refresh로 반영
- rollback 쉬움

단점:

- Stage2·Stage3 양쪽에 공통 helper 연결 필요
- catalog missing/HOLD 정책과 policy version을 명확히 유지해야 함
- report summary에 gate skip reason 추가 필요

### 후보 B — Stage dedup 후 merge 전

구현은 단순하지만 top FAIL 룰북이 제거된 ticker에 차순위 PASS 룰북을 승계하지 못한다.

### 후보 C — Stage merge 후 denylist 전

가장 작은 변경이지만 B와 동일한 대체 룰북 손실 문제가 있다.

### 후보 D — candidate denylist 생성

기존 denylist 메커니즘을 재사용할 수 있지만 denylist 역시 ticker dedup 후 적용된다. 또한 frozen gate와 denylist 동기화 job이 필요하다.

### 후보 F — 학습 artifact 생성 시 제거

가장 상류지만 기존 batch 재생성·복구가 필요하며 frozen catalog와 artifact 의미를 바꾼다. 권장하지 않는다.

## 5. 현재 pool 이동 영향

Event OFF는 별도 축이며 여기서는 포함하지 않는다.

현재 v3 FAIL:

```text
BTBT
BMI
BCS
BNTX
CRK
```

현재 최종 BOIL-exclusive FAIL:

```text
없음
```

현재 candidate ID 기준 제거 대상은 5개다.

단, 권장 지점에서 필터하면 해당 ticker의 차순위 PASS 룰북이 존재할 경우 새 candidate ID로 대체될 수 있다. 따라서 재생성 후 candidate_pool이 반드시 13개가 된다고 단정할 수는 없다.

## 6. 재생성·소급 적용

원본 룰북 재학습은 필요 없다.

필요한 재생성:

```text
elite candidate report
live candidate_pool
```

반영 시점:

- 다음 정규장 refresh
- 또는 명시적 force-evaluate refresh

장외에는 cached pool을 재사용하므로 코드 배포만으로 기존 state가 즉시 바뀌지 않는다.

기존 `live_slots_state.json`을 직접 필터링하는 소급 적용도 가능하지만 파생 state를 수동 수정하는 방식이다. 권장 방식은 정상 refresh 재생성이다.

## 7. shadow hook 필요성

상류 필터가 `build_elite_shadow_report()`에 들어가면 다음 경로는 공통으로 정리된다.

- live candidate slots
- elite shadow trader/simulator
- dashboard candidate exporter
- S2 auto full candidate lookup
- signal-history/pullback report

따라서 현재 live slots hook의 **차단 기능**은 불필요해진다.

다만 전환 초기에는 상류 결과와 기존 hook 결과를 비교하는 audit 용도로 유지할 수 있다. 전수 일치 후 hook을 제거하거나 audit-only로 축소하는 것이 안전하다.

## 8. 되돌리기 성격

권장 report-level 필터는 비가역이 아니다.

```text
코드/config rollback
→ 다음 정규장 refresh
→ 원본 artifact에서 후보 재생성
```

으로 복구된다.

재학습은 필요 없다.

롤백 난이도:

| 방식 | 난이도 |
|---|---|
| 현재 live hook | 가장 쉬움 |
| report 상류 필터 | 쉬움 |
| denylist materialization | 중간 |
| artifact 생성 필터 | 어려움 |

## 9. 최종 권고

삽입점은 확정 가능하다.

```text
engine/live/elite_shadow_report.py
Stage2/Stage3 candidate construction 이후
elite_score 정렬·ticker dedup 이전
```

권장 전환 순서:

1. 상류 filter를 SHADOW로 연결
2. 기존 live hook 결과와 candidate ID 전수 대조
3. 차순위 PASS 승계 여부 확인
4. 상류 BLOCK 전환
5. 다음 정규장 pool 재생성 확인
6. 기존 live hook 차단 기능 제거 또는 audit-only 전환

운영 코드·설정 변경: 0건

## 산출물

- `pool_generation_flow.md`
- `rulebook_id_mapping.csv`
- `insertion_candidates.csv`
- `move_impact.md`
- `readout.md`
