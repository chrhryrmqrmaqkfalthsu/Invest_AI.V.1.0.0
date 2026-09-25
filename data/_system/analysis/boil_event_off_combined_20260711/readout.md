# BOIL 게이트 + direct Event OFF 결합 시 라이브 후보 최종 생존 판정

## 결론

현재 라이브 후보 18개에 BOIL 설계 게이트와 direct Event OFF를 동시에 적용한 저장 component 기반 예상 결과는 다음과 같다.

| 분류 | 표본 수 |
|---|---:|
| SURVIVE_BOTH | 11 |
| DROP_BY_EVENT | 6 |
| DROP_BY_BOIL | 0 |
| DROP_BY_BOTH | 1 |
| BOIL_UNVERIFIABLE | 0 |

## 1. BOIL 적용 가능성

BOIL 게이트는 운영 코드·설정에 구현돼 있지 않다.

근거:

- runtime `engine`, `scripts`, `config`에서 `BOIL` 구현 참조 없음
- `integrated_gate_architecture.json`의 `design_status=DESIGN_ONLY_NOT_IMPLEMENTED`
- `high_vol_volume_blind_near_zero_v3_exclusive` checker의 `operational_implementation=false`
- `boil_block_enforcement_readout.md`의 `운영 구현: false`

따라서 이번 결과는 실제 라이브 차단 결과가 아니라 2026-07-10 dry-run checker를 현재 후보 18개에 매핑한 가상 결합 판정이다.

BOIL 조건:

```text
HIGH_VOL
AND entry_possible_without_volume
AND abs(weight_volume_surge) <= 0.05
AND v3 PASS
```

현재 18개는 모두 `integrated_gate_candidate_dryrun.csv`에 candidate_id exact match가 있어 BOIL 판정 가능했다.

BOIL FAIL:

```text
BNTX 1개
vol_group=HIGH_VOL
weight_volume_surge=0.0
check_boil=FAIL
```

나머지 17개는 BOIL PASS다.

## 2. Event OFF 재확인

저장 `live_slots_state.json`의 `이벤트반응(+x.xx)`, raw score, final score, threshold를 사용했다.

```text
market_multiplier = final_score / raw_score
off_estimated_score = (raw_score - event_contribution) × market_multiplier
```

Event OFF 예상 탈락 7개:

```text
BTBT, BMA, BMI, BNTX, CMC, BWXT, ACMR
```

Event OFF 예상 생존 11개:

```text
CRS, BCS, ALGT, BN, ADMA, BB, ANET, ARKW, CBRL, CRK, AEIS
```

이는 저장 component 기반 예상치이며 모든 후보의 실제 OFF shadow 재평가 결과는 아니다.

## 3. 결합 4분류

### SURVIVE_BOTH — 11개

```text
CRS, BCS, ALGT, BN, ADMA, BB, ANET, ARKW, CBRL, CRK, AEIS
```

모두 BOIL PASS이고 Event OFF 예상 score도 threshold 이상이다.

### DROP_BY_EVENT — 6개

```text
BTBT, BMA, BMI, CMC, BWXT, ACMR
```

BOIL은 PASS지만 direct Event를 제거하면 threshold 미달 예상이다.

### DROP_BY_BOIL — 0개

BOIL만 단독으로 걸리는 후보는 없다.

### DROP_BY_BOTH — 1개

```text
BNTX
```

BNTX는 BOIL FAIL이면서 Event OFF 예상 score도 threshold 미달이다.

### BOIL_UNVERIFIABLE — 0개

18개 모두 기존 dry-run candidate_id에 exact match됐다.

## 4. 최초신호→현재 미실현 변동

가격 소스:

```text
data/_system/live_slots_state.json
first_signal_price
price
last_seen_at=2026-07-10T19:59:53.001495+00:00
```

### SURVIVE_BOTH — 표본 11

```text
CRS -3.6714%
BCS -0.6959%
ALGT +1.0273%
BN +0.5632%
ADMA -1.5727%
BB -0.6793%
ANET +13.6596%
ARKW +1.4940%
CBRL +2.8433%
CRK -0.6391%
AEIS +6.1251%
참고 평균 +1.6776%
```

### DROP_BY_EVENT — 표본 6

```text
BTBT +3.3537%
BMA +3.4370%
BMI -1.8425%
CMC +1.7457%
BWXT -0.1584%
ACMR -3.7926%
참고 평균 +0.4571%
```

### DROP_BY_BOTH — 표본 1

```text
BNTX -2.6862%
```

### DROP_BY_BOIL — 표본 0

없음.

## 해석 제한

- BOIL은 운영 미구현 설계 게이트다.
- Event OFF는 저장 component 기반 예상치다.
- 가격은 동일 시점의 live-slot 미실현 스냅샷이다.
- 최초신호 날짜와 관찰 기간이 종목마다 다르다.
- 수수료·슬리피지·실제 체결 여부를 반영하지 않았다.
- 후보 pool 생존 종목만 포함해 생존편향 가능성이 있다.
- 표본 수 18, 그룹별 11·6·1·0으로 통계적 우열을 단정할 수 없다.
- 참고 평균은 관찰 편의를 위한 값이며 수익성 결론이 아니다.

## 산출물

- `boil_applicability.csv`
- `combined_classification.csv`
- `performance_comparison.csv`
- `readout.md`

운영 코드·설정 변경: 0건
