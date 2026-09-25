# 전 조건 BLOCK 통합 게이트 dry-run

- 기준일: 2026-07-11 KST
- 입력: Stage2 survivors 1,162개 + Stage3 final_rulebooks 15,909개
- 총 원본: **17,071개**
- 정책: `all-causal-conditions-block-dryrun-v1`
- 구현·주문·삭제·재학습·원본/라이브 파일 변경: **0건**

## 1. 판정 규칙

다음 다섯 조건 중 하나라도 걸리면 FAIL이다.

```text
미완성·profile 미검증
평균 PnL < 0
Stage2 승률 < 58.5274% / Stage3 승률 < 50%
HIGH_VOL + abs(weight_volume_surge)<=0.05
CE ratio<1.25 + Top2>=90%
```

CE 동적 자료가 없고 다른 실패가 없으면 `UNJUDGED`로 분리했다. CE까지 판정되고 모든 조건을 통과한 개체만 `PASS`다.

표본 P10(Stage2 35, Stage3 24)은 근거 수치로 기록했지만 이번 BLOCK 목록에 포함되지 않아 경고만 남겼다. 표본 부족까지 fail-closed한 민감도도 비교표에 포함했다.

## 2. 조건별 독립 차단 수

조건은 서로 중복될 수 있다.

| 조건 | Stage2 | Stage3 | 전체 |
|---|---:|---:|---:|
| 완성도 | 0 | 13,897 | **13,897** |
| 평균 PnL `<0` | 0 | 992 | **992** |
| 승률 미달 | 110 | 3,458 | **3,568** |
| BOIL형 | 2 | 442 | **444** |
| CE형 | 0 | 7 | **7** |

완성도 PASS 3,174개만 보면 평균 PnL 음수 0, 승률 미달 288, BOIL형 35, CE형 1개다. 이들의 합집합은 318개다. 평균 PnL 음수 992개는 모두 Stage3 profile 미검증 개체와 중복된다.

조건별 전체 목록은 `all_block_condition_hits.csv`, 개체별 모든 근거는 `all_block_candidate_decisions.csv`에 있다.

## 3. OR 최종 상태

| 상태 | Stage2 | Stage3 | 전체 |
|---|---:|---:|---:|
| `FAIL` | 112 | 14,103 | **14,215** |
| `UNJUDGED` | 1,048 | 1,803 | **2,851** |
| `PASS` | 2 | 3 | **5** |

주요 중복 사유:

- 완성도만: 10,081
- 완성도+승률: 2,491
- 완성도+평균 PnL+승률: 646
- 승률만: 282
- 완성도+평균 PnL: 265
- 완성도+BOIL: 209
- BOIL만: 29
- 승률+BOIL: 6
- CE만: 1

## 4. CE coverage

| CE 상태 | 원본 수 |
|---|---:|
| PASS | 27 |
| FAIL | 7 |
| `UNJUDGED` | **17,037** |

CE 판정 가능 원본은 34개다. 그중 전 조건 PASS 5개, FAIL 29개다.

CE형 7개:

```text
stage3:ANET:fe220620802b
stage3:BB:f1bdfe7f8ad9
stage3:BOIL:9044dc2c67a3
stage3:BTE:4ba9af200f79
stage3:CDE:ceb9fe0512dc
stage3:CE:998b0b638c66
stage3:CWK:2970595abcd4
```

상세 ratio·Top2·PnL·승률·가중치는 전수 판정표에 기록했다. CDE만 다른 조건 없이 CE형 단독 FAIL이다.

## 5. denylist→dedup 교정 적용 결과

선택 순서:

```text
BLOCK 판정 → elite filter/score → denylist → ticker dedup fallback → stage cap
```

### Coverage-aware 검수

FAIL만 제외하고 CE 미판정을 별도 라벨로 유지하면:

| 상태 | Stage2 | Stage3 | 합계 |
|---|---:|---:|---:|
| 확인 PASS | 2 | 2 | 4 |
| UNJUDGED | 11 | 78 | 89 |
| 전체 | **13** | **80** | **93** |

파일: `all_block_final_candidates_coverage_aware.csv`

이 93개는 확정 PASS 목록이 아니다. 89개는 CE 미판정이다. CW ticker에서는 점수가 더 높은 미판정 후보 `stage3:CW:811e0db9237f`가 선택돼 확인 PASS인 `stage3:CW:81ce9154b422`가 coverage-aware 목록에서 밀렸다.

### 판정 가능분만 기준

CE 판정 가능 34개 중 PASS 5, FAIL 29다. denylist→dedup 후 최종 후보는 **5개**다.

파일: `all_block_final_candidates_judged_only.csv`

### UNJUDGED fail-closed

UNJUDGED를 게시 불가로 처리해도 최종 후보는 동일하게 **5개**다.

파일: `all_block_final_candidates_fail_closed.csv`

판정 가능분 기준과 fail-closed 수가 같은 이유는 둘 다 CE 판정 PASS만 게시하기 때문이다.

## 6. 최종 확인 PASS 5개

| candidate_id | 표본 | 평균 PnL | 승률/임계 | vol | volume weight | CE ratio | Top2 |
|---|---:|---:|---:|---|---:|---:|---:|
| `stage2:ALGT:402f72d48c3c` | 44 | 2.3679% | 65.91/58.53 | MID | 2.0000 | 1.3283 | 100.00% |
| `stage2:CMC:4f6ee2739add` | 64 | 2.4713% | 85.94/58.53 | MID | 2.0000 | 2.9210 | 100.00% |
| `stage3:CRS:8695c9ce3320` | 55 | 2.5998% | 74.55/50.00 | MID | 1.2332 | 1.1620 | 76.98% |
| `stage3:BMA:0c978464f9dd` | 31 | 5.7620% | 64.52/50.00 | MID | 1.3782 | 6.7567 | 100.00% |
| `stage3:CW:81ce9154b422` | 38 | 1.7949% | 52.63/50.00 | NON_HIGH proxy | 1.2524 | 2.5122 | 92.22% |

CRS는 ratio가 1.25 미만이지만 Top2가 90% 미만이므로 CE AND 조건을 통과한다.

## 7. 시나리오 비교

| 시나리오 | 후보 | Stage2 | Stage3 | PASS | UNJUDGED |
|---|---:|---:|---:|---:|---:|
| 원본 | 17,071 | 1,162 | 15,909 | 5 | 2,851 |
| OR FAIL | 14,215 | 112 | 14,103 | 0 | 0 |
| OR UNJUDGED | 2,851 | 1,048 | 1,803 | 0 | 2,851 |
| 원본 확인 PASS | 5 | 2 | 3 | 5 | 0 |
| CE 판정 가능 원본 | 34 | 2 | 32 | 5 | 0 |
| Coverage-aware 랭킹 | 93 | 13 | 80 | 4 | 89 |
| 판정 가능분 랭킹 | 5 | 2 | 3 | 5 | 0 |
| UNJUDGED fail-closed | 5 | 2 | 3 | 5 | 0 |

표본 P10 미달까지 추가 fail-closed해도 최종 확인 PASS 5개는 모두 표본 기준을 충족해 결과가 같다.

## 8. 검수 결론

1. 전 조건 BLOCK과 CE fail-closed를 적용하면 최종 후보는 **5개**다.
2. 후보 0 위험은 없지만 기존 93개 대비 약 94.6% 감소한다.
3. 급감의 핵심은 CE coverage다. 알려진 FAIL이 없는 2,856개 중 2,851개가 CE `UNJUDGED`다.
4. 93개 coverage-aware 목록은 검수 대기 목록이지 확정 통과 목록이 아니다.
5. 실제 구현에서 CE 동적 checker가 현재 후보를 평가하기 전 fail-closed를 적용하면 5개만 게시된다.

## 9. 산출물

- `all_block_candidate_decisions.csv` — 17,071개 전수 판정
- `all_block_condition_hits.csv` — 조건별 차단 목록
- `all_block_condition_summary.csv`
- `all_block_final_candidates_coverage_aware.csv` — 93개, PASS+UNJUDGED
- `all_block_final_candidates_judged_only.csv` — 5개
- `all_block_final_candidates_fail_closed.csv` — 5개
- `all_block_scenario_summary.csv`
- `all_block_thresholds.json`
- `all_block_dryrun_summary.json`
- `run_all_block_gate_dryrun.py`
- `all_block_gate_dryrun_readout.md`

원본과 라이브 후보 파일은 불변이다.
