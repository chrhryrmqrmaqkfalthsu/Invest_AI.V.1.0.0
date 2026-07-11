# ANET·BB·CE 진입별 신호 구성 상세 분석

## 범위와 신뢰도

기존 재실행 산출물만 읽었다. 엔진·설정·라이브·주문·재학습 데이터 변경은 없다. 재실행 진입 195건 전량을 지표별 1행으로 펼쳤다: ANET 79, BB 41, CE 75. 누락·중복은 0건이다.

신뢰도는 A=CE `train_2`·`recent_1y` 구간 완전 일치 36건, B=불일치 구간 내 거래 완전 일치 110건, C=신호·진입은 같고 청산/pnl만 다른 31건, D=재실행 전용·부분 불일치 18건이다. C등급의 높은 평균 pnl은 신호 효과가 아니라 청산 차이의 영향일 수 있으므로 A등급을 우선한다.

## 기여값 구조

MA·MACD·RSI·BB·Volume은 한 룰 안에서 발화 시 고정값이었다. 따라서 기술지표는 강약이 아니라 발화 여부와 조합으로 분석했다. 연속값은 NewsTopics와 ratio다. News와 Event는 195건 모두 0, market adjustment는 전 건 1.0이었다.

## 신뢰 높은 CE 36건

평균 pnl -2.84%, 승률 33.33%. 발화는 RSI 35건, BB 25건, MA 19건, NewsTopics 16건, MACD 8건, Volume 3건이었다.

| 조합 | 건수 | 평균 pnl | 승률 |
|---|---:|---:|---:|
| MA+RSI+BB | 9 | -1.78% | 33.33% |
| RSI+BB+NewsTopics | 7 | -3.57% | 28.57% |
| MA+RSI+BB+NewsTopics | 4 | -4.60% | 25.00% |
| MACD+RSI | 3 | -9.59% | 0.00% |
| MACD+RSI+BB | 3 | +6.27% | 100.00% |
| RSI+BB+Volume | 2 | -5.92% | 0.00% |

같은 MACD+RSI에서도 BB가 함께 발화한 3건은 전부 이익, BB가 없는 3건은 전부 손실이었다. Top2나 몰빵 여부만으로 묶으면 이 차이가 사라진다. 소수 발화 1~2개는 6건 평균 -6.30%, 승률 16.67%, 3개 이상은 30건 평균 -2.15%, 승률 36.67%였다.

## NewsTopics와 ratio

NewsTopics 양수 발화 평균/비발화 평균은 ANET +1.82%/+3.10%, BB -0.20%/+1.56%, CE 전체 -3.24%/-0.02%, CE 완전 일치 -3.84%/-2.04%였다. 양수 발화 안의 중앙값 기준 약/강 절반은 ANET +4.21%/-0.56%, BB -1.71%/+1.52%, CE 완전 일치 -6.52%/-1.16%였다. 룰마다 방향이 달라 단일 전역 강도 컷을 지지하지 않는다.

ratio의 Pearson/Spearman은 ANET +0.037/+0.094, BB -0.068/-0.164, CE +0.079/+0.057, CE 완전 일치 +0.229/+0.113이었다. CE 완전 일치 사분위 평균도 -6.54%, +1.17%, -4.50%, -1.50%로 비단조적이다. `ratio ≤ 1.15`만으로 전체 성과를 설명할 수 없다.

## 세 룰 차이

| 룰 | 평균 pnl | 중앙값 | 승률 | 평균 발화 수 | MA | MACD | RSI | BB | Volume | NewsTopics |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ANET | +2.42% | +1.28% | 72.15% | 2.39 | 58.23% | 12.66% | 87.34% | 25.32% | 2.53% | 53.16% |
| BB | +0.91% | -5.17% | 39.02% | 2.78 | 29.27% | 34.15% | 100% | 60.98% | 17.07% | 36.59% |
| CE | -0.87% | -0.86% | 44.00% | 2.76 | 44.00% | 18.67% | 90.67% | 81.33% | 14.67% | 26.67% |

BB는 MACD·BB·Volume 발화가 상대적으로 많다. 그러나 BB `MACD+RSI+BB` 7건 평균 +9.45%는 중앙값 -0.32%, 승률 42.86%이며 +71% 한 건의 영향을 크게 받았다. BB 전체도 평균은 양수지만 중앙값 -5.17%이고 모든 구간이 재현 불일치다. BB의 반대 결과를 구조적 우위로 단정할 수 없다.

## 결론

- 같은 몰빵 정도라도 실제 발화 조합에 따라 성과가 갈렸다.
- 완전 일치 CE에서는 BB 보강 여부가 MACD+RSI 사례를 정반대로 구분했다.
- ratio는 연속값으로도 강한 단조 관계가 없었다.
- 고정 가중 기술지표는 강도보다 발화 조합이 핵심이고 NewsTopics만 연속 강도 분석이 가능했다.
- 결과는 룰 3개와 작은 조합별 표본의 관찰이며 통계적 일반화나 자동 차단 기준이 아니다.

세부 산출물은 `signal_component_entries_*.csv`, `indicator_combo_performance_*.csv`, `indicator_trigger_tags_*.csv`, `indicator_activation_performance.csv`, `news_topics_strength_performance.csv`, `ratio_continuous_relationship.csv`, `ratio_quartile_performance.csv`, `rule_signal_composition_difference.csv`, `reproduction_confidence_performance.csv`, `signal_component_entry_file_index.csv`이다.
