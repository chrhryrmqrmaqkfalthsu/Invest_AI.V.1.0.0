# Turnover Score Validation

## Scope

Selection metric A/B validation for the central backtester.

- Baseline: `selection_metric=confidence`
- Candidate: `selection_metric=turnover_score`
- `turnover_score = avg_realized_pnl_pct / avg_holding_days`
- Eligibility guard: `trade_count >= 30`
- Max positions: 8
- Position sizing: score_weighted
- Sell omen: OFF in loaded rulebooks
- Costs/slippage: same SimBroker FillPolicy for both metrics

Absolute values are diagnostic only. The decision uses turnover_score relative to confidence.

## Pool

- Survivors loaded: 533
- Usable entities: 533
- Unique tickers: 161
- Turnover fields missing: 0
- Matched stage2 trades: 34,051
- Eligible with trade_count >= 30: 532
- Ineligible with trade_count < 30: 1
- Eligible unique tickers: 161
- N=8 pool sufficient: true

## Regression

- default run equals explicit `selection_metric=confidence`: true
- known baseline exact: true
- expected OOS baseline: +64.66211670427089%, MDD -5.807895076304301%, 476 trades
- actual OOS baseline: +64.66211670427089%, MDD -5.807895076304301%, 476 trades

## Comparison

| Period | Metric | Return % | MDD % | Worst Month | Recovery Days | Trades | Turnover % | Return / abs(MDD) |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| OOS | confidence | 64.6621 | -5.8079 | 2026-03 -3.4671% | 29 | 476 | 7609.0140 | 11.1335 |
| OOS | turnover_score | 83.7164 | -9.4281 | 2025-12 -7.3640% | 40 | 814 | 13257.1632 | 8.8794 |
| Stress | confidence | 0.4887 | -12.9725 | 2022-01 -7.2343% | 49 | 264 | 3597.4102 | 0.0377 |
| Stress | turnover_score | 7.8308 | -13.0741 | 2022-04 -8.3288% | 25 | 400 | 5009.4265 | 0.5990 |

## Relative deltas: turnover_score minus confidence

| Period | Return Δ pp | MDD Δ pp | Return/MDD Δ | Turnover Δ pp | Recovery Days Δ | Trades Δ |
|---|---:|---:|---:|---:|---:|---:|
| OOS | 19.0543 | -3.6202 | -2.2540 | 5648.1492 | 11 | 338 |
| Stress | 7.3422 | -0.1016 | 0.5613 | 1412.0164 | -24 | 136 |

## Gates

- OOS return/MDD improved: false
- Stress total return non-inferior: true
- Stress MDD non-inferior: false
- Direction consistent by return/MDD: false

## Verdict

`turnover_score` is **rejected or deferred** as-is for walk-forward promotion.

Reason: OOS absolute return improved, but OOS risk-adjusted performance worsened because MDD expanded materially. Stress return improved and recovery shortened, but MDD was still slightly worse, so the requested non-inferiority gate fails.

This does not mean the idea is dead. It means the raw turnover_score selector is too aggressive as a central selector and needs a risk gate or a hybrid selection design before further walk-forward testing.

## Limitations

- In-sample direction exploration only; not adoption evidence.
- `oos_2025h2` leakage exists in both pools and is assumed to partially cancel.
- Batch may still be incomplete; this used the first 533 survivor pool.
- True decision requires cutoff walk-forward.
- Interpret relative confidence-vs-turnover differences only, not absolute numbers.
