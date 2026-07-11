# v3·BOIL 상류 필터 SHADOW 연결 — report 단계

## 최종 상태

- 삽입 위치: Stage2·Stage3 candidate 생성 후, elite score 정렬·ticker dedup 이전
- 상류 enforcement: `SHADOW`
- 기존 live hook: 유지, `SHADOW`
- 실제 후보 제거: 0
- report 후보 목록 변화: 0
- live candidate pool 변화: 0
- 원본 artifact 변화: 0
- 현재 18개 상류 vs live hook 판정: 18/18 일치

## 1. 권위 소스 정렬

v3 권위:

```text
data/_system/analysis/candidate_selection_audit_20260710/
threshold_p99_weightless_block_candidate_decisions.csv
```

BOIL 권위:

```text
data/_system/analysis/candidate_selection_audit_20260710/
boil_block_exclusive_targets.csv
boil_block_enforcement_decision.json
```

초기 `integrated_gate_candidate_dryrun.csv::check_boil`은 더 이상 runtime 권위로 사용하지 않는다.

BOIL 판정 semantics:

```text
candidate_id가 최종 exclusive catalog에 있으면 FAIL
없으면 PASS
```

이 catalog 자체가 다음 확정 기준을 반영한다.

```text
HIGH_VOL
AND 거래량 없이 진입 가능
AND abs(weight_volume_surge)<=0.05
AND v3 PASS
AND v3 overlap excluded
```

따라서 BNTX는:

```text
v3=FAIL
reason=INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
BOIL=PASS
reason=NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS
aggregate=FAIL
```

로 기록된다. 차단 사유는 BOIL이 아니라 v3다.

## 2. 구현 파일

### 공통 권위 checker

```text
engine/live/candidate_gate.py
```

변경:

- BOIL source를 최종 exclusive catalog로 교체
- BOIL decision JSON을 evidence로 사용
- `upstream_gate_enforcement()` 추가
- 기존 live hook과 상류 hook이 동일 checker를 공유

### 상류 시뮬레이션

```text
engine/live/upstream_candidate_gate.py
```

기능:

```text
raw candidate rows
→ v3·BOIL candidate_id join
→ baseline 정렬·ticker dedup
→ FAIL/HOLD를 제거한 가상 정렬·ticker dedup
→ 제거 대상·동일 ticker 차순위 승계 기록
→ SHADOW에서는 baseline 그대로 반환
```

Runtime 로그:

```text
data/_system/analysis/upstream_gate_shadow/
upstream_gate_YYYYMMDD.jsonl
```

### report hook

```text
engine/live/elite_shadow_report.py
```

Stage2와 Stage3 모두 candidate row를 모두 만든 직후:

```text
apply_upstream_gate_shadow(...)
```

를 호출한다.

이 호출은 기존 정렬·ticker dedup과 동일 sort key를 사용한다.

## 3. config

```yaml
live:
  integrated_gate_enforcement: SHADOW
  upstream_gate_enforcement: SHADOW
```

`upstream_gate_enforcement`가 누락되거나 잘못된 값이면 SHADOW로 fallback한다.

## 4. 전체 report 시뮬레이션

### Stage2

```text
raw candidate rows=78
baseline ticker-dedup selected=13
SHADOW BLOCK simulation selected=10
raw FAIL=4
selected FAIL=4
same-ticker replacement=1
```

선택된 FAIL:

- STM: 대체 없음
- AGI: 대체 없음
- IRM: 대체 없음
- STLD: `stage2:STLD:47a7f7cefa30`으로 승계

### Stage3

```text
raw candidate rows=4,640
baseline top unique=80
SHADOW BLOCK simulation top unique=80
raw FAIL=1,273
selected FAIL=24
same-ticker replacement=10
```

Stage3는 FAIL 제거 후에도 하위 순위 후보가 cap 80을 다시 채워 예상 selected count가 80으로 유지됐다.

## 5. 현재 라이브 후보 18개 시뮬레이션

현재 FAIL 5개:

```text
BTBT
BMI
BCS
BNTX
CRK
```

동일 ticker 차순위 PASS 승계:

```text
BMI  → stage3:BMI:7c8934702364
BCS  → stage3:BCS:64684d1c13c7
BNTX → stage3:BNTX:5cacad3483c0
```

대체 없음:

```text
BTBT
CRK
```

따라서 룰북 slot 관점의 예상 구성은:

```text
18 - 5 + 3 = 16
```

이다. 단순 13개가 아니다.

다만 이 16은 report-level 룰북 구성 시뮬레이션이다. 대체 룰북이 실제 live signal evaluator에서 `should_buy=true`가 되는지는 별도다.

추가로 확인된 downstream 제약:

```text
BMI/BCS/BNTX 대체 candidate ID 모두 현재 live KEEP gate map에 없음
```

따라서 현행 `live_candidate_slots.py`에서는 상류 BLOCK을 켜더라도 이 대체 후보들이 `gate_missing`으로 다시 제외될 가능성이 있다. 실제 BLOCK 전환 전에는 기존 고정 KEEP gate의 ID coverage도 함께 정리해야 한다.

## 6. 기존 live hook과 18개 전수 대조

상류 checker와 기존 `live_candidate_slots.py` hook은 동일 `CandidateGateChecker`를 사용한다.

현재 18개 결과:

```text
aggregate match=18/18
v3 status match=18/18
BOIL status match=18/18
reason match=18/18
```

BNTX도 양쪽 모두:

```text
v3 FAIL
BOIL PASS
```

다.

상세:

```text
live_hook_comparison_18.csv
```

## 7. SHADOW 동작 불변

### report output 비교

변경 전 builder를 Git HEAD에서 메모리 로드해 변경 후 builder와 비교했다.

```text
candidate count 82 → 82
ordered candidate ID list equal=true
stage count {stage3:70, stage2:12} 동일
```

### live state

동일 report 생성 직전·직후:

```text
live_slots_state.json SHA-256 동일
candidate_pool canonical SHA-256 동일
```

### 원본 artifact

```text
central_index.jsonl 1개
stage3 final_rulebooks.jsonl 269개
총 270개 SHA-256 전후 동일
```

원본 룰북 artifact는 수정하지 않았다.

## 8. 테스트

신규·수정 테스트:

```text
tests/test_upstream_candidate_gate_shadow.py
tests/test_candidate_gate_shadow.py
```

검증 항목:

- SHADOW가 baseline dedup 결과를 그대로 반환
- FAIL top 룰북 제거 시 동일 ticker 차순위 PASS 승계
- BLOCK mode에서만 simulated 결과 반환
- 최종 BOIL-exclusive catalog membership
- BNTX가 v3 FAIL / BOIL PASS
- missing v3 row는 HOLD
- 현재 후보 18개 최종 BOIL FAIL 0
- config 기본값 SHADOW

결과:

```text
8 passed
```

## 9. 현재 결론

상류 SHADOW 연결 자체는 완료됐고 라이브 동작은 변하지 않았다.

다만 실제 상류 BLOCK 전환 전에 다음이 필요하다.

1. 대체 candidate ID에 대한 기존 KEEP gate coverage 정리
2. 상류 simulated candidate와 downstream evaluator를 연결한 shadow 평가
3. 기존 live hook을 audit-only로 축소할 시점 결정
4. 정규장 refresh에서 pool 변화 검증

현재 설정은 계속:

```text
upstream_gate_enforcement=SHADOW
integrated_gate_enforcement=SHADOW
```

이다.

## 산출물

- `current18_removal_replacement_simulation.csv`
- `report_stage_simulation.csv`
- `live_hook_comparison_18.csv`
- `invariance_and_hashes.md`
- `shadow_log_sample.jsonl`
- `readout.md`

실제 후보 차단·pool 변경·주문 변경: 0건
