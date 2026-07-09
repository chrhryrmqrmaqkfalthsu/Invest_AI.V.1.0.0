# dashboard-real 직접 매수 fallback 실집행 여부 확인 — READ-ONLY

범위:

```text
대상 경로: /dashboard-real 직접 매수 → /api/real/live_slot_buy → _mark_real_slot_manual_buy → _create_real_buy_intent → _candidate_for_real
확인 방식: 코드/기존 주문 intent/이벤트 로그 read-only inspection
수정/비활성화/서버 실행: 없음
```

최종 판정:

```text
FALLBACK_LIVE_EXECUTES
```

---

## 1. fallback 경로가 Alpaca 주문 제출까지 도달하는가

판정:

```text
도달함. 표시/추적에서 멈추지 않는다.
```

코드 경로:

```text
engine/live/real_dashboard_api.py:2162-2167
  dashboard-real JS markSlotBuy()
  -> POST /api/real/live_slot_buy

engine/live/real_dashboard_api.py:2811-2814
  @app.post('/api/real/live_slot_buy')
  -> _mark_real_slot_manual_buy(req)

engine/live/real_dashboard_api.py:1122
  _mark_real_slot_manual_buy()
  -> _create_real_buy_intent(RealBuyIntentRequest(...))

engine/live/real_dashboard_api.py:698-700
  _create_real_buy_intent()
  -> candidate = _candidate_for_real(req.candidate_id)

engine/live/real_dashboard_api.py:734-742
  if _direct_orders_enabled():
      broker = _get_real_broker()
      price = broker.get_current_price(ticker) or candidate['price']
      shares = notional / price
      order = broker.place_buy(... MARKET ..., client_order_id='km-real-buy-...')

engine/live/real_dashboard_api.py:743-750
  status='submitted'
  execution_mode='direct_alpaca_live_market_order'
  broker_order=<order dict>
```

직접 주문 스위치:

```text
engine/live/real_dashboard_api.py:107-108
  _direct_orders_enabled() returns true when direct order env is one of 1/true/yes/on
```

브로커:

```text
engine/live/real_dashboard_api.py:229-246
  _get_real_broker()
  -> AlpacaBroker(... paper=False)
```

결론:

```text
fallback candidate payload가 _candidate_for_real()에서 반환되면, direct order enabled 상태에서는 동일한 _create_real_buy_intent() 경로로 Alpaca live market buy까지 진행된다.
```

---

## 2. fallback은 언제 트리거되는가

정규 후보 로더:

```text
engine/live/real_dashboard_api.py:633-647
  _real_candidate_state()
  -> data/_system/real_dashboard_buy_candidates.json 읽기

engine/live/real_dashboard_api.py:676-689
  _candidate_for_real(candidate_id)
  -> _real_candidate_state(include_blocked=True).candidates[cid] 조회
  -> row가 없으면 ValueError('real candidate not found or stale: {cid}')
  -> status가 pending/manual_requested가 아니면 거부
  -> manual_buy_enabled is False면 거부
```

fallback patch 설치:

```text
api_server_candidate_only.py:16,23
  install_real_dashboard_holding_days_patch() 호출

engine/live/real_dashboard_holding_days_patch.py:332-340
  install_real_dashboard_holding_days_patch()
  -> _patch_candidate_lookup_for_real_buy()
```

fallback 조건:

```text
engine/live/real_dashboard_holding_days_patch.py:137-143
  patched_candidate_for_real(candidate_id)
  -> 먼저 원본 _candidate_for_real(cid) 호출
  -> ValueError 중 'not found or stale'인 경우에만 fallback 진행
  -> 그 외 오류는 그대로 raise
```

fallback source:

```text
engine/live/real_dashboard_holding_days_patch.py:144-145
  state = real_api._live_slots_state()
  row = real_api._find_live_slot_candidate_raw(state, cid)
```

fallback 추가 조건:

```text
engine/live/real_dashboard_holding_days_patch.py:146-161
  live_slots_state에서 row가 없으면 거부
  ticker가 없으면 거부
  real account에서 이미 held ticker면 거부
  status in {manual_executed, auto_executed, expired, cancelled, canceled, blocked}이면 거부
```

fallback payload marking:

```text
engine/live/real_dashboard_holding_days_patch.py:162-168
  out = dict(row)
  out['status'] = 'pending'
  out['manual_buy_enabled'] = True
  out.setdefault('candidate_source', 'live_slots_state_fallback')
  out.setdefault('real_candidate_fallback', True)
```

현재 정규 후보 파일 상태:

```text
data/_system/real_dashboard_buy_candidates.json
  candidates: {}
  updated_at: ''
```

결론:

```text
fallback은 정규 real_dashboard_buy_candidates.json 후보 조회가 'not found or stale'로 실패할 때만 트리거된다.
정규 후보가 존재하되 manual_buy_disabled, status 불가 등 다른 이유로 실패하면 fallback하지 않는다.
```

---

## 3. 실제 주문 로그/기록에서 fallback 경로 집행 여부

대상 파일:

```text
data/_system/real_dashboard_manual_buy_intent.json
data/_system/live_slots_events.jsonl
```

집계 결과:

| 항목 | 건수 |
|---|---:|
| real_dashboard_manual_buy_intent intents 전체 | 2 |
| candidate_source=live_slots_state_fallback + real_candidate_fallback=True | 2 |
| 위 조건 + status=submitted | 2 |
| 위 조건 + execution_mode=direct_alpaca_live_market_order | 2 |
| 위 조건 + broker_order.order_id 존재 | 2 |
| DASHBOARD_REAL_BUY_INTENT 이벤트 | 2 |

fallback 실집행 목록:

| ticker | candidate_id | status | execution_mode | direct_orders_enabled | fallback | broker_order_id_present | client_order_id | submitted_at | notional |
|---|---|---|---|---:|---:|---:|---|---|---:|
| BMI | stage3:BMI:07d4ee0f7841 | submitted | direct_alpaca_live_market_order | True | True | True | km-real-buy-1783518102-BMI | 2026-07-08T13:41:42.557818+00:00 | 645.0 |
| CE | stage3:CE:998b0b638c66 | submitted | direct_alpaca_live_market_order | True | True | True | km-real-buy-1783520835-CE | 2026-07-08T14:27:15.592260+00:00 | 650.0 |

이벤트 로그:

| line | time | ticker | candidate_id | execution_mode | status | source |
|---:|---|---|---|---|---|---|
| 924 | 2026-07-08T13:41:42.558761+00:00 | BMI | stage3:BMI:07d4ee0f7841 | direct_alpaca_live_market_order | submitted | dashboard-real-detail |
| 972 | 2026-07-08T14:27:15.593482+00:00 | CE | stage3:CE:998b0b638c66 | direct_alpaca_live_market_order | submitted | dashboard-real-detail |

CE 포함 여부:

```text
CE 포함: YES
CE candidate_id: stage3:CE:998b0b638c66
CE fallback source: live_slots_state_fallback
CE execution_mode: direct_alpaca_live_market_order
CE broker_order client_order_id: km-real-buy-1783520835-CE
```

---

## 4. 정규 경로 대비 fallback의 의미

정규 경로:

```text
real_dashboard_buy_candidates.json의 candidates[cid]가 존재해야 한다.
그 row가 pending/manual_requested이고 manual_buy_enabled가 false가 아니어야 한다.
```

fallback 경로:

```text
정규 경로가 'not found or stale'로 실패한 경우에만 live_slots_state에서 같은 candidate_id를 찾는다.
이 row는 compact snapshot candidate payload다.
full rulebook dict 또는 selected_rulebook은 포함하지 않는다.
이 fallback row가 _create_real_buy_intent()의 candidate_snapshot으로 저장된다.
direct order enabled이면 Alpaca live market buy가 제출된다.
```

---

## 최종 판정

```text
FALLBACK_LIVE_EXECUTES
```

근거:

```text
1. 코드상 fallback candidate가 반환되면 _create_real_buy_intent()가 같은 direct live order branch를 탄다.
2. direct order enabled일 때 broker.place_buy(... MARKET ...)가 호출된다.
3. 기존 기록에 fallback candidate_snapshot으로 submitted 상태의 direct_alpaca_live_market_order가 2건 존재한다.
4. 두 건 모두 broker_order id/client_order_id가 기록되어 있다.
5. CE가 그 2건 중 1건이다.
```
