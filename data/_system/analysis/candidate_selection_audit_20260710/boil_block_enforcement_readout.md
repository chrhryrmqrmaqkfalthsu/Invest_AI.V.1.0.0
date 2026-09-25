# BOIL형 게이트 enforcement 결정 dry-run

- 결정: **BLOCK_JUSTIFIED**
- 운영 구현: `false`
- 원본·라이브·운영 코드·재학습·주문·삭제: 0건

## 1. 확정 조건

`HIGH_VOL AND 거래량 없이 진입 가능 AND abs(weight_volume_surge)<=0.05 AND v3 PASS`

v3가 이미 차단한 도달불가 조건은 제외하고 BOIL형 순수 구조적 무시만 순증 차단한다.

## 2. 순증 차단 규모

- 후보: **371개**
- exact-zero: 356개
- near-zero nonzero: 15개
- 고유 entry rule: 125개
- ticker: 18개
- Stage2/Stage3: 2/369

## 3. holdout 성과

| 그룹 | 후보 | 거래 | 평균 PnL | 승률 | 평균 MAE | 평균 MFE |
|---|---:|---:|---:|---:|---:|---:|
| BOIL형 v3 전용 | 371 | 6,769 | 0.4006% | 47.39% | -7.6930% | 10.3650% |
| non-BOIL HIGH_VOL | 2,135 | 36,059 | 3.0484% | 53.64% | -7.8611% | 14.3284% |

고유 entry-rule PnL 차이 BOIL-minus-normal: **-2.7062%p**, 95% CI **[-3.4699, -1.9337]**.

기존 frozen live93 재확인:
- 8 vs 23 CI: [-4.5214, -0.8894]
- v3 이후 5 vs 20 CI: [-5.9438, -1.7373]
- 기존 평균 PnL 1.2258% vs 3.9136% 방향과 0 배제 결과를 재확인했다.

exact-zero와 near-zero nonzero를 분리해도 둘 다 정상군 대비 PnL CI가 0 아래다.

## 4. 과잉 차단 위험

- 절대 양호: 129/371 (34.77%)
- 정상군 stage 중앙값 이상 PnL·승률: 61/371 (16.44%)
- 상대 양호 고유 entry rule: 25개

양호 예외는 존재하지만 holdout을 본 사후 분류이며, 누수 없이 예외만 분리할 정적 특징은 확인되지 않았다.

## 5. 최종 후보

- v3만: 85개 — Stage2 10, Stage3 75
- v3+BOIL BLOCK: **84개** — Stage2 10, Stage3 74
- 기존 탈락 1, fallback 신규 0

현재 85개 중 `stage3:CVNA:2f6d067a7826` 1개가 탈락하며 대체 후보가 없어 84개가 남는다.

## 6. 판정

**BLOCK_JUSTIFIED**

성과 열위와 bootstrap CI 0 배제, 84개 실용 후보 유지 조건을 모두 충족한다.
일부 양호 사례가 있으나 exact-zero와 near-zero 모두 cohort 수준에서 열위이며 비누수 예외조건이 없어 일부 하위조건만 BLOCK할 근거는 없다.

설계 enforcement만 BLOCK으로 확정하며 운영 구현은 false다.

## 7. 산출물

- `boil_block_exclusive_targets.csv`
- `boil_block_overfilter_good_cases.csv`
- `boil_block_performance_comparison.csv`
- `boil_block_bootstrap_summary.csv`
- `boil_block_final_candidates.csv`
- `boil_block_final_candidate_summary.csv`
- `boil_block_enforcement_decision.json`
