# 상대 정규화 feature 정의

## 시점 계약

모든 feature는 D0 진입 판단 시점에 이미 완료된 D-1 bar까지만 사용한다.

계산 순서:

```text
1. OHLCV bar t에서 feature 계산
2. 종목 내부 과거 분포 percentile도 t까지의 값만 사용
3. 결과를 1세션 shift해 D0 행에 D-1 feature로 부착
```

D0 open·high·low·close·volume은 feature 계산에 사용하지 않는다.

ATR14는 Wilder 방식과 동일한 다음 EWM을 사용한다.

```text
TR[t] = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
ATR14[t] = EWM(TR, alpha=1/14, adjust=False)
```

실현변동성은 일간 close-to-close 수익률의 표본표준편차(`ddof=1`)다.

## 신규 feature 14개

| feature | 구분 | 정의 |
|---|---|---|
| `pullback5_atr14` | 상대 pullback | `(max High[D-5:D-1] - Close[D-1]) / ATR14[D-1]` |
| `max_up_day5_atr14` | 상대 surge | `max(max(Close[t]-Close[t-1],0), 최근 5세션) / ATR14[D-1]` |
| `max_down_day5_atr14` | 상대 downside | `max(max(Close[t-1]-Close[t],0), 최근 5세션) / ATR14[D-1]` |
| `net_move5_atr14` | 상대 path | `(Close[D-1]-Close[D-6]) / ATR14[D-1]` |
| `fade_after_surge_atr14` | 상대 surge-fade | `max_up_day5_atr14 × pullback5_atr14` |
| `true_range_d1_atr14` | 상대 range | `TR[D-1] / ATR14[D-1]` |
| `range5_atr14` | 상대 range | `(max High[D-5:D-1]-min Low[D-5:D-1]) / ATR14[D-1]` |
| `avg_true_range5_atr14` | 상대 range | `mean(TR[D-5:D-1]) / ATR14[D-1]` |
| `rv20_ratio_d1_d6` | 변동성 변화 | `RV20[D-1] / RV20[D-6]` |
| `rv5_to_rv20` | 변동성 변화 | `RV5[D-1] / RV20[D-1]` |
| `atr14_change5_pct` | 변동성 변화 | `100 × (ATR14[D-1]/ATR14[D-6]-1)` |
| `close_pos5_history_pctile` | 내부 percentile | `close_pos5[D-1]`의 종목별 인과적 expanding percentile |
| `pullback5_atr_history_pctile` | 내부 percentile | `pullback5_atr14[D-1]`의 종목별 인과적 expanding percentile |
| `range5_atr_history_pctile` | 내부 percentile | `range5_atr14[D-1]`의 종목별 인과적 expanding percentile |

## 내부 percentile 계약

각 시점의 percentile은 그 종목의 snapshot 시작 이후 현재 D-1까지 관측된 값만 사용한다.

```text
percentile = 현재 값을 포함한 expanding distribution의 average-rank / 관측 수
minimum history = 20세션
```

미래 데이터 전체를 이용한 full-sample percentile은 실제 feature에 사용하지 않는다.

`relative_feature_information.csv`의 `within_ticker_rank_*` 열은 feature 값의 종목 간 scale 민감도를 확인하기 위한 사후 통계다. 이것은 실제 feature가 아니라 정보량 민감도 검사다.

## L2 라벨

직전 상대 라벨 조사와 동일하다.

```text
max(High[D+1], High[D+2])
>= Open[D0] × (1 + sqrt(2) × RV20[D-1] / 100)
```

일간 RV20을 `sqrt(2)`로 2세션 환산하는 해석은 **[추정]**이다.

## 상대강도 feature

```text
상태: NOT_AVAILABLE
이유: frozen snapshot에 SPY·QQQ·IWM·VTI 또는 섹터 proxy OHLCV가 없음
```

50종목 동일가중 평균을 임의 시장 proxy로 만들면 조사 universe 자체를 이용한 순환 feature가 되므로 사용하지 않았다.
