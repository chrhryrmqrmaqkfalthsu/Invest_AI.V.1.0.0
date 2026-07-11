# BOIL·v3 BLOCK 전환 전 정합성 조사

## 최종 판정

`NEEDS_MORE`

BNTX 불일치의 원인과 권위 소스는 기존 자료만으로 해소됐다. 그러나 현재 SHADOW 구현이 최종 BOIL 권위가 아닌 초기 integrated BOIL 값을 사용하므로, BLOCK 전환 전 runtime source 정렬과 재대조가 필요하다.

## 1. BNTX 불일치 원인

생성 순서:

```text
f7a1518 / 2026-07-10 16:08:26 UTC
초기 integrated dry-run 생성
BNTX check_boil=FAIL

ce121c3 / 2026-07-10 17:32:46 UTC
weightless-v3 확정
BNTX v3=FAIL
reason=INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99

4cbc5e8 / 2026-07-10 18:06:36 UTC
최종 BOIL enforcement 확정
조건에 v3 PASS와 v3 overlap excluded 명시
BNTX는 boil_block_exclusive_targets.csv에 없음
```

즉 초기 BOIL 판정이 먼저 있었고, 이후 v3가 weight=0 단방향 threshold까지 검사하면서 BNTX를 신규 포섭했다. 최종 BOIL 정책은 v3가 이미 차단한 후보를 BOIL 대상에서 제외한다.

현재 권위 판정:

```text
BNTX v3=FAIL
BNTX BOIL=PASS 또는 NOT_APPLICABLE
BNTX overall=FAIL
```

차단 여부는 변하지 않지만 차단 사유는 BOIL이 아니라 v3다.

## 2. 권위 소스

### v3

```text
data/_system/analysis/candidate_selection_audit_20260710/
threshold_p99_weightless_block_candidate_decisions.csv
```

정책 version:

```text
integrated-gate-v3-p99-reachability-block-weightless
```

### BOIL

```text
boil_block_exclusive_targets.csv
boil_block_enforcement_decision.json
boil_block_enforcement_readout.md
```

최종 조건:

```text
HIGH_VOL
AND 거래량 없이 진입 가능
AND abs(weight_volume_surge)<=0.05
AND v3 PASS
AND v3 overlap excluded
```

초기 `integrated_gate_candidate_dryrun.csv::check_boil`은 최종 BOIL 권위 소스로 쓰면 안 된다.

## 3. 18개 전수 정합성

초기 integrated BOIL과 최종 BOIL-exclusive 대조에서 갈리는 후보:

```text
BNTX 1개
```

나머지 17개는 BOIL PASS로 일치한다.

최신 v3 FAIL 5개:

```text
BTBT  ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
BMI   ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
BCS   ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
BNTX  INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
CRK   ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_MAX
```

상세는 `consistency_18.csv`에 기록했다.

## 4. Event OFF와 v3 BLOCK 대조

Event OFF 실제 탈락 7개:

```text
BTBT, BMA, BMI, BNTX, CMC, BWXT, ACMR
```

Event OFF 실제 생존 11개:

```text
CRS, BCS, ALGT, BN, ADMA, BB, ANET, ARKW, CBRL, CRK, AEIS
```

v3 FAIL 5개 중 Event OFF에서 이미 탈락:

```text
BTBT
BMI
BNTX
```

v3 BLOCK으로 추가 탈락:

```text
BCS
CRK
```

근거:

```text
BCS OFF score=4.9308408026
threshold=3.3016768174
Event OFF에서는 생존
v3 reason=ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99

CRK OFF score=1.8851749773
threshold=1.6845297733
Event OFF에서는 생존
v3 reason=ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_MAX
```

## 5. 최종 생존 수

현재 18개 기준:

```text
초기 후보 18
Event OFF 탈락 7
Event OFF 생존 11
v3 추가 탈락 2
최종 BOIL-exclusive 추가 탈락 0
최종 생존 9
```

최종 생존 후보:

```text
CRS
ALGT
BN
ADMA
BB
ANET
ARKW
CBRL
AEIS
```

## 6. BLOCK 전환 차단 요인

① 불일치는 해소됐다. 따라서 `BLOCKED_BY_①`은 아니다.

하지만 현재 SHADOW 구현의 BOIL checker는 초기 integrated `check_boil`을 권위값으로 사용한다. 최종 정책은 `boil_block_exclusive_targets.csv`를 사용해야 한다.

현재 18개에서는 BNTX가 v3로도 FAIL이므로 총 차단 수는 같지만:

- 차단 사유가 잘못 기록됨
- v3 overlap exclusion이 runtime에 반영되지 않음
- 전체 universe에서 BOIL 범위가 최종 371개 exclusive target과 달라질 가능성이 있음

따라서 현재 판정은:

```text
NEEDS_MORE
```

BLOCK 전환 전 필요한 작업:

1. BOIL runtime 권위 source를 최종 exclusive target으로 정렬
2. BNTX를 v3 FAIL / BOIL PASS 또는 NOT_APPLICABLE로 기록
3. 18개 전수와 전체 frozen target shadow 대조
4. 후보·score·주문 불변 확인

이 정합성 수정과 재검증 전에는 `integrated_gate_enforcement=BLOCK` 전환을 보류해야 한다.

## 산출물

- `bntx_trace.md`
- `consistency_18.csv`
- `v3_block_impact.csv`
- `block_transition_decision.md`
- `readout.md`

운영 코드·설정 변경: 0건
