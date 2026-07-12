# Fitness train↔stress 일관성 페널티 2종목 파일럿

## 가장 먼저 볼 한 줄

# **Stress 평균 정밀도: 43.26% → 69.70% (+26.45%p)**

표본가중 Stress 정밀도도 40.74% → 60.42%로 상승했다.

```text
Stress gate: 0/6 → 2/6
OOS gate: 1/6 → 1/6
Survivor: 0/6 → 0/6
```

## 최종 판정

# **PENALTY_TRADEOFF**

질문의 직접 답은 **YES**다.

```text
train↔stress 정밀도 차이를 fitness에 넣자 stress gate 통과 개체가 2개 발생했다.
```

따라서 Stress 일관성은 GA가 학습할 수 있는 방향이었다. 그러나 λ=0.5에서는 Stress에 맞춘 선택이 독립 OOS 품질을 희생했다.

- Survivor는 여전히 0/6
- 6후보 OOS 평균 정밀도: 59.74% → 56.01%
- 선택 모델 pooled OOS 정밀도: 58.06% → 48.65%
- 선택 모델 pooled 평균수익: +2.0794% → -1.4695%
- 선택 모델 pooled 순차 복리: +146.15% → -37.60%
- MDD: -17.45% → -31.45%

Stress 방향은 유효하지만 λ=0.5는 현재 feature/label에서 과도했다.

## 실행 계약

- 종목: AAP, POWI
- 후보: 2종목 × train_1/2/3 = 6개
- 병렬 worker: 6개
- population: 100
- generation: 50
- patience: 15
- 각 후보 실제 실행: 50세대
- feature: 직전과 동일한 14개·4그룹
- threshold: G1/G2 2~3, G3/G4 2 고정
- rolling 목표일 청산
- early take profit OFF
- D-1 feature cutoff
- D0 gap·flow·orderbook 제외

실행 worker PID:

```text
609103, 609104, 609105, 609106, 609107, 609108
```

## Fitness 변경

```text
gap = max(0, train_precision - stress_precision)
adjusted_precision = train_precision - 0.5 * gap
```

기존 fitness의 train precision 항이 `train_precision × 220`이므로 다음과 같이 적용했다.

```text
fitness = original_fitness - 220 × 0.5 × gap
```

Stress는 다음에 사용하지 않았다.

- feature domain
- G3 percentile floor
- upper-bound fallback 성공 표본
- train 최소표본
- interval·threshold 초기화
- crossover·mutation

같은 train gene을 Stress에 적용한 정밀도를 fitness 감점에만 사용했다.

## Stress 수치 해석

단순 6후보 평균:

| 방식 | Stress 평균 정밀도 | 변화 |
|---|---:|---:|
| Floored hybrid | 43.26% | 기준 |
| Fitness penalty λ=0.5 | **69.70%** | **+26.45%p** |

한 후보인 AAP train_3가 Stress 4건에서 100%였기 때문에 단순 평균이 일부 상승했다. 이를 보정한 값도 개선됐다.

| 계산 방식 | Floored | Penalty | 변화 |
|---|---:|---:|---:|
| Stress 신호 표본가중 정밀도 | 40.74% | **60.42%** | +19.68%p |
| Stress 최소표본 통과 후보 평균 | 39.91% | **63.64%** | +23.74%p |
| 최소표본 통과 후보 수 | 5 | 5 | 동일 |

따라서 상승은 4건짜리 후보 하나만의 착시가 아니다.

## 후보별 게이트

| 종목 | split | Train 정밀도/표본 | Stress 정밀도/표본 | OOS 정밀도/표본 | Stress gate | OOS gate | Survivor |
|---|---|---:|---:|---:|---|---|---|
| AAP | train_1 | 70.00% / 20 | 47.73% / 44 | 60.87% / 23 | FAIL | PASS | NO |
| AAP | train_2 | 90.48% / 21 | **76.92% / 13** | 52.17% / 23 | **PASS** | FAIL | NO |
| AAP | train_3 | 95.00% / 20 | 100.00% / 4 | 64.71% / 17 | 표본 FAIL | FAIL | NO |
| POWI | train_1 | 90.48% / 21 | 75.00% / 20 | 57.14% / 21 | FAIL, 0.48%p 부족 | FAIL | NO |
| POWI | train_2 | 75.00% / 20 | **64.29% / 28** | 42.86% / 14 | **PASS** | FAIL | NO |
| POWI | train_3 | 90.00% / 20 | 54.29% / 35 | 58.33% / 36 | FAIL | FAIL | NO |

Stress gate를 통과한 모델은 두 개다.

```text
AAP train_2
POWI train_2
```

두 모델 모두 OOS precision floor를 통과하지 못해 survivor가 되지 못했다.

### AAP train_2

```text
Train precision: 90.48%
Stress precision: 76.92%
Gap: 13.55%p
Stress gate: PASS
OOS precision: 52.17%
OOS required floor: 75.48%
OOS gate: FAIL
```

### POWI train_2

```text
Train precision: 75.00%
Stress precision: 64.29%
Gap: 10.71%p
Stress gate: PASS
OOS precision: 42.86%
OOS required floor: 60.00%
OOS gate: FAIL
```

## 4-way 비교

| 방식 | OOS coverage | 선택 OOS 정밀도 | Stress 평균 | Stress 표본가중 | Stress gate | Survivor | 평균수익/거래 | 순차 복리 | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Strict-AND 12개 | 4.96% | 60.00% | 40.99% | 43.75% | 2/6 | 2/6 | +0.1446% | +0.8181% | -12.81% |
| Hybrid 몰빵 허용 | 6.94% | 57.14% | 43.28% | 42.39% | 0/6 | 0/6 | +0.0203% | -1.3120% | -19.29% |
| Hybrid 몰빵 제한 | 18.45% | 58.06% | 43.26% | 40.74% | 0/6 | 0/6 | +2.0794% | +146.1539% | -17.45% |
| Hybrid + λ=0.5 | 7.34% | **48.65%** | **69.70%** | **60.42%** | **2/6** | **0/6** | **-1.4695%** | **-37.5994%** | **-31.45%** |

거래 지표는 각 방식의 선택 모델을 이용한 diagnostic 결과다. Survivor 0인 방식의 거래 성과는 배포 가능한 성과가 아니다.

## OOS 희생 내용

Penalty 모델의 선택 거래:

| 종목 | 거래 | 평균수익 | 복리 | MDD | 승률 | +3% 도달률 |
|---|---:|---:|---:|---:|---:|---:|
| AAP | 20 | -1.4318% | -30.0377% | -31.45% | 55.00% | 45.00% |
| POWI | 7 | -1.5773% | -10.8083% | -10.23% | 42.86% | 28.57% |
| Pooled | 27 | -1.4695% | -37.5994% | -31.45% | 51.85% | 40.74% |

AAP의 주요 손실:

```text
2025-07-24 → 2025-07-28: -17.96%
2025-10-08 → 2025-10-10: -13.78%
2025-10-30 → 2025-11-03: -20.30%
```

POWI의 주요 손실:

```text
2025-07-25 → 2025-08-05: 보유 7세션, -8.05%
```

이번 OOS 악화는 연장 보유만의 문제가 아니다. AAP의 큰 손실 세 건은 2세션 또는 3세션 청산으로도 발생했다. Stress consistency를 높이는 gene이 OOS의 다른 가격 regime을 놓친 것이 더 직접적인 원인이다.

## 판정 이유

### PENALTY_WORKS에 해당하는 증거

- Stress 평균 43.26% → 69.70%
- Stress 표본가중 40.74% → 60.42%
- Stress gate 0/6 → 2/6

즉 일반화 일관성을 fitness가 움직일 수 있다는 것은 확인됐다.

### PENALTY_TRADEOFF로 낮춘 이유

- Survivor 0/6
- OOS 평균 정밀도 -3.73%p
- 선택 모델 OOS 정밀도 -9.42%p
- 선택 거래 복리와 MDD 대폭 악화
- Stress가 fitness에 사용됐으므로 Stress 상승 자체는 독립 검증 결과가 아님

OOS가 독립 확인 구간이며, OOS에서 희생이 확인됐기 때문에 λ=0.5를 그대로 확대 적용할 수 없다.

## 다음 단계 제언

방향 자체는 폐기하지 않는다. 다음 단일 변경 실험은 **[추정]**상 λ를 0.2~0.3으로 낮춘 비교가 적절하다.

```text
목표:
Stress 평균을 50% 이상 유지
AND Stress gate ≥1
AND 선택 OOS 정밀도 하락을 3%p 이내로 제한
```

λ를 낮춰도 Stress가 다시 43%대로 회귀한다면, 이 feature/label에서 consistency와 OOS 성능을 동시에 얻기 어렵다는 결론으로 타겟 재정의에 넘어가는 것이 타당하다.
