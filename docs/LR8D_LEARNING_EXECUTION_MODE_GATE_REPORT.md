# LR8D Learning Execution Mode Gate Report

작성일: 2026-06-10 KST  
상태: Phase 1 gate 통과  
실행 모드:

```bash
venv/bin/python scripts/research/run_central_portfolio_noop_gate.py --mode learning_execution_mode_gate
```

---

## 1. 결론

Full PIT rerun의 첫 선행조건인 학습부 execution-mode semantics를 독립 wrapper로 검증했다.

검증 대상:

```text
entry_execution_mode = t_plus_1_open
exit_execution_mode = conservative_core
fold_exit_policy = fold_end_mark_to_market
```

결과:

```text
passed: true
pytest: 46 passed
```

중요:

```text
기존 engine.learning.backtest.run_backtest 본체는 아직 직접 수정하지 않았다.
Phase 1에서는 같은 학습부 helper를 재사용하는 execution-mode 전용 wrapper를 먼저 만들고,
semantics를 테스트로 닫았다.
```

이 접근을 택한 이유:

```text
기존 GA/learning 경로를 한 번에 크게 뜯기 전에,
T+1/conservative_core/fold-bound 규칙을 독립적으로 검증하기 위해서다.
```

---

## 2. 추가 구현

새 파일:

```text
engine/learning/execution_mode_backtest.py
tests/test_learning_execution_modes.py
```

새 CLI mode:

```text
learning_execution_mode_gate
```

출력 경로:

```text
data/_system/research/learning_execution_mode_gate/summary.json
```

---

## 3. 검증한 invariant

### 3.1 close mode backward-compatible semantics

```text
entry_execution_mode=close
entry_price = signal date close
entry_signal_date == entry_fill_date
```

게이트 결과:

```text
close_mode_has_trade: true
close_mode_entry_uses_signal_close: true
```

### 3.2 T+1 open entry

```text
signal date: 2024-03-25
fill date:   2024-03-26
entry_price: next day Open
```

게이트 결과:

```text
tplus1_has_trade: true
tplus1_signal_date: 2024-03-25
tplus1_fill_date: 2024-03-26
tplus1_entry_uses_next_open: true
```

### 3.3 fold_end leakage guard

fold_end:

```text
2024-03-27
```

검증:

```text
fold_end_exit_date: 2024-03-27
fold_end_exit_reason: fold_end_mark_to_market
fold_end_no_future_exit: true
```

즉 entry가 fold 안에 있더라도 exit scoring은 fold_end 이후 가격을 보지 않는다.

### 3.4 fill after fold_end 금지

검증:

```text
same-day fold end에서 T+1 fill이 fold_end를 넘으면 trade_count = 0
fill_after_fold_end_skipped: true
```

---

## 4. 왜 이 gate가 중요한가

기존 학습 backtest는 다음 leakage 위험이 있었다.

```text
2023 OOS fold에서 2023년 말 진입
→ simulate_exit이 전체 df를 사용
→ 2024 가격으로 청산
→ 2023 OOS score가 2024 정보를 본 셈
```

이번 gate는 이 문제를 다음 방식으로 차단한다.

```text
1. fold_end 이후 row를 exit df에서 제거
2. T+1 fill date가 fold_end를 넘으면 진입 스킵
3. fold_end까지 자연청산되지 않으면 fold_end_mark_to_market으로 평가
```

---

## 5. 테스트

실행:

```bash
venv/bin/python -m pytest -q \
  tests/test_learning_execution_modes.py \
  tests/test_exit_policy.py \
  tests/test_exit_policy_adapter.py \
  tests/test_exit_trade_metadata.py \
  tests/test_live_exit_policy_cutover.py \
  tests/test_live_exit_dry_run_rehearsal.py
```

결과:

```text
46 passed
```

신규 테스트:

```text
test_tplus1_entry_uses_next_open_and_records_signal_fill_dates
test_fold_end_mark_to_market_does_not_use_future_prices
test_tplus1_fill_after_fold_end_is_skipped
test_close_mode_keeps_signal_close_entry_price
```

---

## 6. 남은 Phase 1b

현재 상태는 execution-mode semantics가 독립 wrapper에서 닫힌 것이다.

다음 단계:

```text
1. 기존 GA runner가 이 wrapper를 사용할 수 있도록 배선한다.
2. 또는 run_backtest 본체에 동일 인자를 default-preserving 방식으로 통합한다.
3. single ticker smoke에서 legacy vs T+1/conservative_core/fold-bound 차이를 확인한다.
```

주의:

```text
이 gate만으로 full PIT rerun을 시작하면 안 된다.
GA runner가 실제로 이 execution path를 사용한다는 배선 게이트가 추가로 필요하다.
```
