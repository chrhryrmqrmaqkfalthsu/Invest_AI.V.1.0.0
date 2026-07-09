# dashboard-real 직접 매수 청산 배관 구현 readout

기준 설계:

```text
7239908 dashboard-real 직접 매수 청산 배관 연결 설계 readout 추가
```

구현 상태:

```text
IMPLEMENTED
FAKEBROKER_VALIDATED
NO_REAL_ORDER_SUBMITTED
DIRECT_ORDERS_SETTING_UNCHANGED
```

수정 파일:

```text
engine/live/real_dashboard_holding_days_patch.py
```

수정하지 않은 파일/로직:

```text
engine/live/real_dashboard_api.py
scripts/export_real_dashboard_buy_candidates.py
data/_system/ops/live_candidate_slots.py
BuyReconciliationService 내부 로직
PositionManager.register_entry 내부 로직
PendingOrderManager.track_order 내부 로직
SAFETY fallback guard 의미
```

Diff artifact:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/direct_buy_reconciliation_wiring_impl.diff
```

운영 상태 확인:

```text
broker_mode: alpaca_live
direct_orders_enabled: False
holdings_count: 0
real_dashboard_buy_candidates.json canonical file unchanged:
  mtime_utc=2026-07-07 17:17:30.511494461 +0000
  size=433
```

---

## 1. 구현 요약

구현 방식:

```text
engine/live/real_dashboard_holding_days_patch.py에서 runtime patch로 real_api._create_real_buy_intent를 감싼다.
기존 대형 real_dashboard_api.py는 직접 수정하지 않았다.
api_server_candidate_only.py가 기존처럼 install_real_dashboard_holding_days_patch()를 호출하면 신규 patch도 함께 설치된다.
```

추가된 설치 호출:

```text
install_real_dashboard_holding_days_patch()
  -> _patch_candle_refresh_policy()
  -> _patch_candidate_slot_to_dashboard()
  -> _patch_candidate_lookup_for_real_buy()
  -> _patch_direct_buy_reconciliation_wiring()
  -> _patch_real_position_enrichment()
  -> _patch_slot_overlay_js()
```

핵심 신규 함수:

```text
_order_status_value(order)
_is_fallback_candidate(candidate)
_entry_market_context_from_candidate(candidate, selected_rulebook)
_full_rulebook_dict(candidate)
_selected_rulebook_hash(candidate, selected_rulebook)
_real_buy_preflight_metadata(candidate)
_reject_real_buy_intent(...)
_position_snapshot_for_row(position)
_wire_real_buy_reconciliation(...)
_patch_direct_buy_reconciliation_wiring()
```

---

## 2. 직접 매수 경로 구현 흐름

### 2.1 fallback guard 유지

조건:

```text
candidate.real_candidate_fallback is True
OR
candidate.candidate_source == 'live_slots_state_fallback'
```

동작:

```text
status='rejected'
execution_mode='blocked_fallback_candidate_no_verified_full_rulebook'
order_blocked=True
broker_order=None
position_registered=False
pending_order_tracked=False
reconciliation_status='blocked_before_order'
```

중요:

```text
fallback candidate는 broker.place_buy, PositionManager.register_entry, PendingOrderManager.track_order 중 어디에도 진입하지 않는다.
```

### 2.2 place_buy 전 정규 후보 preflight

정규 후보라도 아래 조건이면 broker.place_buy 전에 거부한다.

```text
selected_rulebook missing/invalid
ATR missing or <=0
Rulebook.from_dict(selected_rulebook) restore 실패
```

거부 예:

```text
REJECTED: regular candidate has no valid selected_rulebook — order blocked for exit safety
REJECTED: regular candidate has no valid ATR — order blocked for exit safety
```

### 2.3 FILLED

조건:

```text
order.status == filled
filled_shares > 0
filled_avg_price > 0
```

동작:

```text
Rulebook.from_dict(selected_rulebook)
BuyPreflight(atr, rulebook, entry_market_context)
BuyReconciliationService.reconcile(order, purpose='entry', preflight=preflight)
  -> PositionManager.register_entry() indirectly
```

intent row 기록:

```text
position_registered=True
reconciliation_status='registered'
position_stop_price
position_target_price
position_trailing_stop
position_trailing_distance
position_max_holding_days
position_exit_strategy
position_rulebook_snapshot_len
```

### 2.4 PENDING / PARTIAL / SUBMITTED / ACCEPTED / NEW / PENDING_NEW

동작:

```text
PendingOrderManager.track_order(order, purpose='entry', metadata=metadata)
```

metadata 보존:

```text
selected_rulebook
rulebook
selected_rulebook_hash
preflight_atr
atr
entry_market_context
candidate_id
ticker
candidate_source
real_candidate_fallback
source_file
rulebook_hash
reason='dashboard_real_direct_entry'
```

intent row 기록:

```text
pending_order_tracked=True
pending_order_id
pending_order_client_order_id
pending_order_state
reconciliation_deferred=True
reconciliation_status='pending_order_tracked'
```

### 2.5 terminal no-fill

동작:

```text
register_entry 호출 안 함
track_order 호출 안 함
reconciliation_status='terminal_no_fill:<status>'
```

---

## 3. metadata 1:1 대조표

| 설계 필드 | 구현 metadata 필드 | source | 검증 결과 |
|---|---|---|---:|
| selected_rulebook | selected_rulebook | candidate.selected_rulebook | PASS |
| rulebook | rulebook | selected_rulebook copy | PASS |
| selected_rulebook_hash | selected_rulebook_hash | candidate.selected_rulebook_hash or rulebook_hash or computed hash | PASS |
| preflight_atr | preflight_atr | candidate.preflight_atr or candidate.atr | PASS |
| atr | atr | same ATR | PASS |
| entry_market_context.market_score | market_score | candidate.market_score | PASS |
| entry_market_context.vix_level | vix_level | candidate.vix_level | PASS |
| entry_market_context.sector_score | sector_score | candidate.sector_score | PASS |
| entry_market_context.sector_name | sector_name | selected_rulebook.sector_name or candidate.sector_name | PASS |
| entry_market_context.source | source | constant dashboard_real_candidate_snapshot | PASS |
| entry_market_context.candidate_id | candidate_id | candidate.candidate_id | PASS |
| entry_market_context.rulebook_hash | rulebook_hash | selected_rulebook_hash or candidate.rulebook_hash | PASS |
| candidate_id | candidate_id | candidate.candidate_id | PASS |
| ticker | ticker | candidate.ticker | PASS |
| candidate_source | candidate_source | candidate.candidate_source | PASS |
| real_candidate_fallback | real_candidate_fallback | candidate.real_candidate_fallback | PASS |
| source_file | source_file | candidate.source_file | PASS |
| rulebook_hash | rulebook_hash | candidate.rulebook_hash | PASS |
| reason | reason | dashboard_real_direct_entry | PASS |

---

## 4. 검증 환경

검증 방식:

```text
실제 Alpaca 주문 없음
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1은 테스트 프로세스 환경변수로만 사용
FakeBroker.place_buy 사용
REAL_BUY_CANDIDATES_PATH / REAL_BUY_INTENT_PATH / POSITIONS_PATH / PENDING_ORDERS_PATH는 TemporaryDirectory로 monkeypatch
```

사용 후보:

```text
source temp export file:
/home/g3000kkw/kingmaker/data/_system/.real_dashboard_buy_candidates.json.tmp.224108.20260709T161407Z

candidate:
stage2:ALGT:402f72d48c3c
```

후보 주요 값:

```text
ticker: ALGT
atr: 6.386689045625623
selected_rulebook len: 88
candidate_source: real_dashboard_buy_candidates_export
real_candidate_fallback: False
market_score: 82.1
vix_level: 16.37
sector_score: 100.0
rulebook_hash: 402f72d48c3cd87bae620e2c2c803420787f9ba6b893612d67f754c55a1b14bd
```

---

## 5. 검증 1 — FILLED → register_entry + 청산값 대조

명령 결과:

```text
DIRECT_BUY_RECONCILIATION_WIRING_TEST_OK
```

경로:

```text
FakeBroker.place_buy returns OrderStatus.FILLED
→ BuyReconciliationService.reconcile(purpose='entry', preflight)
→ PositionManager.register_entry()
→ temp positions.json write
```

결과:

```text
position_registered: True
reconciliation_status: registered
rulebook_snapshot_len: 88
```

### 5.1 등록된 청산값 vs 동일 룰북 기대값 대조표

테스트 fake entry:

```text
entry_price: 10.0
shares: 10.0
atr: 6.386689045625623
```

| field | registered position | expected from selected_rulebook | match |
|---|---:|---:|---:|
| stop_price | -12.35341165968968 | -12.35341165968968 | PASS |
| target_price | 45.65802395040249 | 45.65802395040249 | PASS |
| trailing_stop | 3.613310954374377 | 3.613310954374377 | PASS |
| trailing_distance | 6.386689045625623 | 6.386689045625623 | PASS |
| max_holding_days | 11 | 11 | PASS |
| exit_strategy | hybrid | hybrid | PASS |

판정:

```text
REGISTERED_EXIT_LEVELS_MATCH_SELECTED_RULEBOOK
```

주의:

```text
entry_price=10.0은 FakeBroker 검증용 값이라 stop_price가 음수로 나왔다.
검증 목적은 실제 가격 현실성이 아니라 selected_rulebook/ATR로 계산한 값과 register_entry 결과의 일치 여부다.
```

---

## 6. 검증 2 — entry_market_context 조립값 대조

### 6.1 FILLED metadata context

| field | candidate source value | metadata.entry_market_context value | match |
|---|---:|---:|---:|
| market_score | 82.1 | 82.1 | PASS |
| vix_level | 16.37 | 16.37 | PASS |
| sector_score | 100.0 | 100.0 | PASS |
| candidate_id | stage2:ALGT:402f72d48c3c | stage2:ALGT:402f72d48c3c | PASS |
| rulebook_hash | 402f72d48c3cd87bae620e2c2c803420787f9ba6b893612d67f754c55a1b14bd | 402f72d48c3cd87bae620e2c2c803420787f9ba6b893612d67f754c55a1b14bd | PASS |
| sector_name | selected_rulebook/candidate derived | tech | PASS |
| source | constant | dashboard_real_candidate_snapshot | PASS |

판정:

```text
ENTRY_MARKET_CONTEXT_MATCHES_CANDIDATE_SOURCE
```

---

## 7. 검증 3 — PENDING → track_order metadata 보존

경로:

```text
FakeBroker.place_buy returns OrderStatus.PENDING
→ PendingOrderManager.track_order(order, purpose='entry', metadata=metadata)
```

결과:

```text
pending_order_tracked: True
position_registered: False
track_order_records: 1
metadata_rulebook_len: 88
metadata.atr: 6.386689045625623
metadata.preflight_atr: 6.386689045625623
```

metadata context:

| field | value |
|---|---:|
| candidate_id | stage2:ALGT:402f72d48c3c:PENDING |
| market_score | 82.1 |
| vix_level | 16.37 |
| sector_score | 100.0 |
| sector_name | tech |
| source | dashboard_real_candidate_snapshot |

판정:

```text
PENDING_ORDER_METADATA_PRESERVED
```

---

## 8. 검증 4 — PARTIAL → track_order metadata 보존

추가 검증:

```text
DIRECT_BUY_PARTIAL_AND_ATR_TEST_OBSERVED
```

결과:

```text
pending_order_tracked: True
position_registered: False
pending_order_state: PARTIAL
record_state: PARTIAL
record_status: partial
metadata_rulebook_len: 88
metadata_atr: 6.386689045625623
broker_buy_calls: 1
```

판정:

```text
PARTIAL_ORDER_TRACKED_WITH_RULEBOOK_METADATA
```

---

## 9. 검증 5 — selected_rulebook 없는 후보

조건:

```text
regular candidate
selected_rulebook removed
```

결과:

```text
status: rejected
broker_buy_calls: 0
position file empty/no registration
reason: REJECTED: regular candidate has no valid selected_rulebook — order blocked for exit safety
```

판정:

```text
MISSING_SELECTED_RULEBOOK_BLOCKED_BEFORE_PLACE_BUY
```

---

## 10. 검증 6 — ATR invalid 후보

조건:

```text
regular candidate
atr=0
```

결과:

```text
status: rejected
broker_buy_calls: 0
reason: REJECTED: regular candidate has no valid ATR — order blocked for exit safety
```

판정:

```text
INVALID_ATR_BLOCKED_BEFORE_PLACE_BUY
```

---

## 11. 검증 7 — fallback 후보

조건:

```text
candidate_source=live_slots_state_fallback
real_candidate_fallback=True
```

결과:

```text
status: rejected
execution_mode: blocked_fallback_candidate_no_verified_full_rulebook
broker_buy_calls: 0
PositionManager.register_entry: 0
PendingOrderManager.track_order: 0
```

판정:

```text
FALLBACK_STILL_BLOCKED_BEFORE_WIRING
```

---

## 12. 검증 8 — 중복 ticker

조건:

```text
candidate_id:DUP1 and candidate_id:DUP2
same ticker ALGT
both OrderStatus.FILLED from FakeBroker
```

결과:

```text
first_status: registered
second_reconciliation_status: existing_position_returned
positions_count: 1
buy_calls: 2
```

판정:

```text
DUPLICATE_TICKER_DOES_NOT_DOUBLE_REGISTER
```

주의:

```text
두 번째 candidate_id는 place_buy까지는 실행되며, reconciliation 단계에서 기존 포지션을 반환한다.
이번 지시의 검증 항목은 '기존 포지션 반환, 이중 등록 안 됨'이므로 PASS.
주문 전 중복 ticker 차단은 별도 안전 개선 후보다.
```

---

## 13. 최종 판정

```text
DIRECT_BUY_RECONCILIATION_WIRING_IMPLEMENTED
FILLED_REGISTER_ENTRY_PASS
REGISTERED_EXIT_LEVELS_MATCH_SELECTED_RULEBOOK
ENTRY_MARKET_CONTEXT_MATCHES_CANDIDATE_SOURCE
PENDING_TRACK_ORDER_METADATA_PASS
PARTIAL_TRACK_ORDER_METADATA_PASS
MISSING_SELECTED_RULEBOOK_BLOCKED_PRE_ORDER
INVALID_ATR_BLOCKED_PRE_ORDER
FALLBACK_BLOCKED_PRE_ORDER
DUPLICATE_TICKER_NO_DOUBLE_REGISTER
NO_REAL_ORDER_SUBMITTED
DIRECT_ORDERS_SETTING_UNCHANGED_FALSE
```

배관 완성 판정:

```text
FILLED 주문은 selected_rulebook/ATR/context를 BuyPreflight로 넘겨 PositionManager.register_entry까지 연결된다.
PENDING/PARTIAL 주문은 selected_rulebook/ATR/context를 PendingOrderManager metadata에 보존한다.
fallback compact 후보와 selected_rulebook/ATR 누락 후보는 broker.place_buy 전에 차단된다.
```
