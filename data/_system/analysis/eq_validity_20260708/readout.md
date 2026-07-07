# EQ entry quality allow/block validity

- final verdict: **EQ_FILTER_UNVERIFIED**
- 0-step reproducibility: **EQ_NOT_REPRODUCIBLE**
- frozen validation mode: `APPROX_OHLC_SIGNALDATE_CLOSE_NO_EVENT_NEWS_REASONS`
- approximate frozen verdict: **EQ_FILTER_HURTS_APPROX**
- seed: 42

## 0단계 재현 가능성 판정
정확 재현 불가. OHLC 기반 follow-through 지표는 과거 진입일 기준으로 복원 가능하지만, live 현재가와 `evaluate_signal`의 원래 `reasons/components`가 frozen 거래에 저장되어 있지 않다. 특히 EQ의 `event_heavy`, `bottom_fishing` 일부는 reason 문자열에 의존한다.

## Approx frozen group performance — allow vs block / grade
| split   | eq_group   |     n |   win_rate_pct |   avg_net_pct |   sum_net_pct |   net_per_day_pct |   avg_MAE_pct |   worst_MAE_pct | grouping       | eq_grade   | eq_label               |
|:--------|:-----------|------:|---------------:|--------------:|--------------:|------------------:|--------------:|----------------:|:---------------|:-----------|:-----------------------|
| IS      | ALLOW      | 11757 |        53.2619 |      0.937955 |      11027.5  |       -0.0361644  |      -6.43362 |        -73.2888 | allow_vs_block | nan        | nan                    |
| IS      | BLOCK      | 19300 |        53.4819 |      1.15478  |      22287.3  |        0.00354202 |      -6.50494 |        -47.8416 | allow_vs_block | nan        | nan                    |
| OOS     | ALLOW      |  5690 |        61.2302 |      3.21646  |      18301.7  |        0.231821   |      -6.32582 |        -81.8785 | allow_vs_block | nan        | nan                    |
| OOS     | BLOCK      |  7225 |        59.6125 |      3.2718   |      23638.8  |        0.274436   |      -6.55398 |        -52.093  | allow_vs_block | nan        | nan                    |
| IS      | nan        | 18637 |        53.5387 |      1.15536  |      21532.4  |        0.00151499 |      -6.45708 |        -47.8416 | grade4         | FAILED     | nan                    |
| IS      | nan        |  3124 |        53.9052 |      1.07618  |       3361.98 |        0.00148725 |      -6.19027 |        -47.6284 | grade4         | HEALTHY    | nan                    |
| IS      | nan        |  5572 |        52.7638 |      0.821322 |       4576.4  |       -0.0572241  |      -6.93184 |        -73.2888 | grade4         | STRONG     | nan                    |
| IS      | nan        |  3724 |        53.2223 |      1.03225  |       3844.11 |       -0.019026   |      -6.14454 |        -46.2077 | grade4         | WEAK       | nan                    |
| OOS     | nan        |  6910 |        59.7829 |      3.28546  |      22702.5  |        0.294844   |      -6.4965  |        -52.093  | grade4         | FAILED     | nan                    |
| OOS     | nan        |  1434 |        63.4589 |      3.31882  |       4759.19 |        0.257227   |      -6.00591 |        -47.7515 | grade4         | HEALTHY    | nan                    |
| OOS     | nan        |  2885 |        59.896  |      3.35492  |       9678.95 |        0.249366   |      -6.80532 |        -81.8785 | grade4         | STRONG     | nan                    |
| OOS     | nan        |  1686 |        60.6168 |      2.84685  |       4799.79 |        0.104511   |      -6.05564 |        -49.0479 | grade4         | WEAK       | nan                    |
| IS      | nan        | 18637 |        53.5387 |      1.15536  |      21532.4  |        0.00151499 |      -6.45708 |        -47.8416 | label          | nan        | FAILED_FOLLOW_THROUGH  |
| IS      | nan        |  3124 |        53.9052 |      1.07618  |       3361.98 |        0.00148725 |      -6.19027 |        -47.6284 | label          | nan        | HEALTHY_FOLLOW_THROUGH |
| IS      | nan        |  5572 |        52.7638 |      0.821322 |       4576.4  |       -0.0572241  |      -6.93184 |        -73.2888 | label          | nan        | STRONG_FOLLOW_THROUGH  |
| IS      | nan        |  3724 |        53.2223 |      1.03225  |       3844.11 |       -0.019026   |      -6.14454 |        -46.2077 | label          | nan        | WEAK_FOLLOW_THROUGH    |
| OOS     | nan        |  6910 |        59.7829 |      3.28546  |      22702.5  |        0.294844   |      -6.4965  |        -52.093  | label          | nan        | FAILED_FOLLOW_THROUGH  |
| OOS     | nan        |  1434 |        63.4589 |      3.31882  |       4759.19 |        0.257227   |      -6.00591 |        -47.7515 | label          | nan        | HEALTHY_FOLLOW_THROUGH |
| OOS     | nan        |  2885 |        59.896  |      3.35492  |       9678.95 |        0.249366   |      -6.80532 |        -81.8785 | label          | nan        | STRONG_FOLLOW_THROUGH  |
| OOS     | nan        |  1686 |        60.6168 |      2.84685  |       4799.79 |        0.104511   |      -6.05564 |        -49.0479 | label          | nan        | WEAK_FOLLOW_THROUGH    |

## Bootstrap / permutation
| split   |   n_allow |   n_block |   diff_allow_minus_block |   ci95_low |   ci95_high |   p_perm_two_sided | note                                    |
|:--------|----------:|----------:|-------------------------:|-----------:|------------:|-------------------:|:----------------------------------------|
| IS      |     11757 |     19300 |               -0.216829  |  -0.480062 |   0.0416142 |           0.109178 | bootstrap_allow_minus_block_mean_s2_net |
| OOS     |      5690 |      7225 |               -0.0553454 |  -0.527845 |   0.444863  |           0.820836 | bootstrap_allow_minus_block_mean_s2_net |

## OOS portfolio compare — S2 K=20 final_score priority
| scenario               |   K |   total_signals |   realized_trades |   skipped_signals |   skip_rate_pct |   final_multiplier |   CAGR_pct |   MDD_pct |   Sharpe_daily_ann |   avg_active_positions |   max_active_positions |
|:-----------------------|----:|----------------:|------------------:|------------------:|----------------:|-------------------:|-----------:|----------:|-------------------:|-----------------------:|-----------------------:|
| EQ_ignored_all_signals |  20 |           12915 |               726 |             12189 |         94.3786 |            2.26947 |    72.2678 |  -21.9627 |            1.84165 |                19.9894 |                     20 |
| EQ_allow_only          |  20 |            5690 |               695 |              4995 |         87.7856 |            2.01207 |    59.0408 |  -25.6421 |            1.77831 |                19.7739 |                     20 |

## Live ledger check
- sample status: `LIVE_EQ_SAMPLE_INSUFFICIENT_FOR_ALLOW_BLOCK_COUNTERFACTUAL`
- rows: 152
| source                    | eq_group   |   n |   win_rate_pct |   avg_net_pct |   sum_net_pct |   net_per_day_pct |   avg_MAE_pct |   worst_MAE_pct |
|:--------------------------|:-----------|----:|---------------:|--------------:|--------------:|------------------:|--------------:|----------------:|
| elite_shadow_trades       | ALLOW      | 150 |             32 |     -0.379732 |     -56.9598  |         -0.379732 |      -1.40203 |        -7.3038  |
| elite_strategy_sim_trades | ALLOW      |   2 |             50 |     -3.22877  |      -6.45754 |         -3.22877  |      -6.66456 |        -7.53648 |

## Decision
정확 재현이 불가능하므로 approximate frozen 결과만으로 EQ를 게이트로 승격할 수 없다. live shadow ledger는 closed trade 표본은 있으나, 실제로 EQ block된 후보는 애초에 진입하지 않아 counterfactual 성과가 없다. 따라서 지시서의 우선순위 체계상 현재 판정은 EQ_FILTER_UNVERIFIED다.

## Files
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/readout.md`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_trade_labels_approx.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_group_performance.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_portfolio_compare.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_portfolio_equity_curves.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_stat_tests.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_live_ledger_rows.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/eq_live_ledger_performance.csv`
- `/home/g3000kkw/kingmaker/data/_system/analysis/eq_validity_20260708/summary.json`
