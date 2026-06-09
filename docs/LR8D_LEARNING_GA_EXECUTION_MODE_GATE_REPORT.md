# LR8D Learning GA Execution Mode Gate Report

작성일: 2026-06-10 KST  
상태: Phase 1b gate 통과  
실행 모드:

```bash
venv/bin/python scripts/research/run_central_portfolio_noop_gate.py --mode learning_ga_execution_mode_gate
```

---

## 1. 결론

GA `evaluate_fn`이 Phase 1a의 fold-aware execution-mode wrapper를 실제로 호출할 수 있음을 검증했다.

검증 대상:

```text
GA evaluate_fn
→ run_backtest_execution_mode
→ entry_execution_mode=t_plus_1_open
→ exit_execution_mode=conservative_core
→ fold_exit_policy=fold_end_mark_to_market
→ fitness_mode=swing
```

결과:

```text
passed: true
pytest: 46 passed
evaluate_fn_call_count: 11
generations_run: 2
performance_warning: false
```

중요한 범위 제한:

```text
이 gate는 GA callback path 검증이다.
아직 full LR8E/PIT production runner 전체를 돌린 것은 아니다.
다음 단계에서는 이 검증된 path를 single ticker smoke / mini RUN runner에 연결한다.
```

---

## 2. 왜 Phase 1b가 필요한가

Phase 1a는 wrapper semantics를 닫았다.

```text
T+1 fill
conservative_core exit
fold_end_mark_to_market
fill_after_fold_end skip
```

하지만 GA는 다음 구조다.

```text
run_ga(base_rulebook, evaluate_fn)
```

따라서 실제 full rerun의 핵심은 GA 본체가 아니라, runner가 만드는 `evaluate_fn`이 어떤 backtest 함수를 호출하느냐다.

기존 LR8C/LR8D runner는 다음 경로를 썼다.

```text
evaluate_fn → engine.learning.backtest.run_backtest
```

Full PIT 경로는 다음을 써야 한다.

```text
evaluate_fn → engine.learning.execution_mode_backtest.run_backtest_execution_mode
```

이번 gate는 이 callback path를 작은 GA로 검증했다.

---

## 3. 추가 구현

새 파일:

```text
engine/learning/execution_mode_ga_gate.py
```

수정 파일:

```text
engine/learning/execution_mode_backtest.py
scripts/research/run_central_portfolio_noop_gate.py
```

`execution_mode_backtest.py` 수정 내용:

```text
fitness_mode 인자 추가
complexity_penalty_per_mask 인자 추가
fitness_mode=swing 지원
```

이유:

```text
기존 LR8D runner는 fitness_mode=swing을 사용했다.
wrapper가 swing fitness를 반환하지 않으면 GA가 다른 목적함수를 학습하게 된다.
```

---

## 4. GA gate 조건

작은 synthetic GA 설정:

```text
population: 6
generations: 2
elite_ratio: 0.33
mutation_rate: 0.05
mutation_strength: 0.05
seed_pattern_ratio: 0.34
random_seed: 20260610
```

실행 결과:

```text
evaluate_fn_call_count: 11
generations_run: 2
best_fitness: -100.0
ga_elapsed_seconds: 0.0263
```

fitness가 음수인 것은 synthetic fold가 의도적으로 짧고, fold_end_mark_to_market이 발생하도록 설계했기 때문이다. 이 gate의 목적은 수익성 평가가 아니라 callback path와 execution semantics 검증이다.

---

## 5. 검증한 invariant

```text
ga_evaluate_fn_called: true
ga_generations_run_positive: true
ga_best_has_fitness: true
wrapper_returns_backtest_result_shape: true
sample_trade_has_tplus1_mode: true
sample_trade_has_conservative_core_mode: true
sample_trade_fold_end_bounded: true
performance_ratio_positive: true
```

BacktestResult shape:

```text
fitness
trade_count
expectancy_pct
profit_factor
trades
```

sample trade metadata:

```text
entry_execution_mode: t_plus_1_open
exit_execution_mode: conservative_core
fold_exit_policy: fold_end_mark_to_market
exit_date: 2024-03-27
exit_reason: fold_end_mark_to_market
```

---

## 6. 성능 감

20회 반복 synthetic call 기준:

```text
legacy_seconds: 0.0880
wrapper_seconds: 0.0449
wrapper_vs_legacy_ratio: 0.5105
warning_threshold_ratio: 3.0
performance_warning: false
```

해석:

```text
synthetic micro-benchmark에서는 wrapper가 legacy보다 느리지 않았다.
```

주의:

```text
이 수치는 full RUN 시간 추정으로 직접 쓰면 안 된다.
실제 85×4 RUN 전에는 Phase 4 STEP0 timing을 반드시 다시 측정한다.
```

---

## 7. 현재 남은 작업

Phase 1b에서 닫힌 것:

```text
GA evaluate_fn이 execution-mode wrapper를 호출할 수 있음
wrapper가 swing fitness를 반환함
BacktestResult 인터페이스가 기존 GA fitness 경로와 맞음
synthetic performance warning 없음
```

아직 남은 것:

```text
full LR8E/PIT runner 전체 구현
single ticker smoke
mini RUN infra gate
STEP0 timing
full 85×4 shard RUN
```

다음 단계:

```text
Phase 2: single ticker smoke
```

권장 대상:

```text
EME
MCK
MELI
```

목표:

```text
실제 ticker 데이터에서 legacy runner와 PIT execution-mode runner 차이를 확인한다.
특히 trade_count, fold_end_mark_to_market, time_out, stop_loss 변화와 runtime을 본다.
```
