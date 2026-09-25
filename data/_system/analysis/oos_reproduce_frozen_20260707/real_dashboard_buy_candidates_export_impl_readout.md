# real_dashboard_buy_candidates 정규 후보 export 파이프라인 구현 readout

기준 설계:

```text
commit 31b8dd4
산출물: data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_export_design_readout.md
```

구현 상태:

```text
IMPLEMENTED_SCRIPT_ONLY
TEMP_GENERATED_AND_VALIDATED
CANONICAL_OUTPUT_NOT_REPLACED
```

구현 파일:

```text
scripts/export_real_dashboard_buy_candidates.py
```

검증 산출물:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_export_validation_summary.json
data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_export_impl.diff
data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_export_impl_readout.md
```

정규 파일 교체 여부:

```text
NOT_REPLACED
```

기존 정규 파일 상태 유지 확인:

```text
data/_system/real_dashboard_buy_candidates.json
mtime_utc: 2026-07-07 17:17:30.511494461 +0000
size: 433
```

---

## 1. 소비 코드가 실제로 읽는 필드 목록

소비 코드:

```text
engine/live/real_dashboard_api.py
```

### 1.1 _real_candidate_state()

`_real_candidate_state()`가 읽는 필드:

| 소비 위치 | 읽는 필드 | 조건/용도 |
|---|---|---|
| read_json(REAL_BUY_CANDIDATES_PATH, {}) | top-level 전체 | 파일 로드 |
| state.update(data) | top-level fields | 기본 state에 overlay |
| state.get('candidates') | candidates | dict가 아니면 {} 처리 |
| candidates row | row.status | hidden status 제외 |
| real buy intents overlay | candidate_id | pending/submitted intent가 있으면 row status/manual_buy_enabled overlay |

### 1.2 _candidate_for_real()

`_candidate_for_real(candidate_id)`가 읽는 필드:

| 소비 위치 | 읽는 필드 | 조건/용도 |
|---|---|---|
| candidates[cid] | candidate_id key | row가 dict여야 함 |
| row.status | status | pending 또는 manual_requested만 통과 |
| row.manual_buy_enabled | manual_buy_enabled | False이면 거부 |

### 1.3 _create_real_buy_intent()

`_create_real_buy_intent()`가 읽는 필드:

| 소비 위치 | 읽는 필드 | 조건/용도 |
|---|---|---|
| candidate.get('ticker') | ticker | 필수. 없으면 ValueError |
| candidate.get('notional') | notional | req.notional 없을 때 default_notional |
| candidate.get('trade_date') / execution_session | trade_date / execution_session | intent trade_date |
| candidate.get('entity_id') | entity_id | intent metadata |
| candidate.get('price') | price | intent metadata, broker price fallback |
| candidate whole row | candidate_snapshot | intent에 전체 저장 |
| candidate.get('candidate_source') | candidate_source | SAFETY guard. live_slots_state_fallback이면 차단 |
| candidate.get('real_candidate_fallback') | real_candidate_fallback | SAFETY guard. True이면 차단 |

---

## 2. 소비 필드 ↔ export 필드 1:1 대조표

| 소비 코드 요구 | export 필드 | export 생성값 | 누락/불일치 |
|---|---|---|---:|
| candidates[cid] must be dict | candidates.<candidate_id> | validated candidate row dict | 0 |
| status in pending/manual_requested | status | pending | 0 |
| manual_buy_enabled is not False | manual_buy_enabled | true | 0 |
| ticker required | ticker | evaluate_candidate/full candidate ticker | 0 |
| default_notional | notional | live row notional if present else 0.0 | 0 |
| trade_date/execution_session | trade_date, execution_session | live state date/export date | 0 |
| entity_id metadata | entity_id | full candidate entity_id if present | 0 |
| price metadata/fallback | price | evaluate_candidate price | 0 |
| candidate_snapshot full row | selected_rulebook, rulebook, source_file, full_rulebook_verified | final_rulebooks/survivor full rulebook + validation metadata | 0 |
| SAFETY source check | candidate_source | real_dashboard_buy_candidates_export | 0 |
| SAFETY fallback check | real_candidate_fallback | false | 0 |

검증 결과:

```text
소비 코드 필드 ↔ export 필드 누락/불일치: 0건
```

---

## 3. 구현 요약

신규 스크립트:

```text
scripts/export_real_dashboard_buy_candidates.py
```

동작 흐름:

```text
1. data/_system/live_slots_state.json 읽기
2. source_section=slots에서 candidate_id 있는 row 최대 8개 추출
3. build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)로 현재 full candidate report 재구성
4. live slot candidate_id를 full report candidate_id와 매칭
5. _load_rulebook_for_candidate(full_candidate)로 final_rulebooks.jsonl/survivor source에서 full rulebook 재조회
6. full rulebook 검증
7. evaluate_candidate(full_candidate, ctx=get_market_context())로 should_buy 재검증
8. 통과 후보만 정규 candidate row로 변환
9. temp JSON write
10. temp JSON read-back validation
11. 이번 실행에서는 canonical output 교체 없음
```

full rulebook 검증 조건:

```text
len(rulebook) >= 50
필수 필드 포함:
- signal_threshold
- rsi_low
- rsi_high
- event_response_war
- vix_sensitivity
- stop_loss_atr
- take_profit_atr
- trailing_atr
- max_holding_days
```

SAFETY 호환 필드:

```text
candidate_source = real_dashboard_buy_candidates_export
real_candidate_fallback = False
```

정규 파일 교체 제어:

```text
기본 실행: temp 생성 + 검증만 수행
--write 명시 시: 검증 통과 후 os.replace로 canonical output 교체 가능
이번 작업에서는 --write 미사용
```

수정 금지 준수:

```text
engine/live/real_dashboard_api.py 수정 없음
SAFETY guard 수정 없음
data/_system/ops/live_candidate_slots.py 수정 없음
청산 로직 수정 없음
```

---

## 4. dry-run/temp export 실행 결과

실행 명령:

```text
cd ~/kingmaker && PYTHONPATH=. venv/bin/python scripts/export_real_dashboard_buy_candidates.py \
  --source-section slots \
  --limit 8 \
  --summary-path data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_export_validation_summary.json
```

결과:

```text
RC=0
validation_ok=True
canonical_output_replaced=False
replacement_requested=False
```

Temp output:

```text
/home/g3000kkw/kingmaker/data/_system/.real_dashboard_buy_candidates.json.tmp.224108.20260709T161407Z
```

Summary:

```text
ok: true
state_path: data/_system/live_slots_state.json
state_updated_at: 2026-07-09T16:13:54.780091+00:00
source_section: slots
limit: 8
live_slot_count: 8
report_candidate_count: 93
exported_count: 8
skipped_count: 0
skipped_summary: {}
validation_errors: []
validation_stats.candidate_errors: {}
```

Live 슬롯 후보:

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

Export 후보:

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

비교:

```text
live_slot_count=8
exported_count=8
matched_count=8
skipped_count=0
```

---

## 5. full rulebook / selected_rulebook 검증

Temp payload candidates 전체 검증:

| candidate_id | ticker | candidate_source | real_candidate_fallback | rulebook_len | selected_rulebook_len | source_file_exists | should_buy_verified | full_rulebook_verified |
|---|---|---|---:|---:|---:|---:|---:|---:|
| stage2:ALGT:402f72d48c3c | ALGT | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:ADMA:42437a3ee595 | ADMA | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:ALGT:aec5dd5b1dc1 | ALGT | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:BCS:5e7da5a74b01 | BCS | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:BMA:0c978464f9dd | BMA | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:BMI:07d4ee0f7841 | BMI | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:BTBT:363898884d44 | BTBT | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |
| stage3:CE:998b0b638c66 | CE | real_dashboard_buy_candidates_export | False | 88 | 88 | True | True | True |

판정:

```text
full rulebook/selected_rulebook 존재 검증: PASS
compact payload 포함: 0건
fallback source 포함: 0건
```

---

## 6. 실제 주문 없는 정규 매수 경로 검증

검증 방식:

```text
실제 Alpaca 호출 없음
REAL_BUY_CANDIDATES_PATH를 temp export file로 monkeypatch
REAL_BUY_INTENT_PATH는 TemporaryDirectory 내부 파일로 monkeypatch
_get_real_broker는 FakeBroker로 monkeypatch
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1 테스트 프로세스에서만 설정
```

검증 후보:

```text
stage2:ALGT:402f72d48c3c
```

검증 결과:

```text
_candidate_for_real(candidate_id) 정규 row 반환: PASS
candidate_source == real_dashboard_buy_candidates_export: PASS
real_candidate_fallback == False: PASS
SAFETY fallback guard 미발동: PASS
FakeBroker.get_current_price 호출: 1
FakeBroker.place_buy 호출: 1
returned status: submitted
returned execution_mode: direct_alpaca_live_market_order
```

출력:

```text
regular_path_test_candidate stage2:ALGT:402f72d48c3c ALGT
fake_broker_price_calls 1
fake_broker_buy_calls 1
EXPORT_SCHEMA_AND_REGULAR_PATH_TEST_OK
```

---

## 7. 구현 diff

Diff artifact:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_export_impl.diff
```

요약:

```text
scripts/export_real_dashboard_buy_candidates.py | 632 insertions
```

---

## 8. 최종 판정

```text
EXPORT_SCRIPT_IMPLEMENTED
TEMP_EXPORT_VALIDATED
FIELD_MAPPING_MATCHED
FULL_RULEBOOK_VALIDATED
SHOULD_BUY_REVALIDATED
SAFETY_REGULAR_PATH_VALIDATED_WITH_FAKE_BROKER
CANONICAL_OUTPUT_NOT_REPLACED
```

다음 단계:

```text
사용자 승인 후에만 --write로 canonical data/_system/real_dashboard_buy_candidates.json 교체 실행.
```
