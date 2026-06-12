# LASR exit-only GA experiment

진입 규칙은 live 42088d4e 그대로 고정하고 청산 파라미터만 GA로 탐색했다. 4구간 종합 fitness에는 2025H2가 포함되어 있으므로 방법론 검증용이며 실거래 OOS가 아니다.

## Phase 1 — fixed/searched fields

### Searched exit fields

- exit_strategy: ['fixed', 'trailing', 'hybrid']
- breakeven_enabled: [False, True]
- sell_omen_enabled: [False, True]
- stop_loss_atr: (1.0, 3.5)
- stop_loss_atr_bear: (1.0, 5.0)
- take_profit_atr: (1.5, 5.0)
- take_profit_atr_bull: (1.5, 6.0)
- trailing_atr: (1.0, 3.0)
- trailing_atr_volatile: (1.0, 4.0)
- trailing_activation_profit_pct: (1.0, 8.0)
- breakeven_trigger_profit_pct: (4.0, 8.0)
- breakeven_floor_profit_pct: (1.0, 3.0)
- sell_omen_threshold: (0.3, 0.7)
- max_holding_days: (5, 30)

진입 잠금 검산: GA 개체는 `Rulebook.from_dict(live)` 복사본에 위 청산 필드만 덮어쓴다. top3 변경 필드도 모두 청산 필드 안에 있다.

## Phase 2 — fitness formula

```text
fitness = avg_exp + 2.0*min_exp - 0.15*stdev_exp - 0.20*avg_abs_dd - 0.25*worst_abs_dd - 5.0*negative_period_count
```
거래 수는 hard cutoff나 보상에 넣지 않고 기록만 했다.

- population=100, generations=40, seed=202606121950, workers=6, evaluated_unique=3299, elapsed_sec=343.9

## Phase 3 — composite summary

| variant | comp fitness | avg exp | min exp | neg periods | worst DD abs | total trades |
|---|---:|---:|---:|---:|---:|---:|
| baseline_live_42088d4e | -20.789 | 2.064 | -4.637 | 1 | 22.199 | 22 |
| setB_manual_median | 3.873 | 3.710 | 2.331 | 0 | 11.177 | 36 |
| ga_top1 | 18.080 | 7.660 | 6.302 | 0 | 6.199 | 20 |
| ga_top2 | 18.080 | 7.660 | 6.302 | 0 | 6.199 | 20 |
| ga_top3 | 18.080 | 7.660 | 6.302 | 0 | 6.199 | 20 |

## 4-period metrics

| period | variant | exp% | maxDD% | trades | exits |
|---|---|---:|---:|---:|---|
| 2022 | baseline_live_42088d4e | -4.637 | -22.199 | 6 | `{"stop_loss": 2, "time_out": 2, "trailing": 2}` |
| 2022 | setB_manual_median | 4.397 | -9.763 | 10 | `{"breakeven_stop": 3, "time_out": 7}` |
| 2022 | ga_top1 | 7.685 | -2.708 | 5 | `{"time_out": 5}` |
| 2022 | ga_top2 | 7.685 | -2.708 | 5 | `{"time_out": 5}` |
| 2022 | ga_top3 | 7.685 | -2.708 | 5 | `{"time_out": 5}` |
| 2023 | baseline_live_42088d4e | 5.381 | 0.000 | 3 | `{"take_profit": 1, "trailing": 2}` |
| 2023 | setB_manual_median | 2.331 | -9.894 | 4 | `{"breakeven_stop": 2, "stop_loss": 1, "take_profit": 1}` |
| 2023 | ga_top1 | 6.975 | 0.000 | 3 | `{"take_profit": 2, "time_out": 1}` |
| 2023 | ga_top2 | 6.975 | 0.000 | 3 | `{"take_profit": 2, "time_out": 1}` |
| 2023 | ga_top3 | 6.975 | 0.000 | 3 | `{"take_profit": 2, "time_out": 1}` |
| 2024 | baseline_live_42088d4e | 3.364 | -8.544 | 3 | `{"take_profit": 1, "trailing": 2}` |
| 2024 | setB_manual_median | 5.089 | 0.000 | 3 | `{"breakeven_stop": 1, "take_profit": 1, "time_out": 1}` |
| 2024 | ga_top1 | 6.302 | 0.000 | 3 | `{"take_profit": 1, "time_out": 2}` |
| 2024 | ga_top2 | 6.302 | 0.000 | 3 | `{"take_profit": 1, "time_out": 2}` |
| 2024 | ga_top3 | 6.302 | 0.000 | 3 | `{"take_profit": 1, "time_out": 2}` |
| 2025H2 | baseline_live_42088d4e | 4.146 | -18.037 | 10 | `{"stop_loss": 3, "take_profit": 1, "trailing": 6}` |
| 2025H2 | setB_manual_median | 3.025 | -11.177 | 19 | `{"breakeven_stop": 10, "fold_end_mark_to_market": 1, "stop_loss": 1, "take_profit": 2, "time_out": 5}` |
| 2025H2 | ga_top1 | 9.680 | -6.199 | 9 | `{"fold_end_mark_to_market": 1, "take_profit": 1, "time_out": 7}` |
| 2025H2 | ga_top2 | 9.680 | -6.199 | 9 | `{"fold_end_mark_to_market": 1, "take_profit": 1, "time_out": 7}` |
| 2025H2 | ga_top3 | 9.680 | -6.199 | 9 | `{"fold_end_mark_to_market": 1, "take_profit": 1, "time_out": 7}` |

## Exit params

| field | baseline | SetB manual | GA top1 | GA top2 | GA top3 |
|---|---:|---:|---:|---:|---:|
| exit_strategy | hybrid | fixed | fixed | fixed | fixed |
| breakeven_enabled | False | True | False | False | False |
| sell_omen_enabled | False | True | True | False | True |
| stop_loss_atr | 1.739 | 1.771 | 2.789 | 2.789 | 2.725 |
| stop_loss_atr_bear | 2.593 | 4.976 | 3.935 | 3.935 | 3.935 |
| take_profit_atr | 4.716 | 4.641 | 5.000 | 5.000 | 5.000 |
| take_profit_atr_bull | 4.974 | 2.113 | 3.570 | 3.570 | 3.570 |
| trailing_atr | 2.184 | 2.725 | 2.654 | 2.525 | 2.659 |
| trailing_atr_volatile | 4.000 | 1.149 | 2.008 | 2.359 | 2.078 |
| trailing_activation_profit_pct | 4.004 | 3.508 | 3.436 | 4.522 | 3.436 |
| breakeven_trigger_profit_pct | 4.000 | 4.301 | 4.000 | 4.000 | 5.578 |
| breakeven_floor_profit_pct | 1.000 | 1.000 | 2.812 | 1.353 | 2.812 |
| sell_omen_threshold | 0.700 | 0.597 | 0.344 | 0.344 | 0.427 |
| max_holding_days | 30 | 7 | 16 | 16 | 16 |
