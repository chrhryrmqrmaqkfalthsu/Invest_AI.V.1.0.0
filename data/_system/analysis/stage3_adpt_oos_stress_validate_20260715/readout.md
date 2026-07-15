# ADPT OOS + Stress validation — original Stage 3 rulebook probe

## STEP 4 — final verdict

수익률은 entry/exit price 기준 gross return이며 수수료·슬리피지는 미반영이다. 원본 Stage 3 OOS gate는 `train_1`, `train_2`, `recent_1y` 각각 `expectancy_pct >= 1.0`이다. `train_3`는 in-sample reference, stress는 gate 제외 reference다.

|candidate|role|hash|verdict|train_1 exp|train_2 exp|recent_1y exp|recent trades|recent avg ret|recent win|recent MDD|stress exp|stress trades|stress MDD|
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|all3_1|all3|`3c950cfa5f...`|OOS_FAIL_RECENT|2.53|5.27|-3.16|21|-3.11|23.8%|-70.85|-0.96|32|-54.66|
|fold_best_train_1|fold_best|`1b4db2d534...`|OOS_FAIL_RECENT|5.41|-3.07|-1.50|12|-1.45|41.7%|-47.18|-0.95|18|-23.98|
|fold_best_train_2|fold_best|`55c9381b72...`|OOS_FAIL_RECENT|1.83|5.02|-1.66|33|-1.61|33.3%|-78.74|-0.91|36|-60.71|
|fold_best_train_3|fold_best|`3627625692...`|OOS_FAIL_OTHER|0.05|1.96|1.29|10|1.34|60.0%|-4.90|-5.69|6|-24.33|

## STEP 2 — OOS performance

|candidate|period|gate|trades|avg hold|expectancy|avg ret|median ret|win|payoff|MDD|total pct-pts|compounded|exit reasons|
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
|all3_1|train_1|PASS|20|2.60|2.53|2.58|2.64|60.0%|1.48|-32.73|51.57|57.33|entry_interval_break:20|
|all3_1|train_2|PASS|16|2.19|5.27|5.33|5.26|81.2%|3.54|-3.83|85.20|124.76|entry_interval_break:16|
|all3_1|train_3|REF|10|2.40|2.45|2.50|4.55|60.0%|1.10|-39.10|25.02|18.64|entry_interval_break:10|
|all3_1|recent_1y|FAIL|21|2.52|-3.16|-3.11|-3.46|23.8%|0.87|-70.85|-65.30|-50.65|entry_interval_break:19, entry_provisional_atr_stop:2|
|fold_best_train_1|train_1|PASS|16|2.50|5.41|5.46|4.89|100.0%|N/A|0.00|87.33|132.62|entry_interval_break:16|
|fold_best_train_1|train_2|FAIL|28|2.54|-3.07|-3.02|-1.48|32.1%|0.72|-110.62|-84.53|-61.05|entry_interval_break:27, entry_provisional_atr_stop:1|
|fold_best_train_1|train_3|REF|3|2.00|-0.64|-0.59|-1.75|33.3%|1.57|-8.29|-1.77|-2.18|entry_interval_break:3|
|fold_best_train_1|recent_1y|FAIL|12|2.00|-1.50|-1.45|-2.07|41.7%|0.80|-47.18|-17.41|-19.20|entry_interval_break:10, entry_provisional_atr_stop:2|
|fold_best_train_2|train_1|PASS|28|2.61|1.83|1.88|2.15|57.1%|1.57|-23.65|52.69|59.35|entry_interval_break:28|
|fold_best_train_2|train_2|PASS|19|2.11|5.02|5.07|4.60|73.7%|4.86|-3.83|96.41|149.15|entry_interval_break:19|
|fold_best_train_2|train_3|REF|5|2.00|5.82|5.87|3.98|60.0%|2.20|-12.86|29.35|27.43|entry_interval_break:5|
|fold_best_train_2|recent_1y|FAIL|33|2.67|-1.66|-1.61|-0.60|33.3%|0.87|-78.74|-53.02|-45.51|entry_interval_break:28, entry_provisional_atr_stop:5|
|fold_best_train_3|train_1|FAIL|17|2.29|0.05|0.10|-1.59|35.3%|1.89|-33.60|1.63|-3.68|entry_interval_break:17|
|fold_best_train_3|train_2|PASS|4|2.00|1.96|2.01|-1.18|25.0%|6.74|-2.45|8.05|7.24|entry_interval_break:4|
|fold_best_train_3|train_3|REF|20|2.15|6.88|6.93|5.09|90.0%|9.18|-0.89|138.65|269.56|entry_interval_break:20|
|fold_best_train_3|recent_1y|PASS|10|2.30|1.29|1.34|1.38|60.0%|1.82|-4.90|13.38|13.59|entry_interval_break:10|

## STEP 3 — stress reference

|candidate|stress period|trades|expectancy|avg ret|win|MDD|total pct-pts|exit reasons|
|---|---|---:|---:|---:|---:|---:|---:|---|
|all3_1|stress_pre_2022h1|32|-0.96|-0.91|46.9%|-54.66|-29.23|entry_interval_break:30, entry_provisional_atr_stop:2|
|fold_best_train_1|stress_pre_2022h1|18|-0.95|-0.90|38.9%|-23.98|-16.17|entry_interval_break:17, entry_provisional_atr_stop:1|
|fold_best_train_2|stress_pre_2022h1|36|-0.91|-0.86|47.2%|-60.71|-30.80|entry_interval_break:34, entry_provisional_atr_stop:2|
|fold_best_train_3|stress_pre_2022h1|6|-5.69|-5.64|0.0%|-24.33|-33.83|entry_interval_break:5, entry_provisional_atr_stop:1|

## STEP 0/1 — audit

- source run dir: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/ADPT`
- candidates: fold-best 3개 + all3 1개. trend_chop20 후보는 미포함.
- run_exit_ga / GA / qualify 재학습: 미가동.
- worker mode: VM ProcessPoolExecutor max_workers=6
- py_compile: PASS
- mutation helper AST SHA: `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d`
- original OOS criterion: train_1/train_2/recent_1y each expectancy_pct >= 1.0
- stress: stress_pre_2022h1 reference only, gate excluded.

### Data coverage

ADPT SHA `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503`, coverage 2020-05-18~2026-06-15, rows 1527.

|period|rows|first|last|ohlcv nulls|
|---|---:|---|---|---:|
|train_1|251|2022-07-01|2023-06-30|0|
|train_2|250|2023-07-03|2024-06-28|0|
|train_3|250|2024-07-01|2025-06-30|0|
|recent_1y|241|2025-07-01|2026-06-15|0|
|stress_pre_2022h1|535|2020-05-18|2022-06-30|0|

### Input SHA invariant

- `qualify_result.json`: `7643a37e80fb78a307e87825e87d03d226daea686ebe6993c2b4a01a09155c18` -> `7643a37e80fb78a307e87825e87d03d226daea686ebe6993c2b4a01a09155c18` OK
- `qualify_cross_fold_matrix.jsonl`: `8b0135c4d1757179054ac5497e48270f1469e34cd10f46f93a3e28951a759a6d` -> `8b0135c4d1757179054ac5497e48270f1469e34cd10f46f93a3e28951a759a6d` OK
- `qualify_candidate_rulebooks.jsonl`: `b6e3ba9b55dcb0d2a1ca5fd5298a80db5ed30fbca66805198be31f77aeaf97f5` -> `b6e3ba9b55dcb0d2a1ca5fd5298a80db5ed30fbca66805198be31f77aeaf97f5` OK
- `fold_best_summary.json`: `55b8944fa53135b3ec751c4cb182cb6c15f5990f23c1a9fa5f155b6f1fc43d29` -> `55b8944fa53135b3ec751c4cb182cb6c15f5990f23c1a9fa5f155b6f1fc43d29` OK
- `fold_best_trade_level.jsonl`: `9a08d8e36bc9130dc9c52fa349debf9028bb02a96ac214b03c03474411096f14` -> `9a08d8e36bc9130dc9c52fa349debf9028bb02a96ac214b03c03474411096f14` OK
- `qualify_gate_bottleneck.json`: `b3fa3c6e99106ffd650659eb5f07dd11765cc892ba6d95b0ecafa2919252e217` -> `b3fa3c6e99106ffd650659eb5f07dd11765cc892ba6d95b0ecafa2919252e217` OK

### Protected SHA

- `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` -> `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` OK
- `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` -> `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` OK
- `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` -> `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` OK

- daemon PID 494330 alive: True
- source git commit before output: `c13a473`
