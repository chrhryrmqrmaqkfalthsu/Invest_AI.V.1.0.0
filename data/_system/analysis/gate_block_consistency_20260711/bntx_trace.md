# BNTX frozen BOIL vs 최신 v3 불일치 추적

## 생성 순서

1. `f7a1518` — 2026-07-10 16:08:26 UTC
   - `integrated_gate_candidate_dryrun.csv` 생성
   - BNTX: `check_boil=FAIL`
   - 당시 통합 dry-run은 BOIL을 독립 static fail로 기록

2. `ce121c3` — 2026-07-10 17:32:46 UTC
   - 최신 weightless-v3 catalog 생성
   - 정책: `integrated-gate-v3-p99-reachability-block-weightless`
   - BNTX: `final_p99_weightless_block_status=FAIL`
   - 사유: `INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99`
   - `volume_weight=0.0`, `volume_reachability_label=NEAR_UNREACHABLE`

3. `4cbc5e8` — 2026-07-10 18:06:36 UTC
   - 최종 BOIL enforcement 결정
   - BOIL 조건을 `HIGH_VOL AND 거래량 없이 진입 가능 AND abs(weight_volume_surge)<=0.05 AND v3 PASS`로 확정
   - `v3_overlap=EXCLUDED`
   - `boil_block_exclusive_targets.csv` 생성
   - BNTX는 이 최종 BOIL exclusive target에 없음

## 입력·기준 차이

초기 integrated dry-run의 BNTX:

```text
vol_group=HIGH_VOL
weight_volume_surge=0.0
check_boil=FAIL
```

최신 v3:

```text
volume threshold=2.5
training p99보다 큼
volume_weight=0.0
INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99
v3 FAIL
```

최종 BOIL 정책:

```text
v3 PASS 후보만 BOIL 검사
v3가 이미 차단한 후보는 BOIL 대상에서 제외
```

따라서 두 자료는 같은 시점의 동일 정책 결과가 아니다. 초기 integrated BOIL row가 먼저 생성됐고, 이후 v3가 weight=0 threshold까지 검사하도록 확장되면서 BNTX를 v3가 신규 포섭했다. 마지막 BOIL enforcement는 v3 overlap을 제외한 371개만 BOIL 전용 대상으로 확정했다.

## 권위 판정

현재 BLOCK 전환의 권위 소스는 다음 조합이어야 한다.

- v3: `threshold_p99_weightless_block_candidate_decisions.csv`
- BOIL: `boil_block_exclusive_targets.csv` 또는 동일 조건을 반영한 최종 BOIL enforcement decision

초기 `integrated_gate_candidate_dryrun.csv::check_boil`은 현재 최종 BOIL 권위값으로 사용하면 안 된다.

BNTX의 현재 권위 판정:

```text
v3=FAIL
BOIL=PASS/NOT_APPLICABLE (v3 overlap excluded)
overall static gate=FAIL
```

즉 BNTX의 최종 차단 여부는 변하지 않지만 차단 사유는 `BOIL`이 아니라 `v3`다.
