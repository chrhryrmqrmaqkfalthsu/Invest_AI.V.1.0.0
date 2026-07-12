# Step 0 판정 — 2일 내 +3% selector 검증 이력

- 조사 대상: `최근 5일 상태 → 2거래일 내 +3% 상승 예상 → candidate_pool 편입`
- 조사 범위: 현재 저장소, reachable Git history, 기존 analysis 산출물, range/payoff predictor 계보, S1/S2/S3 variant 계보
- 판정: **NO_VERIFICATION_FOUND**
- 분기 결과: **Step 1~3 중단, shadow 구현 보류**

## 결론

정확한 selector에 대한 OOS·hold-out·backtest 검증 통과 기록은 발견되지 않았다.

다음 세 조건이 동시에 충족된 구현·검증 artifact가 없다.

1. 입력: 신호 직전 최소 5거래일 상태
2. label: 이후 2거래일 안에 +3% 도달
3. 사용 목적: 진입 전 candidate_pool pass/fail

따라서 `VERIFIED_PASS`로 판정할 수 없으며, 사용자가 지정한 gate 규칙에 따라 shadow hook 구현과 소급 시뮬레이션을 진행하지 않았다.

## 검증이 수행됐는가

### 정확한 selector

**수행 기록 없음.**

발견되지 않은 항목:

- 2거래일 +3% label 생성 코드
- 해당 label을 사용한 model 또는 GA individual
- 개체별 또는 pooled threshold artifact
- survivor/final-survivor 결과
- OOS/hold-out pass row
- candidate_pool 적용 전후 비교
- live/shadow inference 로그

판정: `NO_VERIFICATION_FOUND`.

### 혼동 가능한 OOS 기록

`data/_system/analysis/exit_variants_20260707/readout.md`에는 `S3_target_3pct` OOS가 존재한다. 그러나 이는 selector 검증이 아니다.

실제 동작:

1. 진입일 또는 다음 bar에서 +2% 도달 여부 확인
2. +2% 도달 시, 기존 S2 청산일까지 +3% exit target 탐색
3. 미달 시 S2 청산으로 fallback

즉 최근 5일 상태를 사용하지 않고, 미래 상승을 예측하지 않으며, candidate_pool을 거르지 않는다.

이 exit variant의 OOS K=20 성능은 S2보다 나빴다.

| 항목 | S2 no-TP | S3 target +3% |
|---|---:|---:|
| CAGR | 80.2297% | 44.1230% |
| MDD | -21.2911% | -25.2302% |
| Sharpe | 1.63396 | 1.25086 |

따라서 이 기록은 `VERIFIED_FAIL_FOR_EXIT_VARIANT`로 분류했으며, 정확한 selector의 `VERIFIED_PASS` 근거로 사용할 수 없다.

## payoff predictor 검증과 대조

가장 가까운 predictive 계보는 다음과 같다.

- 최근 5일 feature + 다음 날 +2% high event: 일반 gate에서 final survivor 17개
- precision 강화 다음 날 +2%: final survivor 0개
- TP +2% / SL -1%: final survivor 0개
- TP-only +2%: final survivor 0개
- 5일 lag + next-day ATR payoff final audit: 검증 코드 존재, 결과 row·pass count `NOT_STORED`

따라서 기존 `payoff_predictor_status_20260712`의 “검증 불완전·재작업 필요” 결론과 정합한다.

## target 변형 이력과 대조

`target_transformation_timeline.csv`의 계보는 다음과 같다.

- 최초 predictor부터 horizon은 `next_day`
- 다음 날 +2% event
- TP/SL +2%/-1%
- TP-only +2%
- large range
- ATR payoff

정확한 2거래일 +3% predictor 구현 시점은 `NOT_FOUND`다. 따라서 “2일3%를 검증한 뒤 next-day로 변경”한 증거도 없다.

확정 가능한 설명은 다음이다.

> 저장된 predictor 계보는 처음부터 next-day로 시작했으며, exact 2-day +3% selector의 구현·검증 artifact는 남아 있지 않다.

2일 설계가 왜 구현에서 빠졌는지에 대한 결정 문서는 `NOT_FOUND`다.

## Step 0 최종 분기

- `VERIFIED_PASS`: 아님
- `VERIFIED_FAIL`: 정확한 selector에는 적용 불가
- `NO_VERIFICATION_FOUND`: **해당**

따라서 사용자 지시의 분기 규칙에 따라 다음을 수행하지 않았다.

- `selector_spec.md` 작성
- elite shadow report hook 추가
- live 또는 shadow 코드 변경
- 현재 후보 10개 소급 pass/fail 계산
- CRS selector pass/fail 추정
- pool 축소 시뮬레이션

CRS의 exact selector 결과도 모델·threshold가 없으므로 `NOT_APPLICABLE / UNRECOVERABLE`이다.

## 다음 진행 전 필요한 검증

shadow 구현 전 최소 요구사항은 다음과 같다.

- exact label 고정: 신호 시점 가격 대비 이후 두 거래일의 최고가가 +3% 이상
- 판단 시점 feature 고정: D-5~D-1 완료 일봉만 사용
- model 구조와 threshold 고정
- frozen train/OOS 분리
- 기존 `final_score >= signal_threshold` 대비 incremental lift
- survivor 수, precision, recall, signal coverage, CAGR, MDD, Sharpe 기록
- q<45 사례처럼 좋은 후보 동반 탈락 분석

이 검증이 먼저 수행되고 `VERIFIED_PASS`가 확인된 뒤에만 shadow hook을 구현해야 한다.
