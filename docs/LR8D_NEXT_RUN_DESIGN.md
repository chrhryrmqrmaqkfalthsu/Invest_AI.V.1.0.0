# LR8D Next RUN Design — hard stop invariant + sell_omen threshold 재검증

작성시각: 2026-06-10 KST  
상태: 설계서 초안  
현재 live 모드: `alpaca_paper`, `EXIT_LIVE_POLICY=1`, 관찰 모드 유지

## 1. 목적

다음 LR8D 계열 RUN은 기존 survivor 룰북을 그대로 신뢰하기보다, 청산 안전 invariant와 sell_omen 탐색 범위를 반영한 상태에서 다시 검증한다.

핵심 목표는 세 가지다.

1. `exit_strategy=trailing`에도 shared core 수준의 hard stop 백스톱을 정식 포함한다.
2. `sell_omen_threshold` 탐색 범위는 현재 점수 분포에 맞춘 `0.30~0.70`을 유지한다.
3. 기존 LR8D survivor 라벨을 새 invariant 기준으로 재검증하고, 기존 성능지표와의 차이를 명시한다.

## 2. 현재 확인된 코드 상태

### 2.1 sell_omen threshold 유전자 범위

현재 범위는 이미 수정 완료되어 있다.

```python
# engine/strategies/rulebook.py
"sell_omen_threshold": (0.30, 0.70)
```

관련 genetic 적용부:

```python
# engine/learning/genetic.py
th_lo, th_hi = PARAM_RANGES["sell_omen_threshold"]
rb.sell_omen_threshold = _clamp_float(getattr(rb, "sell_omen_threshold", th_hi), th_lo, th_hi)
```

따라서 다음 RUN에서는 threshold 범위를 추가로 바꾸지 않고, 이 범위가 실제 survivor에 반영되는지 검증한다.

### 2.2 shared core trailing 분기

현재 shared core의 `trailing` 분기에는 hard stop이 없다.

```python
# engine/core/exit_policy.py
elif strategy == "trailing":
    if breakeven_hit and breakeven_stop is not None:
        reason, trigger_price = "breakeven_stop", breakeven_stop
    elif sell_omen_hit:
        reason, trigger_price = "sell_omen", ref_price
    elif trailing_hit:
        reason, trigger_price = "trailing", updated_trailing
    elif timeout_hit:
        reason, trigger_price = "time_out", ref_price
```

`stop_hit` 자체는 이미 계산돼 있다.

```python
stop_hit = low <= position.stop_price
```

정식 invariant 후보는 다음 형태다.

```python
elif strategy == "trailing":
    if stop_hit:
        reason, trigger_price = "stop_loss", position.stop_price
    elif breakeven_hit and breakeven_stop is not None:
        reason, trigger_price = "breakeven_stop", breakeven_stop
    elif sell_omen_hit:
        reason, trigger_price = "sell_omen", ref_price
    elif trailing_hit:
        reason, trigger_price = "trailing", updated_trailing
    elif timeout_hit:
        reason, trigger_price = "time_out", ref_price
```

주의: 이 변경은 shared core 변경이므로 기존 survivor의 성능 전제를 바꾼다. 라이브에는 이미 live-only hard stop guard가 있으나, shared core 정식 변경은 반드시 RUN 재검증과 같이 처리한다.

## 3. 현재 live 안전 상태

현재 라이브는 shared core를 수정하지 않고, `engine/live/exit_policy_adapter.py`에서 live-only hard stop guard를 적용 중이다.

동작:

```text
현재가 <= position.stop_price
→ 모든 exit_strategy에서 live-only stop_loss 강제 청산
```

현재 실행 상태:

```text
mode=alpaca_paper
EXIT_LIVE_POLICY=1
보유 5종목
```

## 4. 라이브 첫 청산 감시 베이스라인

Alpaca 현재가 기준 stop까지 거리:

| Symbol | Current | Entry | Stop | Stop distance | PnL | Strategy |
|---|---:|---:|---:|---:|---:|---|
| CAKE | 67.31 | 67.27 | 60.11 | 10.69% | +0.05% | trailing |
| CW | 708.89 | 710.05 | 634.70 | 10.47% | -0.16% | trailing |
| LASR | 60.86 | 63.79 | 52.92 | 13.05% | -4.59% | hybrid |
| MPC | 261.49 | 258.13 | 232.26 | 11.18% | +1.30% | fixed |
| NBIX | 161.44 | 161.72 | 148.23 | 8.18% | -0.17% | trailing |

가장 stop에 가까운 종목은 `NBIX`다. 다음은 `CW`, `CAKE`, `MPC`, `LASR` 순서다. 단 LASR는 현재 PnL 기준으로는 가장 약하지만 stop까지의 가격거리 자체는 가장 멀다.

현재 safety state:

```text
orders_today=5
invested_today=150.324126886 USD notional
realized_pnl_today=0.0
consecutive_losses=0
kill_until=""
```

실시간 수동 감시 명령:

```bash
tail -f data/_system/logs/run_live_stage1_alpaca.log \
  | grep -iE 'stop_loss|trailing|breakeven|sell_omen|EXIT|청산|filled.*SELL'
```

## 5. 다음 RUN 설계

### 5.1 RUN 이름

제안명:

```text
lr8d_hardstop_threshold_recheck
```

또는 날짜 포함:

```text
lr8d_hardstop_threshold_recheck_YYYYMMDD
```

### 5.2 변경사항

필수 변경:

1. shared core `engine/core/exit_policy.py`의 `trailing` 분기에 `stop_hit` 우선조건 추가.
2. `sell_omen_threshold` 탐색 범위 `0.30~0.70` 유지 확인.
3. live-only hard stop guard는 RUN 완료 전까지 유지.

선택 변경:

1. `trailing` 전략에 `take_profit`을 넣을지 여부는 이번 RUN에서는 제외한다.
2. 이유: trailing 전략은 원래 수익을 끌고 가는 전략일 수 있으므로, target 익절 추가는 별도 실험 축이다.

### 5.3 통과 기준

최소 통과 기준:

1. strict_k3 survivor가 충분히 남아야 한다.
2. promoted live 후보군 16개 중 대체 가능한 종목 수가 유지되어야 한다.
3. 기존 LR8D 결과 대비 expectancy/DD 악화가 허용범위 안이어야 한다.

권장 기준:

```text
expectancy_pct: 기존 대비 큰 폭 악화 없음
worst DD: 개선 또는 동등 수준
trade count: 종목별 최소 표본 유지
sell_omen coverage: target/stage1/survivor/strict_k3 모두 PASS 유지
```

정확한 수치 기준은 기존 `lr8d_postrun_analysis.py` 출력 형식에 맞춰 비교한다.

## 6. 검증 체크리스트

### 6.1 정적 검증

```bash
python -m py_compile engine/core/exit_policy.py engine/live/exit_policy_adapter.py
python -m pytest -q tests/test_live_exit_policy_cutover.py
```

추가해야 할 테스트:

1. `exit_strategy=trailing`, `price < stop_price`, `highest_profit_pct < activation_pct`에서도 shared core가 `stop_loss` 반환.
2. `exit_strategy=trailing`, `price > target_price`는 현재 설계상 즉시 `take_profit`이 아님을 명시.
3. `hybrid/fixed` 기존 우선순위가 깨지지 않음.

### 6.2 RUN 전 preflight

```bash
python scripts/research/sell_omen_coverage_preflight.py \
  --score-table data/_system/ml_sell_omen/sell_omen_scores_lr8d85.csv
```

확인 항목:

```text
target coverage 100%
stage1 coverage 100%
survivors coverage 100%
strict_k3 coverage 100%
```

### 6.3 RUN 후 비교

1. 기존 LR8D survivor와 새 hard-stop survivor의 교집합/차집합.
2. `exit_strategy=trailing` 종목의 청산 사유 분포 변화.
3. `stop_loss` 증가로 expectancy가 개선됐는지 악화됐는지.
4. `sell_omen_threshold`가 실제로 0.30~0.70 범위에서 선택되는지.
5. live 후보 16개가 새 기준에서도 생존하는지.

## 7. 라이브 운영 방침

RUN 재검증이 끝나기 전까지 live는 다음 상태를 유지한다.

```text
alpaca_paper
EXIT_LIVE_POLICY=1
live-only hard stop guard ON
sell_omen LR8D85 table ON
order_notional=30 USD
```

현재는 모의투자 검증이므로 코드 변경 후 재기동은 가능하지만, shared core hard stop 변경은 RUN 재검증 없이 live 룰북 신뢰 근거로 사용하지 않는다.

## 8. 남은 백로그

1. shared core hard stop invariant 적용 branch 작성.
2. hard stop invariant 기준 LR8D 재검증 RUN.
3. `sell_omen_scores_lr8d85.csv` freshness 문제 검토.
4. ticker_sentiment 최신화로 KT/MPLX stale 해소.
5. pending fill metadata의 `signal_score_at_entry` / `signal_threshold_at_entry` 0.0 기록 문제 개선.
