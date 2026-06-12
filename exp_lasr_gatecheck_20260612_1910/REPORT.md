# LASR 다년 생존 0개 원인 점검 — min_trades 조건 재판정

기존 산출물만 사용했다.

- 22년산: `exp_lasr_multiyear_20260612_1845/period_metrics.csv`
- 25H2년산 역방향: `exp_lasr_reverse_20260612_1856/period_metrics.csv`
- GA/백테스트 신규 실행 없음

## Step 2 — 기준별 통과 수

### 2022-trained forward

| 기준 | general3 통과 | all4 통과 | general_pass_count 0/1/2/3 | stress 통과 |
|---|---:|---:|---|---:|
| 원본 strict_k3 | 0 | 0 | 31 / 65 / 4 / 0 | 91 |
| min_trades 제거 | 0 | 0 | 24 / 72 / 4 / 0 | 92 |
| min_trades ≥3 | 0 | 0 | 24 / 72 / 4 / 0 | 92 |
| trades/member 제거, exp only | 0 | 0 | 24 / 72 / 4 / 0 | 92 |

### 2025H2-trained reverse

| 기준 | general3 통과 | all4 통과 | general_pass_count 0/1/2/3 | stress 통과 |
|---|---:|---:|---|---:|
| 원본 strict_k3 | 0 | 0 | 15 / 77 / 8 / 0 | 91 |
| min_trades 제거 | 6 | 6 | 6 / 52 / 36 / 6 | 92 |
| min_trades ≥3 | 1 | 1 | 13 / 72 / 14 / 1 | 91 |
| trades/member 제거, exp only | 6 | 6 | 6 / 52 / 36 / 6 | 96 |

## Step 3 — 원본 strict_k3 탈락 사유 분해

중복 사유는 중복 집계했다. `only_trades`는 expectancy와 member_score는 통과했는데 trades 조건만 실패한 개체 수다.

### 2022-trained forward

| 구간 | 탈락 수 | exp 실패 | trades 실패 | member 실패 | only exp | only trades | only member |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 99 | 98 | 10 | 3 | 86 | 1 | 0 |
| 2023 | 32 | 27 | 11 | 3 | 19 | 5 | 0 |
| 2024 | 96 | 95 | 2 | 8 | 86 | 1 | 0 |
| 2025H2 | 9 | 8 | 1 | 8 | 0 | 1 | 0 |

판정: 22년산 실험은 min_trades가 주범이 아니다. min_trades와 member_score를 모두 제거해도 general3/all4가 0개다. 2022와 2024에서 expectancy 자체가 대부분 실패한다.

### 2025H2-trained reverse

| 구간 | 탈락 수 | exp 실패 | trades 실패 | member 실패 | only exp | only trades | only member |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 20 | 17 | 6 | 9 | 5 | 3 | 0 |
| 2023 | 91 | 75 | 90 | 1 | 1 | 16 | 0 |
| 2024 | 96 | 66 | 86 | 0 | 10 | 30 | 0 |
| 2025H2 | 9 | 4 | 1 | 8 | 0 | 1 | 4 |

판정: 25H2년산 역방향 실험에서는 min_trades가 큰 병목이다. 특히 2023/2024에서 expectancy는 양호하지만 trades<5라서만 탈락한 개체가 각각 16개, 30개다.

## min_trades 제거 시 all4가 되는 6개

| hash | strict all4 | trades≥3 all4 | no-trades all4 | 2022 exp/tr/member/dd | 2023 | 2024 | 2025H2 |
|---|---:|---:|---:|---|---|---|---|
| 0707c5f2 | False | False | True | 4.863/15/86.4/-10.5 | 4.017/1/86.2/0.0 | 10.208/2/84.8/0.0 | 4.433/19/59.6/-18.7 |
| 2820575b | False | True | True | 2.111/16/48.7/-7.0 | 3.247/4/84.8/0.0 | 7.846/3/80.6/0.0 | 2.948/23/42.8/-14.0 |
| 28291859 | False | False | True | 1.135/14/18.3/-27.4 | 2.203/1/81.3/0.0 | 6.792/2/77.7/0.0 | 1.890/19/17.2/-21.7 |
| 89908043 | False | False | True | 1.871/13/48.5/-7.0 | 4.017/1/86.9/0.0 | 10.894/2/90.5/0.0 | 1.681/18/14.0/-24.8 |
| cd2d26c4 | False | False | True | 3.190/17/84.2/-7.0 | 9.669/1/92.6/0.0 | 3.273/3/65.2/-8.4 | 2.078/16/23.9/-21.2 |
| de9eb672 | False | False | True | 1.759/17/34.6/-7.2 | 6.892/2/89.7/0.0 | 3.965/8/69.5/-3.6 | 1.996/20/22.8/-14.0 |

해석: 6개 모두 expectancy와 member_score는 4구간을 통과하지만, 2023 또는 2024의 거래 수가 1~4개라 원본 strict_k3에서는 탈락한다. 단, 거래 수 1~2개 통과는 표본 신뢰도가 낮으므로, 이 결과는 “min_trades가 병목”이라는 진단이지 “기준을 제거하자”는 결론은 아니다.

## Step 4 — 주범 판정

| 실험 | min_trades 제거 효과 | 주범 |
|---|---|---|
| 2022-trained forward | general3 0 → 0, all4 0 → 0 | C: expectancy 자체 / 시장 구간 차이 |
| 2025H2-trained reverse | general3 0 → 6, all4 0 → 6 | A: min_trades 조건이 직접 병목. 단, 일부는 trades 1~2라 표본 신뢰도 낮음 |

## 한 줄 결론

다년 생존 0개의 원인은 실험별로 다르다. 22년산은 min_trades 문제가 아니라 2022·2024 expectancy 자체가 깨지는 구조 문제다. 반면 25H2년산 역방향은 2023·2024에서 거래 수가 너무 적어 strict_k3에 막힌 것이 직접 원인이며, min_trades를 빼면 4구간 통과 개체가 6개 나온다.
