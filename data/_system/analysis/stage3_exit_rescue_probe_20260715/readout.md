# Stage 3 exit-rescue probe — ADPT

## STEP 4 — verdict table

판정 기준: 원본 Stage 3 OOS gate는 `train_1`, `train_2`, `recent_1y` 각각 `expectancy_pct >= 1.0`이다. Stress는 gate 제외 reference다. 수수료·슬리피지 미반영 gross return이다.

|entry candidate|entry role|exit rescue verdict|best exit rank|fixed recent exp|best recent exp|delta|train_1 exp|train_2 exp|recent exp|stress exp|stress MDD|
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|all3_1|all3|EXIT_DOES_NOT_RESCUE|3|-3.16|-1.22|1.94|8.04|2.87|-1.22|-0.95|-20.02|
|fold_best_train_1|fold_best|EXIT_PARTIAL|2|-1.50|5.39|6.89|0.53|0.48|5.39|1.30|-9.37|
|fold_best_train_2|fold_best|EXIT_DOES_NOT_RESCUE|1|-1.66|-1.87|-0.22|-0.99|4.65|-1.87|0.20|-22.61|
|fold_best_train_3|fold_best|EXIT_PARTIAL|1|1.29|10.72|9.44|-4.38|-3.52|10.72|-2.90|-2.94|

## STEP 3 — exit-rank detail

|candidate|exit rank|verdict|composite fitness|train_1|train_2|recent_1y|stress|recent trades|recent avg hold|recent exit reasons|
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
|all3_1|1|OOS_FAIL_RECENT|12.245|8.04|2.87|-2.85|-0.95|8|4.62|stop_loss:5, trailing:3|
|all3_1|2|OOS_FAIL_RECENT|12.245|8.04|2.87|-2.85|-0.95|8|4.62|stop_loss:5, trailing:3|
|all3_1|3|OOS_FAIL_RECENT|12.245|8.04|2.87|-1.22|-0.95|8|5.50|stop_loss:5, time_out:1, trailing:2|
|fold_best_train_1|1|OOS_FAIL_OTHER|13.795|0.53|0.48|4.46|1.30|5|7.20|fold_end_mark_to_market:1, sell_omen:1, stop_loss:2, time_out:1|
|fold_best_train_1|2|OOS_FAIL_OTHER|13.795|0.53|0.48|5.39|1.30|4|9.00|stop_loss:2, time_out:2|
|fold_best_train_1|3|OOS_FAIL_OTHER|13.795|0.53|0.48|5.39|1.30|4|9.00|stop_loss:2, time_out:2|
|fold_best_train_2|1|OOS_FAIL_RECENT|7.554|-0.99|4.65|-1.87|0.20|9|4.78|sell_omen:2, stop_loss:1, trailing:6|
|fold_best_train_2|2|OOS_FAIL_RECENT|7.416|-2.28|4.71|-2.12|0.12|8|4.50|stop_loss:2, time_out:4, trailing:2|
|fold_best_train_2|3|OOS_FAIL_RECENT|7.352|-2.32|4.26|-2.65|0.11|8|4.50|stop_loss:1, time_out:5, trailing:2|
|fold_best_train_3|1|OOS_FAIL_OTHER|11.374|-4.38|-3.52|10.72|-2.90|3|8.33|sell_omen:3|
|fold_best_train_3|2|OOS_FAIL_OTHER|11.374|-4.38|-3.52|10.72|-2.90|3|8.33|sell_omen:3|
|fold_best_train_3|3|OOS_FAIL_OTHER|11.374|-4.38|-3.52|10.72|-2.90|3|8.33|sell_omen:3|

## STEP 0 — original exit-GA mechanism

원본 `run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001` 기준:
- lines 752-787: `_evaluate_exit_gene()`는 고정 entry rulebook에 exit gene만 `apply_exit()`로 덮어쓰고 stress_pre_2022h1 + bull(train_3)을 backtest해 `composite_exit_fitness()`를 계산한다.
- lines 790-852: `_run_exit_ga_for_entry()`는 entry rulebook 하나를 base로 삼아 청산 14필드 전용 GA를 실행한다.
- lines 855-887: `run_exit_ga()`는 `entry_rulebooks.jsonl`을 읽어 entry별 exit GA를 실행하고 `final_rulebooks.jsonl`을 만든다.
- `engine/pipeline/exit_gene.py:68-82`: `apply_exit()`는 `EXIT_FIELDS`만 overwrite하고 entry/position/metadata는 copy한다.
- `engine/pipeline/exit_gene.py:184-245`: composite fitness는 bull expectancy + downside term + bull floor penalty + stress MDD penalty + holding penalty로 구성된다.

Phase 2 fixed-exit probe와의 차이: Phase 2는 기존 entry-scope provisional exit/interval-break를 그대로 적용했다. 이번 probe는 동일 entry rulebook을 고정한 뒤 원본 exit GA로 청산 필드만 진화시킨 final rulebook을 OOS/stress에 다시 적용했다.

## STEP 1/2 — audit

- actual host: `invest-bot`
- workers: 4
- entry GA / qualify: not run
- exit GA: isolated per entry candidate only
- source run dir: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/ADPT`
- py_compile: PASS
- mutation helper AST SHA: `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d`

### source artifact SHA invariant
- `qualify_result.json`: `7643a37e80fb78a307e87825e87d03d226daea686ebe6993c2b4a01a09155c18` -> `7643a37e80fb78a307e87825e87d03d226daea686ebe6993c2b4a01a09155c18` OK
- `qualify_cross_fold_matrix.jsonl`: `8b0135c4d1757179054ac5497e48270f1469e34cd10f46f93a3e28951a759a6d` -> `8b0135c4d1757179054ac5497e48270f1469e34cd10f46f93a3e28951a759a6d` OK
- `qualify_candidate_rulebooks.jsonl`: `b6e3ba9b55dcb0d2a1ca5fd5298a80db5ed30fbca66805198be31f77aeaf97f5` -> `b6e3ba9b55dcb0d2a1ca5fd5298a80db5ed30fbca66805198be31f77aeaf97f5` OK
- `fold_best_summary.json`: `55b8944fa53135b3ec751c4cb182cb6c15f5990f23c1a9fa5f155b6f1fc43d29` -> `55b8944fa53135b3ec751c4cb182cb6c15f5990f23c1a9fa5f155b6f1fc43d29` OK
- `fold_best_trade_level.jsonl`: `9a08d8e36bc9130dc9c52fa349debf9028bb02a96ac214b03c03474411096f14` -> `9a08d8e36bc9130dc9c52fa349debf9028bb02a96ac214b03c03474411096f14` OK
- `qualify_gate_bottleneck.json`: `b3fa3c6e99106ffd650659eb5f07dd11765cc892ba6d95b0ecafa2919252e217` -> `b3fa3c6e99106ffd650659eb5f07dd11765cc892ba6d95b0ecafa2919252e217` OK

### protected SHA
- `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` -> `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` OK
- `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` -> `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` OK
- `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` -> `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` OK

- daemon PID 494330 alive: True
- pre-output backup commit: `1fa2053`
