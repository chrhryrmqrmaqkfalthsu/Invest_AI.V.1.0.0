# Stage 2/3 entry feature 분포 측정

## 판정

`DISTRIBUTION_MEASURED`

AAP·POWI의 `train_1/2/3`에서 strict-AND 후보 5개 feature를 로컬 OHLCV와 복사본 `indicators.py`만 사용해 계산했다. GA·학습·백테스트·외부 fetch는 실행하지 않았다.

## Feature 정의

```text
ma_relation_pct
= 0.5 × [(MA5/MA20 - 1) + (MA20/MA60 - 1)] × 100

macd_hist_close_pct
= MACD_hist / Close × 100

rsi
= RSI(14)

bb_position
= (Close - BB_lower) / (BB_upper - BB_lower)

volume_ratio
= Volume / Volume_MA5
```

지표는 전체 OHLCV에서 먼저 계산한 뒤 각 train 기간을 잘랐다. 따라서 fold 시작점의 rolling indicator가 인위적으로 초기화되지 않는다.

## 표본

```text
AAP train_1: 251행
AAP train_2: 250행
AAP train_3: 250행
POWI train_1: 251행
POWI train_2: 250행
POWI train_3: 250행
```

5개 연속 feature의 30개 ticker-fold 조합에서:

```text
최대 NaN 비율: 0%
Inf: 0건
최소 unique ratio: 99.6%
```

## 전체 분포 요약

| Feature | 전체 min~max | median q01~q99 | median IQR |
|---|---:|---:|---:|
| MA relation % | -23.7773 ~ 18.7443 | -13.0717 ~ 6.3966 | 6.2681 |
| MACD hist/Close % | -9.2305 ~ 4.3959 | -2.4119 ~ 2.3276 | 1.3067 |
| RSI | 11.3179 ~ 82.6134 | 24.8198 ~ 70.7008 | 16.7253 |
| BB position | -0.4734 ~ 1.5118 | -0.1659 ~ 1.1129 | 0.5280 |
| Volume ratio | 0.1901 ~ 3.9448 | 0.4756 ~ 2.1618 | 0.3667 |

## 분포 해석

### MA relation

종목·fold 차이가 크다. AAP는 `train_1` q01이 -23.28%, `train_3` q99가 17.63%인 반면 POWI의 범위는 더 좁다. 공통 global min/max로 개체를 생성하면 특정 fold에서 도달 불가능 interval이 쉽게 생긴다.

권장:

```text
고정 이론 domain: MA가 양수일 때 (-100%, +∞)
생성 domain: 현재 train fold q01~q99
min/max: 감사 기록용
```

### MACD hist / Close

가격 정규화 후에도 AAP `train_1`에 강한 음의 꼬리가 있다. 전체 min -9.23%는 중앙 분포와 크게 떨어진다.

권장:

```text
raw min/max 생성 금지
fold q01~q99 기반 pair 생성
```

### RSI

이론 범위 `[0,100]`이 명확하고 분포도 연속적이다. strict interval에 가장 안정적인 feature 중 하나다.

권장:

```text
hard domain: [0,100]
empirical domain: fold q01~q99
```

### BB position

0과 1은 각각 하단·상단 band이고, 0 미만과 1 초과는 band 밖의 정상 값이다. 실제 표본도 -0.473~1.512를 보였다.

따라서 다음은 금지해야 한다.

```text
OOD 검사 전에 [0,1] clip
```

밴드 폭이 0 또는 비유한 값일 때만 invalid 처리한다.

### Volume ratio

양수 연속형이지만 오른쪽 꼬리가 길다. 전체 max 3.9448은 q99보다 크게 높다. 기존 `>= threshold` 편측 조건을 low/high로 바꾸는 것이 필수다.

권장:

```text
hard lower bound: 0
생성/OOD domain: fold q01~q99
상한 필수
```

## Interval 최소 폭 후보

현재 단계의 권장값은 최종 상수가 아니라 구현 후보다. fold IQR의 10~20% 범위를 최소 폭 후보로 제시한다.

| Feature | 최소 폭 후보 |
|---|---:|
| MA relation | 0.6 ~ 1.3%p |
| MACD hist/Close | 0.13 ~ 0.26%p |
| RSI | 1.7 ~ 3.3 |
| BB position | 0.05 ~ 0.11 |
| Volume ratio | 0.04 ~ 0.07 |

고정 절대값만 쓰기보다 `max(절대 최소 폭, fold IQR 비율)` 형태가 fold 규모 차이에 안전하다.

## Near-full interval 후보

다음 조건을 near-full로 표시하는 것을 권장한다.

```text
interval width >= 해당 fold q01~q99 span의 80%
```

한 chromosome에서 near-full feature를 여러 개 허용하면 strict-AND가 사실상 무력화된다. 허용 개수는 구현 단계에서 별도로 확정해야 한다.

## Empirical support 후보

각 fold가 약 250행이므로 다음을 권장 후보로 둔다.

```text
feature별 interval 내부 관측치 >= 25행 (약 10%)
5개 strict-AND 공동 통과 관측치 >= 12행 (약 5%)
```

feature별 support만 검사하면 5-way 교집합이 0이 될 수 있으므로 공동 support 검사가 반드시 필요하다.

## Interval 부적합 feature

### Aligned_bull

```text
고유값: 2개
median dominant value share: 약 78%
```

상태형 categorical gate로는 사용할 수 있지만 numeric low/high interval 유전자로는 부적합하다.

### MACD_golden

```text
고유값: 2개
1 발생률: 약 2.8~4.8%
median dominant value share: 약 96.4%
```

희소한 당일 crossover event다. 연속 interval 대신 별도 event flag 또는 진단값으로만 남기는 것이 맞다.

## 구현 전 권장안

```text
train domain:
fold별 q01~q99를 생성·OOD 기준으로 사용
min/max는 감사용으로 저장

minimum width:
fold IQR의 10~20%

near-full:
fold q01~q99 폭의 80% 이상

support:
feature별 >=25행
joint strict-AND >=12행

pair generation:
center/width 또는 관측 quantile pair
low/high 독립 생성 금지
```

위 수치는 분포 기반 권장 범위이며 아직 production 상수로 확정하지 않았다.
