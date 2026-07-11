# 단순 제거 SHADOW 불변·coverage 확인

## enforcement

```yaml
live:
  integrated_gate_enforcement: SHADOW
  upstream_gate_enforcement: SHADOW
```

두 경로 모두 실제 차단을 하지 않는다.

## 현재 18개 시뮬레이션

```text
baseline=18
FAIL 제거=5
replacement=0
refill=0
simulated=13
```

제거 ticker:

```text
BTBT
BMI
BCS
BNTX
CRK
```

예상 잔존 ticker:

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

## 차순위 ID 소멸 확인

이전 승계 시뮬레이션에서 등장했던 ID:

```text
stage3:BMI:7c8934702364
stage3:BCS:64684d1c13c7
stage3:BNTX:5cacad3483c0
```

단순 제거 simulated candidate IDs에서 모두:

```text
present=false
```

이다.

## KEEP gate coverage

상류에서 대체 ID를 만들지 않으므로, 위 세 ID가 기존 KEEP gate map에 없던 문제는 더 이상 발생하지 않는다.

```text
replacement_count=0
replacement IDs reaching downstream=0
gate_missing caused by replacement IDs=0
```

따라서 이 단순 제거 설계에서는 승계 ID coverage 보강이 필요 없다.

## live hook 대조

현재 후보 18개에서:

```text
aggregate match=18/18
v3 match=18/18
BOIL match=18/18
reason match=18/18
```

이다.

## live state·artifact SHA-256

동일 report 생성 직전·직후 전체 해시 비교:

```text
changed_count=0
```

대상:

- `data/_system/live_slots_state.json`
- `exp_batch_stage123_2009_20260616_full/central_index.jsonl`
- `tickers/*/stage3/final_rulebooks.jsonl` 269개

총 원본 artifact 270개와 live state 모두 전후 동일했다.

따라서:

- 실제 candidate pool 변경 0
- score 변경 0
- 주문 변경 0
- ledger 변경 0
- 원본 artifact 변경 0
