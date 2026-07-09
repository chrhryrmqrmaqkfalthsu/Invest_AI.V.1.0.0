# real_dashboard_buy_candidates export --write + 통합 검증 readout

작업 범위:

```text
1. scripts/export_real_dashboard_buy_candidates.py --source-section slots --limit 20 --write 실행
2. write 후 data/_system/real_dashboard_buy_candidates.json 정규 파일 검증
3. 방금 write된 정규 파일 → FakeBroker 매수 요청 → FILLED → PositionManager.register_entry() → 룰북 청산선 등록 end-to-end 검증
```

금지 준수:

```text
실계좌 주문 제출 없음
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS 운영 설정 변경 없음
실제 direct_orders_enabled는 False 유지
live_candidate_slots 계산 로직 수정 없음
export 스크립트 수정 없음
SAFETY guard 수정 없음
```

최종 판정:

```text
EXPORT_WRITE_SUCCESS
REGULAR_FILE_VALIDATED
WRITE_FILE_TO_BUY_TO_EXIT_REGISTRATION_E2E_PASS
DIRECT_ORDERS_SETTING_UNCHANGED_FALSE
NO_REAL_ORDER_SUBMITTED
```

---

## 1. write 전 상태

정규 후보 파일:

```text
path: data/_system/real_dashboard_buy_candidates.json
mtime_kst: 2026-07-08 02:17:30 KST
size: 433
updated_at: ''
candidates: 0
source: real_dashboard_buy_candidates
```

live state:

```text
live_state_updated_at: 2026-07-09T16:57:35.008938+00:00
live_slots: 8
candidate_pool: 27
waitlist: 19
```

백업:

```text
backup/pre_export_write_real_candidates_20260709_165803.tar.gz
```

---

## 2. write 실행

명령:

```text
cd ~/kingmaker && PYTHONPATH=. venv/bin/python scripts/export_real_dashboard_buy_candidates.py \
  --source-section slots \
  --limit 20 \
  --write \
  --summary-path data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_write_summary.json
```

결과:

```text
RC=0
ok=True
validation_ok=True
canonical_output_replaced=True
post_write_validation_ok=True
post_write_validation_errors=[]
post_write_validation_stats.candidate_errors={}
```

write summary:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_write_summary.json
```

temp path:

```text
/home/g3000kkw/kingmaker/data/_system/.real_dashboard_buy_candidates.json.tmp.229615.20260709T165834Z
```

주의:

```text
source_section=slots이고 현재 live 슬롯은 8개라 limit=20이어도 exported_count=8이다.
candidate_pool은 27개였지만 이번 지시의 실행 command가 --source-section slots였으므로 candidate_pool write는 하지 않았다.
```

---

## 3. write 후 정규 파일 상태

정규 후보 파일:

```text
path: data/_system/real_dashboard_buy_candidates.json
mtime_kst: 2026-07-10 01:58:34 KST
size: 93119
updated_at: 2026-07-09T16:58:29.612877+00:00
trade_date: 2026-07-09
source: real_dashboard_buy_candidates
source_section: slots
limit: 20
live_slot_count: 8
exported_count: 8
skipped_count: 0
```

후보 ID:

```text
stage3:BMI:07d4ee0f7841
stage3:BMA:0c978464f9dd
stage3:BTBT:363898884d44
stage3:ADMA:42437a3ee595
stage3:CE:998b0b638c66
stage3:BCS:5e7da5a74b01
stage2:ALGT:402f72d48c3c
stage3:ALGT:aec5dd5b1dc1
```

---

## 4. write 후 후보 payload 검증

각 후보 검증 조건:

```text
candidate_source == real_dashboard_buy_candidates_export
real_candidate_fallback == False
atr > 0
rulebook dict len >= 50
selected_rulebook dict len >= 50
source_file exists
should_buy_verified == True
full_rulebook_verified == True
```

검증 결과:

| candidate_id | ticker | candidate_source | fallback | atr | rulebook_len | selected_len | source_file_exists | should_buy_verified | full_rulebook_verified |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| stage2:ALGT:402f72d48c3c | ALGT | real_dashboard_buy_candidates_export | False | 6.386689045625623 | 88 | 88 | True | True | True |
| stage3:ADMA:42437a3ee595 | ADMA | real_dashboard_buy_candidates_export | False | 0.36327608448120496 | 88 | 88 | True | True | True |
| stage3:ALGT:aec5dd5b1dc1 | ALGT | real_dashboard_buy_candidates_export | False | 6.386689045625623 | 88 | 88 | True | True | True |
| stage3:BCS:5e7da5a74b01 | BCS | real_dashboard_buy_candidates_export | False | 0.6898167819891539 | 88 | 88 | True | True | True |
| stage3:BMA:0c978464f9dd | BMA | real_dashboard_buy_candidates_export | False | 4.56109647340232 | 88 | 88 | True | True | True |
| stage3:BMI:07d4ee0f7841 | BMI | real_dashboard_buy_candidates_export | False | 5.3530176350470295 | 88 | 88 | True | True | True |
| stage3:BTBT:363898884d44 | BTBT | real_dashboard_buy_candidates_export | False | 0.1801082004378081 | 88 | 88 | True | True | True |
| stage3:CE:998b0b638c66 | CE | real_dashboard_buy_candidates_export | False | 2.2478600659746704 | 88 | 88 | True | True | True |

판정:

```text
REGULAR_FILE_PAYLOAD_VALIDATED
bad_count=0
```

---

## 5. 통합 검증 — write된 파일 → 매수 → 청산 자동등록

검증 방식:

```text
실제 Alpaca 주문 없음
FakeBroker 사용
테스트 프로세스에서만 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1 설정
REAL_BUY_CANDIDATES_PATH는 방금 write된 canonical data/_system/real_dashboard_buy_candidates.json 사용
REAL_BUY_INTENT_PATH / POSITIONS_PATH / PENDING_ORDERS_PATH는 TemporaryDirectory로 monkeypatch
```

검증 후보:

```text
candidate_id: stage3:BMI:07d4ee0f7841
ticker: BMI
candidate_source: real_dashboard_buy_candidates_export
real_candidate_fallback: False
rulebook_len: 88
selected_rulebook_len: 88
atr: 5.3530176350470295
entry_price(fake current price): 146.3699951171875
shares_requested: 0.6832001321031506
```

검증 결과:

```text
WRITE_FILE_TO_BUY_TO_EXIT_REGISTRATION_E2E_OK
```

경로 판정:

```text
_candidate_for_real() 정규 파일 row 반환: PASS
SAFETY fallback guard 미발동: PASS
FakeBroker.get_current_price 호출: 1
FakeBroker.place_buy 호출: 1
BuyReconciliationService.reconcile(purpose='entry', preflight) 경유: PASS
PositionManager.register_entry() 경유: PASS
position_registered: True
pending_order_tracked: False
reconciliation_status: registered
```

---

## 6. 청산선 값 대조 — registered vs selected_rulebook expected

| field | registered position | expected from selected_rulebook | match |
|---|---:|---:|---:|
| stop_price | 127.73380992665025 | 127.73380992665025 | PASS |
| target_price | 164.29607835367614 | 164.29607835367614 | PASS |
| trailing_stop | 135.2650754080895 | 135.2650754080895 | PASS |
| trailing_distance | 11.104919709097997 | 11.104919709097997 | PASS |
| max_holding_days | 18 | 18 | PASS |
| exit_strategy | trailing | trailing | PASS |

판정:

```text
REGISTERED_EXIT_LEVELS_MATCH_WRITTEN_SELECTED_RULEBOOK
```

---

## 7. entry_market_context 값 대조

| field | candidate source | metadata.entry_market_context | match |
|---|---:|---:|---:|
| market_score | 83.1 | 83.1 | PASS |
| vix_level | 16.07 | 16.07 | PASS |
| sector_score | 100.0 | 100.0 | PASS |
| candidate_id | stage3:BMI:07d4ee0f7841 | stage3:BMI:07d4ee0f7841 | PASS |
| rulebook_hash | 07d4ee0f7841289628c40f20e3b7571522ab3cd12491ee889af8bbe8be5c6b5d | 07d4ee0f7841289628c40f20e3b7571522ab3cd12491ee889af8bbe8be5c6b5d | PASS |
| source | dashboard_real_candidate_snapshot | dashboard_real_candidate_snapshot | PASS |

판정:

```text
ENTRY_MARKET_CONTEXT_MATCHES_WRITTEN_CANDIDATE
```

---

## 8. 운영 스위치/실계좌 안전 확인

write 및 FakeBroker 검증 후 실제 API 상태:

```text
broker_mode: alpaca_live
direct_orders_enabled: False
holdings_count: 0
```

판정:

```text
DIRECT_ORDERS_SETTING_UNCHANGED_FALSE
NO_REAL_ORDER_SUBMITTED
```

---

## 9. 최종 판정

```text
EXPORT_WRITE_SUCCESS
CANONICAL_REAL_CANDIDATES_REPLACED_ATOMICALLY
POST_WRITE_SCHEMA_VALIDATED
FULL_RULEBOOK_SELECTED_RULEBOOK_PRESENT_FOR_ALL_CANDIDATES
FALLBACK_SOURCE_ZERO
ATR_VALID_FOR_ALL_CANDIDATES
WRITE_FILE_TO_BUY_TO_EXIT_REGISTRATION_E2E_PASS
REGISTERED_EXIT_LEVELS_MATCH_WRITTEN_SELECTED_RULEBOOK
ENTRY_MARKET_CONTEXT_MATCHES_WRITTEN_CANDIDATE
DIRECT_ORDERS_SETTING_UNCHANGED_FALSE
NO_REAL_ORDER_SUBMITTED
```

남은 참고 사항:

```text
이번 write는 --source-section slots 기준이라 8개 후보만 정규 파일에 들어갔다.
20개 후보까지 확보하려면 별도 승인 후 --source-section candidate_pool --limit 20 방식으로 재검증/재write가 필요하다.
```
