# 상대 정규화 feature 정보량 조사

## 먼저 볼 숫자

# **신규 상대 feature Top-5 MI 합: 0.0302 bits**

비교 기준:

```text
기존 14개 + L2: 0.1290 bits
기존 14개 + L0: 0.2559 bits
신규 상대 14개 + L2: 0.0302 bits
```

# 최종 판정: **RELATIVE_FEATURE_NOHELP**

검증 질문에 대한 답은 **NO**다.

```text
상대 정규화 feature가 L2 라벨에 대해 기존 14개 대비 MI/상관을 회복하지 못했다.
```

신규 feature Top-5 MI 합은 기존 L2의 23.39%, L0 기준선의 11.79%에 불과했다. Pearson 절대상관 0.10 이상 feature도 0개였다.

따라서 현재 5일 가격·변동성 데이터에서 단위만 ATR·RV·종목 내부 percentile로 바꾸는 방식은 상대 상승 예측 신호를 복원하지 못했다. 다음 단계는 동일 feature로 L2 GA를 실행하는 것이 아니라, 더 긴 window와 외부·상대 데이터 소스를 검토하는 것이다.

## 분석 표본

- 종목: 50
- 종목당 행: 1,495
- 총 행: 74,750
- 기간: 2020-07-15 ~ 2026-07-01
- L2 positive rate: 35.84%
- 기존 L0 라벨 재계산 불일치: 0건
- D-1 cutoff: 유지
- GA·학습: 미실행

신규 feature의 `RV20[D-1]/RV20[D-6]` 때문에 직전 75,000행보다 종목당 5행이 줄었다. 공정 비교를 위해 기존 14개 L0·L2도 동일한 74,750행에서 다시 계산했다.

## 3-way 정보량 비교

| Feature 세트 | 라벨 | 기존 benchmark Top-5 MI | 동일 표본 Top-5 MI | 최대 단일 MI | |corr|≥0.10 | 종목 내 rank Top-5 MI |
|---|---|---:|---:|---:|---:|---:|
| 기존 14개 | L0 고정 +3% | 0.2559 | 0.2562 | 0.1000 | 6 | 0.0690 |
| 기존 14개 | L2 RV20 상대 | 0.1290 | 0.1291 | 0.0931 | 0 | 0.0605 |
| **신규 상대 14개** | **L2 RV20 상대** | — | **0.0302** | **0.0085** | **0** | **0.0165** |

동일 표본에서 기존 L2는 0.1291 bits로 직전 0.1290을 재현했다. 따라서 신규 feature의 저하는 표본 기간 차이 때문이 아니다.

### 기준 대비 변화

```text
신규 vs 기존 L2 Top-5 MI: -76.61%
신규 / 기존 L2: 23.39%
신규 / L0 기준선: 11.79%
```

`RELATIVE_FEATURE_PROMISING` 기준으로 둔 기존 L2 대비 +10% material threshold는 0.1419 bits였다. 신규 값은 이 기준의 약 21%다.

## 신규 feature별 결과

| 순위 | Feature | 범주 | Pearson | MI bits |
|---:|---|---|---:|---:|
| 1 | `close_pos5_history_pctile` | 내부 percentile | -0.0326 | 0.00850 |
| 2 | `rv5_to_rv20` | 변동성 변화 | +0.0379 | 0.00649 |
| 3 | `pullback5_atr_history_pctile` | 내부 percentile | +0.0372 | 0.00648 |
| 4 | `max_up_day5_atr14` | 상대 surge | -0.0460 | 0.00443 |
| 5 | `true_range_d1_atr14` | 상대 range | +0.0342 | 0.00428 |
| 6 | `atr14_change5_pct` | 변동성 변화 | +0.0127 | 0.00406 |
| 7 | `rv20_ratio_d1_d6` | 변동성 변화 | -0.0397 | 0.00384 |
| 8 | `avg_true_range5_atr14` | 상대 range | +0.0211 | 0.00332 |
| 9 | `range5_atr_history_pctile` | 내부 percentile | +0.0121 | 0.00286 |
| 10 | `max_down_day5_atr14` | 상대 downside | -0.0061 | 0.00240 |
| 11 | `net_move5_atr14` | 상대 path | -0.0330 | 0.00109 |
| 12 | `pullback5_atr14` | 상대 pullback | +0.0305 | 0.00000 |
| 13 | `fade_after_surge_atr14` | 상대 surge-fade | -0.0145 | 0.00000 |
| 14 | `range5_atr14` | 상대 range | +0.0018 | 0.00000 |

최대 절대상관도 0.0460에 그쳤다. 단일 feature 중 기존 L2의 `single_up_day5_pct` 0.0931 bits에 근접한 항목이 하나도 없었다.

## 종목 내부 rank 민감도

신규 feature를 다시 종목 내부 full-sample percentile rank로 바꾼 사후 민감도에서도 Top-5 MI 합은 0.0165 bits였다.

```text
신규 raw Top-5 MI: 0.0302
신규 within-ticker-rank Top-5 MI: 0.0165
기존 L2 within-ticker-rank Top-5 MI: 0.0605
```

따라서 종목 간 scale 차이가 신규 feature의 신호를 가리고 있었다는 설명도 지지되지 않는다.

실제 feature로 사용한 내부 percentile은 미래를 쓰지 않는 인과적 expanding percentile이다. `within_ticker_rank_*`는 분석 민감도 열일 뿐 라이브 feature가 아니다.

## 왜 정보량이 더 줄었는가

직전 조사에서는 고정 +3% 라벨의 강한 정보가 절대 변동성 수준에 크게 의존한다는 사실이 확인됐다. 이번에는 그 절대 변동성 효과를 제거하기 위해 path와 range를 ATR 배수로 바꾸고, 변동성 변화율과 종목 내부 percentile을 사용했다.

그러나 결과는 다음과 같다.

- ATR 배수 pullback·range 자체에는 거의 신호가 없음
- RV20 변화와 RV5/RV20에도 약한 정보만 존재
- 인과적 내부 percentile도 최대 0.0085 bits
- 5일 path의 방향·순서 정보가 L2 도달 여부를 충분히 분리하지 못함

즉 변동성 착시를 제거하고 남은 5일 자기 가격 정보에는 강한 상대 상승 예측 신호가 확인되지 않았다.

## 상대강도 feature

```text
상태: NOT_AVAILABLE
확인한 proxy: SPY, QQQ, IWM, VTI
```

Frozen OHLCV snapshot에 시장·섹터 proxy가 없었다. 50종목 평균을 임의 시장으로 사용하면 동일 universe로 자기 자신을 정규화하는 순환 구조가 되므로 사용하지 않았다.

따라서 이번 `NOHELP` 판정은 다음 범위에 한정된다.

```text
5일 자기 종목 OHLCV + ATR/RV 정규화 + 자기 이력 percentile
```

시장·섹터 상대강도나 외부 catalyst 데이터까지 무효하다는 뜻은 아니다.

## 판정 기준 적용

### RELATIVE_FEATURE_PROMISING 조건

```text
Top-5 MI > 기존 L2 0.1290을 유의미하게 초과
AND |corr| >= 0.10 feature 3개 이상
```

실제:

```text
Top-5 MI = 0.0302
|corr| >= 0.10 = 0개
```

모두 미충족이다.

### RELATIVE_FEATURE_MARGINAL이 아닌 이유

약간의 개선도 없었다. 기존 L2 대비 76.61% 감소했다.

따라서 `RELATIVE_FEATURE_NOHELP`가 적합하다.

## 다음 단계 제언

현재 5일 자기 가격·변동성 데이터만으로 상대 상승 타겟을 예측하는 방향은 중단하는 것이 타당하다.

다음 데이터 확장은 **[추정]**상 우선순위가 다음과 같다.

1. 20·60·120일 regime: 장기 추세, 변동성 percentile, drawdown 위치, trend persistence
2. 시장·섹터 proxy: SPY·QQQ·IWM 및 업종 ETF 대비 5·20일 상대강도
3. cross-sectional rank: 동일 날짜 universe 내 수익률·변동성·거래량 순위
4. catalyst 데이터: 실적일, 뉴스·공시, gap history, 이벤트 전후 거래량
5. 옵션·short interest·borrow 또는 analyst revision 등 외부 데이터

다음 실험은 GA가 아니라 먼저 각 데이터 축의 단변량 MI와 시간 분할 안정성을 조사해야 한다. 신규 데이터에서도 L2 정보량이 회복되지 않으면 2일 도달형 binary label 자체를 다시 검토해야 한다.
