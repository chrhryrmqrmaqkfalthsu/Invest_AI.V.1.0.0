# v3·BOIL 검증 근거 판정

## v3

대상:

```text
one_sided_threshold_p99_reachability_weightless
policy=integrated-gate-v3-p99-reachability-block-weightless
```

근거 파일:

- `threshold_p99_weightless_block_readout.md`
- `threshold_p99_weightless_block_candidate_decisions.csv`
- `threshold_p99_weightless_block_fail_evidence.csv`
- `threshold_p99_weightless_block_summary.json`
- `integrated_gate_architecture.json`

검증된 내용:

- Stage2 1,162 + Stage3 15,909 = 17,071개 후보에 구조적 dry-run 적용
- 단방향 임계가 training-window p99/p01 또는 max/min 밖인지 판정
- v2 대비 weight=0 Volume 임계 524개를 추가 포섭
- 전체 v3 FAIL 4,491개
- BOIL 원형의 저장 Volume threshold 2.5가 training max 2.179716보다 커 UNREACHABLE로 포섭
- 최종 dry-run 후보 85개 유지

검증 성격:

```text
STRUCTURAL_REACHABILITY_DRY_RUN
```

이는 임계가 학습기간 분포에서 도달 가능한지 확인한 구조 검증이다. v3 BLOCK 자체의 독립 hold-out/OOS 수익성 개선을 직접 입증하는 별도 실험은 확인되지 않았다.

따라서 v3에 대해 “검증 완료”라고 부를 수 있는 범위는:

```text
도달불가/희소 임계 구조 검출과 후보 attrition dry-run 완료
```

까지다. 실제 라이브 성과 개선 검증 완료로 확대 해석하면 안 된다.

## BOIL

대상:

```text
high_vol_volume_blind_near_zero_v3_exclusive
```

근거 파일:

- `boil_block_enforcement_readout.md`
- `boil_block_enforcement_decision.json`
- `boil_block_performance_comparison.csv`
- `boil_block_bootstrap_summary.csv`
- `run_boil_enforcement_dryrun.py`

조건:

```text
HIGH_VOL
AND entry_possible_without_volume
AND abs(weight_volume_surge)<=0.05
AND v3 PASS
```

성과 근거:

```text
BOIL형 v3 전용 후보: 371
holdout trades: 6,769
후보 동일가중 평균 PnL: 0.4006%
승률: 47.39%

non-BOIL HIGH_VOL 후보: 2,135
holdout trades: 36,059
후보 동일가중 평균 PnL: 3.0484%
승률: 53.64%
```

고유 entry-rule PnL 차이:

```text
-2.7062%p
95% bootstrap CI [-3.4699, -1.9337]
```

추가 frozen live93 확인:

```text
prior live93 CI [-4.5214, -0.8894]
v3 survivor live93 CI [-5.9438, -1.7373]
```

데이터 경로는 `run_boil_enforcement_dryrun.py`에서 직접 확인된다.

- Stage2: `trades.jsonl`, `period_label=oos_2025h2`
- Stage3: `exit_trades.jsonl`, `period_label=recent_1y`
- frozen live93: `oos_reproduce_frozen_20260707/oos_trades_frozen.csv`, `split=OOS`

판정:

```text
BOIL_BLOCK_JUSTIFIED_BY_HOLDOUT_AND_FROZEN_OOS
```

다만 제한이 있다.

- Stage3 `recent_1y`는 architecture 문서에서 diagnostic validation으로 표현됨
- BOIL 조건과 enforcement 결정은 동일 holdout 결과를 보고 확정한 사후 정책 결정
- 일부 양호 예외 129/371 존재
- 운영 shadow/A-B 결과는 아직 없음

따라서 BOIL은 v3보다 강한 성과 근거가 있지만, 완전한 독립 prospective live 검증까지 끝난 상태는 아니다.

## 종합

| 항목 | 구조 검증 | hold-out/OOS 성과 검증 | prospective live 검증 |
|---|---|---|---|
| v3 | 있음 | 직접 근거 확인 안 됨 | 없음 |
| BOIL | 있음 | 있음 | 없음 |

로드맵의 “검증 완료”는 두 항목에 동일 의미로 적용할 수 없다.
