# temp export 후보 payload vs 기존 정규 후보 파일 스키마 대조 — READ-ONLY

범위:

```text
temp export file:
  /home/g3000kkw/kingmaker/data/_system/.real_dashboard_buy_candidates.json.tmp.224108.20260709T161407Z

기존 정규 파일, 교체 전 원본:
  data/_system/real_dashboard_buy_candidates.json

확인 방식:
  코드/JSON read-only inspection

금지 준수:
  --write 실행 안 함
  data/_system/real_dashboard_buy_candidates.json 교체 안 함
  운영 코드 수정 없음
```

최종 판정:

```text
SCHEMA_MATCH_HAPPYPATH_ONLY
```

판정 이유:

```text
1. temp export 후보 payload는 _candidate_for_real() 및 _create_real_buy_intent()가 직접 읽는 happy-path 매수 필드를 모두 갖고 있다.
2. 각 후보의 selected_rulebook/rulebook은 full dict 88개 필드이며, 청산 트리거 관련 주요 하위 필드도 전 후보에서 유효하다.
3. FakeBroker 기반 매수 로직 경로는 SAFETY guard에 걸리지 않고 정규 후보로 submitted까지 통과했다.
4. 그러나 현재 dashboard-real 직접 매수 경로 자체는 broker.place_buy 후 candidate_snapshot을 intent에 저장할 뿐, 그 자리에서 PositionManager.register_entry()를 호출하거나 pending order metadata에 selected_rulebook을 연결하는 코드는 확인되지 않았다.
5. 후속 BUY reconciliation/PositionManager 경로는 selected_rulebook/preflight_atr/entry_market_context를 pending metadata 또는 Rulebook object로 읽는다. temp candidate_snapshot에 이 정보는 있지만, 현재 direct dashboard-real 경로가 그것을 pending metadata로 전달한다는 연결은 확인되지 않았다.
```

따라서:

```text
매수 happy path 스키마: MATCH
full rulebook/청산 trigger payload: PRESENT
매수 체결 후 포지션 등록/청산 연결까지의 end-to-end 스키마: NOT_CONFIRMED
```

---

## 1. 기존 정규 파일 스키마 상태

파일:

```text
data/_system/real_dashboard_buy_candidates.json
```

상태:

```text
candidates: 0
```

Top-level keys:

```text
buy_mode
candidates
isolated
manual_buy_enabled
note
schema_version
source
trade_date
updated_at
```

기존 정규 파일에는 후보 row가 없다.

```text
기존 정규 파일 후보 payload 스키마: NOT_AVAILABLE_FROM_FILE
```

따라서 기존 파일과 temp 후보 payload의 후보-level 필드 차이는 다음처럼 해석해야 한다.

```text
기존 파일 기준 후보 누락 필드: 기존 candidates=0이라 비교 불가
코드 소비 기준 후보 요구 필드: real_dashboard_api.py / buy_reconciliation.py / position_manager.py / exit_policy.py에서 역추적
```

---

## 2. temp export 파일 상태

Temp path:

```text
/home/g3000kkw/kingmaker/data/_system/.real_dashboard_buy_candidates.json.tmp.224108.20260709T161407Z
```

Top-level keys:

```text
buy_mode
candidates
export_meta
isolated
manual_buy_enabled
note
schema_version
source
trade_date
updated_at
```

Candidate count:

```text
temp_candidates: 8
```

Candidate field union:

```text
atr
bucket
candidate_id
candidate_source
components
created_at
down_deprioritize
entity_id
execution_session
exit_strategy
exit_strategy_name
expectancy_pct
exported_at
exporter
final_score
first_final_score
first_signal_at
first_signal_price
fitness
full_rulebook_field_count
full_rulebook_key
full_rulebook_source
full_rulebook_verified
full_rulebook_verified_at
gate_keep
gate_status
last_seen_at
manual_buy_enabled
market_score
max_holding_days
mdd_pct
notional
price
priority_group
ratio
raw_score
real_candidate_fallback
reasons
rulebook
rulebook_hash
rulebook_hash_short
sector_score
selected_rulebook
selected_rulebook_hash
should_buy_verified
should_buy_verified_at
slot
slot_no
source_file
source_rank
source_row_index
stage
status
stop_loss_atr
take_profit_atr
threshold
ticker
trade_count
trade_date
trade_file
trailing_atr
updated_at
vix_level
vol_group
win_rate
```

Temp 추가 top-level field:

```text
export_meta
```

Temp 추가 candidate-level fields:

```text
기존 정규 파일에는 후보 row가 없으므로 candidate-level 추가/누락은 파일 대비가 아니라 코드 소비 기준으로만 판정 가능하다.
```

---

## 3. _candidate_for_real() 및 직접 매수 경로가 읽는 필드

소비 코드:

```text
engine/live/real_dashboard_api.py
```

### 3.1 _real_candidate_state()

읽는 필드:

| code path | field | temp 존재/유효 |
|---|---|---:|
| state.get('candidates') | candidates dict | PASS |
| row.status hidden filter | status | PASS |

### 3.2 _candidate_for_real()

읽는 필드:

| code path | field | temp 존재/유효 | temp 값 |
|---|---|---:|---|
| candidates[cid] | candidate row dict | PASS | 8 rows |
| row.status | status | PASS | pending |
| row.manual_buy_enabled | manual_buy_enabled | PASS | true |

### 3.3 _create_real_buy_intent()

읽는 필드:

| code path | field | temp 존재/유효 | 비고 |
|---|---|---:|---|
| candidate.get('ticker') | ticker | PASS | 8/8 non-empty |
| candidate.get('notional') | notional | PRESENT | 8/8 present, value 0.0. 사용자가 req.notional을 주면 문제 없음. req.notional 없으면 _positive_float에서 거부 가능. |
| candidate.get('trade_date') or execution_session | trade_date / execution_session | PASS | 8/8 non-empty |
| candidate.get('entity_id') | entity_id | OPTIONAL_EMPTY | 0/8. metadata 용도라 direct buy 필수 아님. |
| candidate.get('price') | price | PASS | 8/8 positive |
| candidate whole row | candidate_snapshot | PASS | full row 저장 가능 |
| candidate.get('candidate_source') | candidate_source | PASS | real_dashboard_buy_candidates_export |
| candidate.get('real_candidate_fallback') | real_candidate_fallback | PASS | False |

SAFETY guard 관점:

```text
candidate_source == live_slots_state_fallback: 0건
real_candidate_fallback == True: 0건
```

---

## 4. 매수 → 포지션 등록 → 청산 연결 경로가 읽는 필드

### 4.1 직접 dashboard-real 매수 경로

코드:

```text
engine/live/real_dashboard_api.py:698-784
  _create_real_buy_intent()
```

직접 경로 동작:

```text
_candidate_for_real(candidate_id)
-> ticker/notional/trade_date/entity_id/price/candidate_source/real_candidate_fallback 읽기
-> broker.place_buy(... MARKET ...)
-> intent row에 candidate_snapshot 저장
-> REAL_BUY_INTENT_PATH에 저장
```

확인된 한계:

```text
이 함수는 PositionManager.register_entry()를 호출하지 않는다.
이 함수는 PendingOrderManager.track_order(... metadata=...)를 호출하지 않는다.
이 함수는 candidate_snapshot.selected_rulebook을 pending reconciliation metadata로 직접 넘기지 않는다.
```

### 4.2 BUY reconciliation / 포지션 등록 경로

코드:

```text
engine/live/buy_reconciliation.py:67-113
```

읽는 필드:

| code path | field/source | 목적 | temp 후보에 존재/유효 |
|---|---|---|---:|
| preflight_from_metadata() | selected_rulebook or rulebook_override or rulebook | Rulebook.from_dict 복원 | candidate row에는 PASS, 단 direct path가 metadata로 전달하는 연결은 미확인 |
| preflight_from_metadata() | preflight_atr or atr | PositionManager.register_entry atr_value | atr PASS, preflight_atr는 candidate row에 없음 |
| preflight_from_metadata() | entry_market_context | entry market context | candidate row에는 없음. optional |
| requires_selected_rulebook_metadata() | entity_id or selected_rulebook_hash | central/next_open fail-closed 여부 판단 | selected_rulebook_hash PASS, entity_id optional empty |
| reconcile() | metadata.rulebook | PositionManager.register_entry rulebook | candidate row selected_rulebook PASS, metadata handoff 미확인 |
| reconcile() | metadata.atr | PositionManager.register_entry atr_value | candidate row atr PASS, metadata handoff 미확인 |

중요:

```text
BuyReconciliationService는 pending metadata에서 selected_rulebook/preflight_atr/entry_market_context를 읽는다.
temp candidate row에는 selected_rulebook과 atr이 있지만, dashboard-real direct buy path가 이 row를 pending metadata로 넘긴다는 코드 연결은 확인되지 않았다.
```

### 4.3 PositionManager.register_entry()

코드:

```text
engine/live/position_manager.py:233-310
```

읽는 rulebook/entry 필드:

| code path | field | temp selected_rulebook 존재/유효 |
|---|---|---:|
| rulebook.direction | direction | PASS, Rulebook default 가능 |
| initialize_position_state() | stop_loss_atr | PASS |
| initialize_position_state() | stop_loss_atr_bear | PASS |
| initialize_position_state() | take_profit_atr | PASS |
| initialize_position_state() | take_profit_atr_bull | PASS |
| initialize_position_state() | trailing_atr | PASS |
| initialize_position_state() | trailing_atr_volatile | PASS |
| initialize_position_state() | max_holding_days | PASS |
| register_entry | exit_strategy | PASS |
| register_entry | win_rate | optional/default |
| register_entry | rulebook.to_dict() | selected_rulebook full dict로 Rulebook 생성 가능 |
| register_entry | member_hash | compute_member_hash(rulebook) 가능 |

### 4.4 ExitPolicy / 청산 트리거 경로

코드:

```text
engine/core/exit_policy.py:207-263
engine/core/exit_policy.py:433-566
engine/live/exit_policy_adapter.py:220-228
```

초기 포지션 레벨 계산 필드:

| trigger/level | field | temp selected_rulebook 존재/유효 |
|---|---|---:|
| hard stop / stop_price | stop_loss_atr | PASS |
| bear dynamic hard stop | stop_loss_atr_bear | PASS |
| take profit / target_price | take_profit_atr | PASS |
| bull dynamic target | take_profit_atr_bull | PASS |
| trailing distance | trailing_atr | PASS |
| high VIX dynamic trailing | trailing_atr_volatile | PASS |
| timeout | max_holding_days | PASS |
| strategy priority | exit_strategy | PASS |

라이브 청산 판단 필드:

| trigger | field/source | temp selected_rulebook 존재/유효 |
|---|---|---:|
| stop_loss | position.stop_price initialized from rulebook | PASS |
| trailing | position.trailing_stop/distance initialized from rulebook | PASS |
| trailing activation | execution config + rulebook/position state | selected_rulebook.trailing_activation_profit_pct PASS |
| take_profit | position.target_price initialized from rulebook + config take_profit_enabled | PASS |
| time_out | position.max_holding_days | PASS |
| breakeven_stop | breakeven_enabled / breakeven_trigger_profit_pct / breakeven_floor_profit_pct | PASS |
| sell_omen | sell_omen_enabled / sell_omen_threshold | PASS |
| snapshot restore | position.rulebook_snapshot | selected_rulebook can become snapshot if reconciliation wires it into Rulebook |

---

## 5. temp 후보 필드 유효성 집계

전체 후보:

```text
8개
```

필드별 존재/유효성:

| field | present | valid | examples |
|---|---:|---:|---|
| candidate_id | 8 | 8 | stage2:ALGT:402f72d48c3c, stage3:ADMA:42437a3ee595 |
| ticker | 8 | 8 | ALGT, ADMA |
| status | 8 | 8 | pending |
| manual_buy_enabled | 8 | 8 | True |
| candidate_source | 8 | 8 | real_dashboard_buy_candidates_export |
| real_candidate_fallback | 8 | 8 | False |
| notional | 8 | 8 | 0.0 |
| trade_date | 8 | 8 | 2026-07-09 |
| execution_session | 8 | 8 | 2026-07-09 |
| entity_id | 0 | 0 | optional metadata |
| price | 8 | 8 | positive |
| atr | 8 | 8 | positive |
| selected_rulebook | 8 | 8 | dict_len=88 |
| rulebook | 8 | 8 | dict_len=88 |
| source_file | 8 | 8 | existing path |
| full_rulebook_verified | 8 | 8 | True |
| should_buy_verified | 8 | 8 | True |

청산 관련 selected_rulebook 하위 필드:

| selected_rulebook field | present | valid |
|---|---:|---:|
| stop_loss_atr | 8 | 8 |
| stop_loss_atr_bear | 8 | 8 |
| take_profit_atr | 8 | 8 |
| take_profit_atr_bull | 8 | 8 |
| trailing_atr | 8 | 8 |
| trailing_atr_volatile | 8 | 8 |
| trailing_activation_profit_pct | 8 | 8 |
| max_holding_days | 8 | 8 |
| exit_strategy | 8 | 8 |
| breakeven_enabled | 8 | 8 |
| breakeven_trigger_profit_pct | 8 | 8 |
| breakeven_floor_profit_pct | 8 | 8 |
| sell_omen_enabled | 8 | 8 |
| sell_omen_threshold | 8 | 8 |

---

## 6. 기존 정규 파일 대비 temp 누락/추가

### 6.1 Top-level 비교

기존 원본 top-level:

```text
buy_mode
candidates
isolated
manual_buy_enabled
note
schema_version
source
trade_date
updated_at
```

Temp top-level:

```text
buy_mode
candidates
export_meta
isolated
manual_buy_enabled
note
schema_version
source
trade_date
updated_at
```

Top-level 추가:

```text
export_meta
```

Top-level 누락:

```text
0건
```

### 6.2 Candidate-level 비교

기존 정규 파일:

```text
candidates=0
```

따라서 기존 후보 payload와의 직접 비교는 불가능하다.

```text
기존 후보 스키마 대비 누락/추가: NOT_AVAILABLE_FROM_FILE
```

코드 소비 기준 누락:

```text
매수 happy path 필수 필드 누락: 0건
full rulebook/selected_rulebook 필수 필드 누락: 0건
청산 주요 trigger 하위 필드 누락: 0건
```

후속 reconciliation metadata 관점 잠재 누락:

```text
preflight_atr field는 temp candidate row에 없음.
다만 atr field는 있음.
buy_reconciliation.preflight_from_metadata()는 preflight_atr or atr을 허용하므로 candidate row 자체에는 대응값이 존재한다.
문제는 candidate row가 pending metadata로 실제 전달되는 연결이 현재 direct dashboard-real 코드에서 확인되지 않았다는 점이다.
```

---

## 7. Happy-path 로직 검증 결과

기존 검증:

```text
REAL_BUY_CANDIDATES_PATH를 temp export file로 monkeypatch
REAL_BUY_INTENT_PATH는 임시 파일로 monkeypatch
_get_real_broker는 FakeBroker
실제 Alpaca 주문 없음
```

결과:

```text
_candidate_for_real(candidate_id) 정규 row 반환: PASS
candidate_source == real_dashboard_buy_candidates_export: PASS
real_candidate_fallback == False: PASS
SAFETY fallback guard 미발동: PASS
FakeBroker.get_current_price 호출: 1
FakeBroker.place_buy 호출: 1
returned status: submitted
execution_mode: direct_alpaca_live_market_order
```

---

## 8. 판정

```text
SCHEMA_MATCH_HAPPYPATH_ONLY
```

근거:

```text
- _candidate_for_real() 및 _create_real_buy_intent() 직접 소비 필드 기준: complete.
- selected_rulebook/rulebook full dict 및 청산 트리거 하위 필드 기준: complete.
- 기존 정규 파일은 candidates=0이라 과거 후보 스키마와 직접 비교 불가.
- 매수 후 PositionManager.register_entry()/ExitPolicy까지 candidate_snapshot.selected_rulebook이 자동 전달되는 end-to-end 연결은 현재 direct dashboard-real 코드에서 확인되지 않음.
```

완전 일치를 주장하지 않는 이유:

```text
SCHEMA_MATCH_COMPLETE로 보려면 다음 연결까지 확인되어야 한다.
1. dashboard-real broker order가 PendingOrderManager에 track_order로 등록된다.
2. track_order metadata에 selected_rulebook 또는 rulebook, preflight_atr 또는 atr, entry_market_context가 들어간다.
3. BuyReconciliationService.preflight_from_metadata()가 그 metadata를 읽어 Rulebook.from_dict()를 복원한다.
4. PositionManager.register_entry()가 rulebook_snapshot을 생성한다.
5. ExitPolicy가 그 snapshot으로 stop/trailing/take_profit/time_out을 계산한다.

현재 확인 범위에서는 1~2 연결이 확인되지 않았다.
```
