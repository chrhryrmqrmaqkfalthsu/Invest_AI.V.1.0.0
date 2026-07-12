# 확장 지표 하이브리드 그룹 제안

## 분석 계약

- 분석 대상: 기존 12개 D-5~D-1 가격 path 지표와 frozen OHLCV에서 D-1까지 계산 가능한 변동성 15개·거래량 10개, 총 37개 지표.
- 표본: rolling 파일럿과 동일한 50종목·75,900일.
- 라벨: D0 시가 대비 D+1~D+2 고가 최대값이 +3% 이상.
- 재학습: 없음.
- 각 지표는 train 분포의 경험적 percentile로 변환한 뒤, train에서 양성과 같은 방향이 높은 점수가 되도록 방향을 통일했다.
- 아래 threshold는 구조 검증용 **[추정]**이며 최종 학습 파라미터가 아니다.

## 제안 구조

```text
그룹 안: 방향 정렬된 percentile 점수 합산/평균
그룹 사이: AND

PASS = G1_PULLBACK_SETUP 통과
   AND G2_VOLATILITY_REGIME 통과
   AND G3_RANGE_EXPANSION 통과
   AND G4_VOLUME_CONFIRMATION 통과
```

그룹 안에서는 같은 성격의 지표가 서로 보강할 수 있게 허용하되, 서로 다른 축은 합산으로 상쇄하지 못하게 분리한다.

## G1 — PULLBACK_SETUP

| 지표 | 성공 방향 | 역할 |
|---|---|---|
| `pullback_from_high5_pct` | 높을수록 | 5일 고점 대비 충분한 pullback |
| `fade_after_surge_score` | 높을수록 | 초기 impulse 후 최근 fade |
| `close_pos5` | 낮을수록, 점수 반전 | 5일 range 하단 위치 |
| `ret_d1_pct` | 낮을수록, 점수 반전 | 마지막 날 약세·반전 setup의 비선형 보조 |

성격은 “급등 가능성이 있는 변동성 종목이 최근 눌리고 있는가”다.

OOS 방향 정렬 후 내부 평균 상관은 0.5045, 최소 상관은 0.2381이었다. 그룹 평균 0.65 이상 통과 중 한 지표가 0.20 미만인 상쇄 사례는 89/3,622건, 2.46%였다. 상쇄 사례 정밀도는 61.80%로 clean 통과 56.33%보다 낮지 않았다.

**판정: 그룹 내 합산 안전 후보.**

## G2 — VOLATILITY_REGIME

| 지표 | 성공 방향 | 역할 |
|---|---|---|
| `single_up_day5_pct` | 높을수록 | 최근 impulse 존재 |
| `atr14_pct` | 높을수록 | 종목의 +3% 도달 capacity |
| `realized_vol20_pct` | 높을수록 | 중기 close-return 변동성 regime |
| `bb_width20_pct` | 높을수록 | 중기 가격 분산 폭 |

OOS 내부 평균 상관은 0.5968, 최소 0.3716이었다. 상쇄 사례는 174/4,895건, 3.55%였으며 정밀도는 68.39%로 clean 통과 58.53%보다 높았다.

**판정: 그룹 내 합산 안전 후보.**

`mean_range5_pct`는 OOS 상관 0.1749, MI entropy 비율 2.99%로 강하지만 ATR14%와 OOS 상관이 0.8548이다. 두 지표를 동시에 강하게 가중하면 동일한 range regime를 중복 계산한다. 따라서 다음 단계에서는:

- 기본안: `atr14_pct` 사용
- 대체 ablation: `atr14_pct` 대신 `mean_range5_pct`

으로 비교하고 둘을 독립 표처럼 중복 가중하지 않는다.

## G3 — RANGE_EXPANSION

| 지표 | 성공 방향 | 역할 |
|---|---|---|
| `true_range_d1_pct` | 높을수록 | D-1의 실제 range·gap 포함 확장 |
| `range_vs_atr14` | 높을수록 | 현재 range가 종목 ATR보다 큰가 |
| `range_vs_range20` | 높을수록 | 현재 range가 20일 평소보다 큰가 |

OOS 내부 평균 상관은 0.7157, 최소 0.5978이었다. 그룹 평균 0.65 이상 통과 중 severe miss는 4/4,687건, 0.085%에 불과했지만 그 4건의 정밀도는 25%였다.

**판정: 그룹 내 합산 가능하되 catastrophic member floor 필요.**

초기 구조는 다음으로 제안한다.

```text
G3 평균 >= group threshold
AND 각 member oriented percentile >= 0.15  [추정]
```

`range_vs_atr14`와 `range_vs_range20`의 상관이 0.9422이므로 둘 중 하나를 제거하는 ablation도 필수다. 두 개를 같이 둘 경우 동일 가중이나 가중 합계가 G3의 50%를 넘지 않게 제한하는 것이 안전하다는 것은 **[추정]**이다.

`intraday_range_d1_pct`는 `true_range_d1_pct`와 상관 0.9338이므로 제외한다. `bb_width20_chg1_pctpoint`까지 섞은 broad G3는 상쇄 사례가 14.39%로 증가하므로 그룹에서 분리·제외한다.

## G4 — VOLUME_CONFIRMATION

| 지표 | 성공 방향 | 역할 |
|---|---|---|
| `volume_ratio5_prior` | 높을수록 | 단기 평균 대비 거래량 수준 |
| `volume_ratio20_prior` | 높을수록 | 장기 평균 대비 거래량 수준 |
| `volume_chg1_pct` | 높을수록 | 거래량 가속 |

OOS 내부 평균 상관은 0.6338, 최소 0.4882였다. 상쇄 사례는 17/3,797건, 0.45%였다.

단독 group lift는 +1.60%p로 약하다. 따라서 이 그룹은 독립 alpha 원천이 아니라 “가격·변동성 조건이 실제 관심 증가를 동반하는가”를 확인하는 경계다.

**판정: 그룹 내 합산은 안전하지만 그룹 간 AND confirmation으로만 사용.**

다음은 중복으로 제외한다.

- `volume_ratio10_prior`: ratio20과 r=0.9555
- `volume_chg3_pct`: ratio5와 r=0.9582
- `volume_z20_prior`: ratio20과 r=0.9349
- `volume_ratio5_inclusive`: ratio5 prior와 r=0.9079
- surge 1.5/2.0 플래그: 연속 volume ratio의 파생 binary

## 그룹화하지 않을 기존 약한 path 지표

다음 7개를 `WEAK_PATH_BREADTH`로 합산하는 가설도 검사했다.

```text
ret_d5_pct, ret_d4_pct, ret_d3_pct, ret_d2_pct,
cumulative_ret5_pct, up_days5, days_since_high5
```

그룹 평균 0.65 이상 통과 1,890건 중 한 지표가 0.20 미만인 severe offset은 918건, **48.57%**였다. 내부 방향 정렬 상관도 평균 0.1081, 최소 -0.3997이었다.

**판정: 서로 다른 방향과 중복·상충이 섞여 합산 부적합. 삭제 후보.**

## 그룹 간 AND가 막는 상쇄

### 시작 threshold 0.55 — 구조 검증용 [추정]

| 방식 | OOS 통과 | Coverage | 정밀도 | Baseline 대비 lift |
|---|---:|---:|---:|---:|
| 네 그룹 전체 평균 합산 | 5,408 | 42.92% | 57.95% | +7.80%p |
| 네 그룹 각각 0.55 이상 AND | 928 | **7.37%** | **63.58%** | **+13.43%p** |

기존 12개 strict-AND survivor의 OOS 전체 잔존율 0.20%와 비교하면, 제안 구조는 descriptive simulation에서 7.37% coverage를 남겼다. 단, 대상과 scoring contract가 달라 정확한 동일 모델 비교는 아니며 다음 재학습에서 확인해야 한다.

### BOIL형 상쇄

정의:

```text
네 그룹 전체 평균은 threshold 이상
BUT G3 range 또는 G4 volume < 0.35
```

0.55 threshold에서 764건이 발생했다. 이들의 정밀도는 57.72%로 네 그룹 AND 통과 63.58%보다 5.86%p 낮았다. 그룹 간 AND는 764건 전부를 차단했다.

### CE형 집중 상쇄

정의:

```text
네 그룹 전체 평균은 threshold 이상
AND 한 그룹 >= 0.85
AND 다른 그룹 < 0.35
```

0.55 threshold에서 1,371건이 발생했고 정밀도는 54.85%였다. 네 그룹 AND보다 8.73%p 낮으며 그룹 간 AND가 전부 차단했다.

따라서 서로 다른 축을 하나의 총점으로 합치는 방식은 실제로 과다한 한 축이 심한 미달 축을 보상하는 BOIL/CE형 통과를 만든다.

## 시작안

다음 구현·재학습 단계의 baseline 구조로 제안한다.

```text
G1 = mean(or weighted mean) of
     pullback, fade, inverted close_pos5, inverted ret_d1

G2 = mean of
     single_up_day5, ATR14%, realized_vol20%, BB_width20%

G3 = mean of
     true_range_d1%, range_vs_ATR14, range_vs_range20
     plus member floor 0.15 [추정]

G4 = mean of
     volume_ratio5_prior, volume_ratio20_prior, volume_chg1%

PASS = G1>=T1 AND G2>=T2 AND G3>=T3 AND G4>=T4
```

초기 equal-weight·T=0.55는 구조 비교용 baseline일 뿐이다. 최종 weight와 threshold는 train에서만 선택하고 stress·OOS는 검증 전용으로 남겨야 한다.

## 필수 ablation

1. G2의 ATR14% 대 mean_range5%.
2. G3의 range_vs_ATR 대 range_vs_range20 중 하나 제거.
3. G4 전체 제거 대비 confirmation 효과.
4. 네 그룹 AND 대 전체 총점 대 기존 12개 strict AND.
5. 각 그룹 member floor 0 / 0.10 / 0.15 / 0.20.
6. coverage 최소 게이트를 함께 두고 정밀도만 최대화하지 않도록 한다.
