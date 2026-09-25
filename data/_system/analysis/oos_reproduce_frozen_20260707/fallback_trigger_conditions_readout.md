# fallback 발동 조건 현황 확인 — READ-ONLY

범위:

```text
대상 파일: data/_system/real_dashboard_buy_candidates.json
대상 코드: engine/live/real_dashboard_api.py, engine/live/real_dashboard_holding_days_patch.py, api_server_candidate_only.py
확인 방식: 기존 파일/코드 read-only inspection
코드/운영 데이터 수정: 없음
```

---

## 1. 현재 real_dashboard_buy_candidates.json 상태

파일 상태:

```text
exists: YES
path: data/_system/real_dashboard_buy_candidates.json
mtime_utc: 2026-07-07 17:17:30.511494461 +0000
mtime_kst: 2026-07-08 02:17:30.511494 KST
size: 433 bytes
```

JSON 내부 상태:

```text
schema_version: 1
source: real_dashboard_buy_candidates
buy_mode: real_isolated
trade_date: ''
updated_at: ''
manual_buy_enabled: True
candidates type: dict
candidates count: 0
```

현재 fresh/stale 판정:

```text
시간 기준 fresh/stale 판정: NOT_IMPLEMENTED / UNKNOWN
후보 조회 기준 상태: NO_REAL_CANDIDATES_AVAILABLE
원본 _candidate_for_real(candidate_id) 결과: candidates[cid]가 없으면 ValueError('real candidate not found or stale: {cid}')
```

해석:

```text
- 파일은 존재한다.
- 내부 updated_at은 빈 문자열이다.
- candidates가 비어 있다.
- 코드에는 updated_at/mtime을 기준으로 몇 분/몇 시간 뒤 stale 처리하는 로직이 없다.
- 따라서 현재 상태를 시간 기준으로 fresh/stale로 판정할 수 없다.
- 다만 어떤 candidate_id를 정규 real 후보 파일에서 찾으려 하면 row가 없기 때문에 원본 경로는 'not found or stale' 오류를 낸다.
```

---

## 2. stale 판정 기준과 fallback 발동 판정 로직

### 2.1 정규 후보 state 로더

파일/함수/라인:

```text
engine/live/real_dashboard_api.py:633-673
  _real_candidate_state(include_blocked=False)
```

관련 코드:

```text
633 def _real_candidate_state(*, include_blocked: bool = False) -> dict[str, Any]:
634     data = read_json(REAL_BUY_CANDIDATES_PATH, {})
635     if not isinstance(data, dict) or not data:
636         state = _default_real_candidate_state()
637     else:
638         state = dict(_default_real_candidate_state())
639         state.update(data)
640         if not isinstance(state.get('candidates'), dict):
641             state['candidates'] = {}
...
652     candidates = {
653         str(cid): dict(row)
654         for cid, row in (state.get('candidates') or {}).items()
655         if isinstance(row, dict) and str(row.get('status') or 'pending') not in hidden
656     }
672     state['candidates'] = candidates
673     return state
```

확인 결과:

```text
updated_at age check: NOT_FOUND
mtime age check: NOT_FOUND
stale_after_minutes/hours config: NOT_FOUND
시간 임계값: NONE
하드코딩 임계값: NONE
설정값 임계값: NONE
```

### 2.2 정규 후보 단건 조회

파일/함수/라인:

```text
engine/live/real_dashboard_api.py:676-689
  _candidate_for_real(candidate_id)
```

관련 코드:

```text
680 state = _real_candidate_state(include_blocked=True)
681 row = (state.get('candidates') or {}).get(cid)
682 if not isinstance(row, dict):
683     raise ValueError(f'real candidate not found or stale: {cid}')
684 status = str(row.get('status') or 'pending')
685 if status not in {'pending', 'manual_requested'}:
686     raise ValueError(f'real candidate is not pending: {cid}')
687 if row.get('manual_buy_enabled') is False:
688     raise ValueError(f'real candidate manual buy disabled: {cid}')
689 return row
```

확인 결과:

```text
'not found or stale' 문구는 row가 dict가 아닐 때만 발생한다.
여기서 stale은 timestamp 계산 결과가 아니라 missing row와 같은 오류 메시지 문자열이다.
```

### 2.3 fallback patch 발동 조건

patch 설치:

```text
api_server_candidate_only.py:16,23
  from engine.live.real_dashboard_holding_days_patch import install_real_dashboard_holding_days_patch
  install_real_dashboard_holding_days_patch()
```

fallback 설치 함수:

```text
engine/live/real_dashboard_holding_days_patch.py:332-340
  install_real_dashboard_holding_days_patch()
  -> _patch_candidate_lookup_for_real_buy()
```

fallback 판정 함수:

```text
engine/live/real_dashboard_holding_days_patch.py:137-169
  patched_candidate_for_real(candidate_id)
```

발동 조건:

```text
137 def patched_candidate_for_real(candidate_id: str) -> dict[str, Any]:
139     try:
140         return _ORIG_CANDIDATE_FOR_REAL(cid)
141     except ValueError as exc:
142         if 'not found or stale' not in str(exc):
143             raise
144     state = real_api._live_slots_state()
145     row = real_api._find_live_slot_candidate_raw(state, cid)
```

정확한 판정:

```text
fallback은 원본 _candidate_for_real(cid)가 ValueError를 던지고,
그 오류 문자열에 'not found or stale'이 포함될 때만 발동한다.
```

fallback이 발동하지 않는 경우:

```text
- status not pending/manual_requested → 'real candidate is not pending' 오류. fallback 안 함.
- manual_buy_enabled is False → 'manual buy disabled' 오류. fallback 안 함.
- candidate_id 빈 값 → 'candidate_id required' 오류. fallback 안 함.
```

fallback 내부 추가 조건:

```text
engine/live/real_dashboard_holding_days_patch.py:144-161
  live_slots_state에서 같은 candidate_id row가 있어야 한다.
  ticker가 있어야 한다.
  실제 계좌 보유 ticker면 거부한다.
  status가 manual_executed/auto_executed/expired/cancelled/canceled/blocked면 거부한다.
```

fallback payload marking:

```text
engine/live/real_dashboard_holding_days_patch.py:162-168
  out = dict(row)
  out['candidate_id'] = cid
  out['ticker'] = ticker
  out['status'] = 'pending'
  out['manual_buy_enabled'] = True
  out.setdefault('candidate_source', 'live_slots_state_fallback')
  out.setdefault('real_candidate_fallback', True)
```

---

## 3. fallback branch → broker.place_buy 정확 코드 경로

엔드포인트/프론트 경로:

```text
engine/live/real_dashboard_api.py:2162-2167
  markSlotBuy(cid, notional, slot)
  -> POST /api/real/live_slot_buy

engine/live/real_dashboard_api.py:2811-2814
  @app.post('/api/real/live_slot_buy')
  -> _mark_real_slot_manual_buy(req)
```

서버 내부 경로:

```text
engine/live/real_dashboard_api.py:1122
  _mark_real_slot_manual_buy()
  -> _create_real_buy_intent(RealBuyIntentRequest(...))

engine/live/real_dashboard_api.py:698-700
  _create_real_buy_intent(req)
  -> candidate = _candidate_for_real(req.candidate_id)
```

fallback monkey patch 연결:

```text
engine/live/real_dashboard_holding_days_patch.py:171
  real_api._candidate_for_real = patched_candidate_for_real
```

주문 제출 경로:

```text
engine/live/real_dashboard_api.py:734-742
  if _direct_orders_enabled():
      broker = _get_real_broker()
      price = broker.get_current_price(ticker) or candidate['price']
      shares = notional / price
      order = broker.place_buy(ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=f'km-real-buy-{...}')
```

주문 결과 저장:

```text
engine/live/real_dashboard_api.py:743-750
  row.update({
      'status': 'submitted',
      'submitted_at': utc_now_iso(),
      'shares_requested': shares,
      'execution_mode': 'direct_alpaca_live_market_order',
      'broker_order': _order_dict(order),
  })
```

직접 주문 활성 조건:

```text
engine/live/real_dashboard_api.py:107-108
  _direct_orders_enabled()
  -> env KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS in {'1','true','yes','on'}
```

브로커 타입:

```text
engine/live/real_dashboard_api.py:229-246
  _get_real_broker()
  -> AlpacaBroker(... paper=False)
```

---

## 4. full 룰북 guard 존재 여부

확인 결과:

```text
fallback branch에서 full rulebook 존재 여부 검사: NOT_FOUND
_create_real_buy_intent()에서 candidate_snapshot.rulebook 검사: NOT_FOUND
_create_real_buy_intent()에서 selected_rulebook 검사: NOT_FOUND
_create_real_buy_intent()에서 build_elite_shadow_report / evaluate_candidate 재검증: NOT_FOUND
_create_real_buy_intent()에서 real_candidate_fallback=True 차단: NOT_FOUND
```

구체적 근거:

```text
fallback patch는 live_slots_state row를 dict(row)로 복사하고 candidate_source/real_candidate_fallback만 setdefault한다.
그 뒤 _create_real_buy_intent()는 ticker, notional, price만 확인한 뒤 direct order enabled면 broker.place_buy로 진행한다.
```

비교 참고:

```text
engine/live/s2_auto_trader.py:434-436
  if not full or not isinstance(full.get('rulebook'), Mapping):
      return {'ok': False, 'reason': 'full_rulebook_unavailable', ...}
  rb = Rulebook.from_dict(dict(full['rulebook']))

engine/live/s2_auto_trader.py:454
  intent['selected_rulebook'] = dict(full['rulebook'])
```

이와 같은 full rulebook guard는 dashboard-real fallback 직접 매수 경로에는 확인되지 않는다.

---

## 5. 현재 fallback 발동 가능성 현황

현재 정규 후보 파일:

```text
real_dashboard_buy_candidates.json exists: YES
candidates count: 0
updated_at: ''
```

현재 코드 기준:

```text
정규 후보 파일이 비어 있으므로, /api/real/live_slot_buy가 live 슬롯 candidate_id로 들어오면
원본 _candidate_for_real()은 row missing으로 'real candidate not found or stale'를 던진다.
patch는 이 문자열을 fallback 발동 신호로 해석한다.
해당 candidate_id가 live_slots_state slots/candidate_pool/waitlist에 있고, 보유/blocked가 아니면 fallback candidate payload를 반환한다.
```

따라서:

```text
현재 real_dashboard_buy_candidates.json 기준으로는 live slot 후보에 대해 fallback 발동 조건이 열려 있다.
시간 stale 때문이 아니라 정규 candidates가 비어 있기 때문이다.
```

---

## 6. 수정 후보 지점 — 설계만, 코드 변경 없음

### 후보 1: compact fallback payload 주문 거부

최소 변경 위치:

```text
engine/live/real_dashboard_api.py:698-734
  _create_real_buy_intent(req)
  candidate = _candidate_for_real(req.candidate_id) 직후
```

설계:

```text
if candidate.get('real_candidate_fallback') is True or candidate.get('candidate_source') == 'live_slots_state_fallback':
    raise ValueError('fallback compact candidate cannot be used for live order without full rulebook')
```

장점:

```text
- 주문 직전 중앙 경로에서 막는다.
- fallback patch 외 다른 경로가 compact payload를 반환해도 방어된다.
- broker.place_buy 직전 guard로 효과가 명확하다.
```

### 후보 2: fallback 시 full 룰북 재조회/재검증 후만 통과

최소 변경 위치:

```text
engine/live/real_dashboard_holding_days_patch.py:162-169
  patched_candidate_for_real()에서 out 반환 직전
```

설계:

```text
fallback row의 candidate_id로 full candidate를 재조회한다.
full candidate의 rulebook 존재 여부를 확인한다.
필요하면 evaluate_candidate(full, ctx=get_market_context())로 현재 should_buy 재검증한다.
성공 시 out에 selected_rulebook/full_rulebook_source/full_rulebook_verified_at 등을 붙여 반환한다.
실패 시 ValueError로 주문 경로 진입 차단.
```

참고 구현 패턴:

```text
engine/live/s2_auto_trader.py:300-329
  _candidate_full_payload(candidate_id)
  _validate_candidate_signal(row)

engine/live/s2_auto_trader.py:434-455
  full['rulebook'] 확인 후 selected_rulebook 저장
```

장점:

```text
- fallback 표시/조회는 유지하면서 주문 후보 payload만 full rulebook-backed 상태로 승격할 수 있다.
- s2_auto_trader의 full rulebook guard와 같은 정책으로 맞출 수 있다.
```

---

## 최종 요약

```text
현재 real_dashboard_buy_candidates.json:
  exists=YES
  mtime_kst=2026-07-08 02:17:30 KST
  updated_at=''
  candidates=0

stale 임계값:
  시간 기준 threshold 없음.
  하드코딩/설정값 모두 NOT_FOUND.
  'stale'은 현재 row missing 오류 메시지에 포함된 문자열로 쓰인다.

fallback 발동:
  원본 _candidate_for_real()이 'real candidate not found or stale' ValueError를 낼 때만.
  현재 파일이 candidates=0이라 live slot 후보 주문 요청에서는 이 조건이 성립 가능.

full 룰북 guard:
  dashboard-real fallback 직접 주문 경로에는 NOT_FOUND.

broker.place_buy 도달:
  direct_orders_enabled이면 fallback candidate도 _create_real_buy_intent()에서 AlpacaBroker.place_buy(... MARKET ...)까지 도달한다.
```
