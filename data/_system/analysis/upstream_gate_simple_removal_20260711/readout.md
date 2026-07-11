# v3·BOIL 상류 필터 — 차순위 승계 제거, 단순 제거 SHADOW

## 최종 판정

`READY_FOR_BLOCK`

상류 게이트의 차순위 승계·빈자리 재충원 로직을 제거했다.

현재 설정은 계속:

```yaml
live:
  integrated_gate_enforcement: SHADOW
  upstream_gate_enforcement: SHADOW
```

이므로 실제 후보·pool·주문 동작은 변하지 않는다.

## 1. 구현 변경

변경 파일:

```text
engine/live/upstream_candidate_gate.py
tests/test_upstream_candidate_gate_shadow.py
```

이전 시뮬레이션:

```text
raw 후보에서 FAIL/HOLD 제거
→ 다시 정렬·ticker dedup
→ 동일 ticker 차순위 PASS 승계
→ cap 빈자리 하위 후보로 재충원
```

변경 후:

```text
raw 후보 전체를 dedup 이전에 판정
→ 기존 baseline 정렬·ticker dedup 결과 계산
→ baseline에서 FAIL/HOLD만 단순 제거
→ 재-dedup 없음
→ 차순위 승계 없음
→ 다른 ticker 재충원 없음
```

정책 label:

```text
SIMPLE_REMOVAL_NO_REPLACEMENT_NO_REFILL
```

SHADOW에서는 baseline 후보를 그대로 반환하고 단순 제거 결과만 로그에 기록한다.

## 2. 현재 후보 18개

제거 대상:

```text
BTBT
BMI
BCS
BNTX
CRK
```

결과:

```text
baseline=18
removed=5
replacement=0
refill=0
simulated=13
```

예상 잔존 13개:

```text
BMA
CRS
ALGT
CMC
BN
BWXT
ADMA
BB
ACMR
ANET
ARKW
CBRL
AEIS
```

## 3. 제거 종목 완전 소멸

이전 승계 후보:

```text
stage3:BMI:7c8934702364
stage3:BCS:64684d1c13c7
stage3:BNTX:5cacad3483c0
```

새 simulated candidate IDs에서 모두:

```text
present=false
```

이다.

따라서 FAIL ticker 5개는 report-level BLOCK 시뮬레이션에서 완전히 빠진다.

## 4. 전체 report 시뮬레이션

### Stage2

```text
raw candidate rows=78
baseline selected=13
FAIL/HOLD 제거 후=9
vacated=4
replacement=0
refill=0
```

제거:

```text
STM
AGI
IRM
STLD
```

### Stage3

```text
raw candidate rows=4,640
baseline selected=80
FAIL/HOLD 제거 후=56
vacated=24
replacement=0
refill=0
```

이전에는 하위 후보가 cap 80을 다시 채웠지만, 현재는 24개 빈자리를 그대로 남긴다.

## 5. KEEP gate coverage 문제

차순위 candidate ID를 만들지 않으므로 이전의:

```text
replacement candidate ID not in KEEP gate map
→ gate_missing
```

문제는 발생하지 않는다.

```text
replacement_count=0
replacement IDs reaching downstream=0
```

따라서 이 단순 제거 방식에서는 승계 ID coverage 정리가 불필요하다.

## 6. 권위 판정

권위 source는 변경하지 않았다.

v3:

```text
threshold_p99_weightless_block_candidate_decisions.csv
```

BOIL:

```text
boil_block_exclusive_targets.csv
boil_block_enforcement_decision.json
```

BNTX:

```text
v3=FAIL
reason=INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
BOIL=PASS
reason=NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS
```

## 7. 기존 live hook 대조

현재 18개:

```text
aggregate match=18/18
v3 match=18/18
BOIL match=18/18
reason match=18/18
```

제거 대상 판정은 기존 live hook과 동일하다.

## 8. 불변 확인

동일 report 생성 직전·직후:

```text
live_slots_state.json SHA-256 unchanged
central_index.jsonl unchanged
stage3 final_rulebooks.jsonl 269개 unchanged
changed_count=0
```

총 원본 artifact 270개가 모두 불변이다.

SHADOW이므로:

- 실제 report 후보 목록 변화 0
- live candidate pool 변화 0
- score 변화 0
- 주문 변화 0
- ledger 변화 0

이다.

## 9. 테스트

```text
PYTHONPATH=. venv/bin/python -m pytest -q \
  tests/test_upstream_candidate_gate_shadow.py \
  tests/test_candidate_gate_shadow.py
```

결과:

```text
8 passed
```

검증 항목:

- SHADOW baseline 반환
- FAIL 단순 제거
- 동일 ticker 차순위 미승계
- cap 빈자리 미충원
- BLOCK일 때만 단순 제거 결과 반환
- 최종 BOIL exclusive 권위
- BNTX v3 FAIL / BOIL PASS

## 결론

요청한 단순 제거 semantics는 구현·검증됐다.

현재는 SHADOW이므로 실제 동작 변화가 없으며, BLOCK 전환 시 현재 18개 기준 예상 pool은 정확히 13개다.

현재 조사 범위에서 추가 차단 요인은 확인되지 않아 판정은:

```text
READY_FOR_BLOCK
```

이다.

## 산출물

- `current18_simple_removal.csv`
- `report_stage_simulation.csv`
- `live_hook_comparison_18.csv`
- `invariance_and_coverage.md`
- `readout.md`
