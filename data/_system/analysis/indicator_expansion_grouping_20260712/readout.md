# 지표 확장 인벤토리 + 그룹핑 분석 판정

## 최종 판정

# **GROUPING_VIABLE**

변동성 지표를 추가하자 기존 12개 가격 path만 사용했을 때보다 2일 내 +3% 라벨과의 단변량 관계가 명확하게 강해졌다. 또한 지표를 성격별로 묶은 뒤 그룹 안에서 합산하고 그룹 사이를 AND로 분리하면, 전체 총점에서 발생하는 BOIL·CE형 상쇄 통과를 실제 과거 데이터에서 차단할 수 있었다.

이 판정은 하이브리드 구조의 구현·재학습을 진행할 근거가 있다는 뜻이다. 현재 equal-weight와 threshold는 read-only 기술통계에서 얻은 **[추정]**이며 완성된 모델이나 새로운 OOS 성과가 아니다.

## 분석 범위

- 종목: rolling 파일럿과 동일한 50종목
- 평가 행: 75,900
- 기존 지표: D-5~D-1 가격 path 12개
- 추가 계산 지표: 변동성 15개, 거래량 10개
- 전체 분석 후보: 37개
- 라벨: D0 시가 대비 D+1~D+2 고가 최대값 +3% 이상
- 데이터: 기존 frozen OHLCV와 기존 라벨 산출물만 사용
- 재학습: 없음
- D0 gap·flow·orderbook: 제외

추가 지표의 데이터 coverage는 최소 99.01%였고 대부분 100%였다. 20일 realized volatility 일부 초기 행만 warm-up 결측이었다.

## Step 1 — 라이브 계산 가능성

`engine/live/elite_shadow_trader.py` 244~272행은 ticker별 1년 OHLCV를 로드한 뒤 `calc_indicators()`를 적용한다. `engine/core/indicators.py`는 ATR14, ATR_pct, Bollinger width, Volume_MA5·20, Volume_ratio를 생성한다.

따라서 다음 계열은 completed D-1 bar를 명시적으로 자르면 live 원자료에서 재현 가능하다.

- ATR·ATR_pct
- true range와 high-low range
- 3·5·10·20일 realized/range volatility
- Bollinger width
- 거래량의 5·10·20일 평균 대비 비율
- 거래량 변화율·z-score·surge flag

`STORED`는 현재 candidate row에 이미 필드가 기록된다는 뜻이 아니라, live OHLCV/표준 indicator frame에서 원천 데이터가 보장되어 결정적으로 계산 가능하다는 뜻이다. 현재 live candidate integration은 별도 구현이 필요하다.

다음은 제외했다.

- flow/orderbook: ordinary daily OHLCV에 없어 `NOT_STORED`
- `STK_gap_d0`, ETF gap_d0: completed D-1 cutoff 위반
- 동기화되지 않은 market/news optional 자료

## Step 2 — 예측 기여도

### OOS 상위 지표

| 순위 | 지표 | OOS Pearson | OOS MI / label entropy | 판정 |
|---:|---|---:|---:|---|
| 1 | `atr14_pct` | 0.1780 | 3.37% | KEEP_CORE |
| 2 | `mean_range3_pct` | 0.1757 | 3.04% | 중복으로 대체 제외 |
| 3 | `mean_range5_pct` | 0.1749 | 2.99% | ATR 대체 ablation |
| 4 | `intraday_range_d1_pct` | 0.1638 | 2.73% | true range와 중복 |
| 5 | `true_range_d1_pct` | 0.1434 | 2.62% | KEEP_CORE |
| 6 | `realized_vol20_pct` | 0.0853 | 2.51% | KEEP_CORE |
| 7 | `realized_vol10_pct` | 0.0791 | 2.01% | 대체·진단 후보 |
| 8 | `bb_width20_pct` | 0.1157 | 1.69% | KEEP_CORE |
| 11 | `pullback_from_high5_pct` | 0.1032 | 1.09% | KEEP_CORE |

기존 SHARED_LIMIT 분석에서 가장 유효했던 `pullback_from_high5_pct`, `single_up_day5_pct`, `fade_after_surge_score`는 그대로 유지됐다. 그러나 새 변동성 수준 지표가 이들보다 더 높은 OOS 정보량을 보였다.

### 거래량 지표

거래량 지표의 단독 OOS 효과는 약했다.

- Pearson: 대체로 0.02~0.04
- MI/entropy: 대부분 0.24% 미만

따라서 거래량을 독립 alpha 그룹으로 해석하면 안 된다. 다만 거래량 ratio·change 지표끼리는 OOS 상관 0.49~0.96으로 동조했고, 가격·변동성 setup을 확인하는 confirmation 축으로 사용할 수 있다.

### 제거 후보

기존 12개 중 다음은 약하거나 regime 부호가 불안정했다.

```text
ret_d5_pct, ret_d4_pct, ret_d3_pct, ret_d2_pct,
cumulative_ret5_pct, up_days5, days_since_high5
```

중복 제거 대상도 명확했다.

- `mean_range3` ↔ `mean_range5`: r=0.9475
- intraday range ↔ true range: r=0.9338
- realized vol5 ↔ single_up: r=0.9427
- range_vs_ATR ↔ range_vs_range20: r=0.9422
- volume ratio5 ↔ volume_chg3: r=0.9582
- volume ratio10 ↔ ratio20: r=0.9555
- volume ratio20 ↔ z20: r=0.9349

동일 축을 여러 이름으로 중복 가중하면 총점 집중과 과적합 위험이 커진다.

## Step 3 — 제안 그룹

### G1 PULLBACK_SETUP

```text
pullback_from_high5_pct
fade_after_surge_score
inverted close_pos5
inverted ret_d1_pct
```

### G2 VOLATILITY_REGIME

```text
single_up_day5_pct
atr14_pct
realized_vol20_pct
bb_width20_pct
```

`mean_range5_pct`는 ATR14% 대체 ablation으로 둔다.

### G3 RANGE_EXPANSION

```text
true_range_d1_pct
range_vs_atr14
range_vs_range20
```

희소하지만 위험한 상쇄를 막기 위해 member percentile floor 0.15를 시험하는 것이 **[추정]**상 적절하다.

### G4 VOLUME_CONFIRMATION

```text
volume_ratio5_prior
volume_ratio20_prior
volume_chg1_pct
```

독립 수익 신호가 아니라 confirmation 경계다.

## Step 4 — 상쇄 위험 결과

### 그룹 내 합산

| 그룹 | 내부 평균 상관 | Severe offset 비중 | 판정 |
|---|---:|---:|---|
| G1 Pullback | 0.5045 | 2.46% | SAFE_SUM |
| G2 Volatility regime | 0.5968 | 3.55% | SAFE_SUM |
| G3 Range compact | 0.7157 | 0.09% | SAFE_WITH_FLOOR |
| G4 Volume compact | 0.6338 | 0.45% | SAFE_SUM_BUT_WEAK |
| 약한 path 7개 | 0.1081 | **48.57%** | NOT_GROUPABLE |

약한 기존 path 7개는 같은 그룹으로 합칠 수 없다. 한 지표의 과다가 다른 지표의 심한 미달을 보상하는 사례가 통과의 거의 절반이었다.

### 그룹 간 AND

구조 비교용 threshold 0.55에서:

| 규칙 | OOS coverage | 정밀도 | Lift |
|---|---:|---:|---:|
| 네 그룹 전체 총점 | 42.92% | 57.95% | +7.80%p |
| 네 그룹 각각 통과 AND | **7.37%** | **63.58%** | **+13.43%p** |

기존 통합 파일럿의 12개 strict-AND 전체 OOS 잔존율은 0.20%였다. 제안 구조는 descriptive simulation에서 7.37%를 남겨 신호 소멸을 크게 완화했다.

동시에 그룹 간 AND는 다음을 모두 차단했다.

- BOIL형: 전체 총점은 통과하지만 range 또는 volume 그룹이 0.35 미만인 764건
- CE형: 한 그룹이 0.85 이상이고 다른 그룹이 0.35 미만인 집중 통과 1,371건

BOIL형 정밀도는 57.72%, CE형은 54.85%로 4그룹 AND 63.58%보다 낮았다. 그룹 경계가 단순 규제 장치가 아니라 실제 품질 저하 사례를 제거했다.

Threshold 0.60은 coverage 4.95%, 정밀도 63.46%였다. 0.65는 coverage만 3.06%로 줄고 정밀도는 63.12%여서 시작점으로는 지나치게 엄격했다.

## 판정 근거

### GROUPING_VIABLE인 이유

1. 변동성 지표가 기존 가격 path보다 강한 OOS 정보량을 추가했다.
2. 성격별 그룹 내부 상관이 충분히 높고 severe offset이 낮았다.
3. 서로 다른 축 사이 AND가 실제 BOIL·CE형 보상 통과를 차단했다.
4. 12개 strict AND보다 신호 coverage를 크게 회복했다.
5. flow/orderbook 없이도 OHLCV만으로 네 축을 구성할 수 있다.

### NEEDS_MORE_INDICATORS가 아닌 이유

거래량 단독 신호는 약하지만 ATR·range·BB·realized volatility가 의미 있는 신규 정보를 제공했다. 최소한 하이브리드 구조를 구현해 검증할 만큼의 확장 정보는 확보됐다.

### NOT_SEPARABLE이 아닌 이유

Pullback, volatility capacity, current range expansion, volume confirmation의 네 축은 성격과 상관 구조가 구분되며, 그룹 간 경계가 concentration·offset 사례를 실제로 분리했다.

## 다음 단계 밑그림

### 1. Feature extractor

completed D-1 bar guard를 강제하고 아래 compact set만 계산한다.

```text
G1: pullback, fade, close_pos5, ret_d1
G2: single_up, ATR14%, realized_vol20%, BB_width20%
G3: true_range_d1%, range_vs_ATR, range_vs_range20
G4: volume_ratio5_prior, volume_ratio20_prior, volume_chg1%
```

### 2. Train-only 그룹 학습

- 각 지표를 train-frozen percentile 또는 robust scaler로 정규화
- 그룹 안 weight 학습 또는 equal-weight baseline
- 그룹별 threshold 학습
- coverage 최소 게이트 포함
- 한 지표 weight가 그룹의 과반을 차지하지 못하도록 concentration 제한 검토 **[추정]**

### 3. 비교군

반드시 다음을 같은 데이터와 비용 계약으로 비교한다.

1. 기존 12개 strict AND
2. 전체 지표 단일 총점
3. 4그룹 AND
4. G4 volume 제거
5. ATR14% ↔ mean_range5% 대체
6. range_vs_ATR ↔ range_vs20 하나 제거

### 4. 검증

- Train에서만 weight·threshold 결정
- Stress·OOS 이중 게이트
- 종목 HHI와 coverage 보고
- BOIL·CE offset count 별도 기록
- rolling 목표일 exit와 실제 거래 성과 비교
- 마지막으로 prospective shadow 검증

현재 분석의 0.55 threshold와 equal weights는 같은 OOS에서 구조를 설명하기 위한 진단값이다. 다음 재학습에서 이를 고정 성능 주장으로 사용하면 안 된다.
