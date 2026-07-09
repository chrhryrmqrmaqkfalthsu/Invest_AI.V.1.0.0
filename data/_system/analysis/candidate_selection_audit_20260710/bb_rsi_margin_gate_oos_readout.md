# BB+RSI 단독 통과 차단 및 margin 컷 gate OOS 검증 readout

범위: 라이브 코드·설정·주문 변경 없음. 재학습 없음. 기존 `oos_reproduce_frozen_20260707` 정본 거래 CSV와 기존 룰북을 사용해, 신호일의 `evaluate_signal` 컴포넌트만 재현하고 gate를 선별 단계 후처리로 적용했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/bb_rsi_margin_gate_oos_comparison.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/bb_rsi_margin_gate_blocked_stats.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/bb_rsi_margin_gate_oos_readout.md`

## 0. 방법과 검증 정확도

기준 입력:

- frozen trades: `data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv`
- frozen candidates: `data/_system/analysis/oos_reproduce_frozen_20260707/candidate_universe.json`
- frozen OHLCV: `data/_system/analysis/ohlc_snapshot_20260707/*_ohlcv.csv`
- 기존 룰북: `candidate_universe.json`의 각 candidate가 가리키는 full rulebook

컴포넌트 재현 품질:

| 항목 | 값 |
| --- | ---: |
| total trade rows | `43,972` |
| component rows ok | `43,972` |
| component rows error | `0` |
| candidate errors | `0` |
| score diff abs mean vs CSV | `2.63e-12` |
| score diff abs p95 vs CSV | `4.86e-12` |
| score diff abs max vs CSV | `4.99e-11` |

따라서 gate 판정에 사용한 `BB/RSI/MA/MACD/volume/events/news` 컴포넌트는 frozen CSV의 `entry_signal_score`와 사실상 동일한 평가 경로에서 재현됐다.

포트폴리오 지표:

- `CAGR`, `Sharpe`, `MDD`는 S2 K=20 priority-score 방식의 daily equity curve로 계산했다.
- 이 방식은 기존 연구 스크립트의 K=20 slot simulation 계열과 맞췄다.
- `avg PnL`, `win rate`, `trade_count`는 trade-level 전체 신호 기준이다.
- `accepted_signals/skipped_signals`는 K=20 포트폴리오 시뮬레이션에서 실제 슬롯에 들어간 수와 초과로 skip된 수다.

주의: `oos_reproduce_frozen` 자체는 93개 candidate를 독립 순회한 trade table이고 전역 K 제한이 없다. 따라서 trade-level 지표와 K=20 portfolio 지표가 다르게 움직일 수 있다.

## 1. 베이스라인 확정

| split | trades | avg PnL % | win % | sum PnL % | CAGR % | Sharpe | MDD % | K20 accepted | K20 skipped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| IS | `31,057` | `1.052123` | `54.1875` | `32,675.7689` | `-7.5931` | `-0.1263` | `-59.5639` | `1,808` | `29,249` |
| OOS | `12,915` | `3.099643` | `61.3395` | `40,031.8894` | `88.3028` | `2.0723` | `-18.4961` | `761` | `12,154` |

이 값이 이후 gate 비교의 기준이다.

## 2. Gate A — BB+RSI 단독 통과 차단

### Gate A 정의

운영형 Gate A:

```text
block if:
  BB > 0
  RSI > 0
  volume == 0
  MACD == 0
  MA_align == 0
```

뉴스/events는 “추세·거래량 확인”이 아니므로 차단 조건에서 confirmation으로 인정하지 않았다. 이 정의를 써야 CE처럼 `events`가 아주 작게 붙은 BB+RSI 턱걸이도 잡힌다.

참고로 literal-only 정의도 별도 blocked stats에 계산했다.

```text
literal-only block if:
  BB > 0 and RSI > 0
  MA/MACD/volume/events/news/news_topics all == 0
```

literal-only는 OOS CE를 0건 차단해 CE 유형을 놓치므로, 본 검증의 Gate A 성과 판단에는 운영형 Gate A를 사용했다.

### Gate A 성과

| split | trades | blocked | avg PnL % | win % | CAGR % | Sharpe | MDD % | verdict basis |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| IS baseline | 31,057 | 0 | 1.0521 | 54.1875 | -7.5931 | -0.1263 | -59.5639 | baseline |
| IS Gate A | 21,680 | 9,377 | 1.0338 | 54.3404 | -1.6399 | 0.0738 | -48.8560 | IS improves portfolio, trade avg slightly worse |
| OOS baseline | 12,915 | 0 | 3.0996 | 61.3395 | 88.3028 | 2.0723 | -18.4961 | baseline |
| OOS Gate A | 10,282 | 2,633 | 3.0627 | 61.1651 | 96.3623 | 2.2276 | -19.6696 | CAGR/Sharpe up, MDD worse |

Gate A OOS delta:

| metric | delta |
| --- | ---: |
| CAGR | `+8.0596 pp` |
| Sharpe | `+0.1554` |
| MDD | `-1.1735 pp` worse |
| avg PnL | `-0.0369 pp` worse |
| win rate | `-0.1744 pp` worse |

Gate A blocked trades:

| split | blocked | blocked avg PnL % | blocked win % | CE blocked | BOIL blocked |
| --- | ---: | ---: | ---: | ---: | ---: |
| IS | 9,377 | `+1.0945` | 53.8338 | 87 | 179 |
| OOS | 2,633 | `+3.2439` | 62.0205 | 36 | 102 |

판정:

```text
Gate A verdict: OOS_DEGRADED
```

이유: OOS CAGR/Sharpe는 좋아졌지만, MDD가 `-18.50% → -19.67%`로 악화됐고, 차단된 OOS 거래의 원래 평균 PnL이 `+3.24%`로 baseline 평균보다 높았다. 즉 Gate A는 CE/BOIL 유형을 많이 막지만, frozen OOS에서는 손실군을 선택적으로 제거하지 못했다.

## 3. Gate B — margin 컷 sweep

Gate B 정의:

```text
block if final_score / threshold < x
x in {1.05, 1.10, 1.15, 1.25}
```

### Gate B 단독 성과

| scenario | split | trades | blocked | avg PnL % | win % | CAGR % | Sharpe | MDD % | OOS verdict |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | IS | 31,057 | 0 | 1.0521 | 54.1875 | -7.5931 | -0.1263 | -59.5639 | - |
| baseline | OOS | 12,915 | 0 | 3.0996 | 61.3395 | 88.3028 | 2.0723 | -18.4961 | - |
| GateB ratio>=1.05 | IS | 29,242 | 1,815 | 1.0162 | 54.1037 | -7.3879 | -0.1183 | -59.2594 | OOS_NEUTRAL |
| GateB ratio>=1.05 | OOS | 12,272 | 643 | 3.1309 | 61.2370 | 88.3028 | 2.0723 | -18.4961 | OOS_NEUTRAL |
| GateB ratio>=1.10 | IS | 27,345 | 3,712 | 1.0110 | 53.8636 | -8.1891 | -0.1479 | -59.5619 | OOS_NEUTRAL |
| GateB ratio>=1.10 | OOS | 11,486 | 1,429 | 3.1220 | 61.2572 | 88.3028 | 2.0723 | -18.4961 | OOS_NEUTRAL |
| GateB ratio>=1.15 | IS | 24,657 | 6,400 | 1.0236 | 53.6521 | -8.0977 | -0.1430 | -60.1100 | OOS_DEGRADED |
| GateB ratio>=1.15 | OOS | 10,507 | 2,408 | 3.1569 | 61.2925 | 87.2583 | 2.0078 | -20.8353 | OOS_DEGRADED |
| GateB ratio>=1.25 | IS | 20,192 | 10,865 | 0.9930 | 53.6153 | -8.3196 | -0.1528 | -55.2858 | OOS_DEGRADED |
| GateB ratio>=1.25 | OOS | 9,006 | 3,909 | 3.2048 | 61.6478 | 93.6785 | 2.0999 | -19.6562 | OOS_DEGRADED |

### Gate B blocked trade 원래 성과

| gate | split | blocked | blocked avg PnL % | blocked win % | CE blocked | BOIL blocked |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| ratio < 1.05 | IS | 1,815 | 1.6301 | 55.5372 | 20 | 7 |
| ratio < 1.05 | OOS | 643 | 2.5040 | 63.2970 | 2 | 4 |
| ratio < 1.10 | IS | 3,712 | 1.3552 | 56.5733 | 38 | 14 |
| ratio < 1.10 | OOS | 1,429 | 2.9197 | 62.0014 | 23 | 8 |
| ratio < 1.15 | IS | 6,400 | 1.1622 | 56.2500 | 110 | 20 |
| ratio < 1.15 | OOS | 2,408 | 2.8497 | 61.5449 | 49 | 13 |
| ratio < 1.25 | IS | 10,865 | 1.1620 | 55.2508 | 110 | 40 |
| ratio < 1.25 | OOS | 3,909 | 2.8573 | 60.6293 | 66 | 31 |

### Gate B 해석

- `1.05`, `1.10`은 OOS K20 포트폴리오 CAGR/Sharpe/MDD가 baseline과 동일했다. 차단된 낮은-margin OOS 신호들이 대부분 K20 포트폴리오에서 원래도 skip되었기 때문이다. 판정은 `OOS_NEUTRAL`.
- `1.15`는 OOS CAGR, Sharpe, MDD가 모두 악화했다. `OOS_DEGRADED`.
- `1.25`는 OOS CAGR/Sharpe/trade avg는 좋아졌지만 MDD가 `-18.50% → -19.66%`로 1.16pp 악화했고, IS CAGR/Sharpe도 나빠졌다. 리스크 개선 없는 trade-off라 `OOS_DEGRADED`로 판정했다.

## 4. 커브 피팅 점검

| scenario | IS CAGR delta | OOS CAGR delta | IS Sharpe delta | OOS Sharpe delta | IS MDD delta | OOS MDD delta | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Gate A | +5.9533 | +8.0596 | +0.2000 | +0.1554 | +10.7080 | -1.1735 | OOS_DEGRADED |
| Gate B 1.05 | +0.2052 | +0.0000 | +0.0080 | +0.0000 | +0.3045 | +0.0000 | OOS_NEUTRAL |
| Gate B 1.10 | -0.5960 | +0.0000 | -0.0216 | +0.0000 | +0.0021 | +0.0000 | OOS_NEUTRAL |
| Gate B 1.15 | -0.5046 | -1.0445 | -0.0167 | -0.0644 | -0.5461 | -2.3393 | OOS_DEGRADED |
| Gate B 1.25 | -0.7265 | +5.3757 | -0.0265 | +0.0276 | +4.2781 | -1.1601 | OOS_DEGRADED |

판정 기준:

- `OOS_IMPROVED`: OOS CAGR와 Sharpe가 모두 개선되고, MDD가 0.5pp 이상 악화되지 않음.
- `OOS_NEUTRAL`: OOS 포트폴리오 변화가 거의 없거나 개선·악화가 혼재하되 악화폭이 제한적임.
- `OOS_DEGRADED`: OOS CAGR/Sharpe가 악화하거나, 수익 개선이 있어도 MDD가 1pp 이상 악화됨.

이 기준에서 Gate A/B 단독 중 `OOS_IMPROVED`는 없다.

## 5. A+B 조합

원칙상 OOS가 개선된 단독 gate만 조합을 볼 예정이었으나, 확인 차원에서 A+B 조합도 sweep했다. 모두 OOS MDD가 악화되어 reject다.

| scenario | OOS trades | OOS avg PnL % | OOS CAGR % | OOS Sharpe | OOS MDD % | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 12,915 | 3.0996 | 88.3028 | 2.0723 | -18.4961 | - |
| A+B 1.05 | 9,730 | 3.0917 | 90.8455 | 2.1662 | -20.6829 | OOS_DEGRADED |
| A+B 1.10 | 9,174 | 3.0979 | 90.7409 | 2.1589 | -20.3430 | OOS_DEGRADED |
| A+B 1.15 | 8,412 | 3.1152 | 90.1020 | 2.1492 | -19.6099 | OOS_DEGRADED |
| A+B 1.25 | 7,246 | 3.1644 | 90.0449 | 2.1389 | -21.2134 | OOS_DEGRADED |

A+B는 거래 수를 많이 줄이고 CAGR/Sharpe 일부는 올리지만, MDD를 악화시키므로 라이브 권고 불가다.

## 6. CE·BOIL 유형 차단 효과

Gate A operational은 OOS에서 CE 36건, BOIL 102건을 차단했다. 그러나 이 차단군 전체의 원래 평균 PnL은 `+3.2439%`, win rate는 `62.0205%`였다.

Gate B margin 컷은 threshold가 높아질수록 CE/BOIL을 더 많이 차단한다.

| gate | OOS CE blocked | OOS BOIL blocked | OOS blocked avg PnL % |
| --- | ---: | ---: | ---: |
| Gate A | 36 | 102 | +3.2439 |
| ratio < 1.05 | 2 | 4 | +2.5040 |
| ratio < 1.10 | 23 | 8 | +2.9197 |
| ratio < 1.15 | 49 | 13 | +2.8497 |
| ratio < 1.25 | 66 | 31 | +2.8573 |

결론: CE·BOIL 유형을 막는 데에는 성공하지만, frozen OOS 전체에서는 이 유형이 일관된 손실군으로 검증되지 않았다. 특히 BOIL 과거 OOS의 일부는 수익 trade로 남아 있어 단순 BB+RSI 차단이 성과를 보장하지 않는다.

## 7. 최종 권고

최종 판정:

```text
Gate A: REJECT / OOS_DEGRADED
Gate B 1.05: REJECT_FOR_LIVE / OOS_NEUTRAL
Gate B 1.10: REJECT_FOR_LIVE / OOS_NEUTRAL
Gate B 1.15: REJECT / OOS_DEGRADED
Gate B 1.25: REJECT / OOS_DEGRADED
A+B 조합: REJECT / OOS_DEGRADED
```

라이브 적용 권고:

```text
현재 검증 결과만으로는 라이브 적용 권고 없음.
```

이유:

1. Gate A는 CE·BOIL 유형을 막지만, OOS에서 차단된 거래가 평균적으로 수익성이 있었다.
2. Gate B 낮은 컷(1.05/1.10)은 OOS 포트폴리오에 실질 개선이 없었다.
3. Gate B 높은 컷(1.15/1.25)은 거래 수를 줄이고 일부 평균 PnL을 높이지만, OOS MDD 또는 IS 성능이 악화했다.
4. A+B 조합도 OOS MDD가 baseline보다 악화했다.

다음 후보는 단순 BB+RSI/margin 컷이 아니라, 이번 검증에서 손실군으로 확인된 별도 축을 찾아야 한다. 예를 들면 `실전 deny-list`, `rule_hash별 exit 품질`, `HIGH_VOL인데 volume weight=0`, `sector fallback 데이터 품질`, `stop/time_out 과다 룰 제외`처럼 CE·BOIL의 실제 손실 원인에 더 가까운 축을 별도로 검증하는 편이 낫다.
