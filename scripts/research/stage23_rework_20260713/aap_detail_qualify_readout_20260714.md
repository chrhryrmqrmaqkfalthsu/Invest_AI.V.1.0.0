# AAP 상세 qualify 재실행 readout

- 실행 범위: AAP 단일 종목
- 실행 규모: **축소 qualify population 40 / generations 15**
- seed base: `2026071301`
- 실행 코드 커밋: `02d340cade4353dfcf2ddca013f6b15969805576`
- unique 후보: 119
- cross-fold 평가: 119 후보 × train_1/2/3 = 357행
- qualify 결과: 실패 (`all3=0`), Entry 이후 단계 미실행

## 최종 판정

**`MIXED — train_2는 CAUSE_A_TOO_TIGHT 성격, train_1은 CAUSE_B_INFO_LACK 성격`**

두 원인 중 하나로 강제하면 실제 로그를 왜곡한다.

- `CAUSE_A_TOO_TIGHT`의 근거는 train_2에 집중된다. 거래수 중앙값이 4건이고 119개 중 67개(56.30%)가 support 하한 5건에 미달했다.
- `CAUSE_B_INFO_LACK`의 근거는 train_1에 나타난다. train_2+train_3을 통과한 all2 후보 28개는 train_1에서 거래가 15~18건으로 충분했지만 기대값 중앙값이 -0.9624%였고 최고도 -0.2096%였다.
- 따라서 AAP 전체를 POWI와 동일한 순수 B형으로 볼 수 없지만, 단순 interval 완화만으로 해결되는 순수 A형도 아니다.
- 운영 결론은 **train_2의 진입 interval/support 병목을 표적 완화할 여지는 있으나, 청산 로직의 광범위 완화는 금지**다. 전체 후보×fold MDD의 61.03%가 유형2(누적/방치)이기 때문이다.

## 1. Cross-fold 통과 근접도

| 통과 fold 수 | 후보 수 |
|---|---:|
| all3 | 0 |
| all2 | 53 |
| all1 | 55 |
| all0 | 11 |

all2 53개의 조합:

| 통과 조합 | 후보 수 | 실패 fold | 실패 특징 |
|---|---:|---|---|
| train_2 + train_3 | 28 | train_1 | 27개 기대값 단독 실패, 1개 member score+기대값 실패 |
| train_1 + train_3 | 25 | train_2 | 24개 support 미달, 25개 전부 기대값 실패 |
| train_1 + train_2 | 0 | train_3 | 해당 없음 |

all1 55개의 분포는 train_3만 통과 44개, train_2만 통과 6개, train_1만 통과 5개다. 모든 all2가 train_3을 포함하므로 최신 fold에서의 적합성은 높지만 과거 fold 재현성이 부족하다.

### all2 실패 fold 상세

- train_1 실패 28개: 거래수 15~18건(중앙값 18), 기대값 -1.0908%~-0.2096%(중앙값 -0.9624%), 승률 중앙값 50.0%. 신호 부족이 아니라 성과 붕괴다.
- train_2 실패 25개: 거래수 3~5건(중앙값 3), 기대값 -3.1869%~1.1963%(중앙값 -1.4789%), 승률 중앙값 33.33%. support 고갈과 성과 저하가 동시에 발생했다.

## 2. 전체 후보 거래 수 분포

| fold | min | p25 | 중앙값 | p75 | max | 평균 | 5건 미달 |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 5 | 10 | 11 | 17 | 19 | 12.78 | 0 / 119 (0.00%) |
| train_2 | 1 | 4 | 4 | 10 | 12 | 6.00 | 67 / 119 (56.30%) |
| train_3 | 4 | 11 | 12 | 14 | 16 | 11.71 | 1 / 119 (0.84%) |

train_2만 명확한 support 병목이다. train_1과 train_3의 거래수는 전반적으로 충분하다.

전체 357개 후보-fold 실패 metric 누계:

- 기대값 미달: 191
- 거래수 미달: 68
- member score 미달: 25

## 3. Fold-best 성과와 청산

| fold | 거래 | 승률 | 기대값 | MDD | MDD 유형 | interval-break |
|---|---:|---:|---:|---:|---|---:|
| train_1 | 10 | 90.00% | 3.292300% | -1.148367% | 유형1 | 10 / 10 |
| train_2 | 12 | 75.00% | 3.450180% | -3.192157% | 유형1 | 12 / 12 |
| train_3 | 12 | 66.67% | 5.505547% | -10.889392% | 유형1 | 11 / 12 |

fold-best의 interval-break 청산은 **33/34 = 97.06%**다. 나머지 1건은 train_3의 ATR stop이다. 지난 baseline의 26/27과 동일하게 provisional exit가 사실상 interval-break에 지배된다.

fold-best MDD 발생 거래:

- train_1: 2022-09-15 진입 → 2022-09-19 청산, interval-break, 2일, -1.1484%
- train_2: 2023-08-21 진입 → 2023-08-23 청산, interval-break, 2일, -3.1922%
- train_3: 2025-04-03 진입 → 2025-04-07 청산, ATR stop, 2일, -10.8894%

각 fold-best MDD는 단발 손실인 유형1이다.

## 4. 전체 후보 MDD 유형

분류 규칙:

- 유형1(사고): MDD episode의 손실 거래가 1개이고 해당 손실 보유일이 7일 미만
- 유형2(방치): MDD episode의 손실 거래가 2개 이상이거나 손실 거래 중 보유일 7일 이상
- 무낙폭: 기존 backtest MDD 계산식 기준 MDD가 0 이상

| 유형 | 후보-fold 수 | 낙폭 발생분 비율 |
|---|---:|---:|
| 유형1 사고 | 136 | 38.97% |
| 유형2 방치 | 213 | 61.03% |
| 무낙폭 | 8 | - |

fold별 유형2 비율:

- train_1: 83/117 = 70.94%
- train_2: 45/113 = 39.82%
- train_3: 85/119 = 71.43%

fold-best만 보면 유형1이지만 전체 후보에서는 유형2가 우세하다. 따라서 interval-break 비율이 높다는 이유만으로 exit 조건을 일괄 완화하면 손실 누적 위험을 키울 수 있다.

## 5. Feature 병목과 strict-AND

| fold | strict 통과율 | ma_trend | macd_hist | rsi | bb_position | volume_ratio | 최저 feature |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 17.13% | 43.43% | 96.02% | 43.82% | 59.36% | 78.49% | ma_trend |
| train_2 | 12.80% | 63.60% | 66.40% | 36.00% | 52.40% | 50.40% | rsi |
| train_3 | 10.80% | 59.60% | 57.60% | 44.40% | 27.60% | 56.40% | bb_position |

병목 feature가 fold마다 달라 단일 feature 제거만으로 일반화 문제가 해결된다는 근거는 없다. `ma_trend`는 train_1의 최저 feature지만 train_2·3에서는 주 병목이 아니다.

quality score가 threshold 이상인데 strict-AND로 차단된 날은 train_1/2/3 각각 41/53/49일이며 quality override는 모두 0이다.

## 6. Trade-level 보존 검증

`fold_best_trade_level_details.jsonl`에는 34개 fold-best 거래가 모두 기록됐다.

- 진입 신호일, 실제 진입일, 진입가
- 청산일, 청산가, 청산 사유
- 보유일수, 수익률, 누적 수익률, 거래 후 drawdown
- 진입 시점 5-feature 원시값
- feature별 empirical domain/hard domain/interval 통과 여부와 low/high/q01/q99
- strict interval pass, quality score/threshold
- MDD episode 포함 여부

34개 거래 모두 5-feature가 완전하고 진입 시 strict-AND가 true다.

## 7. GA 수렴과 실행 종료

| fold GA | 세대 | 최초 best | 최종 best | 비감소 |
|---|---:|---:|---:|---|
| train_1 | 15 | 59.153821 | 95.795213 | True |
| train_2 | 15 | 79.787009 | 96.916162 | True |
| train_3 | 15 | 92.672329 | 103.568607 | True |

모든 GA가 정확히 15세대에서 종료됐고 무한루프 징후는 없다. qualify가 실패했으므로 Entry/Exit/Validate는 실행되지 않았다.

## 8. 실행 전후 보호 게이트

manifest snapshot gate:

- repository-root SHA-pinned 단일 source: 통과
- `market_history.csv` SHA 고정: 통과
- `market_history_v2.csv` SHA 고정: 통과
- 필수 컬럼: 통과
- 거래일 freshness: 통과
- auto-fetch/auto-regenerate 차단: 유지
- fail-closed: 유지
- AAP OHLCV snapshot SHA: 통과

보호 SHA는 시작·종료·최종 확인 시 동일하다.

- `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce`
- `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`
- `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611`

Daemon PID 494330은 실행 전후 동일 starttime tick `36014393`이며 최종 확인 시에도 유지됐다.

## 9. 산출물

- `qualify_candidate_rulebooks.jsonl`: 119개 후보 원문
- `qualify_cross_fold_matrix.jsonl`: 357개 후보-fold pass/metric
- `qualify_candidate_pass_vectors.jsonl`: 119개 all3/all2/all1/all0 벡터
- `qualify_trade_count_distribution.json`: fold별 거래 수 분포
- `qualify_mdd_episodes.jsonl`: 357개 MDD episode와 관련 거래·청산 사유
- `qualify_mdd_type_summary.json`: MDD 유형 집계
- `fold_best_signal_statistics.jsonl`: fold-best 신호/feature 통계
- `fold_best_trade_level_details.jsonl`: 34개 fold-best 거래 상세
- `generation_best_fitness.jsonl`: 45개 세대 로그
- `generation_convergence_summary.json`: 수렴 요약
- `run.log`: 전체 실행 로그
- `SHA256SUMS.txt`: 산출물 SHA-256
