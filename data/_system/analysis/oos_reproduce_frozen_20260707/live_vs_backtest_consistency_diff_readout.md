# CE 룰북 + 시간축 gate — live vs backtest 정합성 통합 diff (READ-ONLY)

대상: `stage3:CE:998b0b638c66`

범위:

```text
backtest 기준: data/_system/analysis/oos_reproduce_frozen_20260707 및 생성 스크립트 data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py
live 기준: data/_system/ops/live_candidate_slots.py, engine/live/real_dashboard_api.py 계열, engine/live/elite_shadow_trader.py
계산/확인 방식: 기존 파일·코드·CSV read-only inspection. 코드 수정·학습·서버 실행 없음.
```

---

## Part A — 룰북 필드 diff

### A1. 로드 경로 추적

#### backtest / oos_reproduce_frozen 계열

```text
재현 스크립트: data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py
후보 파일: data/_system/analysis/oos_reproduce_frozen_20260707/candidate_universe.json
CE candidate_id: stage3:CE:998b0b638c66
CE source_file: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
CE rulebook_hash: 998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78
형식: full final_rulebooks.jsonl row['rulebook']
```

근거:

```text
run_ohlc_freeze_rebuild.py:330-336
  rb_dict = _load_rulebook_for_candidate(candidate)
  rb_dict['ticker'] = ticker
  rb = Rulebook.from_dict(rb_dict)
```

#### live_candidate_slots / 후보 산출 계열

```text
후보 산출 스크립트: data/_system/ops/live_candidate_slots.py
CE source_file: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
로드 함수: engine/live/elite_shadow_trader.py::_load_rulebook_for_candidate(candidate)
형식: full final_rulebooks.jsonl row['rulebook']
```

근거:

```text
live_candidate_slots.py:381
  report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)

live_candidate_slots.py:403
  ev = evaluate_candidate(candidate, ctx=ctx)

elite_shadow_trader.py:397-402
  rb_dict = _load_rulebook_for_candidate(candidate)
  rb = Rulebook.from_dict(rb_dict)

elite_shadow_trader.py:223-239
  stage3 source_file + rulebook_hash로 final_rulebooks.jsonl row['rulebook'] 반환
```

판정:

```text
backtest full rulebook vs live_candidate_slots full revalidation: SAME_FILE
```

#### real_dashboard 계열

```text
상태 파일: data/_system/live_slots_state.json
형식: compact candidate snapshot
full rulebook dict 저장 여부: 없음
selected_rulebook 저장 여부: 없음
```

기존 readout 확인값:

```text
dashboard-real CE 직접 매수 source: live_slots_state_fallback
full_rulebook_revalidation: NOT_FOUND_IN_THIS_PATH
candidate_snapshot.rulebook present: False
selected_rulebook present: False
```

판정:

```text
backtest full rulebook vs real_dashboard compact snapshot: DIFFERENT_FILE
```

### A2. 필드 diff 요약

Full rulebook 경로 기준:

| field group | backtest값 | live_candidate_slots값 | 일치 |
|---|---:|---:|---:|
| sector_name | tech | tech | YES |
| use_market_entry_adjustment | False | False | YES |
| sector_strength_weight | -0.6208615991099308 | -0.6208615991099308 | YES |
| vix_sensitivity | -1.0 | -1.0 | YES |
| signal_threshold | 2.6541866643896674 | 2.6541866643896674 | YES |
| event_response_* | full nonzero/mixed values | same full nonzero/mixed values | YES |
| RSI params | rsi_low=36.93850271203499, rsi_high=80.0 | same | YES |
| MACD param | weight_macd_golden=1.167787881408684, macd_min_hist=0.0 | same | YES |
| BB params | weight_bb_near_lower=0.8477763470822091, bb_proximity=1.1324786725411682 | same | YES |
| stop/trailing/timeout/take_profit | hybrid, SL=2.9488902763720244, trailing=2.53183095624166, max_holding_days=10, TP=3.7074125087814065 | same | YES |

Compact snapshot을 full rulebook 대체물로 복원하는 경우의 차이:

| field group | backtest full | snapshot/default reconstructed | 상태 |
|---|---:|---:|---|
| use_market_entry_adjustment | False | True | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT |
| vix_sensitivity | -1.0 | 0.0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT |
| event_response_* | nonzero/mixed | 0.0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT |
| RSI params | 36.93850271203499 / 80.0 | 30.0 / 70.0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT |
| BB params | 0.8477763470822091 / 1.1324786725411682 | 1.0 / 1.05 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT |

전체 필드 CSV:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/live_vs_backtest_consistency_field_diff.csv
```

### Part A 판정

```text
Part A live_candidate_slots 후보 산출만 기준: RULEBOOK_CONSISTENT
Part A real_dashboard 직접 매수 계열 포함 기준: RULEBOOK_DRIFT_HARMFUL
Part A 최종: RULEBOOK_DRIFT_HARMFUL
```

판정 근거:

```text
- oos_reproduce_frozen과 live_candidate_slots 후보 산출은 같은 full final_rulebooks.jsonl을 읽는다.
- real_dashboard 직접 매수 fallback 경로는 full 룰북 재검증 없이 live_slots_state compact snapshot candidate payload를 사용한 사례가 확인되어 있다.
- compact snapshot에는 full rulebook dict/selected_rulebook이 없다.
```

---

## Part B — 시간축 신호 gate diff

### B1. backtest / oos_reproduce_frozen 후보 진입 경로

확인된 경로:

```text
run_ohlc_freeze_rebuild.py:386
  sig = evaluate_signal(...)

run_ohlc_freeze_rebuild.py:391-393
  if not sig.should_buy: continue
  meta['buy_signals'] += 1

run_ohlc_freeze_rebuild.py:401-421
  simulate_exit(...)
  row = record_from_trade(...)
```

검색 결과:

```text
build_signal_history call in oos_reproduce_frozen path: NOT_FOUND
judge_buy_gate call in oos_reproduce_frozen path: NOT_FOUND
consecutive_buy_days gate in oos_reproduce_frozen path: NOT_FOUND
chase_from_first_buy_pct gate in oos_reproduce_frozen path: NOT_FOUND
```

따라서:

```text
oos_reproduce_frozen 후보 진입 gate: current evaluate_signal().should_buy only
시간축 gate 적용: NO
```

### B2. live_candidate_slots / dashboard-real 후보 선정 경로

확인된 경로:

```text
live_candidate_slots.py:403
  ev = evaluate_candidate(candidate, ctx=ctx)

live_candidate_slots.py:414-418
  if not ev.get('should_buy'): continue
  pool.append(public_candidate_row(candidate, ev, gate, spy))
```

검색 결과:

```text
build_signal_history call in live_candidate_slots.py: NOT_FOUND
judge_buy_gate call in live_candidate_slots.py: NOT_FOUND
consecutive_buy_days gate in live_candidate_slots.py: NOT_FOUND
chase_from_first_buy_pct gate in live_candidate_slots.py: NOT_FOUND
```

따라서:

```text
live_candidate_slots 후보 선정 gate: gate_keep + current evaluate_candidate().should_buy only
시간축 gate 적용: NO
```

### B3. first_signal_at 사용 여부

확인된 경로:

```text
live_candidate_slots.py:419-450
  first_seen_signals에 first_signal_at / last_seen_at / first_signal_price / first_final_score 저장
  row['first_signal_at'] = rec.get('first_signal_at')
  row['last_seen_at'] = rec.get('last_seen_at') or now_iso
```

판정:

```text
first_signal_at 판단 사용: NOT_FOUND
first_signal_at 후보 자격 gate 사용: NO
first_signal_at 용도: 표시/추적용 metadata
```

### B4. 참고: elite_strategy_sim의 시간축 gate

확인된 별도 경로:

```text
engine/live/elite_strategy_sim.py:169-213
  judge_buy_gate(candidate, ev=ev, days=12)
  build_signal_history(..., days=12)
  consecutive_buy_days / chase_from_first_buy_pct / pullback/rebound 조건 계산

engine/live/elite_strategy_sim.py:460
  judgment = judge_buy_gate(candidate, ev=ev, days=12)
```

상태:

```text
elite_strategy_sim: time-axis gate APPLIED
live_candidate_slots: time-axis gate NOT_APPLIED
oos_reproduce_frozen: time-axis gate NOT_APPLIED
```

이 항목은 `oos_reproduce_frozen` 기준 Part B 판정에는 포함하지 않는다.

### B5. gate 기여도

기존 frozen 기록:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv columns: 28
judge_buy_gate / build_signal_history / consecutive_buy_days / chase_from_first_buy_pct 관련 컬럼: 없음
```

키워드 검색:

```text
data/_system/analysis/oos_reproduce_frozen_20260707 내부 judge_buy_gate 기록: NOT_FOUND
NO_BUY_LATE_CHASE / WAIT_PRICE_CHASE / BUY_FRESH 기록: NOT_FOUND
```

건수:

| 항목 | 값 |
|---|---:|
| oos_reproduce_frozen에서 judge_buy_gate로 탈락한 후보/시점 | 0 / NOT_APPLIED |
| gate 통과 진입 건수 | UNKNOWN_AS_GATE_NOT_RECORDED |
| gate 무시 시 추가 진입 건수 | UNKNOWN_AS_GATE_NOT_RECORDED |
| gate 적용/무시 건수 차이 | 0 within oos_reproduce_frozen recorded path, because gate absent |

### Part B 판정

```text
Part B oos_reproduce_frozen vs live_candidate_slots: GATE_CONSISTENT
```

판정 근거:

```text
- oos_reproduce_frozen 후보 진입에는 build_signal_history/judge_buy_gate(days=12)가 적용되지 않는다.
- live_candidate_slots 후보 선정에도 build_signal_history/judge_buy_gate(days=12)가 적용되지 않는다.
- 둘 다 current should_buy 단일 tick 기준이다.
- first_signal_at은 live 후보 판단 gate가 아니라 metadata다.
```

HARMFUL 기준 확인:

```text
backtest가 시간축 gate로 진입을 걸렀는데 live에는 그 gate가 없는 경우: NOT_FOUND for oos_reproduce_frozen
```

---

## 최종 판정

```text
Part A: RULEBOOK_DRIFT_HARMFUL
Part B: GATE_CONSISTENT
```

주의 범위:

```text
- Part A HARMFUL은 live_candidate_slots 후보 산출 full-rulebook 경로가 아니라 real_dashboard 직접 매수 fallback 계열을 포함한 통합 판정이다.
- Part B는 oos_reproduce_frozen 기준 판정이다. elite_strategy_sim 기준으로 비교하면 시간축 gate 적용 여부가 달라진다.
```
