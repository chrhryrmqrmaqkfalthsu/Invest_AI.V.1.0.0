# dashboard-real 직접 매수 청산 배관 연결 설계 readout — 1단계

상태:

```text
DESIGN_ONLY
IMPLEMENTATION_NOT_STARTED
STOP_AFTER_STEP_1
```

대상 리스크:

```text
R4: dashboard-real 직접 매수 후 PositionManager.register_entry() 미호출
R5: dashboard-real 직접 매수 후 PendingOrderManager.track_order(metadata=selected_rulebook/atr) 미연결
R6: selected_rulebook/atr은 정규 후보 payload에 있으나 reconciliation/청산으로 전달되지 않음
```

엄수 사항:

```text
read-only 설계만 수행
코드 수정 없음
주문 제출 없음
--write 없음
SAFETY guard / export script / live_candidate_slots 계산 로직 수정 없음
```

---

## 1. 현재 경로와 문제 지점

현재 직접 매수 경로:

```text
/dashboard-real
→ POST /api/real/live_slot_buy
→ _mark_real_slot_manual_buy(req)
→ _create_real_buy_intent(RealBuyIntentRequest(...))
→ _candidate_for_real(candidate_id)
→ broker.place_buy(...)
→ real_dashboard_manual_buy_intent.json에 intent 저장
```

근거:

```text
engine/live/real_dashboard_api.py:698-784
  _create_real_buy_intent()

engine/live/real_dashboard_api.py:1136-1168
  _mark_real_slot_manual_buy()
```

현재 `_create_real_buy_intent()`에서 확인되는 동작:

```text
- candidate_snapshot은 row에 저장한다.
- direct_orders_enabled=True이면 broker.place_buy()를 호출한다.
- order 결과를 broker_order에 저장한다.
- order.status가 submitted/pending/filled인지에 따른 reconciliation 분기가 없다.
- PositionManager.register_entry() 호출이 없다.
- BuyReconciliationService.reconcile() 호출이 없다.
- PendingOrderManager.track_order(... metadata=...) 호출이 없다.
```

현재 SAFETY guard:

```text
engine/live/real_dashboard_api.py:734-764
```

```text
candidate.real_candidate_fallback=True
또는 candidate.candidate_source == 'live_slots_state_fallback'
이면 broker.place_buy 이전에 rejected로 정상 반환한다.
```

설계 원칙:

```text
fallback candidate는 절대 register_entry/track_order 배관을 타면 안 된다.
SAFETY guard가 반환한 rejected row는 이후 reconciliation/position registration 대상이 아니다.
```

---

## 2. 목표 흐름 설계

### 2.1 정상 정규 후보 + full 룰북 + 직접 주문 ON

목표 흐름:

```text
1. _candidate_for_real(candidate_id)로 정규 후보 row 획득
2. SAFETY guard로 fallback 후보 차단
3. candidate_snapshot에서 selected_rulebook/rulebook, atr, entry_market_context를 추출/검증
4. broker.place_buy(...) 호출
5. order.status 분기
   5-A. FILLED + filled_shares > 0 + filled_avg_price > 0
        → BuyReconciliationService.reconcile(order, purpose='entry', preflight=BuyPreflight(...))
        → PositionManager.register_entry(...)
        → positions.json에 rulebook_snapshot/stop/target/trailing/max_holding_days 등록
   5-B. PENDING/PARTIAL/accepted/submitted 등 미완료
        → PendingOrderManager.track_order(order, purpose='entry', metadata=selected_rulebook/atr/context)
        → 추후 runner/pending reconciliation이 metadata로 Rulebook 복원
   5-C. REJECTED/CANCELLED/FAILED 등 terminal no-fill
        → register_entry 금지
        → 필요 시 intent status를 terminal/rejected 계열로 기록
6. intent row에는 reconciliation/position_registration 결과를 명시적으로 기록
```

중요:

```text
register_entry를 직접 호출하기보다 BuyReconciliationService.reconcile(..., preflight=...)를 호출하는 설계를 우선한다.
이유: reconcile()이 filled_shares/filled_avg_price 검증, 기존 포지션 중복 확인, PositionManager.register_entry 호출을 이미 포함한다.
```

---

## 3. candidate_snapshot → preflight metadata 매핑

정규 후보 export temp 설계 기준 후보 row에는 다음이 있다.

```text
candidate.selected_rulebook: full rulebook dict
candidate.rulebook: full rulebook dict duplicate
candidate.selected_rulebook_hash: rulebook hash
candidate.atr: positive float
candidate.market_score: float
candidate.vix_level: float
candidate.sector_score: float
candidate.ticker/stage/bucket/candidate_id/source_file/rulebook_hash
```

### 3.1 BuyReconciliationService가 읽는 metadata 필드

근거:

```text
engine/live/buy_reconciliation.py:130-150
  preflight_from_metadata()
```

| consumer field | 코드상 읽는 이름 | 의미 |
|---|---|---|
| rulebook payload | selected_rulebook or rulebook_override or rulebook | Rulebook.from_dict()로 복원 |
| ATR | preflight_atr or atr | PositionManager.register_entry atr_value |
| market context | entry_market_context | register_entry의 entry_market_context |

fail-closed 조건 근거:

```text
engine/live/buy_reconciliation.py:152-163
  requires_selected_rulebook_metadata()
```

```text
purpose='entry'이고 metadata에 entity_id 또는 selected_rulebook_hash가 있거나 client_order_id가 central/next_open이면 selected_rulebook metadata가 없을 때 fail-closed.
```

### 3.2 우리가 넘길 metadata 설계

`PendingOrderManager.track_order(... metadata=metadata)`와 intent row에 동일한 metadata subset을 남기는 설계.

| metadata field | source | required | 검증 |
|---|---|---:|---|
| selected_rulebook | candidate.selected_rulebook 우선, 없으면 candidate.rulebook | YES | dict, non-empty, full rulebook, Rulebook.from_dict 가능 |
| selected_rulebook_hash | candidate.selected_rulebook_hash or candidate.rulebook_hash | YES | non-empty string 권장 |
| rulebook | selected_rulebook copy | OPTIONAL/YES 권장 | preflight_from_metadata fallback 호환 |
| atr | candidate.atr | YES | float > 0 |
| preflight_atr | candidate.atr | YES | float > 0 |
| entry_market_context | candidate에서 구성 | OPTIONAL | dict |
| candidate_id | candidate.candidate_id | YES | 추적용 |
| ticker | candidate.ticker | YES | 추적용 |
| candidate_source | candidate.candidate_source | YES | fallback 아님 확인용 |
| real_candidate_fallback | candidate.real_candidate_fallback | YES | False이어야 함 |
| source_file | candidate.source_file | YES | 룰북 출처 추적 |
| rulebook_hash | candidate.rulebook_hash | YES | 룰북 출처 추적 |
| reason | 'dashboard_real_direct_entry' | YES | reconciliation 정책 식별 |

entry_market_context 구성:

```text
{
  'market_score': candidate.market_score,
  'vix_level': candidate.vix_level,
  'sector_score': candidate.sector_score,
  'sector_name': selected_rulebook.get('sector_name') or candidate.get('sector_name') or '',
  'source': 'dashboard_real_candidate_snapshot',
  'candidate_id': candidate_id,
  'rulebook_hash': selected_rulebook_hash,
}
```

주의:

```text
BuyReconciliationService.preflight_from_metadata()는 entry_market_context를 optional로 취급한다.
하지만 PositionManager.register_entry()는 이 context를 market_context_to_exit_context()로 변환하고 stop/target/trailing dynamic params에 영향을 줄 수 있으므로 가능하면 포함한다.
```

---

## 4. metadata 필드 1:1 대조표

| BuyReconciliationService.preflight_from_metadata() 소비 | 우리가 넘길 metadata | fallback/default | 판정 |
|---|---|---|---|
| payload.get('selected_rulebook') | selected_rulebook = full candidate selected_rulebook | 없음. 없으면 배관 중단 | MATCH |
| payload.get('rulebook_override') | 사용 안 함 | 없음 | NOT_REQUIRED |
| payload.get('rulebook') | rulebook = selected_rulebook copy | selected_rulebook backup | MATCH |
| Rulebook.from_dict(rb_payload) | selected_rulebook full dict | 실패 시 register/track 중단 | MATCH |
| payload.get('preflight_atr') | preflight_atr = candidate.atr | 없음. <=0이면 중단 | MATCH |
| payload.get('atr') | atr = candidate.atr | preflight_atr backup | MATCH |
| payload.get('entry_market_context') | entry_market_context dict | optional | MATCH |

`PositionManager.register_entry()` 소비 대조:

| register_entry parameter | source | 검증 |
|---|---|---|
| ticker | order.ticker / candidate.ticker | non-empty, 일치 확인 |
| entry_price | order.filled_avg_price | >0 |
| shares | order.filled_shares | >0 |
| rulebook | Rulebook.from_dict(selected_rulebook) via BuyPreflight | valid Rulebook |
| atr_value | preflight_atr / atr | >0 |
| entry_market_context | metadata.entry_market_context | dict optional |

청산 레벨 생성 근거:

```text
engine/live/position_manager.py:233-310
  register_entry()

engine/core/exit_policy.py:225-263
  initialize_position_state()
```

---

## 5. 구현 위치 설계 — 다음 단계용, 이번 단계 미구현

수정 후보 파일:

```text
engine/live/real_dashboard_api.py
```

수정 후보 위치:

```text
_create_real_buy_intent() 내부
broker.place_buy(...) 직후
row.update(status='submitted', broker_order=...) 전후
```

신규 helper 후보:

```text
_build_real_buy_preflight_metadata(candidate: dict) -> dict
```

역할:

```text
- fallback candidate 거부 상태에서는 호출하지 않는다.
- selected_rulebook/rulebook full dict 검증.
- atr > 0 검증.
- entry_market_context 구성.
- metadata dict 반환.
- 실패 시 명확한 reason 반환 또는 ValueError.
```

신규 helper 후보:

```text
_register_or_track_real_buy_order(order, candidate, metadata, row) -> dict
```

역할:

```text
- order.status 분기.
- FILLED이면 BuyReconciliationService.reconcile(preflight=BuyPreflight(...)) 호출.
- 미체결/부분체결이면 PendingOrderManager.track_order(metadata=metadata) 호출.
- 결과를 row에 position_registered / pending_order_tracked / reconciliation_error 등으로 기록.
```

필요 import 후보:

```text
from engine.live.broker.base import OrderStatus
from engine.live.buy_reconciliation import BuyPreflight, BuyReconciliationService
from engine.live.position_manager import PositionManager
from engine.live.pending_order_manager import PendingOrderManager
from engine.strategies.rulebook import Rulebook
```

단, 다음 단계에서 실제 의존성/초기화 비용과 순환 import 여부를 확인해야 한다.

---

## 6. order status별 처리 설계

### 6.1 FILLED

조건:

```text
order.status == OrderStatus.FILLED
filled_shares > 0
filled_avg_price > 0
selected_rulebook valid
atr > 0
```

동작:

```text
1. Rulebook.from_dict(selected_rulebook)로 Rulebook 복원
2. BuyPreflight(atr=atr, rulebook=rulebook, entry_market_context=context) 생성
3. BuyReconciliationService(...).reconcile(order, purpose='entry', preflight=preflight) 호출
4. 반환 PositionEntry가 있으면 row에 기록:
   position_registered=True
   position_ticker=ticker
   position_entry_price
   position_shares
   position_member_hash
   position_stop_price
   position_target_price
   position_trailing_stop
   position_max_holding_days
   position_exit_strategy
```

중복 처리:

```text
BuyReconciliationService.reconcile()는 position_manager.get(ticker)가 이미 있으면 기존 포지션을 반환한다.
따라서 동일 ticker 중복 register_entry를 피하는 기본 방어가 있다.
```

### 6.2 PENDING / SUBMITTED / ACCEPTED / PARTIAL

조건:

```text
order.status != OrderStatus.FILLED
단, terminal no-fill이 아닌 경우 포함
```

동작:

```text
1. PendingOrderManager.track_order(order, purpose='entry', metadata=metadata) 호출
2. metadata에는 selected_rulebook/preflight_atr/entry_market_context 포함
3. row에 기록:
   pending_order_tracked=True
   pending_order_id
   pending_order_state
   reconciliation_deferred=True
```

부분체결:

```text
OrderStatus.PARTIAL이면 PendingOrderManager.track_order()가 state=STATE_PARTIAL로 저장한다.
즉시 partial shares를 register_entry하지 않고, 최종 FILLED/RECONCILING reconciliation이 처리하도록 보류한다.
```

근거:

```text
engine/live/pending_order_manager.py:325-352
  track_order()
```

### 6.3 REJECTED / CANCELLED / FAILED / terminal no-fill

동작:

```text
- register_entry 금지
- pending metadata track은 선택적으로 terminal 기록만 남길 수 있으나, 청산 배관 목적상 필수 아님
- row.status를 broker 상태에 맞춰 rejected/cancelled/failed 계열로 명확히 기록
- broker_order는 저장
- position_registered=False
```

주의:

```text
filled_shares <= 0 또는 filled_avg_price <= 0인 주문은 절대 register_entry하지 않는다.
BuyReconciliationService.reconcile()도 같은 검증을 한다.
```

---

## 7. fallback / missing rulebook / missing ATR 처리

### 7.1 fallback candidate

조건:

```text
candidate.get('real_candidate_fallback') is True
OR candidate.get('candidate_source') == 'live_slots_state_fallback'
```

처리:

```text
기존 SAFETY guard에서 rejected 후 return.
새 배관 helper 호출 금지.
PositionManager.register_entry() 호출 금지.
PendingOrderManager.track_order() 호출 금지.
```

### 7.2 selected_rulebook 없음/invalid

조건:

```text
selected_rulebook/rulebook payload가 dict가 아님
또는 empty
또는 Rulebook.from_dict() 실패
```

처리 설계:

```text
- broker.place_buy 전에 검증하는 것이 안전하다.
- selected_rulebook invalid이면 주문 제출 자체를 거부한다.
- row.status='rejected'
- rejection_reason='REJECTED: regular candidate has no valid selected_rulebook — order blocked for exit safety'
- broker_order=None
- register_entry/track_order 금지
```

이 설계가 필요한 이유:

```text
주문 후에야 selected_rulebook invalid를 발견하면 이미 실보유가 생길 수 있고, 자동 청산 배관이 없는 orphan buy가 된다.
```

### 7.3 atr 없음/invalid

조건:

```text
candidate.atr <= 0 또는 누락
```

처리 설계:

```text
- broker.place_buy 전에 검증한다.
- invalid이면 주문 제출 자체를 거부한다.
- reason='REJECTED: regular candidate has no valid ATR — order blocked for exit safety'
```

---

## 8. 중복 호출 / idempotency 설계

현재 intent_id:

```text
real-buy:{candidate_id}
```

중복 호출 리스크:

```text
동일 candidate_id로 버튼을 두 번 누르면 같은 intent_id row를 덮어쓸 수 있다.
정규 candidates row가 존재하면 _candidate_for_real()은 기존 intent overlay를 통해 manual_buy_enabled=False로 막을 수 있다.
단, real candidate file이 비어 fallback으로 가는 경우엔 SAFETY guard가 차단한다.
```

추가 설계:

```text
- _create_real_buy_intent() 시작부에서 기존 intent row가 status in {'pending','submitted'}이면 재주문 거부 권장.
- 단 이번 R4/R5/R6 핵심 배관 구현 범위에서는 최소 변경 원칙상 optional.
- PendingOrderManager는 order_id/client_order_id 기준 existing record를 반환해 중복 track을 줄인다.
- PositionManager.register_entry는 ticker 기존 포지션이 있으면 BuyReconciliationService.reconcile()에서 기존 position을 반환한다.
```

---

## 9. 로그/intent 기록 설계

성공/실패를 사용자에게 전달하기 위해 intent row에 다음 필드를 추가하는 설계.

성공 FILLED + registered:

```text
position_registered=True
position_registered_at=<utc>
position_entry_price=<float>
position_shares=<float>
position_stop_price=<float>
position_target_price=<float>
position_trailing_stop=<float>
position_max_holding_days=<int>
position_exit_strategy=<str>
reconciliation_status='registered'
```

미체결/pending:

```text
pending_order_tracked=True
pending_order_tracked_at=<utc>
pending_order_id=<order_id>
pending_order_client_order_id=<client_order_id>
reconciliation_status='pending_order_tracked'
```

배관 차단:

```text
status='rejected'
rejection_reason='REJECTED: ... order blocked for exit safety'
order_blocked=True
reconciliation_status='blocked_before_order'
```

오류:

```text
reconciliation_status='failed'
reconciliation_error='<type>: <message>'
position_registered=False
```

오류 시 정책:

```text
- broker.place_buy 전 validation 실패: 주문 제출 전 거부.
- broker.place_buy 후 reconciliation 실패: 주문은 이미 나간 상태이므로 intent에 CRITICAL reconciliation_error 기록. 다음 구현 단계에서 pending_manager.track_reconciliation 또는 별도 recovery path를 검토해야 한다.
```

---

## 10. 이번 설계의 최소 구현 원칙

다음 단계 구현 시 최소 변경 범위:

```text
수정 허용 후보:
  engine/live/real_dashboard_api.py 내부 helper와 _create_real_buy_intent()의 direct order branch만

수정 금지 유지:
  SAFETY guard 의미 변경 금지
  scripts/export_real_dashboard_buy_candidates.py 변경 금지
  data/_system/ops/live_candidate_slots.py 변경 금지
  청산 정책/ExitPolicy 변경 금지
```

추천 순서:

```text
1. broker.place_buy 전에 selected_rulebook/atr preflight metadata를 검증한다.
2. fallback guard는 기존처럼 그보다 먼저 유지한다.
3. place_buy 후 order.status 분기:
   - FILLED → BuyReconciliationService.reconcile(preflight=BuyPreflight)
   - not FILLED → PendingOrderManager.track_order(metadata=metadata)
4. 실제 주문 없는 FakeBroker/FakePositionManager/FakePendingManager 검증 추가.
```

---

## 11. 검증 설계 — 다음 단계용

실제 주문 없이 수행할 검증:

### 11.1 fallback 거부 유지

```text
candidate_source=live_slots_state_fallback, real_candidate_fallback=True
→ broker.place_buy 호출 0
→ register_entry/reconcile 호출 0
→ track_order 호출 0
→ status='rejected'
```

### 11.2 정규 후보 + FILLED

```text
candidate_source=real_dashboard_buy_candidates_export
selected_rulebook full dict
atr > 0
FakeBroker.place_buy returns OrderStatus.FILLED with filled_shares/filled_avg_price
→ BuyReconciliationService.reconcile called once with preflight
→ PositionManager.register_entry called once indirectly
→ position_registered=True
→ rulebook_snapshot len >= 50
```

### 11.3 정규 후보 + PENDING

```text
FakeBroker.place_buy returns OrderStatus.PENDING
→ PositionManager.register_entry 호출 0
→ PendingOrderManager.track_order called once
→ metadata.selected_rulebook present
→ metadata.preflight_atr > 0
→ metadata.entry_market_context dict
```

### 11.4 selected_rulebook missing

```text
정규 후보지만 selected_rulebook 없음
→ broker.place_buy 호출 0
→ status='rejected'
→ rejection_reason includes selected_rulebook
```

### 11.5 ATR missing

```text
정규 후보지만 atr <= 0
→ broker.place_buy 호출 0
→ status='rejected'
→ rejection_reason includes ATR
```

---

## 12. 최종 설계 판정

```text
DESIGN_CONFIRMED_FOR_R4_R5_R6
IMPLEMENTATION_NOT_STARTED
STOP_AFTER_STEP_1
```

핵심 결론:

```text
정규 후보 파일이 full selected_rulebook/atr을 제공하면,
직접 매수 경로는 broker.place_buy 전에 이 metadata를 검증해야 한다.
주문이 즉시 체결되면 BuyReconciliationService.reconcile(preflight=...)를 통해 PositionManager.register_entry()를 호출한다.
주문이 미체결/부분체결이면 PendingOrderManager.track_order(metadata=selected_rulebook/preflight_atr/entry_market_context)를 호출해 나중에 같은 룰북으로 포지션 등록되게 한다.
fallback candidate는 기존 SAFETY guard에서 끝나야 하며, 이 배관에 진입하면 안 된다.
```
