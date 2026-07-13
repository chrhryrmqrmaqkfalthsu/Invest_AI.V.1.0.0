# Stage 3 신호 연속성(N일 통과) 유효성 측정

## 최종 결론

`연속성 도입 근거 없음`

AAP·POWI의 strict-AND 일별 pass를 기준으로 다음 6개 조합을 비교했다.

```text
완전연속 N=2, 3, 5
다수결   N=2, 3, 5
baseline N=1
```

주 분석인 joint support 25 reference box와 support 민감도 12·25·50에서:

```text
USEFUL: 0건
```

주 분석 support 25의 pooled 판정:

| 조합 | 판정 |
|---|---|
| 완전연속 N=2 | TOO_SPARSE |
| 완전연속 N=3 | NO_GAIN |
| 완전연속 N=5 | NO_GAIN |
| 다수결 N=2 | TOO_SPARSE |
| 다수결 N=3 | OVERFIT |
| 다수결 N=5 | NO_GAIN |

가장 신호가 많이 남는 `다수결 N=3`도 train에서는 좋아 보였지만 OOS에서 무너졌다.

```text
pooled train lift: 1.016 → 1.201
pooled OOS lift:   1.000 → 0.588
pooled OOS 기대값: 2.451% → 1.027%
```

따라서 evaluator에 N일 연속성 조건을 추가하지 않고, 현행 **당일 1회 strict-AND pass(N=1)**를 유지하는 것이 적절하다.

---

## 중요한 방법론 제한

현재 저장된 산출물에는 재설계 schema v2의 learned interval rulebook 본문이 없다.

```text
AAP dry-run: 요약값만 저장, rulebook 본문 없음
무효 정식 실행: hash만 저장, rulebook 본문 없음
```

GA 실행은 금지되어 있으므로 특정 learned chromosome의 daily tape를 재현할 수 없었다. 임의 성과 최적화를 피하기 위해 다음 **비지도 deterministic reference box**를 사용했다.

```text
각 train fold의 D-5 feature 분포 중앙값 중심
현재 최소 interval 폭 규칙 준수
feature support >= 25
joint strict support 목표 >= 25
label을 전혀 보지 않고 가장 좁은 valid box 선택
OOS에는 train_3 box를 고정 적용
```

강건성 확인을 위해 joint support 목표를 12·25·50으로 바꿔 같은 측정을 반복했다. 세 수준 모두 USEFUL 조합이 0이었다.

이 결과는 특정 미래 GA 개체 하나의 성과 예측이 아니라, **현재 strict interval 구조에 연속성 필터를 추가할 일반적 근거가 있는지**를 보는 민감도 측정이다.

---

## 입력·기간

대상:

```text
AAP
POWI
```

기간:

```text
train_1: 2022-07-01 ~ 2023-06-30
train_2: 2023-07-01 ~ 2024-06-30
train_3: 2024-07-01 ~ 2025-06-30
OOS:     2025-07-01 ~ 2026-07-06
```

완전한 L2 horizon이 없는 마지막 3거래일은 라벨 계산에서 제외했다.

```text
train: 종목당 751일
OOS:   종목당 251일
```

OHLCV 입력:

```text
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
```

시장 데이터는 strict pass와 L2 라벨 계산에 직접 쓰이지 않지만, 지정된 root 단일 소스 SHA를 확인했다.

```text
market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

새 데이터 fetch는 사용하지 않았다.

---

## 신호·라벨 정의

### Strict pass

현재 evaluator와 동일한 5개 feature를 사용했다.

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

기술 feature 시점:

```text
신호일 D
→ feature는 D-5 거래일 단일값
```

벡터화된 `shift(5)` 결과와 `extract_entry_features()`를 AAP·POWI 각각 3개 날짜에서 대조했다.

```text
최대 절대차: 0.0
```

각 일별 pass는 `evaluate_entry_intervals()`로 계산했다. 시장 점수나 quality score는 strict pass를 뒤집지 않는다.

### L2 라벨

현재 실행 시점에 맞게 정의했다.

```text
D일 신호
D+1 open 진입
D+2 또는 D+3 high의 최대값
진입가 대비 +3% 이상이면 승(label=1)
```

수식:

```text
forward_max_return_pct
= max(High[D+2], High[D+3]) / Open[D+1] - 1

L2 = 1 if forward_max_return_pct >= 3%
```

### 연속성 조건

```text
baseline N=1: 당일 pass
완전연속 N: 최근 N거래일이 전부 pass
다수결 N:   최근 N거래일 중 과반 pass
```

`다수결 N=2`는 과반이 2/2이므로 `완전연속 N=2`와 수학적으로 동일하다.

Rolling window에는 당일 D의 pass와 직전 거래일 pass가 포함되며, 각 pass 자체는 D-5 feature만 본다. 당일 가격이나 미래 데이터는 신호 조건에 포함되지 않는다.

---

## Reference box

주 분석 joint support 목표 25:

| 종목 | fold | 중앙 quantile mass | 실제 joint support |
|---|---|---:|---:|
| AAP | train_1 | 0.50 | 25 |
| AAP | train_2 | 0.58 | 26 |
| AAP | train_3 | 0.50 | 25 |
| POWI | train_1 | 0.56 | 25 |
| POWI | train_2 | 0.56 | 26 |
| POWI | train_3 | 0.52 | 25 |

각 box는 다음 검증을 통과했다.

```text
편측 interval 없음
NaN/Inf 없음
hard domain 위반 없음
q01~q99 domain과 겹침
최소 width 충족
feature support >= 25
joint support >= 25
near-full 제한 충족
```

---

# 측정 결과 1 — 신호와 L2 관계

## AAP

| 구간 | 조합 | 신호 수 | 승/패 | 승률 | lift | 기대값 % | MI bits | baseline 잔존율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| train | baseline N=1 | 76 | 30/46 | 39.47% | 1.033 | 2.395 | 0.0001 | 100.0% |
| train | 완전 N=2 | 33 | 17/16 | 51.52% | 1.348 | 3.120 | 0.0024 | 43.4% |
| train | 완전 N=3 | 16 | 7/9 | 43.75% | 1.145 | 2.405 | 0.0002 | 21.1% |
| train | 완전 N=5 | 6 | 3/3 | 50.00% | 1.308 | 2.038 | 0.0003 | 7.9% |
| train | 다수결 N=2 | 33 | 17/16 | 51.52% | 1.348 | 3.120 | 0.0024 | 43.4% |
| train | 다수결 N=3 | 63 | 29/34 | 46.03% | 1.205 | 2.722 | 0.0017 | 82.9% |
| train | 다수결 N=5 | 56 | 22/34 | 39.29% | 1.028 | 1.804 | 0.0000 | 73.7% |
| OOS | baseline N=1 | 22 | 12/10 | 54.55% | 1.061 | 2.690 | 0.0003 | 100.0% |
| OOS | 완전 N=2 | 5 | 2/3 | 40.00% | 0.778 | -0.054 | 0.0008 | 22.7% |
| OOS | 완전 N=3 | 0 | 0/0 | — | — | — | 0.0000 | 0.0% |
| OOS | 완전 N=5 | 0 | 0/0 | — | — | — | 0.0000 | 0.0% |
| OOS | 다수결 N=2 | 5 | 2/3 | 40.00% | 0.778 | -0.054 | 0.0008 | 22.7% |
| OOS | 다수결 N=3 | 12 | 4/8 | 33.33% | 0.649 | 1.082 | 0.0048 | 54.5% |
| OOS | 다수결 N=5 | 6 | 3/3 | 50.00% | 0.973 | 3.381 | 0.0000 | 27.3% |

AAP 해석:

- 완전 N=2는 train에서는 lift와 기대값이 개선됐지만 OOS에서는 음의 기대값으로 전환됐다.
- 다수결 N=3은 거래 수는 12건 남았지만 OOS lift가 1.061에서 0.649로 악화됐다.
- 다수결 N=5는 OOS 기대값은 높지만 6건뿐이고 lift가 baseline보다 낮다.

## POWI

| 구간 | 조합 | 신호 수 | 승/패 | 승률 | lift | 기대값 % | MI bits | baseline 잔존율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| train | baseline N=1 | 76 | 29/47 | 38.16% | 0.998 | 2.508 | 0.0000 | 100.0% |
| train | 완전 N=2 | 31 | 12/19 | 38.71% | 1.013 | 2.696 | 0.0000 | 40.8% |
| train | 완전 N=3 | 12 | 4/8 | 33.33% | 0.872 | 1.570 | 0.0001 | 15.8% |
| train | 완전 N=5 | 0 | 0/0 | — | — | — | 0.0000 | 0.0% |
| train | 다수결 N=2 | 31 | 12/19 | 38.71% | 1.013 | 2.696 | 0.0000 | 40.8% |
| train | 다수결 N=3 | 59 | 27/32 | 45.76% | 1.198 | 3.125 | 0.0015 | 77.6% |
| train | 다수결 N=5 | 49 | 22/27 | 44.90% | 1.175 | 2.649 | 0.0009 | 64.5% |
| OOS | baseline N=1 | 6 | 2/4 | 33.33% | 0.686 | 1.577 | 0.0017 | 100.0% |
| OOS | 완전 N=2 | 3 | 1/2 | 33.33% | 0.686 | 0.277 | 0.0008 | 50.0% |
| OOS | 완전 N=3 | 2 | 0/2 | 0.00% | 0.000 | -2.218 | 0.0077 | 33.3% |
| OOS | 완전 N=5 | 0 | 0/0 | — | — | — | 0.0000 | 0.0% |
| OOS | 다수결 N=2 | 3 | 1/2 | 33.33% | 0.686 | 0.277 | 0.0008 | 50.0% |
| OOS | 다수결 N=3 | 5 | 1/4 | 20.00% | 0.412 | 0.896 | 0.0052 | 83.3% |
| OOS | 다수결 N=5 | 5 | 0/5 | 0.00% | 0.000 | -0.396 | 0.0194 | 83.3% |

POWI 해석:

- 다수결 N=3·5는 train에서는 개선처럼 보이지만 OOS에서 각각 1승 4패, 0승 5패다.
- 다수결 N=5의 MI가 0.0194로 높지만, 이는 신호가 모두 패배를 식별했기 때문이다. MI 크기만으로 방향성 있는 유용성을 판단하면 안 된다.
- 모든 조합이 baseline보다 OOS 기대값이 낮다.

## Pooled

| 구간 | 조합 | 신호 수 | 승/패 | 승률 | lift | 기대값 % | MI bits | baseline 잔존율 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| train | baseline N=1 | 152 | 59/93 | 38.82% | 1.016 | 2.452 | 0.0000 | 100.0% |
| train | 완전 N=2 | 64 | 29/35 | 45.31% | 1.186 | 2.914 | 0.0007 | 42.1% |
| train | 완전 N=3 | 28 | 11/17 | 39.29% | 1.028 | 2.047 | 0.0000 | 18.4% |
| train | 완전 N=5 | 6 | 3/3 | 50.00% | 1.308 | 2.038 | 0.0002 | 3.9% |
| train | 다수결 N=2 | 64 | 29/35 | 45.31% | 1.186 | 2.914 | 0.0007 | 42.1% |
| train | 다수결 N=3 | 122 | 56/66 | 45.90% | 1.201 | 2.917 | 0.0016 | 80.3% |
| train | 다수결 N=5 | 105 | 44/61 | 41.90% | 1.097 | 2.198 | 0.0003 | 69.1% |
| OOS | baseline N=1 | 28 | 14/14 | 50.00% | 1.000 | 2.451 | 0.0000 | 100.0% |
| OOS | 완전 N=2 | 8 | 3/5 | 37.50% | 0.750 | 0.070 | 0.0007 | 28.6% |
| OOS | 완전 N=3 | 2 | 0/2 | 0.00% | 0.000 | -2.218 | 0.0040 | 7.1% |
| OOS | 완전 N=5 | 0 | 0/0 | — | — | — | 0.0000 | 0.0% |
| OOS | 다수결 N=2 | 8 | 3/5 | 37.50% | 0.750 | 0.070 | 0.0007 | 28.6% |
| OOS | 다수결 N=3 | 17 | 5/12 | 29.41% | 0.588 | 1.027 | 0.0044 | 60.7% |
| OOS | 다수결 N=5 | 11 | 3/8 | 27.27% | 0.545 | 1.664 | 0.0035 | 39.3% |

---

# 측정 결과 2 — 거래 기회 잔존량

## Fold별 신호 수

| 종목 | fold | baseline | 완전 N2 | 완전 N3 | 완전 N5 | 다수 N2 | 다수 N3 | 다수 N5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| AAP | train_1 | 25 | 15 | 11 | 5 | 15 | 19 | 19 |
| AAP | train_2 | 26 | 7 | 2 | 0 | 7 | 19 | 15 |
| AAP | train_3 | 25 | 11 | 3 | 1 | 11 | 25 | 22 |
| POWI | train_1 | 25 | 8 | 3 | 0 | 8 | 17 | 15 |
| POWI | train_2 | 26 | 10 | 3 | 0 | 10 | 19 | 15 |
| POWI | train_3 | 25 | 13 | 6 | 0 | 13 | 23 | 19 |

## Train/OOS 잔존율

| 종목 | 조합 | train 신호 | train 잔존율 | OOS 신호 | OOS 잔존율 |
|---|---|---:|---:|---:|---:|
| AAP | 완전 N=2 | 33 | 43.4% | 5 | 22.7% |
| AAP | 완전 N=3 | 16 | 21.1% | 0 | 0.0% |
| AAP | 완전 N=5 | 6 | 7.9% | 0 | 0.0% |
| AAP | 다수결 N=2 | 33 | 43.4% | 5 | 22.7% |
| AAP | 다수결 N=3 | 63 | 82.9% | 12 | 54.5% |
| AAP | 다수결 N=5 | 56 | 73.7% | 6 | 27.3% |
| POWI | 완전 N=2 | 31 | 40.8% | 3 | 50.0% |
| POWI | 완전 N=3 | 12 | 15.8% | 2 | 33.3% |
| POWI | 완전 N=5 | 0 | 0.0% | 0 | 0.0% |
| POWI | 다수결 N=2 | 31 | 40.8% | 3 | 50.0% |
| POWI | 다수결 N=3 | 59 | 77.6% | 5 | 83.3% |
| POWI | 다수결 N=5 | 49 | 64.5% | 5 | 83.3% |
| pooled | 완전 N=2 | 64 | 42.1% | 8 | 28.6% |
| pooled | 완전 N=3 | 28 | 18.4% | 2 | 7.1% |
| pooled | 완전 N=5 | 6 | 3.9% | 0 | 0.0% |
| pooled | 다수결 N=2 | 64 | 42.1% | 8 | 28.6% |
| pooled | 다수결 N=3 | 122 | 80.3% | 17 | 60.7% |
| pooled | 다수결 N=5 | 105 | 69.1% | 11 | 39.3% |

완전연속 N=3·5는 거래 기회를 거의 제거한다. 다수결 N=3은 기회는 보존하지만 OOS 라벨 관계가 악화된다.

---

# 조합별 판정

판정 기준:

```text
라벨 관계 개선:
  lift >= baseline × 1.05
  기대값 >= baseline + 0.05%p

실용성:
  train 신호 >= 36
  OOS 신호 >= 12
  train/OOS 모두 baseline의 25% 이상 잔존
```

## 종목별

| 조합 | AAP | POWI |
|---|---|---|
| 완전연속 N=2 | TOO_SPARSE | NO_GAIN |
| 완전연속 N=3 | NO_GAIN | NO_GAIN |
| 완전연속 N=5 | NO_GAIN | NO_GAIN |
| 다수결 N=2 | TOO_SPARSE | NO_GAIN |
| 다수결 N=3 | OVERFIT | TOO_SPARSE |
| 다수결 N=5 | NO_GAIN | TOO_SPARSE |

## Pooled

| 조합 | train 개선 | OOS 개선 | 실용성 | 판정 |
|---|---|---|---|---|
| 완전연속 N=2 | 예 | 아니오 | 아니오 | TOO_SPARSE |
| 완전연속 N=3 | 아니오 | 아니오 | 아니오 | NO_GAIN |
| 완전연속 N=5 | 아니오 | 아니오 | 아니오 | NO_GAIN |
| 다수결 N=2 | 예 | 아니오 | 아니오 | TOO_SPARSE |
| 다수결 N=3 | 예 | 아니오 | 예 | OVERFIT |
| 다수결 N=5 | 아니오 | 아니오 | 아니오 | NO_GAIN |

---

# Support 폭 민감도

특정 reference box 폭에만 의존한 결론인지 확인하기 위해 joint support 목표를 바꿨다.

| joint support 목표 | USEFUL | OVERFIT | TOO_SPARSE | NO_GAIN | 비교 단위 |
|---:|---:|---:|---:|---:|---:|
| 12 | 0 | 0 | 9 | 9 | AAP·POWI·pooled × 6조합 |
| 25 | 0 | 2 | 6 | 10 | AAP·POWI·pooled × 6조합 |
| 50 | 0 | 2 | 7 | 9 | AAP·POWI·pooled × 6조합 |

세 폭 모두 `USEFUL=0`이다.

Support 50처럼 baseline 신호가 넓어져 OOS 표본이 충분한 경우에도:

- AAP의 모든 조합은 `NO_GAIN`.
- POWI는 일부 train 개선이 보이나 OOS가 무너져 `TOO_SPARSE`.
- pooled 완전 N=2·다수 N=2는 실용 표본이 남지만 OOS 악화로 `OVERFIT`.
- pooled 다수 N=3·5는 표본은 충분하나 개선이 없어 `NO_GAIN`.

따라서 “reference box가 너무 좁아서 연속성이 실패했다”는 설명으로 결과를 뒤집을 수 없다.

---

# 권장 결론

```text
권장 조합: 없음
연속성 evaluator 반영: 하지 않음
Stage 3 baseline: 당일 strict-AND N=1 유지
```

이유:

1. 완전연속은 N=2부터 OOS 거래 수를 크게 줄이고, N=3·5는 거의 0건으로 붕괴한다.
2. 다수결 N=3은 거래 기회는 보존하지만 AAP·POWI 모두 OOS lift와 기대값이 악화한다.
3. 다수결 N=5도 POWI OOS에서 0승 5패이며 pooled 개선이 없다.
4. support 목표 12·25·50 어디에서도 USEFUL 조합이 나오지 않았다.
5. train에서 보이는 개선은 OOS에서 재현되지 않아 연속성은 정보 추가보다 regime-specific clustering을 강화하는 경향이 있다.

종목별로도 결론이 갈리지 않는다.

```text
AAP: 일부 train 개선은 있으나 OOS 악화 또는 희소화
POWI: OOS에서 모든 연속성 조합이 baseline 이하
```

---

## 실행 제약 준수

```text
GA 실행: 0
백테스트 실행: 0
학습 실행: 0
외부 fetch: 0
코드 수정: 0
입력 데이터 수정: 0
시장 데이터 수정: 0
```

실행한 것은 지표 계산, D-5 strict interval 판정, rolling persistence 집계, L2 라벨 통계뿐이다.

Daemon PID 494330은 유지했다.

사전 백업:

```text
backup/pre_stage23_signal_persistence_20260713T103326Z.tar.gz
backup/pre_stage23_signal_persistence_20260713T103326Z.manifest.sha256
```
