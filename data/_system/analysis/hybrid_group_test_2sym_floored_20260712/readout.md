# AAP·POWI 4그룹 하이브리드 + 몰빵 제한 재테스트

## 최종 판정

# **SIMILAR**

몰빵 제한은 코드와 실제 학습 결과에서 완전히 작동했다. 그러나 핵심 목표였던 stress·OOS 일반화는 살아나지 않았다.

```text
Train gate: 6/6
Stress gate: 0/6
OOS gate: 1/6
Survivor: 0/6
```

비-survivor 선택 모델의 OOS 거래 성과는 직전 unfloored hybrid보다 크게 좋아졌지만, 검증을 통과한 모델이 아니므로 전체 판정을 `FLOORED_BETTER`로 올릴 수 없다.

정리하면:

```text
threshold mechanics: 개선
진단용 OOS 거래: 개선
stress robustness: 미개선
survivor: 미개선
전체 판정: SIMILAR
```

## 실행 조건

- 종목: AAP, POWI
- 명시 그룹 기준 feature: 14개
- D-1 completed bar까지만 사용
- D0 gap·flow·orderbook 제외
- label: D0 open 대비 D+1~D+2 high 최대 +3%
- population: 100
- generation: 50
- patience: 15
- split: train_1, train_2, train_3
- 후보: 6개
- rolling 목표일 청산, TP OFF
- 왕복비용: 10bp
- 실행시간: 11.12초

## 체크포인트 1 — 몰빵이 사라졌는가

**YES.**

| 종목 | split | G1 | G2 | G3 | G4 |
|---|---|---:|---:|---:|---:|
| AAP | train_1 | 3/4 | 3/4 | 2/3 | 2/3 |
| AAP | train_2 | 2/4 | 3/4 | 2/3 | 2/3 |
| AAP | train_3 | 2/4 | 2/4 | 2/3 | 2/3 |
| POWI | train_1 | 3/4 | 2/4 | 2/3 | 2/3 |
| POWI | train_2 | 2/4 | 3/4 | 2/3 | 2/3 |
| POWI | train_3 | 3/4 | 2/4 | 2/3 | 2/3 |

직전 문제였던 다음 형태는 0건이다.

```text
1/N으로 그룹 무력화
4/4 또는 3/3으로 한 그룹 전부 요구
```

검증:

- `group_threshold_check.csv` 72행 전부 `[2, size-1]` 통과
- `training_log.csv` 300세대 행 전부 floor/cap 통과
- full-group threshold 0건

## 체크포인트 2 — Survivor가 생겼는가

**NO. 여전히 0/6이다.**

| 종목 | split | Train 정밀도/표본 | Stress 정밀도/표본 | OOS 정밀도/표본 | 결과 |
|---|---|---:|---:|---:|---|
| AAP | train_1 | 61.90% / 21 | 38.30% / 47 | 59.46% / 37 | Stress 실패 |
| AAP | train_2 | 91.67% / 24 | 44.44% / 9 | 61.82% / 55 | Stress·OOS gap 실패 |
| AAP | train_3 | 86.36% / 22 | 60.00% / 5 | 65.31% / 49 | Stress 얇음·gap 실패 |
| POWI | train_1 | 100.00% / 27 | 42.47% / 73 | 52.63% / 38 | Stress·OOS gap 실패 |
| POWI | train_2 | 85.71% / 21 | 29.17% / 24 | 64.71% / 17 | Stress·OOS gap 실패 |
| POWI | train_3 | 90.48% / 21 | 45.16% / 31 | 54.55% / 22 | Stress·OOS gap 실패 |

Floored 전체 후보 평균 정밀도:

| Regime | Unfloored 평균 | Floored 평균 |
|---|---:|---:|
| Train | 87.11% | 86.02% |
| Stress | 43.28% | 43.26% |
| OOS | 50.33% | 59.74% |

OOS 평균은 개선됐지만 Stress는 사실상 변하지 않았다. 한 그룹 몰빵만이 과적합 원인의 전부가 아니었다.

## 선택 모델

Survivor가 없으므로 거래는 각 종목의 train-fitness 최고 비-survivor 모델을 사용한 진단 결과다.

### AAP

```text
split: train_2
threshold: G1 2/4, G2 3/4, G3 2/3, G4 2/3
Train precision: 91.67%
Stress precision: 44.44%
OOS precision: 61.82%
OOS signal: 55일
```

### POWI

```text
split: train_1
threshold: G1 3/4, G2 2/4, G3 2/3, G4 2/3
Train precision: 100.00%
Stress precision: 42.47%
OOS precision: 52.63%
OOS signal: 38일
```

Threshold는 균형적이지만 train precision이 지나치게 높아 validation precision floor도 각각 76.67%, 85.00%까지 상승했다. Stress/OOS가 이를 재현하지 못했다.

## 3자 비교

### 2종목 pooled

| 방식 | OOS 신호 | Coverage | 정밀도 | 거래 | 평균수익 | 순차 복리 | MDD | 승률 | Survivor |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 기존 12개 strict-AND | 25 | 4.96% | 60.00% | 19 | +0.1446% | +0.8181% | -12.81% | 63.16% | 2/6 |
| 직전 hybrid, 몰빵 허용 | 35 | 6.94% | 57.14% | 18 | +0.0203% | -1.3120% | -19.29% | 61.11% | 0/6 |
| 이번 hybrid, 몰빵 제한 | 93 | **18.45%** | 58.06% | 48 | **+2.0794%** | **+146.1539%** | -17.45% | 64.58% | **0/6** |

이번 floored 방식은 직전 hybrid보다:

- Coverage: 6.94% → 18.45%
- OOS 정밀도: 57.14% → 58.06%
- 평균수익: +0.0203% → +2.0794%
- 순차 복리: -1.31% → +146.15%
- MDD: -19.29% → -17.45%

으로 진단 거래 성과가 개선됐다.

하지만 +146.15%는 다음 한계가 있다.

1. 두 모델 모두 survivor가 아니다.
2. AAP 거래와 POWI 거래를 차례로 이어 붙인 단순 순차 복리다.
3. 실제 시간순 포트폴리오 수익률·CAGR이 아니다.
4. 같은 OOS에서 선택 모델을 관찰한 diagnostic 결과다.

따라서 배포 가능 성과로 해석하지 않는다.

## 종목별 floored 거래 결과

| 종목 | OOS 신호 | 거래 | 평균수익 | 복리 | MDD | 승률 | +3% 도달률 | 평균/최장 보유 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AAP | 55 | 25 | +2.5023% | +76.1962% | -15.80% | 72.00% | 72.00% | 3.68 / 8 |
| POWI | 38 | 23 | +1.6196% | +39.7045% | -17.45% | 56.52% | 60.87% | 2.96 / 6 |

AAP와 POWI 모두 신호 수가 크게 늘었다. Interval gene이 threshold 균형 제약을 보상하도록 더 넓거나 다른 구간으로 이동한 결과다.

## 큰 손실과 연장 보유

| 방식 | 연장 거래 손실 | -5% 이하 연장 손실 | 최악 연장 손실 | 총 연장 횟수 |
|---|---:|---:|---:|---:|
| strict-AND | 1 | 1 | -5.80% | 6 |
| unfloored hybrid | 4 | 1 | -11.30% | 17 |
| floored hybrid | **9** | **5** | **-15.01%** | **45** |

큰 손실 문제는 사라지지 않았다. 오히려 coverage가 확대되면서 연장 거래와 연장 손실의 절대 건수가 증가했다.

대표 손실:

### AAP

```text
2025-10-24 → 2025-11-03
보유 6세션, 연장 2회
순수익 -15.01%
진입 시 +3%는 장중 달성했지만 TP OFF라 계속 보유
```

```text
2026-02-25 → 2026-03-06
보유 7세션, 연장 3회
순수익 -5.33%
```

### POWI

```text
2025-07-29 → 2025-08-04
보유 4세션, 연장 1회
순수익 -6.86%
```

```text
2025-10-01 → 2025-10-09
보유 6세션, 연장 2회
순수익 -5.88%
```

따라서 threshold 균형과 별개로 rolling 연장 청산이 tail loss를 만들 수 있다는 사실은 유지된다.

## BOIL·CE 상쇄 차단

| 종목 | Global-count 통과 | Floored hybrid 통과 | 상쇄 차단 | BOIL형 | CE형 |
|---|---:|---:|---:|---:|---:|
| AAP | 116 | 55 | 61 | 20 | 42 |
| POWI | 111 | 38 | 73 | 21 | 69 |
| 합계 | 227 | 93 | **134** | **41** | **111** |

그룹 간 AND는 여전히 총점 상쇄를 차단했다. Floored 구조는 직전보다 모든 그룹을 실제로 사용했으므로 형식적 AND 문제는 해소됐다.

## 왜 최종 판정이 SIMILAR인가

### FLOORED_BETTER 요소

- 1/N·N/N threshold 몰빵 완전 제거
- OOS 평균 precision 개선
- Coverage 확대
- 비-survivor 진단 거래 P&L 개선
- 직전보다 MDD 소폭 개선

### 개선되지 않은 핵심

- Stress gate 0/6
- Survivor 0/6
- Train→Stress precision 붕괴 유지
- 선택 모델 둘 다 비-survivor
- 연장 보유 tail loss 증가

이 테스트의 목적은 단순 수익 상승뿐 아니라 몰빵 과적합 차단 후 stress·OOS generalization이 살아나는지 확인하는 것이었다. 그 핵심 목표는 달성되지 않았다.

## 결론

몰빵 제한은 유지할 가치가 있다. 그러나 이것만으로 5일+확장 feature의 2일3% 예측을 robust하게 만들지는 못했다.

다음 재시험에서는 threshold floor를 유지하면서 다음 중 하나를 별도 변경해야 한다는 것이 **[추정]**이다.

```text
1. train precision에 과도하게 연동된 validation floor 완화가 아니라,
   train split 간 안정성을 fitness/선택에 직접 반영
2. rolling 연장 횟수 또는 최대 보유세션 제한 ablation
3. G4 volume을 hard AND가 아닌 veto/confirmation으로 비교
```

이번 범위에서는 추가 학습하지 않았다.
