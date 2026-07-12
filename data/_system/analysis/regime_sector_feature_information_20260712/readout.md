# Regime·섹터 feature 정보량 확인

# 최종 판정: **REGIME_MARGINAL**

복구된 `market_history.csv`의 시장 regime·VIX·기술섹터 정보를 AAP·POWI의 L2 상대 타깃에 D-1로 정렬해 검사했다.

신규 feature는 기존 5개 가격 feature와 낮은 상관을 보였고 같은 표본에서는 분명한 MI 증분을 만들었다. 그러나 종목별 계산과 시계열 circular-shift null 보정을 적용한 결합 Top-5 MI가 외부 기준선 `0.129018 bits`를 넘지 못했다.

따라서 신규 정보가 전혀 없다는 `REGIME_NOHELP`는 아니지만, 바로 데이터 소스 확장을 확정할 수준의 `REGIME_ADDS`도 아니다.

## 1. 표본과 시점 계약

```text
종목: AAP, POWI
섹터 매핑: 둘 다 tech
표본: 종목당 1,459행, 총 2,918행
기간: 2020-09-09 ~ 2026-07-01
L2 positive rate: 34.5785%
시장 미래누출 행: 0
```

라벨:

```text
max(High[D+1], High[D+2])
>= Open[D0] × (1 + sqrt(2) × RV20_pct[D-1] / 100)
```

시점:

```text
기존 strict-AND 가격 feature: D-5 거래행
시장·섹터 feature: latest market row <= D0-1 calendar day
```

## 2. 사용한 신규 feature

시장:

```text
market_score
regime_code
sp500_60d
sp500_60d_delta5
sp500_60d_delta20
sp500_60d_vs_ma20
vix_level
vix_delta1
vix_delta5
vix_vs_ma20
```

기술섹터:

```text
sector_tech_score
sector_tech_delta5
sector_tech_delta20
sector_tech_vs_ma20
sector_tech_return60_proxy
sector_vs_sp500_60d_proxy
sector_minus_market_score
```

중요한 제한:

`market_history.csv`에는 원시 S&P 500·XLK 가격이 없다. 저장된 값은 `sp500_60d`와 60일 섹터 점수다. 따라서 `sp500_60d_vs_ma20`은 S&P 500 가격 MA 교차가 아니라 **저장된 60일 수익률의 20일 평균 대비 편차**다.

`sector_tech_return60_proxy`도 다음 역변환 proxy이며 원시 XLK 수익률이 아니다.

```text
(sector_tech_score - 50) / 5
```

## 3. 원시 pooled 결과

| Feature set | Top-5 MI 합 |
|---|---:|
| 기존 5개 | 0.020189 bits |
| 신규 regime·sector | 0.172831 bits |
| 결합 | 0.172831 bits |
| 외부 기존 14개 기준선 | 0.129018 bits |

원시 pooled 결합 Top-5는 전부 신규 feature였다.

```text
sector_minus_market_score
vix_delta5
sector_vs_sp500_60d_proxy
sector_tech_delta5
market_score
```

원시 pooled 기준으로는 외부 기준선보다 `+0.043813 bits`, 약 `+33.96%` 높다.

하지만 같은 시장 날짜의 feature가 AAP와 POWI 행에 반복되고 두 종목의 라벨률이 다르므로 pooled MI가 과대평가될 수 있다. 이 값을 최종 판정에 단독 사용하지 않았다.

## 4. 종목별 평균 기준

AAP·POWI에서 각각 MI를 계산한 뒤 feature별 평균을 사용했다.

| Feature set | Top-5 MI 합 | 외부 0.129 대비 |
|---|---:|---:|
| 기존 5개 | 0.036958 | -0.092061 |
| 신규 regime·sector | 0.082663 | -0.046355 |
| 결합 | 0.096728 | -0.032290 |

결합 Top-5:

```text
sp500_60d_vs_ma20
rsi
sector_tech_delta5
market_score
sector_tech_delta20
```

같은 표본 기존 5개 대비 증분은 `+0.059770 bits`다. 신규 정보가 존재한다는 신호는 있지만 외부 기준선에는 미달한다.

## 5. 시계열 null 보정

라벨의 시계열 자기상관으로 MI가 부풀 수 있어, 각 종목 라벨을 독립적으로 circular shift한 null을 100회 계산했다.

Null 평균을 뺀 Top-5:

| Feature set | Bias-adjusted Top-5 MI 합 |
|---|---:|
| 기존 5개 | 0.019521 bits |
| 신규 regime·sector | 0.044026 bits |
| 결합 | 0.057092 bits |

결합 증분:

```text
0.057092 - 0.019521 = +0.037570 bits
```

양의 증분은 남지만 외부 기준선 `0.129018 bits`에는 크게 못 미친다.

가장 안정적인 신규 feature는 `sp500_60d_vs_ma20`이었다.

```text
AAP MI:  0.018012 bits
POWI MI: 0.036146 bits
종목 평균 empirical p: 0.0198
종목 평균 bias-adjusted MI: 0.018762 bits
```

반면 `sector_tech_delta5`는 AAP에서는 강했지만 POWI에서는 거의 0이었다.

```text
AAP MI:  0.034276 bits
POWI MI: 0.000610 bits
```

섹터 변화 feature의 종목 간 안정성은 아직 부족하다.

## 6. 기존 feature와의 중복

신규 Top feature와 기존 5개 feature의 최대 절대상관:

| 신규 feature | 가장 높은 기존 상관 | |corr| |
|---|---|---:|
| `market_score` | `ma_trend` | 0.2885 |
| `sp500_60d_vs_ma20` | `bb_position` | 0.2686 |
| `sector_tech_delta5` | `bb_position` | 0.0599 |
| `vix_delta5` | `rsi` | 0.0776 |
| `sector_vs_sp500_60d_proxy` | `ma_trend` | 0.0774 |
| `sector_minus_market_score` | `ma_trend` | 0.1810 |

선택된 신규 feature는 모두 기존 5개와 `|corr| < 0.30`이다. 기존 volume·변동성 feature의 단순 복제라는 증거는 없다.

또한 feature 간 `|corr| < 0.70`을 강제한 greedy Top-5도 원시 pooled MI 합이 `0.170654 bits`로, 무보정 `0.172831 bits`와 차이가 작았다.

따라서 이번 증분은 어제 지적한 “기존 변동성 feature와의 중복 착시”로만 설명되지는 않는다.

## 7. 판정 근거

### REGIME_ADDS가 아닌 이유

- robust 종목별 결합 Top-5가 `0.096728 bits`로 기준선 0.129 미달
- circular-shift bias-adjusted 결합 Top-5는 `0.057092 bits`
- 섹터 변화 feature가 AAP·POWI에서 일관되지 않음
- 원시 pooled 0.172831은 반복 시장 날짜 구조의 과대평가 위험이 있음

### REGIME_NOHELP가 아닌 이유

- 같은 표본 기존 5개 대비 종목별 결합 증분 `+0.059770 bits`
- null 보정 후에도 증분 `+0.037570 bits`
- 신규 feature와 기존 가격 feature 상관이 낮음
- `sp500_60d_vs_ma20`은 두 종목에서 모두 양의 MI를 보임

따라서 최종 판정은 **REGIME_MARGINAL**이다.

## 8. 다음 소스 우선순위

1. **원시 시장·섹터 proxy 가격 시계열**
   - SPY 또는 S&P 500 close
   - XLK close
   - 실제 5·20·60일 수익률
   - 실제 단기/중기 MA 관계
   - XLK-SPY 상대수익률

2. **횡단면 섹터 상대강도**
   - 6개 섹터 ETF 동시 순위
   - tech의 percentile rank
   - 섹터 breadth와 dispersion

3. **유동성·거래대금 구조**
   - dollar volume 변화
   - market-wide liquidity regime

현재 복구 파일의 변환 점수만으로는 marginal 신호까지만 확인됐다. 다음 실험은 점수의 파생변수를 늘리기보다 원시 SPY·XLK 가격으로 진짜 상대강도를 계산하는 것이 우선이다.
