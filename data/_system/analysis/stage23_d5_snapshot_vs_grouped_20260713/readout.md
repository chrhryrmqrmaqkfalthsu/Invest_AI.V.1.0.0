# D-5 스냅샷 vs D-5~D-1 5일 묶기 안정성 측정

## 최종 요약

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
OOS recent_1y: 2025-07-01 ~ 2026-07-06
```

비교 방식:

```text
snapshot = raw_feature.shift(5)
grouped  = raw_feature.rolling(5, min_periods=5).mean().shift(1)
```

따라서 신호일 D 기준:

```text
snapshot: D-5 단일값
grouped:  D-5, D-4, D-3, D-2, D-1 평균
```

두 방식 모두 D를 포함하지 않으며, 앞쪽 5거래일은 NaN이다.

전체 지표 판정:

```text
SNAPSHOT_BETTER: 1
GROUPED_BETTER: 0
MIXED: 2
NO_DIFFERENCE: 2
```

핵심 결론:

```text
5일 묶기를 전 지표에 일괄 적용할 근거 없음
현행 D-5 snapshot 유지가 합리적
bb_position은 snapshot 우위
volume_ratio는 grouped가 fold 안정성은 높지만 OOS 유사성이 크게 악화
ma_trend는 두 방식 모두 train/OOS 괴리가 커 제외 검토 1순위
```

## 입력 및 무결성

OHLCV 입력:

```text
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
```

SHA-256:

```text
AAP_ohlcv.csv
6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717

POWI_ohlcv.csv
bd683b376899ff9784eb86a807bd154f74502c8f0bb025ceb120b3e16b8fb400
```

지표 계산 코드:

```text
scripts/research/stage23_rework_20260713/engine/core/indicators.py
21c82425537e32600821e4460bdea53b09dc90597971b9a7fbdc4f3dfa50db38
```

Stage 3 wrapper:

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py
3fb837c3a575d98260e3bc71eb45678440797a8f61188cdff366c93a0f8ebe7d
```

시장 데이터는 이 기술 feature 분포 계산에 수치 입력으로 쓰이지 않지만, 지정된 root 단일 소스와 SHA를 확인했다.

```text
data/_system/market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

data/_system/market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

## 측정 정의

### Fold 안정성

각 방식·지표·종목별 train 3개 fold에서 다음을 계산했다.

```text
median_disp_norm
= fold median의 표준편차 / fold IQR 중앙값

iqr_cv
= fold IQR 표준편차 / fold IQR 평균

train_boundary_overlap
= 세 fold의 q01~q99 구간 pairwise intersection/union 평균

stability_risk
= [median_disp_norm + iqr_cv + (1 - boundary_overlap)] / 3
```

`stability_risk`가 낮을수록 fold 간 분포가 안정적이다.

### Train/OOS 유사성

3개 train fold를 합쳐 OOS recent_1y와 비교했다.

```text
boundary_overlap
= train q01~q99와 OOS q01~q99의 intersection/union

ks_stat
= two-sample KS statistic

quantile_distance_iqr
= q01/q25/q50/q75/q99 평균 절대차 / train IQR

oos_risk
= [(1 - boundary_overlap) + ks_stat + quantile_distance_iqr] / 3
```

`oos_risk`가 낮을수록 train/OOS 분포가 유사하다.

KS 값은 자기상관이 있는 일별 시계열에 대한 기술적 거리로만 사용했으며 독립표본 유의성 검정으로 해석하지 않았다.

### 판정 규칙

두 종목 평균 기준으로 stability risk와 OOS risk를 비교했다.

```text
한 방식이 두 risk에서 모두 5% 초과 개선 → 해당 방식 BETTER
두 차이가 모두 5% 이내                → NO_DIFFERENCE
그 외                                   → MIXED
```

## 지표별 최종 판정

| feature | label | snapshot stability risk | grouped stability risk | snapshot OOS risk | grouped OOS risk | grouped stability 개선 | grouped OOS 개선 | 제외 검토 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| ma_trend | NO_DIFFERENCE | 0.2489 | 0.2540 | 0.4680 | 0.4650 | -2.01% | +0.63% | 우선 검토 |
| macd_hist | NO_DIFFERENCE | 0.1690 | 0.1642 | 0.2833 | 0.2855 | +2.86% | -0.76% | 검토 |
| rsi | MIXED | 0.1297 | 0.1550 | 0.2838 | 0.2942 | -16.36% | -3.51% | 검토 |
| bb_position | SNAPSHOT_BETTER | 0.0607 | 0.1014 | 0.1060 | 0.1127 | -40.13% | -5.99% | 유지 |
| volume_ratio | MIXED | 0.0799 | 0.0662 | 0.1333 | 0.2435 | +17.10% | -45.26% | snapshot 유지 |

### 해석

#### `ma_trend` — NO_DIFFERENCE, 제외 검토 1순위

두 방식의 위험이 거의 같다. 묶기로 바꿔도 일반화가 개선되지 않는다.

두 종목 평균 train/OOS 지표:

| method | overlap | KS | quantile distance/IQR |
|---|---:|---:|---:|
| snapshot | 0.6502 | 0.3597 | 0.6945 |
| grouped | 0.6552 | 0.3550 | 0.6952 |

POWI의 경우 OOS risk가 양 방식 모두 약 0.5365로 가장 높다. 이 지표는 lag 표현 방식보다 구조적 regime 이동에 취약할 가능성이 높다.

#### `macd_hist` — NO_DIFFERENCE

묶기가 fold 안정성을 2.86% 개선하지만 OOS risk는 0.76% 악화돼 실질적 차이가 없다.

| method | overlap | KS | quantile distance/IQR |
|---|---:|---:|---:|
| snapshot | 0.6955 | 0.1170 | 0.4284 |
| grouped | 0.6813 | 0.1092 | 0.4286 |

전역 방식 변경 근거가 없다.

#### `rsi` — MIXED, snapshot 쪽 우세지만 2축 동시 5% 우위는 아님

Snapshot은 fold 안정성이 16.36% 우수하고 OOS risk도 3.51% 낮다. OOS 개선 폭이 판정 기준 5%에는 못 미쳐 MIXED로 분류했다.

| method | overlap | KS | quantile distance/IQR |
|---|---:|---:|---:|
| snapshot | 0.7485 | 0.2186 | 0.3814 |
| grouped | 0.7491 | 0.2443 | 0.3873 |

Grouped로 바꿀 이유는 없다.

#### `bb_position` — SNAPSHOT_BETTER

Snapshot은 fold 안정성 40.13%, OOS risk 5.99% 우위다.

| method | overlap | KS | quantile distance/IQR |
|---|---:|---:|---:|
| snapshot | 0.9438 | 0.1326 | 0.1291 |
| grouped | 0.9532 | 0.1445 | 0.1468 |

Grouped가 q01~q99 경계 overlap만 조금 높지만 KS와 분위수 거리가 더 나쁘고, train fold 안정성도 크게 악화됐다.

#### `volume_ratio` — MIXED, snapshot 유지

Grouped는 fold 안정성을 17.10% 개선하지만 OOS risk를 45.26% 악화한다.

| method | overlap | KS | quantile distance/IQR |
|---|---:|---:|---:|
| snapshot | 0.8507 | 0.0633 | 0.1874 |
| grouped | 0.6978 | 0.0652 | 0.3632 |

5일 평균이 분산을 줄여 train fold끼리는 안정적으로 보이지만 OOS 분포 폭과 경계가 달라진다. 과적합 완화가 아니라 정보 압축에 따른 분포 왜곡 가능성이 크므로 snapshot을 유지하는 편이 안전하다.

## 종목별 판정

| ticker | feature | label | snapshot stability risk | grouped stability risk | snapshot OOS risk | grouped OOS risk |
|---|---|---|---:|---:|---:|---:|
| AAP | ma_trend | NO_DIFFERENCE | 0.2649 | 0.2684 | 0.3995 | 0.3934 |
| AAP | macd_hist | MIXED | 0.2131 | 0.2076 | 0.3497 | 0.3787 |
| AAP | rsi | SNAPSHOT_BETTER | 0.1130 | 0.1365 | 0.2721 | 0.2984 |
| AAP | bb_position | MIXED | 0.0517 | 0.0803 | 0.0932 | 0.0819 |
| AAP | volume_ratio | SNAPSHOT_BETTER | 0.0645 | 0.0692 | 0.1096 | 0.2324 |
| POWI | ma_trend | NO_DIFFERENCE | 0.2329 | 0.2396 | 0.5365 | 0.5366 |
| POWI | macd_hist | MIXED | 0.1249 | 0.1208 | 0.2169 | 0.1923 |
| POWI | rsi | MIXED | 0.1464 | 0.1736 | 0.2955 | 0.2899 |
| POWI | bb_position | SNAPSHOT_BETTER | 0.0697 | 0.1225 | 0.1187 | 0.1436 |
| POWI | volume_ratio | MIXED | 0.0953 | 0.0633 | 0.1571 | 0.2547 |

## Fold 안정성 비교

| ticker | feature | method | median dispersion/IQR | IQR CV | train q01~q99 overlap | stability risk |
|---|---|---|---:|---:|---:|---:|
| AAP | ma_trend | snapshot | 0.1144 | 0.3376 | 0.6572 | 0.2649 |
| AAP | ma_trend | grouped | 0.1028 | 0.3465 | 0.6441 | 0.2684 |
| AAP | macd_hist | snapshot | 0.1160 | 0.0264 | 0.5030 | 0.2131 |
| AAP | macd_hist | grouped | 0.1049 | 0.0559 | 0.5379 | 0.2076 |
| AAP | rsi | snapshot | 0.0356 | 0.0509 | 0.7475 | 0.1130 |
| AAP | rsi | grouped | 0.0477 | 0.0734 | 0.7117 | 0.1365 |
| AAP | bb_position | snapshot | 0.0300 | 0.0452 | 0.9201 | 0.0517 |
| AAP | bb_position | grouped | 0.0193 | 0.1251 | 0.9034 | 0.0803 |
| AAP | volume_ratio | snapshot | 0.0283 | 0.0052 | 0.8399 | 0.0645 |
| AAP | volume_ratio | grouped | 0.0157 | 0.0535 | 0.8615 | 0.0692 |
| POWI | ma_trend | snapshot | 0.1937 | 0.2113 | 0.7062 | 0.2329 |
| POWI | ma_trend | grouped | 0.2071 | 0.2209 | 0.7091 | 0.2396 |
| POWI | macd_hist | snapshot | 0.0400 | 0.1404 | 0.8056 | 0.1249 |
| POWI | macd_hist | grouped | 0.0240 | 0.1577 | 0.8192 | 0.1208 |
| POWI | rsi | snapshot | 0.1131 | 0.1390 | 0.8131 | 0.1464 |
| POWI | rsi | grouped | 0.1382 | 0.1855 | 0.8029 | 0.1736 |
| POWI | bb_position | snapshot | 0.0821 | 0.0679 | 0.9408 | 0.0697 |
| POWI | bb_position | grouped | 0.1062 | 0.1454 | 0.8841 | 0.1225 |
| POWI | volume_ratio | snapshot | 0.0240 | 0.1262 | 0.8643 | 0.0953 |
| POWI | volume_ratio | grouped | 0.0340 | 0.0860 | 0.9302 | 0.0633 |

## Train/OOS 분포 유사성 비교

| ticker | feature | method | train n | OOS n | q01~q99 overlap | KS | quantile distance/IQR | OOS risk |
|---|---|---|---:|---:|---:|---:|---:|---:|
| AAP | ma_trend | snapshot | 751 | 254 | 0.7140 | 0.3806 | 0.5317 | 0.3995 |
| AAP | ma_trend | grouped | 751 | 254 | 0.7169 | 0.3778 | 0.5194 | 0.3934 |
| AAP | macd_hist | snapshot | 751 | 254 | 0.6137 | 0.1245 | 0.5385 | 0.3497 |
| AAP | macd_hist | grouped | 751 | 254 | 0.5615 | 0.1054 | 0.5921 | 0.3787 |
| AAP | rsi | snapshot | 751 | 254 | 0.7789 | 0.2472 | 0.3480 | 0.2721 |
| AAP | rsi | grouped | 751 | 254 | 0.7497 | 0.2687 | 0.3762 | 0.2984 |
| AAP | bb_position | snapshot | 751 | 254 | 0.9469 | 0.1243 | 0.1024 | 0.0932 |
| AAP | bb_position | grouped | 751 | 254 | 0.9783 | 0.1215 | 0.1024 | 0.0819 |
| AAP | volume_ratio | snapshot | 751 | 254 | 0.8881 | 0.0648 | 0.1520 | 0.1096 |
| AAP | volume_ratio | grouped | 751 | 254 | 0.7265 | 0.0676 | 0.3560 | 0.2324 |
| POWI | ma_trend | snapshot | 751 | 254 | 0.5865 | 0.3387 | 0.8572 | 0.5365 |
| POWI | ma_trend | grouped | 751 | 254 | 0.5936 | 0.3323 | 0.8711 | 0.5366 |
| POWI | macd_hist | snapshot | 751 | 254 | 0.7772 | 0.1095 | 0.3184 | 0.2169 |
| POWI | macd_hist | grouped | 751 | 254 | 0.8012 | 0.1131 | 0.2650 | 0.1923 |
| POWI | rsi | snapshot | 751 | 254 | 0.7181 | 0.1899 | 0.4147 | 0.2955 |
| POWI | rsi | grouped | 751 | 254 | 0.7485 | 0.2199 | 0.3983 | 0.2899 |
| POWI | bb_position | snapshot | 751 | 254 | 0.9407 | 0.1408 | 0.1559 | 0.1187 |
| POWI | bb_position | grouped | 751 | 254 | 0.9281 | 0.1675 | 0.1912 | 0.1436 |
| POWI | volume_ratio | snapshot | 751 | 254 | 0.8134 | 0.0617 | 0.2229 | 0.1571 |
| POWI | volume_ratio | grouped | 751 | 254 | 0.6691 | 0.0627 | 0.3704 | 0.2547 |

# Fold별 분포표

## AAP — ma_trend

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | -23.2794 | -5.7016 | -2.7681 | -0.8298 | 4.8904 | 4.8718 |
| snapshot | train_2 | 250 | -17.4193 | -6.8107 | -1.3017 | 4.3209 | 11.7195 | 11.1317 |
| snapshot | train_3 | 250 | -16.6171 | -8.1823 | -4.4189 | 3.6692 | 17.6309 | 11.8515 |
| snapshot | OOS | 254 | -10.2890 | -2.9673 | 2.4118 | 4.2338 | 17.2327 | 7.2011 |
| grouped | train_1 | 251 | -23.0742 | -5.7376 | -2.7941 | -0.9341 | 4.5756 | 4.8035 |
| grouped | train_2 | 250 | -15.9980 | -6.7239 | -1.4351 | 4.4421 | 11.6261 | 11.1659 |
| grouped | train_3 | 250 | -16.3133 | -8.2290 | -4.2474 | 3.8707 | 17.2948 | 12.0997 |
| grouped | OOS | 254 | -10.0691 | -2.8160 | 2.3784 | 3.9715 | 16.9159 | 6.7876 |

## AAP — macd_hist

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | -8.3319 | -0.5718 | 0.0966 | 0.8009 | 1.4282 | 1.3726 |
| snapshot | train_2 | 250 | -2.4981 | -0.5024 | 0.0397 | 0.9141 | 3.1707 | 1.4166 |
| snapshot | train_3 | 250 | -3.4781 | -0.4376 | 0.4132 | 1.0267 | 3.9140 | 1.4643 |
| snapshot | OOS | 254 | -2.8088 | -0.8981 | 0.0032 | 0.7269 | 2.0022 | 1.6250 |
| grouped | train_1 | 251 | -7.8280 | -0.6418 | 0.1122 | 0.7710 | 1.8483 | 1.4128 |
| grouped | train_2 | 250 | -2.3888 | -0.4686 | 0.0097 | 0.8370 | 3.1130 | 1.3056 |
| grouped | train_3 | 250 | -3.3370 | -0.4686 | 0.3624 | 1.0288 | 3.7339 | 1.4975 |
| grouped | OOS | 254 | -2.3309 | -0.8678 | -0.0189 | 0.6807 | 1.9556 | 1.5484 |

## AAP — rsi

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | 11.7522 | 33.0208 | 45.0033 | 53.1379 | 70.2608 | 20.1171 |
| snapshot | train_2 | 250 | 26.4156 | 37.9726 | 46.4749 | 56.2976 | 80.5995 | 18.3249 |
| snapshot | train_3 | 250 | 17.7211 | 36.9238 | 45.2025 | 54.8382 | 75.8100 | 17.9145 |
| snapshot | OOS | 254 | 28.0039 | 44.9265 | 52.0539 | 58.5389 | 77.2034 | 13.6125 |
| grouped | train_1 | 251 | 11.8711 | 32.8898 | 43.9413 | 52.9385 | 68.3823 | 20.0487 |
| grouped | train_2 | 250 | 28.3383 | 38.7542 | 46.0269 | 57.2121 | 79.5483 | 18.4579 |
| grouped | train_3 | 250 | 18.4430 | 37.5236 | 45.4521 | 54.2625 | 71.2407 | 16.7388 |
| grouped | OOS | 254 | 29.8855 | 45.9099 | 52.0100 | 57.0481 | 74.4205 | 11.1383 |

## AAP — bb_position

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | -0.2390 | 0.1711 | 0.4283 | 0.7224 | 1.0924 | 0.5512 |
| snapshot | train_2 | 250 | -0.1804 | 0.2013 | 0.4679 | 0.7506 | 1.1921 | 0.5492 |
| snapshot | train_3 | 250 | -0.2389 | 0.2072 | 0.4553 | 0.7064 | 1.0796 | 0.4992 |
| snapshot | OOS | 254 | -0.2075 | 0.2645 | 0.5422 | 0.7732 | 1.0936 | 0.5087 |
| grouped | train_1 | 251 | -0.1452 | 0.1619 | 0.4250 | 0.7259 | 1.0407 | 0.5640 |
| grouped | train_2 | 250 | -0.0493 | 0.1947 | 0.4387 | 0.7348 | 1.0554 | 0.5401 |
| grouped | train_3 | 250 | -0.0671 | 0.2551 | 0.4504 | 0.6740 | 0.9814 | 0.4189 |
| grouped | OOS | 254 | -0.0861 | 0.3062 | 0.5457 | 0.7460 | 1.0397 | 0.4399 |

## AAP — volume_ratio

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | 0.5244 | 0.8008 | 0.9791 | 1.1663 | 2.2669 | 0.3655 |
| snapshot | train_2 | 250 | 0.3449 | 0.7878 | 0.9539 | 1.1520 | 2.4842 | 0.3641 |
| snapshot | train_3 | 250 | 0.4673 | 0.7933 | 0.9648 | 1.1543 | 2.1651 | 0.3610 |
| snapshot | OOS | 254 | 0.4362 | 0.7852 | 0.9342 | 1.1520 | 2.2884 | 0.3668 |
| grouped | train_1 | 251 | 0.6797 | 0.9001 | 1.0069 | 1.0787 | 1.6812 | 0.1786 |
| grouped | train_2 | 250 | 0.5892 | 0.9076 | 1.0014 | 1.0875 | 1.5460 | 0.1800 |
| grouped | train_3 | 250 | 0.5938 | 0.9158 | 1.0006 | 1.0756 | 1.6657 | 0.1597 |
| grouped | OOS | 254 | 0.6302 | 0.9077 | 0.9893 | 1.0985 | 1.3824 | 0.1908 |

## POWI — ma_trend

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | -6.5643 | -3.3086 | 0.8326 | 3.3388 | 7.0493 | 6.6474 |
| snapshot | train_2 | 250 | -5.8883 | -3.7607 | -1.9360 | 2.3033 | 5.7440 | 6.0640 |
| snapshot | train_3 | 250 | -11.9547 | -3.4948 | -1.2279 | 0.4279 | 5.3702 | 3.9228 |
| snapshot | OOS | 254 | -10.7568 | -5.2558 | 3.0062 | 7.7201 | 17.4829 | 12.9759 |
| grouped | train_1 | 251 | -6.4366 | -3.2549 | 0.9734 | 3.4775 | 6.6959 | 6.7325 |
| grouped | train_2 | 250 | -5.7461 | -3.7258 | -1.9831 | 2.2898 | 5.4108 | 6.0155 |
| grouped | train_3 | 250 | -11.4872 | -3.3885 | -1.1571 | 0.4675 | 5.1120 | 3.8560 |
| grouped | OOS | 254 | -10.5578 | -5.2255 | 2.9748 | 7.8932 | 17.0549 | 13.1187 |

## POWI — macd_hist

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | -1.7115 | -0.5417 | 0.0470 | 0.7275 | 1.8587 | 1.2693 |
| snapshot | train_2 | 250 | -1.4270 | -0.5319 | -0.0526 | 0.3808 | 1.7050 | 0.9126 |
| snapshot | train_3 | 250 | -2.3257 | -0.5080 | -0.0062 | 0.5087 | 2.0272 | 1.0167 |
| snapshot | OOS | 254 | -2.2226 | -0.7660 | 0.1195 | 0.7121 | 2.6522 | 1.4780 |
| grouped | train_1 | 251 | -1.6262 | -0.5296 | 0.0006 | 0.6637 | 1.7422 | 1.1932 |
| grouped | train_2 | 250 | -1.3494 | -0.4861 | -0.0518 | 0.3228 | 1.5811 | 0.8089 |
| grouped | train_3 | 250 | -2.1083 | -0.4730 | -0.0043 | 0.5124 | 1.8590 | 0.9854 |
| grouped | OOS | 254 | -2.0939 | -0.7021 | 0.1004 | 0.6677 | 2.3706 | 1.3698 |

## POWI — rsi

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | 30.8790 | 43.3713 | 50.4282 | 58.0903 | 71.1407 | 14.7190 |
| snapshot | train_2 | 250 | 29.7983 | 39.7848 | 46.3996 | 55.4503 | 70.2048 | 15.6654 |
| snapshot | train_3 | 250 | 22.7626 | 41.6255 | 47.8610 | 52.8151 | 65.9736 | 11.1895 |
| snapshot | OOS | 254 | 25.9766 | 42.0280 | 53.0304 | 62.3465 | 84.2208 | 20.3185 |
| grouped | train_1 | 251 | 32.8824 | 43.8081 | 50.5465 | 58.5327 | 69.1442 | 14.7247 |
| grouped | train_2 | 250 | 31.0244 | 40.4045 | 45.8468 | 54.3111 | 67.7257 | 13.9065 |
| grouped | train_3 | 250 | 27.3464 | 43.1381 | 47.9780 | 52.5174 | 62.6366 | 9.3793 |
| grouped | OOS | 254 | 29.0022 | 41.7207 | 53.2529 | 60.3909 | 80.4010 | 18.6702 |

## POWI — bb_position

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | -0.0477 | 0.2246 | 0.4516 | 0.7817 | 1.1120 | 0.5570 |
| snapshot | train_2 | 250 | -0.1515 | 0.1741 | 0.3582 | 0.6707 | 1.1201 | 0.4966 |
| snapshot | train_3 | 250 | -0.1245 | 0.2324 | 0.4354 | 0.7078 | 1.1138 | 0.4754 |
| snapshot | OOS | 254 | -0.1138 | 0.2978 | 0.5625 | 0.8061 | 1.2180 | 0.5082 |
| grouped | train_1 | 251 | -0.0055 | 0.2484 | 0.4687 | 0.7877 | 1.0707 | 0.5393 |
| grouped | train_2 | 250 | -0.0751 | 0.2099 | 0.3696 | 0.6037 | 1.0523 | 0.3938 |
| grouped | train_3 | 250 | -0.0385 | 0.2555 | 0.4534 | 0.6655 | 0.9449 | 0.4100 |
| grouped | OOS | 254 | -0.0547 | 0.3244 | 0.5721 | 0.8010 | 1.1240 | 0.4767 |

## POWI — volume_ratio

| method | fold | n | q01 | q25 | median | q75 | q99 | IQR |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| snapshot | train_1 | 251 | 0.4839 | 0.7871 | 0.9404 | 1.1933 | 1.8537 | 0.4062 |
| snapshot | train_2 | 250 | 0.4977 | 0.7845 | 0.9485 | 1.1384 | 2.0770 | 0.3539 |
| snapshot | train_3 | 250 | 0.3871 | 0.7462 | 0.9250 | 1.2273 | 2.0453 | 0.4811 |
| snapshot | OOS | 254 | 0.3053 | 0.8024 | 0.9598 | 1.1425 | 2.2821 | 0.3401 |
| grouped | train_1 | 251 | 0.6642 | 0.8957 | 0.9990 | 1.1084 | 1.3872 | 0.2128 |
| grouped | train_2 | 250 | 0.6456 | 0.9107 | 1.0166 | 1.1205 | 1.3411 | 0.2097 |
| grouped | train_3 | 250 | 0.6468 | 0.8757 | 1.0063 | 1.1279 | 1.4010 | 0.2522 |
| grouped | OOS | 254 | 0.5692 | 0.9145 | 1.0080 | 1.0972 | 1.6870 | 0.1828 |

## 지표 개수 축소 시사점

### 즉시 결론

이번 측정은 분포 안정성과 train/OOS 유사성만 본 것이며, 각 feature의 독립적인 수익 기여도나 다른 feature와의 상호보완성은 측정하지 않았다. 따라서 이 결과만으로 feature를 바로 삭제하면 안 된다.

### 제외 검토 우선순위

1. `ma_trend`
   - 두 방식 모두 OOS risk가 가장 높다.
   - 5일 평균으로 바꿔도 개선되지 않는다.
   - 특히 POWI에서 train median이 음수인 fold가 많지만 OOS median은 약 +3으로 regime 이동이 크다.
   - feature 개수 축소 실험을 한다면 첫 번째 ablation 후보다.

2. `macd_hist`
   - 묶기와 snapshot 차이는 거의 없고, 평균 quantile distance가 약 0.43 IQR이다.
   - 제거보다는 ablation 비교 대상이다.

3. `rsi`
   - snapshot이 fold 안정성에서 더 낫다.
   - OOS 괴리는 존재하지만 ma_trend보다 작다.
   - 현재 단계에서는 유지하되 ablation 후보로 분류한다.

### 유지 근거가 강한 지표

```text
bb_position
volume_ratio(snapshot 방식)
```

두 지표는 snapshot에서 train/OOS risk가 가장 낮다. 특히 volume ratio를 grouped로 바꾸면 OOS 유사성이 크게 악화된다.

## 최종 권고

```text
전역 lag 방식: 현행 D-5 snapshot 유지
5일 grouped 전환: 하지 않음
지표별 혼용: 현재 근거로는 하지 않음
첫 ablation 후보: ma_trend
그다음 검토 후보: macd_hist, rsi
유지 우선: bb_position, volume_ratio
```

## 실행 제약 준수

```text
GA 실행: 0
백테스트 실행: 0
학습 실행: 0
코드 수정: 0
입력 데이터 수정: 0
시장 데이터 수정: 0
```

분석은 기존 OHLCV snapshot에 지표를 계산한 뒤 분포 통계와 KS 거리만 산출했다.
