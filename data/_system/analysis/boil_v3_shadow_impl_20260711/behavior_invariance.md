# SHADOW 동작 불변 확인

## 설정

```yaml
live:
  integrated_gate_enforcement: SHADOW
```

## 현재 후보 18개 검증

검증 전후 파일:

```text
data/_system/live_slots_state.json
```

SHA-256:

```text
before=91d878230ca342b2704b0dd920efde1dcd1cca9288cc4010093bc77595c21b3f
after =91d878230ca342b2704b0dd920efde1dcd1cca9288cc4010093bc77595c21b3f
```

결과:

```text
STATE_HASH_UNCHANGED
```

현재 candidate_pool 18개를 모두 SHADOW로 평가했지만 다음은 변경되지 않았다.

- candidate_pool row 수
- candidate ID
- final_score
- threshold
- price
- slots/waitlist
- 후보 통과 여부
- 주문·ledger

## 차단 동작 분리

`CandidateGateDecision.should_block`은 다음 조건에서만 true다.

```text
enforcement == BLOCK
AND aggregate_status in {FAIL, HOLD}
```

SHADOW에서는 v3/BOIL FAIL 또는 HOLD가 있어도 항상:

```text
should_block=false
```

이다.

현재 18개 결과:

```text
aggregate PASS=13
aggregate FAIL=5
aggregate HOLD=0
should_block=true=0
```

## BOIL frozen dry-run 대조

현재 18개 중 BOIL FAIL:

```text
BNTX 1개
```

기존 `integrated_gate_candidate_dryrun.csv`와 18/18 일치했다.

## 주의

최신 weightless-v3 catalog에서는 BNTX가 v3 FAIL이다. 그러나 기존 통합 BOIL dry-run은 BNTX를 BOIL FAIL로 확정했다. 새 조건을 정의하지 않기 위해 BOIL checker는 frozen integrated `check_boil`을 권위값으로 사용하고, 최신 v3 상태는 evidence에 병기한다.
