# Stage 3 OOS + Stress 검증 방식 probe — read-only

## 결론

원본 Stage 3의 recent_1y OOS와 stress_pre_2022h1 검증 방식은 리포 안에 존재한다. 현재 rework v5/null-test 실행 경로에는 이 validate/OOS/stress 단계가 연결되어 있지 않다.

- 원본 구현: `scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001`, SHA `bc3e191a449d6b67980dd0884a2a510acf9ecda719a8b12e76b0e8178de33004`.
- root wrapper `scripts/research/run_stage3_aggressive.py`는 원본 backup을 로드하고 qualify early-stop만 감싼다. lines `1-14`, `26-44`.
- rework 원본 backup도 동일 SHA로 존재: `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001`.
- rework strict wrapper `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py`는 원본을 `_base`로 로드하고 strict entry/D-5/market snapshot fail-closed를 얹는다. lines `1-11`, `31-52`, `73-82`.
- 현재 v5 host `run_stage3_aap_eec_penalty_v5_host.py`는 v4/v3 chain에 postprocess만 얹는다. lines `397-402`. v3 host도 base `main()` 후 postprocess만 한다. `run_stage3_aap_tradecount_factor_v3_host.py:596-614`.

## STEP 1 — OOS recent_1y 방식

원본 기간 정의는 original backup lines `75-90`에 있다.

| period | 정의 |
|---|---|
| train_1 | `2022-07-01~2023-06-30` |
| train_2 | `2023-07-01~2024-06-30` |
| train_3 | `2024-07-01~2025-06-30` |
| recent_1y | `start=2025-07-01`, `end=None` |
| PURE_OOS_VALIDATION_PERIODS | `train_1`, `train_2`, `recent_1y` |

recent_1y는 동적 1년 계산이 아니라 `start=2025-07-01` 하드코딩이고, validate 시 `end=None`을 `ctx.data_end`로 대체한다. 근거: original lines `1200-1227`, `1314-1317`, `1382-1385`.

최종 개체 적용 방식:

- `run_validate()`는 `final_rulebooks.jsonl`을 prerequisite로 읽는다. original lines `1182-1198`.
- 각 final row를 `Rulebook.from_dict()`로 복원하고 `PURE_OOS_VALIDATION_PERIODS`를 순회한다. original lines `1216-1228`.
- `_validate_one_period()`는 `run_backtest_period(rulebook, ctx, start, end)`만 실행해 metrics, holding summary, exit distribution, trades를 반환한다. original lines `891-912`.
- 따라서 OOS는 재학습이 아니라 final rulebook read-only 적용이다.

OOS 통과 기준:

- `engine/pipeline/stage3_gate.py:14`: `STAGE3_FINAL_OOS_PERIODS = (train_1, train_2, recent_1y)`.
- `engine/pipeline/stage3_gate.py:32-47`: `eligibility_min_expectancy_pct = 1.0`. MDD/holding은 label용.
- `engine/pipeline/stage3_gate.py:230-267`: `stage3_basic_eligibility()`는 세 OOS period 각각의 `expectancy_pct >= 1.0`만 검사한다.
- original `run_validate()` docstring lines `1182-1193`도 같은 의미를 명시한다.

OOS 산출: `validation_results.jsonl`, `stage3_profile_catalog.jsonl`, `stage3_ineligible.jsonl`, `validate_result.json`. 근거: original lines `1373-1407`.

## STEP 2 — Stress 방식

stress 정의:

- `STRESS_PERIOD = {label: stress_pre_2022h1, start: None, end: 2022-06-30}`: original line `81`.
- `EXIT_CHECK_PERIOD = {label: stress_pre_2022h1, start: None, end: 2022-06-30, role: exit_check}`: original line `89`.

stress는 자동 탐지가 아니라 하드코딩된 과거 구간이다. 시작은 데이터 시작일, 종료는 `2022-06-30`.

stress 사용 위치:

1. exit learning fitness: `_evaluate_exit_gene()`가 stress+bull 결합 fitness를 계산한다. original lines `752-787`. stress는 `STRESS_PERIOD`, bull은 `BULL_PERIOD=train_3`.
2. validate reference: `stress_check = _validate_one_period(... EXIT_CHECK_PERIOD)` 실행 후 `gate_included=False`, `role=exit_check`로 기록한다. original lines `1257-1289`. validation row에는 `stress_reference_metrics`가 들어간다. original lines `1318-1322`.

validate 순서: OOS 3구간 실행 → stress reference 실행 → OOS 3구간 expectancy로 eligibility 판정. stress는 gate 제외 reference다.

## STEP 3 — rework 차이 및 이식 지점

현재 rework 상태:

| 파일 | 상태 | 근거 |
|---|---|---|
| rework `run_stage3_aggressive.py` | 원본 validate/stress는 `_base`에 존재 | lines `1-11`, `31-52` |
| `run_stage3_baseline_light.py` | light full pipeline에 validate 호출 예시 존재 | `_audit_periods()` lines `456-465`; `run_exit_ga()` 후 `run_validate()` 호출 lines `564-577` |
| `run_stage3_aap_tradecount_factor_v3_host.py` | 현재 실험 chain에는 validate 없음 | lines `596-614` |
| `run_stage3_aap_overlap_entry_v4_host.py` | validate 없음 | lines `610-613` |
| `run_stage3_aap_eec_penalty_v5_host.py` | validate 없음 | lines `397-402` |

권장 이식: 기존 v5/null-test 산출물의 후보 rulebook을 읽어 별도 OOS/stress probe를 실행한다. 원본 `run_exit_ga()`를 켜지 않는다.

| 이식 지점 | 내용 | 커플링 위험 |
|---|---|---|
| 새 helper `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_oos_stress_probe.py` | 기존 `qualify_candidate_rulebooks.jsonl`, `qualify_cross_fold_matrix.jsonl`, `fold_best_summary.json`에서 후보 rulebook 로드 | 낮음 |
| helper 내 period constants | 원본 constants 복제: OOS=`train_1/train_2/recent_1y`, stress=`stress_pre_2022h1` | 낮음 |
| helper 내 `_validate_one_period` | original lines `891-912` 구조 이식. current rework backtest function 호출 | 중간. 호출할 function을 명확히 고정 필요 |
| eligibility | `engine.pipeline.stage3_gate.stage3_basic_eligibility()` 그대로 사용 | 낮음 |
| optional postprocess 연결 | v5 host `_postprocess()` 뒤에서 optional call 가능 | 낮음~중간 |

커플링 점검:

| 항목 | 별도 probe 영향 |
|---|---|
| entry/exit logic | 수정 없음 |
| should_buy | 수정 없음. C1-S식 오염 없음 |
| strict-AND | 기존 rulebook interval 그대로 적용 |
| EEC penalty | 학습에는 영향 없음. post-hoc 성과 측정만 함 |
| legacy/fixed-sizing | 수정 없음 |
| 기존 산출물 | SHA 전후 대조로 bitwise 불변 가능 |

피해야 할 것: `run_exit_ga()`를 현재 v5/null-test chain에 재연결하지 말 것. stress+bull exit fitness가 새 학습을 수행하므로 기존 fold-best/qualify 결과와 목적이 달라진다.

## 데이터 커버리지

ADPT cache SHA `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503`, rows 1527, coverage `2020-05-18~2026-06-15`.

| period | ADPT rows | trading range | 판정 |
|---|---:|---|---|
| stress_pre_2022h1 | 535 | 2020-05-18~2022-06-30 | 사용 가능 |
| train_1 | 251 | 2022-07-01~2023-06-30 | 사용 가능 |
| train_2 | 250 | 2023-07-03~2024-06-28 | 사용 가능. calendar boundary는 비거래일 |
| train_3 | 250 | 2024-07-01~2025-06-30 | 사용 가능 |
| recent_1y | 241 | 2025-07-01~2026-06-15 | 사용 가능 |

XBI cache SHA `6f0412b2722f9e6b6a5e2c3deae49ea2c0d51f9070ccfce57778a9bb100884e0`, coverage `2020-05-18~2026-06-12`. stress/recent_1y 사용 가능.

## STEP 4 — Phase 2 계획 초안

1. 새 probe helper 추가.
2. 입력: `--ticker`, `--source-run-dir`, `--candidate-source all3|fold_best|hash_list`, `--out-dir`, `--market-cutoff-date`, `--no-fetch`, `--no-regenerate`.
3. all3 후보는 `qualify_cross_fold_matrix.jsonl`에서 all3 hash를 찾고 `qualify_candidate_rulebooks.jsonl`에서 rulebook 로드.
4. fold-best 후보는 `fold_best_summary.json` 또는 `fold_best_trade_level.jsonl`의 candidate_hash 기반으로 로드.
5. `train_1`, `train_2`, `recent_1y`, `stress_pre_2022h1` 평가.
6. recent_1y end는 `ctx.data_end`, stress는 `gate_included=False`.
7. `stage3_basic_eligibility()`로 OOS 3구간 expectancy>=1.0 검사.
8. 산출: `oos_stress_results.jsonl`, `oos_stress_trade_level.jsonl`, `oos_stress_summary.json`, `readout.md`, `SHA256SUMS.txt`, `launch_command.json`.

argv 초안:

```bash
python scripts/research/run_stage3_oos_stress_probe.py \
  --ticker ADPT \
  --source-run-dir data/_system/analysis/stage3_adpt_feature_nulltest_v9_20260715/trend_chop20/REAL \
  --candidate-source all3 \
  --out-dir data/_system/analysis/stage3_adpt_trend_chop20_oos_stress_20260715 \
  --market-cutoff-date 2026-07-10 \
  --no-fetch \
  --no-regenerate
```

정적 검증: no retrain, source SHA 불변, source-run-dir SHA 불변, period correctness, 보호파일 SHA, daemon 유지.

금지: `run_exit_ga()` 재연결, `should_buy`/`exit_simulator`/`execution_mode_backtest` 수정, OOS/stress를 fitness에 반영, recent_1y를 train/qualify에 포함.

## 보호파일 / daemon / git

보호파일 시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6dbb86c6347548e9f4c611` |

- 실제 host: `invest-bot`.
- daemon PID `494330` 유지 확인.
- 산출 전 backup commit: `709cf72`.
