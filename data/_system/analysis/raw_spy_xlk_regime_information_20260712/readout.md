# 원시 ^GSPC·XLK 기반 regime feature 정보량 재확인

# 최종 판정: **RAW_SAME**

복구된 시장 점수 proxy가 정보량을 열화시켰는지 확인하기 위해 `^GSPC`, `^VIX`, XLK와 5개 추가 섹터 ETF를 7년 범위로 메모리에서 직접 조회했다.

원시 가격 feature는 무보정 종목 평균에서 proxy보다 소폭 개선됐지만, 시계열 circular-shift 보정 후에는 proxy와 동급이거나 약간 낮았다. 따라서 이전 `REGIME_MARGINAL` 판정은 proxy 열화 때문이 아니라 현재 AAP·POWI L2 문제에서 시장 regime 정보 자체의 한계에 가깝다.

## 1. Fetch 및 저장 계약

조회 심볼:

```text
^GSPC
^VIX
XLK
XLF
XLE
XLV
XLY
XLI
```

조회 결과:

```text
^GSPC 및 6개 섹터 ETF: 각 1,759행
^VIX: 1,761행
범위: 2019-07-10/11 ~ 2026-07-10
```

모든 원시 시장 데이터는 메모리에서만 사용했다.

```text
원시 fetch 결과 CSV 저장: 0건
market_history.csv 쓰기: 0건
외부 데이터 cache 생성: 0건
```

## 2. 표본과 시점 정렬

```text
종목: AAP, POWI
섹터 매핑: 둘 다 tech
표본: 종목당 1,459행, 총 2,918행
기간: 2020-09-09 ~ 2026-07-01
L2 positive rate: 34.5785%
시장 미래누출 행: 0
```

시점 계약:

```text
기존 가격 feature: D-5 거래행
원시 시장·섹터 feature: latest raw market row <= D0-1 calendar day
L2 라벨: D+1~D+2 high와 D-1 RV20 상대 타깃
```

## 3. 원시 feature

### ^GSPC

```text
5·20·60일 수익률
close vs MA20
MA5 vs MA20
MA20 vs MA60
20일 실현변동성
```

### XLK

```text
5·20·60일 수익률
close vs MA20
MA5 vs MA20
MA20 vs MA60
20일 실현변동성
```

### VIX

```text
수준
1일 변화
5일 수익률
MA5 vs MA20
```

### XLK−^GSPC 상대강도

```text
5·20·60일 상대수익률
```

### 6개 섹터 횡단면

각 5·20·60일 창에서:

```text
XLK percentile rank
섹터 수익률 dispersion
XLK cross-sectional z-score
양의 수익 섹터 breadth
```

## 4. Proxy와 원시 가격 직접 대조

### 종목별 MI 평균

| Feature set | Proxy | 원시 가격 | 변화 |
|---|---:|---:|---:|
| 신규 feature 단독 Top-5 | 0.082663 | 0.105771 | +0.023108 |
| 기존 5개 + 신규 결합 Top-5 | 0.096728 | 0.114342 | +0.017614 |

무보정 종목 평균에서는 원시 가격이 개선됐다.

원시 결합 Top-5:

```text
gspc_realized_vol20_pct
rsi
gspc_ret20_pct
xlk_sector_zscore_ret60
xlk_realized_vol20_pct
```

하지만 결합값 `0.114342 bits`는 외부 기존 14개 기준선 `0.129018 bits`보다 여전히 `0.014677 bits` 낮다.

### Circular-shift bias-adjusted

| Feature set | Proxy | 원시 가격 | 변화 |
|---|---:|---:|---:|
| 신규 feature 단독 Top-5 | 0.044026 | 0.041563 | -0.002462 |
| 결합 Top-5 | 0.057092 | 0.054776 | -0.002316 |
| 기존 5개 대비 순증분 | 0.037570 | 0.035255 | -0.002316 |

시계열 null을 제거하면 원시 가격은 proxy보다 좋아지지 않았다.

원시 bias-adjusted 결합 Top-5:

```text
rsi
gspc_ret20_pct
xlk_sector_zscore_ret60
gspc_realized_vol20_pct
sector_dispersion_ret5_pct
```

## 5. 판정 규칙

`RAW_RECOVERS`는 다음을 모두 요구했다.

```text
종목 평균 결합 개선 >= 외부 기준선의 10% = 0.012902 bits
bias-adjusted 결합 개선 >= 0.010000 bits
bias-adjusted 순증분 개선 >= 0.010000 bits
양 종목에서 관측되고 기존 feature와 저상관인 신규 feature 최소 1개
```

실제 결과:

```text
종목 평균 결합 개선: +0.017614 bits → PASS
bias-adjusted 결합 개선: -0.002316 bits → FAIL
bias-adjusted 순증분 개선: -0.002316 bits → FAIL
cross-ticker 저상관 feature 존재 → PASS
```

따라서 최종 판정은 `RAW_SAME`이다.

## 6. 원시 가격에서 남은 약한 신호

가장 안정적인 원시 feature는 다음이었다.

```text
xlk_sector_zscore_ret60
```

결과:

```text
AAP MI: 0.012550 bits
POWI MI: 0.028871 bits
종목 평균 bias-adjusted MI: 0.011736 bits
circular-shift empirical p: 0.0495
기존 5개와 최대 |corr|: 0.1361
```

이는 6개 섹터 중 XLK의 60일 상대 위치가 독립 정보를 일부 담는다는 뜻이다. 하지만 단일 feature의 약한 신호이며, 전체 결합 정보량을 proxy보다 회복시키지는 못했다.

`gspc_ret20_pct`도 양 종목에서 MI가 있었지만 종목 평균 empirical p는 `0.0792`로 5% 기준을 넘었다.

## 7. 중복 정보

원시 feature와 기존 5개 feature의 상관은 대체로 낮거나 중간 수준이었다.

Bias-adjusted Top-5 신규 feature의 최대 기존 상관:

```text
gspc_ret20_pct              0.3519
xlk_sector_zscore_ret60     0.1361
gspc_realized_vol20_pct     0.2691
sector_dispersion_ret5_pct  0.0240
```

상관 `0.70` 미만을 강제한 저중복 Top-5가 원래 bias-adjusted Top-5와 동일했고 합도 `0.054776 bits`로 같았다. 이번 실패는 기존 가격 feature 중복 착시 때문이 아니다.

## 8. Proxy 열화 여부

원시 feature와 proxy feature의 상관은 상당히 높았다.

예:

```text
gspc_ret60_pct ↔ proxy_market_score: 0.9576
vix_ret5_pct ↔ proxy_vix_delta5: 0.9640
xlk_sector_zscore_ret60 ↔ proxy_sector_minus_market_score: 0.7697
```

복구된 proxy가 원시 정보의 상당 부분을 이미 보존하고 있었다. 원시 가격은 feature 선택의 표현력을 늘렸지만, null 보정 후 추가 예측 정보는 회복하지 못했다.

## 9. 결론

```text
Proxy가 크게 열화돼서 REGIME_MARGINAL이 나온 것: NO
원시 ^GSPC·XLK가 의미 있는 회복을 만든 것: NO
시장·섹터 정보가 기존 5개와 독립적인 것: YES
현재 두 종목 L2 문제를 해결할 만큼 강한 것: NO
```

따라서 시장 regime 파이프라인 통합은 착수하지 않는다.

`xlk_sector_zscore_ret60`은 향후 더 큰 종목 표본에서 재검증할 보조 후보로 남길 수 있지만, AAP·POWI 2종목만으로 production feature에 넣을 근거는 부족하다.

## 10. 다음 데이터 소스 우선순위

1. **종목 횡단면 정보**
   - 동일 날짜 유니버스 내 종목 모멘텀 순위
   - 거래대금·유동성 순위
   - 산업 내 상대강도
   - breadth와 dispersion

2. **실적·펀더멘털 context**
   - 실적 surprise
   - 매출·마진 변화
   - 추정치 revision
   - earnings proximity

3. **기업 이벤트 context**
   - 공시·가이던스 변화
   - analyst revision
   - corporate action

다음 단계는 regime feature를 더 가공하는 것이 아니라, 시장 공통값이 아닌 **종목별 횡단면 또는 기업 고유 정보**로 이동하는 것이 타당하다.
