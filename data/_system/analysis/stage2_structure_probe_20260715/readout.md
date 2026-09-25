# Stage 2 Phase 1 구조 probe

## 결론

판정: **DIVERGENT_FULL_COPY / STAGE2_CORE_IDENTICAL**.

작업 복사본 후보는 `scripts/research/stage23_rework_20260713/`이다. `cleanup_and_copy_report.md`에 정식 Stage 2/3 원본 166개를 복사한 독립 작업 기준점으로 기록돼 있다. 복사 당시 원본/복사본 aggregate SHA는 모두 `466b1bb08f03ce6aa2432f16430fa35062222978e47ff618dcc5df760294c17d`, 불일치 0/166이었다.

현재 재대조: 166개 중 match 159, diff 7, missing 0, extra 190. 전체 복사본은 Stage3 rework 진행으로 원본과 다르다. 그러나 Stage 2 핵심 파일은 현재도 원본과 SHA가 일치한다.

| Stage2 핵심 파일 | SHA | 판정 |
|---|---|---|
| `scripts/research/run_stage2.py` | `9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c` | MATCH |
| `engine/pipeline/stage2_gate.py` | `b3018f9323fb7f0194990ce726979841b9db5c5a852711dac3fb7a1d3357f15a` | MATCH |
| `engine/pipeline/rolling_validation.py` | `95511ca90c769dc3338fe99659e09720d01760a053b5d20a220c9c2ac0dbc7ce` | MATCH |
| `engine/pipeline/topn_survivor.py` | `d457da84972078087dceec6260dad66273229bfb657c152a9725f6fb07cbc306` | MATCH |
| `engine/pipeline/full_training.py` | `4fd9d72a28db823ce6a5bd375758dc657abb9d7ba28fe52a0dbf52448a005187` | MATCH |
| `scripts/research/run_stage23_batch.py` | `2718a3e12eb8eea71011543efa41d435850402aeaa70c5517d5eded9761a00e3` | MATCH |

불일치 7개: `engine/__init__.py`, `engine/learning/execution_mode_backtest.py`, `engine/learning/genetic.py`, `engine/strategies/evaluator.py`, `engine/strategies/exit_simulator.py`, `engine/strategies/rulebook.py`, `scripts/research/run_stage3_aggressive.py`. 이는 Stage3 rework 관련 파일이다.

작업 판단: Phase 2 Stage2 수정 기준점으로 현재 복사본을 쓸 수 있다. 단 전체 pristine copy는 아니므로 `DIVERGENT_FULL_COPY` caveat를 유지한다.

## Stage 2 흐름

복사본 기준 runner는 `scripts/research/stage23_rework_20260713/scripts/research/run_stage2.py`. lines `2-9`는 ticker-agnostic Stage 2 runner이며 historical exp script를 import하지 않고 `engine.pipeline.stage2_gate`만 gate로 사용한다고 명시한다.

| 단계 | 함수/파일 | 라인 | 설명 |
|---|---|---|---|
| context/period 구성 | `run_stage2()` | `981-1009` | context, data_start/end, periods, config |
| split별 GA | `run_training()` / `train_one_split()` | `559-629`, `442-556` | train_1/2/3 각각 GA |
| fitness | `train_one_split.evaluate_fn()` | `458-470` | `run_backtest_execution_mode()`의 `result.fitness` 최적화 |
| 대표 hash | `build_representatives()` | `632-650` | 같은 hash 중 train_fitness 최고 rulebook |
| early-cut 평가 | `evaluate_periods()` | `653-903` | alive set을 period별 통과자만 유지 |
| score | `_score_period_candidates()` | `topn_survivor.py:101-136` | expectancy/pf/drawdown percentile member score |
| gate | `stage2_fail_reasons()` | `stage2_gate.py:104-197` | period kind별 gate |
| survivor | `evaluate_periods()` | `814-889`, `1033-1037` | 모든 period 통과 hash 기록 |

Stage2 실행 모드 상수: `ENTRY_EXECUTION_MODE=t_plus_1_open`, `EXIT_EXECUTION_MODE=conservative_core`, `FOLD_EXIT_POLICY=fold_end_mark_to_market` (`run_stage2.py:53-60`).

## 롤링 선별 방식

`PERIODS_TEMPLATE` (`run_stage2.py:69-76`):

1. `stress_pre_2022h1` stress, data_start~2022-06-30
2. `train_3_eval` train, 2024-07-01~2025-06-30
3. `train_2_eval` train, 2023-07-01~2024-06-30
4. `train_1_eval` train, 2022-07-01~2023-06-30
5. `oos_2025h2` oos, 2025-07-01~data_end

`evaluate_periods()`는 `alive`만 평가하고 통과자만 `next_alive`에 남긴다 (`665-682`, `714-737`, `811-814`). 실패 후 미평가 period는 skipped 처리된다 (`815-867`).

Gate 기준 (`stage2_gate.py:16-29`, `104-197`): train은 trade_count>=5, member_score>=10, expectancy>=1.0. stress는 expectancy>=1.0, MDD>=-20, cumulative_return/abs(MDD)>1.0. oos는 trade_count>=5, member_score>=10, expectancy>=1.0, MDD>=-15.

Stage3 OOS gate와 차이: Stage2는 stress를 gate에 포함하고 member_score/trade_count/MDD를 본다. Stage3는 train_1/train_2/recent_1y expectancy 중심이고 stress는 reference다.

## 진화 목적·진입/청산 구조

현재 Stage2 fitness는 `run_backtest_execution_mode()`의 swing `result.fitness`다 (`run_stage2.py:458-470`, `base_kwargs` `310-318`). Stage2는 진입/청산 분리 GA가 아니라 하나의 Rulebook으로 entry와 conservative_core exit를 동시평가한다 (`rulebook.py:240-303`, `run_stage2.py:458-470`, `687-697`).

복사본 Rulebook/evaluator는 이미 Stage3 strict 5-feature schema를 포함한다: `ma_trend`, `macd_hist`, `rsi`, `bb_position`, `volume_ratio` (`rulebook.py:21-62`, `240-253`; `evaluator.py:59-117`, `137-286`).

Stage3 rework v5 진화 목적은 entry-scope fitness + EEC multiplier다 (`run_stage3_aap_eec_penalty_v5_host.py:1-8`, install `44-47`). 실제 구성은 `execution_mode_backtest.py:622-760`: min trades 8, win_rate>=60, mean(net pnl/holding days), trade-count factor, EEC target/floor 6/0.5, MAE/realized loss penalty, disqualified fitness.

## 이식 지점

목표: Stage2 rolling/early-cut 선별은 유지하고 Stage3 entry-scope fitness를 Stage2 training fitness에만 이식한다.

| 유지 대상 | 파일/라인 |
|---|---|
| period order | `run_stage2.py:69-76` |
| alive/next_alive early-cut | `run_stage2.py:665-682`, `714-737`, `811-814` |
| Stage2 gate | `stage2_gate.py:16-29`, `104-197` |
| survivor 산출 | `run_stage2.py:814-889`, `1033-1037` |
| batch index | `run_stage23_batch.py:413-438`, `718-740`, `857-905` |

| 수정 후보 | 라인 | 내용 | 위험 |
|---|---|---|---|
| 복사본 `run_stage2.py::train_one_split.evaluate_fn` | `458-470` | rulebook에 entry-scope marker를 set/reset하고 `run_backtest_execution_mode()` 호출 | 높음 |
| 복사본 `run_stage2.py::base_kwargs` | `310-318` | `fitness_mode='swing'` 유지 가능. marker가 entry fitness override | 낮음 |
| 복사본 GA config | `505-516` | Stage2 pop100/gen50/patience15 유지 권장 | 중간 |
| 복사본 `evaluate_periods()` | `687-697` | marker 절대 켜지 말 것 | 높음 |
| 복사본 `execution_mode_backtest.py` | `48-65`, `622-760` | 기존 entry-scope/EEC 로직 재사용 | 중간 |
| 복사본 `rulebook.py`/`evaluator.py` | `rulebook.py:21-62`, `240-253`; `evaluator.py:59-117`, `137-286` | 5-feature strict interval 이미 존재 | 중간~높음 |
| 복사본 `build_config()` | `936-972` | entry-scope/EEC 적용 여부 기록 | 낮음 |

커플링 위험: Stage2도 `run_backtest_execution_mode()`, `evaluate_signal()`, `should_buy` 계열을 공유한다. entry-scope marker를 전역처럼 켜거나 `should_buy`를 수정하면 training뿐 아니라 rolling 평가/청산/interval-break가 오염된다.

Phase2 안전 순서: marker context manager 추가 -> `train_one_split.evaluate_fn()` 안에서만 set/reset -> py_compile/AST/SHA 검증 -> marker off/on smoke로 trades 동일·fitness만 변경 확인 -> 소수 ticker pilot -> EEC diagnostics는 기록만 하고 Stage2 gate에는 섞지 않는다.

## 보호파일 / daemon / git

보호파일 시작·종료 SHA 동일: `.env` `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce`, `market_history.csv` `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`, `market_history_v2.csv` `b7db98bd5b17b7a95cc852cde6f6dbb86c6347548e9f4c611`.

실제 host: `invest-bot`. daemon PID `494330` 유지 확인. 산출 전 backup commit: `429ae3f`.
