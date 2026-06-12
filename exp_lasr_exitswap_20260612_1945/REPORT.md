# LASR exit-only swap experiment

진입 규칙은 live `42088d4e` rulebook 그대로 유지하고, 청산 필드만 교체했다. GA 없음. 라이브 파일 수정 없음.

## Settings

- ticker=LASR
- data_end=2026-06-09
- entry=t_plus_1_open
- exit=conservative_core
- fold_exit_policy=fold_end_mark_to_market
- live_hard_stop_guard=True
- fitness_mode=swing

## Entry fixed check

- Set A changed fields: breakeven_enabled, breakeven_floor_profit_pct, breakeven_trigger_profit_pct, exit_strategy, max_holding_days, sell_omen_enabled, sell_omen_threshold, stop_loss_atr, stop_loss_atr_bear, take_profit_atr, take_profit_atr_bull, trailing_activation_profit_pct, trailing_atr, trailing_atr_volatile
- Set B changed fields: breakeven_enabled, breakeven_floor_profit_pct, breakeven_trigger_profit_pct, exit_strategy, max_holding_days, sell_omen_enabled, sell_omen_threshold, stop_loss_atr, stop_loss_atr_bear, take_profit_atr, take_profit_atr_bull, trailing_activation_profit_pct, trailing_atr, trailing_atr_volatile
- 두 세트 모두 변경 필드는 청산 필드 목록 안에만 있음.

## Exit parameter sets

| field | baseline live | Set A 2820575b | Set B six-survivor median/majority |
|---|---:|---:|---:|
| exit_strategy | hybrid | fixed | fixed |
| stop_loss_atr | 1.739 | 1.771 | 1.771 |
| stop_loss_atr_bear | 2.593 | 3.223 | 4.976 |
| take_profit_atr | 4.716 | 4.641 | 4.641 |
| take_profit_atr_bull | 4.974 | 2.113 | 2.113 |
| trailing_atr | 2.184 | 1.113 | 2.725 |
| trailing_atr_volatile | 4.000 | 1.149 | 1.149 |
| trailing_activation_profit_pct | 4.004 | 5.126 | 3.508 |
| breakeven_enabled | False | True | True |
| breakeven_trigger_profit_pct | 0.000 | 5.176 | 4.301 |
| breakeven_floor_profit_pct | 0.000 | 1.000 | 1.000 |
| max_holding_days | 30 | 7 | 7 |
| sell_omen_enabled | False | True | True |
| sell_omen_threshold | 1.000 | 0.667 | 0.597 |

## 3-way metrics

| period | variant | expectancy% | Δ vs base | max DD% | ΔDD vs base | trades | Δtrades | exits |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 2022 | baseline_live_42088d4e | -4.637 | +0.000 | -22.199 | +0.000 | 6 | +0 | `{"stop_loss": 2, "time_out": 2, "trailing": 2}` |
| 2022 | setA_2820575b_exit_only | 4.397 | +9.034 | -9.763 | +12.436 | 10 | +4 | `{"breakeven_stop": 3, "time_out": 7}` |
| 2022 | setB_6survivor_median_exit_only | 4.397 | +9.034 | -9.763 | +12.436 | 10 | +4 | `{"breakeven_stop": 3, "time_out": 7}` |
| 2023 | baseline_live_42088d4e | 5.381 | +0.000 | 0.000 | +0.000 | 3 | +0 | `{"take_profit": 1, "trailing": 2}` |
| 2023 | setA_2820575b_exit_only | 4.584 | -0.797 | -9.739 | -9.739 | 4 | +1 | `{"breakeven_stop": 1, "stop_loss": 1, "take_profit": 2}` |
| 2023 | setB_6survivor_median_exit_only | 2.331 | -3.050 | -9.894 | -9.894 | 4 | +1 | `{"breakeven_stop": 2, "stop_loss": 1, "take_profit": 1}` |
| 2024 | baseline_live_42088d4e | 3.364 | +0.000 | -8.544 | +0.000 | 3 | +0 | `{"take_profit": 1, "trailing": 2}` |
| 2024 | setA_2820575b_exit_only | 7.024 | +3.661 | 0.000 | +8.544 | 3 | +0 | `{"take_profit": 1, "time_out": 2}` |
| 2024 | setB_6survivor_median_exit_only | 5.089 | +1.725 | 0.000 | +8.544 | 3 | +0 | `{"breakeven_stop": 1, "take_profit": 1, "time_out": 1}` |
| 2025H2 | baseline_live_42088d4e | 4.146 | +0.000 | -18.037 | +0.000 | 10 | +0 | `{"stop_loss": 3, "take_profit": 1, "trailing": 6}` |
| 2025H2 | setA_2820575b_exit_only | 1.510 | -2.635 | -20.889 | -2.852 | 19 | +9 | `{"breakeven_stop": 9, "fold_end_mark_to_market": 1, "stop_loss": 2, "take_profit": 2, "time_out": 5}` |
| 2025H2 | setB_6survivor_median_exit_only | 3.025 | -1.121 | -11.177 | +6.860 | 19 | +9 | `{"breakeven_stop": 10, "fold_end_mark_to_market": 1, "stop_loss": 1, "take_profit": 2, "time_out": 5}` |

## Phase 4 판정 재료

- 2022 expectancy: baseline -4.637, Set A 4.397, Set B 4.397
- 2022 maxDD: baseline -22.199, Set A -9.763, Set B -9.763
- 2025H2 expectancy: baseline 4.146, Set A 1.510, Set B 3.025
- 2025H2 maxDD: baseline -18.037, Set A -20.889, Set B -11.177

## One-line conclusion placeholder

숫자 기준 판정은 최종 응답에서 요약한다.
