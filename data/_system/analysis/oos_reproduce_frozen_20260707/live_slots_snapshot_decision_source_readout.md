# live_slots_state snapshot 실전 판단 사용 여부 — READ-ONLY

대상: `stage3:CE:998b0b638c66`

## 최종 판정

```text
SNAPSHOT_USED_IN_DECISION
```

정확한 의미:

```text
- live_candidate_slots.py의 should_buy 후보 생성 판단은 snapshot이 아니라 full 룰북을 다시 읽는다.
- s2_auto_trader의 자동매수 검증/주문 metadata도 snapshot이 아니라 full 룰북을 다시 읽는다.
- PositionManager/ExitPolicy 자동청산은 entry 시점에 저장된 position.rulebook_snapshot을 읽는다.
- 그러나 어제 CE 실제 /dashboard-real 직접 매수는 full 룰북 재검증 없이 live_slots_state fallback snapshot을 후보 소스로 사용해 Alpaca live 주문을 냈다.
- CE 대시보드 예약청산 OCO 상태도 position_snapshot.context_source/backtest_source가 live_slots_state.slots로 남아 있다.
```

따라서 snapshot은 전체 시스템에서 표시 전용만은 아니다. 특히 dashboard-real 직접 주문 경로에서는 실전 주문 후보 식별/가격/수량 산정의 입력으로 쓰였다.

주의: 확인된 위험은 `Rulebook.from_dict(live_slots_state_row)`로 룰북을 복원해 진입/청산한 것이 아니다. 확인된 위험은 `live_slots_state` compact snapshot이 full 룰북 재검증 없이 직접 주문 경로의 후보 payload로 사용된 것이다.

## 1. live_candidate_slots.py should_buy / 후보 생성 경로

판정:

```text
source: FULL_RULEBOOK
snapshot: NOT_USED_FOR_SHOULD_BUY
```

근거 코드:

```text
data/_system/ops/live_candidate_slots.py:381
  report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)

data/_system/ops/live_candidate_slots.py:403
  ev = evaluate_candidate(candidate, ctx=ctx)

data/_system/ops/live_candidate_slots.py:414-418
  if not ev.get("should_buy"): ... continue
  pool.append(public_candidate_row(candidate, ev, gate, spy))
```

`evaluate_candidate()` 내부:

```text
engine/live/elite_shadow_trader.py:397
  rb_dict = _load_rulebook_for_candidate(candidate)

engine/live/elite_shadow_trader.py:402
  rb = Rulebook.from_dict(rb_dict)

engine/live/elite_shadow_trader.py:423-431
  evaluate_signal(...)
```

Stage3 룰북 로더:

```text
engine/live/elite_shadow_trader.py:223-239
  path = Path(str(candidate.get("source_file") or ""))
  target_hash = str(candidate.get("rulebook_hash") or "")
  if row["rulebook_hash"] == target_hash:
      return row["rulebook"]
```

CE source:

```text
candidate_id: stage3:CE:998b0b638c66
source_file: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
rulebook_hash: 998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78
```

결론:

```text
live_candidate_slots.py가 should_buy를 만들 때는 live_slots_state snapshot을 룰북으로 복원하지 않는다.
full final_rulebooks.jsonl의 row["rulebook"]을 Rulebook.from_dict()로 복원한다.
```

## 2. s2_auto_trader 자동매수 판단 경로

판정:

```text
source: FULL_RULEBOOK
snapshot: USED_ONLY_FOR_POOL_SORTING/ID_SELECTION
```

근거 코드:

```text
engine/live/s2_auto_trader.py:290-298
  candidate_pool()은 live_slots_state.candidate_pool에서 후보 row를 읽고 정렬한다.
```

하지만 실제 should_buy 검증은 full payload를 다시 찾는다.

```text
engine/live/s2_auto_trader.py:300-308
  _candidate_full_payload(candidate_id)
  -> build_elite_shadow_report(...)
  -> candidate_id가 같은 full cand 반환

engine/live/s2_auto_trader.py:313-329
  _validate_candidate_signal(row)
  -> evaluate_candidate(full, ctx=get_market_context())
  -> should_buy가 false면 차단
```

주문 제출 metadata도 full 룰북이다.

```text
engine/live/s2_auto_trader.py:433-436
  full = _candidate_full_payload(plan.candidate_id)
  rb = Rulebook.from_dict(dict(full["rulebook"]))

engine/live/s2_auto_trader.py:445-455
  intent["selected_rulebook"] = dict(full["rulebook"])
```

체결 reconciliation:

```text
engine/live/s2_auto_trader.py:462-470
  _StaticRulebookProvider(plan.ticker, rb, atr, {})
  BuyReconciliationService(...)
  reconcile(... BuyPreflight(rulebook=rb))
```

결론:

```text
s2_auto_trader는 live_slots_state snapshot에서 후보 ID/정렬값은 읽지만,
실제 should_buy 검증과 selected_rulebook metadata는 full 룰북을 다시 읽는다.
```

현재 설정:

```text
master_enabled: false
auto_buy_enabled: false
auto_exit_enabled: false
exit.engine: PositionManager_ExitPolicy
```

## 3. 자동청산 / PositionManager / ExitPolicy 룰북 소스

판정:

```text
source: POSITION_ENTRY_RULEBOOK_SNAPSHOT
live_slots_state snapshot: NOT_USED_BY_POSITIONMANAGER_EXITPOLICY
```

PositionManager 등록 시점:

```text
engine/live/position_manager.py:250-255
  snapshot = rulebook.to_dict()
  member_hash = compute_member_hash(rulebook)

engine/live/position_manager.py:279-297
  PositionEntry(... rulebook_snapshot=dict(snapshot), member_hash=member_hash ...)
```

청산 체크:

```text
engine/live/position_manager.py:652-659
  if _exit_live_policy_enabled() and pos.rulebook_snapshot:
      policy_evaluation = self._evaluate_policy(ticker, pos, price)
      if policy_evaluation.decision.should_exit:
          exit_reason = policy_evaluation.decision.reason
```

ExitPolicy 룰북 복원:

```text
engine/live/exit_policy_adapter.py:220-227
  snapshot = pos.rulebook_snapshot
  return Rulebook.from_dict(dict(snapshot)), "position_snapshot"
```

결론:

```text
PositionManager/ExitPolicy 자동청산은 live_slots_state가 아니라 포지션 등록 시점의 pos.rulebook_snapshot을 사용한다.
```

단, CE 현재 상태:

```text
positions.json에 CE 없음
=> 어제 CE 직접 매수는 PositionManager 등록 경로로 확인되지 않음
=> CE 자동청산은 PositionManager/ExitPolicy 경로가 아니라 dashboard-real Alpaca 예약 주문/OCO 경로로 확인됨
```

## 4. dashboard-real 직접 매수 경로

판정:

```text
source: LIVE_SLOTS_STATE_SNAPSHOT
full rulebook revalidation: NOT_FOUND_IN_THIS_PATH
Rulebook.from_dict(snapshot): NOT_USED
```

base 후보 로더:

```text
engine/live/real_dashboard_api.py:676-689
  _candidate_for_real(candidate_id)
  -> _real_candidate_state(...).candidates에서 후보를 찾음
```

fallback patch:

```text
engine/live/real_dashboard_holding_days_patch.py:137-145
  patched_candidate_for_real(candidate_id)
  -> base _candidate_for_real 실패 시
  -> state = real_api._live_slots_state()
  -> row = real_api._find_live_slot_candidate_raw(state, cid)
```

fallback payload marking:

```text
engine/live/real_dashboard_holding_days_patch.py:162-168
  out = dict(row)
  out["status"] = "pending"
  out["manual_buy_enabled"] = True
  out.setdefault("candidate_source", "live_slots_state_fallback")
  out.setdefault("real_candidate_fallback", True)
```

직접 주문:

```text
engine/live/real_dashboard_api.py:698-729
  candidate = _candidate_for_real(req.candidate_id)
  row["candidate_snapshot"] = candidate

engine/live/real_dashboard_api.py:734-742
  price = broker.get_current_price(ticker) or candidate["price"]
  shares = notional / price
  broker.place_buy(... MARKET ...)
```

확인된 CE intent:

```text
intent_id: real-buy:stage3:CE:998b0b638c66
execution_mode: direct_alpaca_live_market_order
source: dashboard-real-detail
candidate_source: live_slots_state_fallback
real_candidate_fallback: True
broker_order_client_id: km-real-buy-1783520835-CE
selected_rulebook present: False
candidate_snapshot.rulebook present: False
```

관련 로그:

```text
data/_system/live_slots_events.jsonl
{"event":"DASHBOARD_REAL_BUY_INTENT","candidate_id":"stage3:CE:998b0b638c66","ticker":"CE","execution_mode":"direct_alpaca_live_market_order","source":"dashboard-real-detail","status":"submitted","time":"2026-07-08T14:27:15.593482+00:00"}
```

결론:

```text
어제 CE 실제 직접 매수는 live_slots_state snapshot fallback을 사용했다.
이 경로에는 full final_rulebooks.jsonl 재검증이나 Rulebook.from_dict(snapshot) 복원이 없다.
```

## 5. dashboard-real Alpaca 예약청산 / OCO 경로

판정:

```text
source: ALPACA_LIVE_POSITION + USER/REQUEST TP_SL_PRICES + DASHBOARD_SNAPSHOT_METADATA
Rulebook.from_dict(snapshot): NOT_USED
PositionManager/ExitPolicy: NOT_USED_FOR_CE
```

예약 주문 생성:

```text
engine/live/real_dashboard_alpaca_exit_orders_patch.py:220-237
  _create_or_replace_exit_order(req)
  -> position = _held_position(ticker)
  -> held_shares = position["shares"]
  -> take_profit, stop_loss = _validate_exit_prices(position, req.take_profit_price, req.stop_loss_price)

engine/live/real_dashboard_alpaca_exit_orders_patch.py:259-267
  _submit_exit_order(... take_profit=take_profit, stop_loss=stop_loss ...)

engine/live/real_dashboard_alpaca_exit_orders_patch.py:270-284
  state["orders"][ticker] includes position_snapshot = position
```

`position` enrichment source:

```text
engine/live/real_dashboard_api.py:373-418
  _candidate_context_for_real_holding(ticker)
  -> live_slots_state slots/waitlist/candidate_pool
  -> real_buy_intent candidate_snapshot

engine/live/real_dashboard_api.py:440-462
  _enrich_real_position_with_candidate_context(row)
  -> context_source/backtest_source 등 표시/메타 필드 주입
```

CE 예약청산 state 확인:

```text
source: dashboard_real_exit_panel
mode: alpaca_reserved_exit_order
order_kind: oco
position_snapshot.backtest_source: live_slots_state.slots
position_snapshot.context_source: live_slots_state.slots
position_snapshot.rulebook keys:
  direction, exit_strategy, expectancy_pct, fitness, max_drawdown_pct,
  max_holding_days, sell_omen_enabled, signal_threshold, stop_loss_atr,
  take_profit_atr, trade_count, trailing_atr, win_rate
```

fractional watch:

```text
engine/live/real_dashboard_alpaca_exit_oco_fix.py:224-259
  evaluate_fractional_exit_watches()
  -> saved take_profit_price / stop_loss_price 비교
```

결론:

```text
CE 예약청산은 full 룰북/PositionManager ExitPolicy가 아니라 dashboard-real Alpaca OCO state 경로다.
주문 트리거 가격 자체는 req의 TP/SL 가격과 Alpaca live holding/current price로 처리된다.
다만 저장된 position_snapshot의 context/backtest metadata는 live_slots_state snapshot에서 왔다.
```

## 6. snapshot을 Rulebook.from_dict()로 복원하는 실전 판단 경로 여부

확인 결과:

```text
live_candidate_slots.py should_buy: 없음
s2_auto_trader should_buy validation: 없음
s2_auto_trader submit/reconcile: 없음, full["rulebook"] 사용
PositionManager/ExitPolicy: live_slots_state가 아니라 pos.rulebook_snapshot 사용
CE dashboard direct buy: 없음, snapshot dict를 candidate payload로 직접 사용
CE dashboard OCO: 없음, position/enriched metadata로 사용
```

따라서:

```text
Rulebook.from_dict(live_slots_state_snapshot)로 실전 판단한 경로: NOT_FOUND
live_slots_state snapshot이 실전 주문 판단 payload로 사용된 경로: FOUND
```

## 7. 어제 CE 진입/청산 source 추적

### CE 진입

```text
time: 2026-07-08T14:27:15Z
endpoint/path: /dashboard-real direct buy -> _create_real_buy_intent
source: live_slots_state_fallback snapshot
execution_mode: direct_alpaca_live_market_order
full_rulebook_revalidation: NOT_FOUND
Rulebook.from_dict(snapshot): NOT_USED
```

### CE 청산 보호 / 예약 주문

```text
time: 2026-07-08T18:03:50Z
path: dashboard_real_exit_panel -> Alpaca reserved OCO
source: Alpaca live holding + user/request TP/SL prices
metadata/context: live_slots_state.slots snapshot
PositionManager CE entry: NOT_FOUND
positions.json CE: NOT_FOUND
Rulebook.from_dict(snapshot): NOT_USED
```

## 8. 최종 요약

| 흐름 | 룰북/후보 소스 | snapshot 사용 | 판정 |
|---|---|---:|---|
| live_candidate_slots.py should_buy | full final_rulebooks.jsonl via build_elite_shadow_report/evaluate_candidate | 아니오 | FULL_RULEBOOK |
| s2_auto_trader 자동매수 검증 | full final_rulebooks.jsonl 재조회/evaluate_candidate | 후보 ID/정렬값만 | FULL_RULEBOOK_FOR_DECISION |
| s2_auto_trader 주문 metadata | full["rulebook"] as selected_rulebook | 아니오 | FULL_RULEBOOK |
| PositionManager/ExitPolicy 자동청산 | position.rulebook_snapshot | 아니오 | POSITION_SNAPSHOT |
| dashboard-real CE 직접 매수 | live_slots_state fallback candidate snapshot | 예 | SNAPSHOT_USED |
| dashboard-real CE Alpaca OCO | Alpaca holding + req TP/SL, metadata from live_slots_state | 예, metadata/context | SNAPSHOT_METADATA_USED |

최종 판정:

```text
SNAPSHOT_USED_IN_DECISION
```
