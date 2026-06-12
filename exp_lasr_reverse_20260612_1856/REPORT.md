# LASR 2025H2-trained individual reverse multi-year diagnostic
주의: 이 실험은 2025H2까지 학습한 개체를 과거 2022~2024에 적용하는 역방향 진단이며, 시간순 OOS가 아니다. 실거래 방법으로 직접 사용할 수 없다.

## Phase 0 — strict_k3 criteria
- survivor_k=3 general years: 2022/2023/2024
- min_trades=5
- min_member_score=10.0
- general expectancy >= 1.0
- stress 2025H2 expectancy >= 0.0

## Execution settings
- entry=t_plus_1_open, exit=conservative_core, fold_exit_policy=fold_end_mark_to_market, live_hard_stop_guard=True

## Phase 1 — GA
- population=100, generations=40, seed=21264911, candidates=100, ga_seconds=1519.607, best_train_fitness=54.025329

## Phase 3 — pass-rate summary
- 2022 pass count=80
- general 3-year pass count=0
- stress pass count=91
- all4 survivor count=0
- general_pass_count distribution={'0': 15, '1': 77, '2': 8}

## Comparison with 2022-trained experiment
| experiment | candidates | 2022 pass | general3 pass | stress pass | all4 pass |
|---|---:|---:|---:|---:|---:|
| 2022-trained forward | 100 | 1 | 0 | 91 | 0 |
| 2025H2-trained reverse | 100 | 80 | 0 | 91 | 0 |

## All4 survivors
- no 2025H2-trained candidate passed all four periods under strict_k3 individual criteria.

## Top 20 candidate survival summary
| rank | hash | pass count | stress pass | 2022 exp/dd/t/member | 2023 | 2024 | 2025H2 |
|---:|---|---:|---:|---|---|---|---|
| 40 | de9eb672 | 2 | True | 1.76/-7.18/17/34.6 | 6.89/0.00/2/89.7 | 3.96/-3.63/8/69.5 | 2.00/-13.97/20/22.8 |
| 43 | cab94982 | 2 | True | 1.87/-7.03/13/45.7 | 0.90/0.00/1/76.0 | 1.91/-11.47/6/62.3 | 3.37/-14.18/23/51.3 |
| 76 | 8890976c | 2 | True | 1.26/-7.18/19/24.8 | 5.85/-20.85/8/81.0 | 0.52/-17.24/8/56.2 | 2.06/-17.87/22/23.0 |
| 82 | 87ae6cc4 | 2 | True | 1.27/-9.05/21/26.0 | 1.33/-19.72/8/70.7 | 0.53/-17.63/10/57.0 | 1.71/-13.97/26/17.5 |
| 83 | 7cca67a6 | 2 | True | 3.50/-12.31/17/75.4 | 5.39/-29.28/13/80.1 | 0.98/-15.67/15/59.9 | 6.34/-23.20/22/86.3 |
| 87 | 01c6268e | 2 | True | 1.33/-9.05/20/27.2 | 1.33/-19.72/8/69.9 | 0.22/-24.77/12/54.2 | 2.16/-13.97/27/26.1 |
| 93 | e6d49233 | 2 | True | 2.43/-11.20/17/47.1 | 3.79/-27.07/11/77.6 | -1.90/-36.67/17/17.7 | 3.33/-23.54/24/43.9 |
| 64 | 521a5a45 | 2 | False | 0.55/-27.38/18/8.8 | 10.05/-6.23/6/88.3 | 2.08/-10.65/8/63.3 | -1.42/-57.39/22/0.2 |
| 1 | b9c2ed40 | 1 | True | 2.89/-7.03/17/73.4 | 9.13/0.00/1/91.5 | 0.00/0.00/0/36.4 | 5.38/-4.27/9/86.0 |
| 2 | 14d290e1 | 1 | True | 2.74/-7.03/15/67.6 | 0.90/0.00/1/76.0 | 10.21/0.00/2/84.8 | 2.39/-13.97/19/30.7 |
| 3 | 442ed131 | 1 | True | 3.08/-7.03/17/81.0 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 3.96/-4.27/10/66.9 |
| 4 | 63917124 | 1 | True | 3.08/-7.03/17/81.0 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 5.16/-4.27/10/81.1 |
| 5 | f30d1cf5 | 1 | True | 2.64/-7.03/16/63.0 | 0.00/0.00/0/40.5 | 10.21/0.00/2/84.8 | 3.25/-13.97/20/45.9 |
| 6 | 1e33167e | 1 | True | 2.43/-7.03/18/55.6 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 2.36/-4.04/6/45.0 |
| 7 | 24d6de8f | 1 | True | 2.89/-7.03/17/73.4 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 5.00/-4.27/10/77.5 |
| 8 | 497f99ee | 1 | True | 2.83/-7.03/18/71.1 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 3.32/-8.28/6/52.5 |
| 9 | 53e19c75 | 1 | True | 2.89/-7.03/17/73.4 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 5.28/-4.27/9/83.9 |
| 10 | 65443725 | 1 | True | 3.02/-7.03/17/76.3 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 5.22/-4.27/9/82.5 |
| 11 | 70491ce0 | 1 | True | 2.43/-7.03/18/55.8 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 5.72/-4.04/6/90.3 |
| 12 | 9b6ee804 | 1 | True | 3.08/-7.03/17/77.0 | 0.00/0.00/0/40.5 | 0.00/0.00/0/36.4 | 4.39/-4.27/9/72.6 |

## Phase 4 — exit parameter comparison
- comparison pool: 2022-pass candidates because all4 survivors=0, count=80
| param | live_42088d4e | pool_min | pool_max |
|---|---:|---:|---:|
| exit_strategy | hybrid | ['fixed', 'hybrid', 'trailing'] |  |
| stop_loss_atr | 1.7392999496783188 | 1.0 | 3.5 |
| stop_loss_atr_bear | 2.5926661869463077 | 2.2259231191526614 | 5.0 |
| take_profit_atr | 4.7163376780224535 | 1.9102669496432436 | 5.0 |
| take_profit_atr_bull | 4.9736775936215345 | 1.5 | 4.046308516747413 |
| trailing_atr | 2.183521241686879 | 1.0 | 3.0 |
| trailing_atr_volatile | 4.0 | 1.1491780743707507 | 4.0 |
| trailing_activation_profit_pct | 4.004132007445814 | 1.0 | 7.3975216323737545 |
| breakeven_enabled | False | 0.0 | 1.0 |
| breakeven_trigger_profit_pct | 0.0 | 0.0 | 6.762076110753078 |
| breakeven_floor_profit_pct | 0.0 | 0.0 | 2.017744722888538 |
| max_holding_days | 30 | 5.0 | 17.0 |
| sell_omen_enabled | False | 0.0 | 1.0 |
| sell_omen_threshold | 1.0 | 0.5053995052942033 | 1.0 |
