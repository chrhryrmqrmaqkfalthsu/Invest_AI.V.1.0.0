# real_dashboard_buy_candidates 정규 후보 export 파이프라인 — 1단계 설계 readout

상태:

```text
DESIGN_ONLY
IMPLEMENTATION_NOT_STARTED
STOP_AFTER_STEP_1
```

목적:

```text
live_slots_state.json의 표시 후보를 그대로 복사하지 않고,
candidate_id를 기준으로 현재 full elite candidate report와 final_rulebooks.jsonl에서 full rulebook을 재조회한 뒤,
full rulebook 기준 should_buy를 다시 검증하여,
검증된 후보만 data/_system/real_dashboard_buy_candidates.json 정규 매수 후보 파일로 export한다.
```

핵심 원칙:

```text
- compact payload는 정규 후보 파일에 넣지 않는다.
- full rulebook을 못 찾은 후보는 export하지 않는다.
- selected_rulebook/source_file은 필수다.
- export 직전 should_buy를 full rulebook으로 재검증한다.
- SAFETY guard, _create_real_buy_intent, live_candidate_slots 계산 로직, 청산 로직은 수정하지 않는다.
- 2단계 구현은 별도 스크립트로만 한다.
```

---

## 1. 현재 소비 코드 기준 정규 후보 파일 스키마

대상 파일:

```text
data/_system/real_dashboard_buy_candidates.json
```

소비 코드:

```text
engine/live/real_dashboard_api.py
```

### 1.1 top-level state

`_default_real_candidate_state()` 기준:

```text
engine/live/real_dashboard_api.py:615-630
```

기대 top-level 필드:

| field | required | 설계값 |
|---|---:|---|
| schema_version | YES | 1 |
| buy_mode | YES | real_isolated |
| source | YES | real_dashboard_buy_candidates |
| isolated | YES | true |
| trade_date | YES | 현재 UTC 날짜 또는 state 기준 거래일. 없으면 '' 허용. |
| updated_at | YES | export 시각 UTC ISO |
| manual_buy_enabled | YES | true |
| candidates | YES | dict[candidate_id, candidate_row] |
| note | NO | export 설명 |
| export_meta | NO | exporter, input state, counts, skip summary, validation result |

API가 런타임에 추가/덮어쓰는 필드:

```text
engine/live/real_dashboard_api.py:642-647
  source='real_dashboard_buy_candidates'
  isolated=True
  state_path
  order_intent_path
  direct_orders_enabled
  connection
```

따라서 파일에는 `state_path`, `connection`을 저장하지 않아도 되지만, 저장해도 API가 런타임 값으로 덮어쓴다.

### 1.2 candidates dict 소비 방식

`_real_candidate_state()` 기준:

```text
engine/live/real_dashboard_api.py:652-656
```

후보 dict 필터:

```text
candidate row는 dict여야 한다.
include_blocked=False일 때 hidden status는 제외된다.
hidden = manual_executed, auto_executed, expired, cancelled, canceled, blocked
```

`_candidate_for_real()` 기준:

```text
engine/live/real_dashboard_api.py:676-689
```

주문 후보로 통과하려면:

| 조건 | 코드 기준 |
|---|---|
| candidates[cid] 존재 | row must be dict |
| status | pending 또는 manual_requested |
| manual_buy_enabled | False가 아니어야 함 |
| ticker | _create_real_buy_intent에서 필수 |
| notional | req.notional이 없을 경우 default_notional로 사용. 0이면 사용자가 notional을 넣어야 함 |
| price | intent metadata 및 fallback current price 보조값 |
| candidate_source | SAFETY guard 때문에 live_slots_state_fallback이면 안 됨 |
| real_candidate_fallback | SAFETY guard 때문에 True이면 안 됨 |

`_create_real_buy_intent()`가 후보 row에서 읽는 필드:

```text
engine/live/real_dashboard_api.py:698-731
```

| field | 사용처 |
|---|---|
| ticker | 필수. 주문 ticker |
| notional | req.notional 없을 때 default_notional |
| trade_date 또는 execution_session | intent trade_date |
| entity_id | intent metadata |
| price | intent price 및 broker price fallback |
| 전체 candidate row | candidate_snapshot으로 저장 |

SAFETY guard 기준:

```text
engine/live/real_dashboard_api.py:734-764
```

정규 export 후보는 반드시 아래를 만족해야 한다.

```text
candidate.get('real_candidate_fallback') is not True
candidate.get('candidate_source') != 'live_slots_state_fallback'
```

### 1.3 candidate row 설계 스키마

필수 필드:

| field | 값/출처 |
|---|---|
| candidate_id | live slot row의 candidate_id. 예: stage3:CE:998b0b638c66 |
| ticker | full candidate 또는 evaluation ticker |
| stage | full candidate stage |
| bucket | full candidate bucket |
| status | pending |
| manual_buy_enabled | true |
| candidate_source | real_dashboard_buy_candidates_export |
| real_candidate_fallback | false |
| source_file | full candidate source_file. final_rulebooks.jsonl 경로 |
| source_row_index | stage2 후보일 때 필요. stage3는 null/없어도 됨 |
| rulebook_hash | full candidate rulebook_hash |
| rulebook_hash_short | candidate_id suffix와 일치해야 함 |
| selected_rulebook | final_rulebooks.jsonl에서 재조회한 full rulebook dict |
| rulebook | selected_rulebook과 같은 full rulebook dict |
| selected_rulebook_hash | rulebook_hash |
| full_rulebook_verified | true |
| full_rulebook_verified_at | export 시각 UTC ISO |
| full_rulebook_source | source_file |
| full_rulebook_key | rulebook_hash 또는 source_row_index |
| should_buy_verified | true |
| should_buy_verified_at | export 시각 UTC ISO |
| price | evaluate_candidate 결과 price |
| atr | evaluate_candidate 결과 atr |
| final_score | evaluate_candidate 결과 score |
| raw_score | evaluate_candidate 결과 raw_score |
| threshold | evaluate_candidate 결과 threshold |
| ratio | evaluate_candidate 결과 ratio |
| reasons | evaluate_candidate 결과 reasons |
| market_score | evaluate_candidate 결과 market_score |
| sector_score | evaluate_candidate 결과 sector_score |
| vix_level | evaluate_candidate 결과 vix_level |
| first_signal_at | live_slots_state row에서 복사. 표시/추적 metadata |
| first_signal_price | live_slots_state row에서 복사 |
| first_final_score | live_slots_state row에서 복사 |
| last_seen_at | live_slots_state row에서 복사 |
| slot_no | live_slots_state row에서 복사 |
| source_rank | live_slots_state row 또는 full candidate rank |
| win_rate/expectancy_pct/mdd_pct/fitness/trade_count | full candidate metrics 또는 live row metadata |
| max_holding_days/exit_strategy/stop_loss_atr/take_profit_atr/trailing_atr | selected_rulebook 기준 |

금지 필드/값:

| field | 금지값 |
|---|---|
| candidate_source | live_slots_state_fallback |
| real_candidate_fallback | true |
| selected_rulebook | absent, null, compact dict |
| rulebook | absent, null, compact dict |
| source_file | absent |

full rulebook 판정:

```text
selected_rulebook이 dict이고, final_rulebooks.jsonl 원본 row['rulebook'] 전체여야 한다.
compact 후보 rulebook은 필드 수가 작고 event_response/RSI/BB 등 많은 파라미터가 빠지므로 불합격.
검증 조건은 len(selected_rulebook) >= 50 과 핵심 필드 존재로 둔다.
핵심 필드 예: signal_threshold, rsi_low, rsi_high, event_response_war, vix_sensitivity, stop_loss_atr, take_profit_atr, trailing_atr, max_holding_days.
```

---

## 2. 데이터 매핑 설계

### 2.1 입력

기본 입력:

```text
data/_system/live_slots_state.json
```

사용 section:

```text
slots
```

설계 이유:

```text
/dashboard-real에 실제 표시되는 매수 후보 슬롯은 기본 8개다.
정규 매수 파일의 1차 목적은 화면에 표시된 후보가 fallback이 아니라 정규 후보 row로 매수 경로를 타게 하는 것이다.
따라서 기본 export 대상은 live_slots_state.slots의 candidate_id가 있는 row다.
```

옵션 설계:

```text
--source-section slots            # 기본값
--source-section candidate_pool   # 후속 확장 가능. 기본 구현 범위는 slots.
--limit 8                         # 기본값. dashboard-real visible slot 수와 맞춤.
```

### 2.2 live slot row는 candidate_id/order만 사용

live slot row에서 사용할 필드:

```text
candidate_id
ticker
slot_no / slot
first_signal_at
first_signal_price
first_final_score
last_seen_at
priority_group
```

live slot row에서 신뢰하지 않을 것:

```text
should_buy 판정
compact rulebook
stop/take/trailing 등 compact 파라미터
final_score/threshold/ratio의 최종 주문 판정
```

### 2.3 candidate_id → full candidate lookup

구현 스크립트는 현재 full report를 새로 만든다.

```text
from engine.live.elite_shadow_report import build_elite_shadow_report
report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
```

lookup:

```text
full_by_id = {
  candidate_id: candidate
  for candidate in report['candidates']
}
```

candidate_id 생성 규칙:

```text
stage2: {stage}:{ticker}:{rulebook_hash_short}
stage3: {stage}:{ticker}:{rulebook_hash_short}
```

참고 패턴:

```text
engine/live/s2_auto_trader.py:300-308
  _candidate_full_payload(candidate_id)
  build_elite_shadow_report(...)
  candidate_id 매칭
```

### 2.4 final_rulebooks.jsonl full rulebook 조회

재사용할 로더:

```text
engine/live/elite_shadow_trader.py:205-240
  _load_rulebook_for_candidate(candidate)
```

stage3:

```text
candidate['source_file'] = exp_batch_stage123_2009_20260616_full/tickers/<TICKER>/stage3/final_rulebooks.jsonl
candidate['rulebook_hash']로 row['rulebook_hash'] 일치 row 찾기
row['rulebook'] full dict 반환
```

stage2:

```text
candidate['source_file'] + candidate['source_row_index']로 source row를 찾고 row['rulebook'] 반환
```

불합격:

```text
source_file 없음
source_file 존재하지 않음
rulebook_hash/source_row_index 없음
row['rulebook']이 dict가 아님
full rulebook 핵심 필드가 없음
```

### 2.5 full rulebook 기준 should_buy 재검증

재사용할 evaluator:

```text
from engine.market.context import get_market_context
from engine.live.elite_shadow_trader import evaluate_candidate
ctx = get_market_context()
ev = evaluate_candidate(full_candidate, ctx=ctx)
```

통과 조건:

```text
ev['ok'] is True
ev['should_buy'] is True
_load_rulebook_for_candidate(full_candidate) returns full dict
selected_rulebook validation passes
```

불합격 후보는 export에서 제외하고 skip reason에 기록한다.

### 2.6 정규 후보 row 변환

mapping:

| output field | source |
|---|---|
| candidate_id | live slot candidate_id |
| ticker/stage/bucket/rulebook_hash/source_file/source_row_index | full candidate |
| selected_rulebook/rulebook | final_rulebooks full rulebook dict |
| selected_rulebook_hash | full candidate rulebook_hash |
| status/manual_buy_enabled | pending/true |
| candidate_source | real_dashboard_buy_candidates_export |
| real_candidate_fallback | false |
| price/atr/final_score/raw_score/threshold/ratio/reasons/market_score/sector_score/vix_level | evaluate_candidate result |
| first_signal_at/first_signal_price/first_final_score/last_seen_at/slot_no/priority_group | live slot row metadata |
| metrics fields | full candidate metrics 또는 live slot fallback metadata |
| export_meta fields | export script metadata |

---

## 3. 실행 방식 설계

### 3.1 스크립트

구현 예정 파일:

```text
scripts/export_real_dashboard_buy_candidates.py
```

실행 방식:

```text
cd ~/kingmaker && PYTHONPATH=. venv/bin/python scripts/export_real_dashboard_buy_candidates.py
```

기본 동작:

```text
- live_slots_state.json 읽기
- slots 8개 candidate_id 추출
- build_elite_shadow_report로 full candidates 재구성
- candidate_id 매칭
- final_rulebooks.jsonl에서 full rulebook 재조회
- evaluate_candidate로 should_buy 재검증
- 통과 후보만 정규 스키마로 변환
- 임시 JSON 파일에 먼저 쓰기
- 임시 파일을 다시 읽어 schema/full rulebook/SAFETY guard compatibility 검증
- 검증 통과 시 os.replace로 real_dashboard_buy_candidates.json 원자적 교체
```

### 3.2 수동 실행 우선

1차 구현은 수동 실행 스크립트만 만든다.

```text
cron/systemd 등록 없음
live_candidate_slots daemon 수정 없음
real_dashboard_api 수정 없음
SAFETY guard 수정 없음
```

주기 실행은 별도 세션에서 결정한다.

### 3.3 옵션 설계

| option | 기본값 | 설명 |
|---|---|---|
| --state-path | data/_system/live_slots_state.json | 입력 state |
| --output-path | data/_system/real_dashboard_buy_candidates.json | 정규 후보 파일 |
| --source-section | slots | export 대상 section |
| --limit | 8 | 최대 후보 수 |
| --dry-run | false | 파일 교체 없이 temp/summary만 출력 |
| --allow-empty | false | false면 validated 후보 0개일 때 정식 파일 미교체 |
| --summary-path | optional | 검증 summary JSON 저장 |

---

## 4. 원자적 쓰기/검증 설계

### 4.1 쓰기 순서

```text
1. current output file stat/hash 기록
2. payload 구성
3. temp path 생성: data/_system/.real_dashboard_buy_candidates.json.tmp.<pid>
4. temp JSON write
5. temp JSON read-back
6. validate_payload(temp_payload)
7. validate_candidate_rows(temp_payload)
8. validated_count > 0 확인, 단 --allow-empty일 때는 예외
9. os.replace(temp_path, output_path)
10. post-write read-back 검증
```

### 4.2 payload 검증 조건

Top-level:

```text
schema_version == 1
buy_mode == 'real_isolated'
source == 'real_dashboard_buy_candidates'
isolated is True
manual_buy_enabled is True
candidates is dict
```

Candidate:

```text
candidate_id key == row['candidate_id']
row['status'] == 'pending'
row['manual_buy_enabled'] is True
row['candidate_source'] == 'real_dashboard_buy_candidates_export'
row.get('real_candidate_fallback') is False
row['ticker'] non-empty
row['price'] > 0
row['selected_rulebook'] full dict
row['rulebook'] full dict
row['source_file'] exists
row['should_buy_verified'] is True
row['full_rulebook_verified'] is True
```

SAFETY compatibility:

```text
candidate_source != 'live_slots_state_fallback'
real_candidate_fallback is not True
```

---

## 5. 실패 케이스 처리

| 실패 케이스 | 처리 |
|---|---|
| live_slots_state 없음/파싱 실패 | exit nonzero, output 미교체 |
| slots 후보 0개 | exit nonzero, output 미교체. --allow-empty일 때만 empty payload 쓰기 |
| candidate_id가 full report에 없음 | 해당 후보 skip, reason=candidate_not_found_in_current_report |
| final_rulebooks full rulebook 못 찾음 | 해당 후보 skip, reason=full_rulebook_unavailable |
| full rulebook 핵심 필드 누락 | 해당 후보 skip, reason=full_rulebook_validation_failed |
| evaluate_candidate exception | 해당 후보 skip, reason=evaluate_candidate_failed:<type> |
| ev.ok false | 해당 후보 skip, reason=ev.reason |
| ev.should_buy false | 해당 후보 skip, reason=should_buy_false_at_export_check |
| validated candidates 0 | 기본은 output 미교체, exit nonzero |
| temp write 실패 | output 미교체, exit nonzero |
| read-back validation 실패 | output 미교체, exit nonzero |
| os.replace 실패 | output 미교체 또는 기존 파일 유지, exit nonzero |

skip summary는 top-level export_meta에 기록한다.

```text
export_meta.skipped = [
  {candidate_id, ticker, stage, reason}
]
export_meta.skipped_summary = {reason: count}
```

---

## 6. 검증 계획

2단계 구현 후 실행할 검증:

### 6.1 생성 파일 검증

```text
python script 자체 validation
python 별도 read-back check
```

확인 항목:

```text
- candidates_count > 0
- 각 후보 selected_rulebook dict 존재
- 각 후보 rulebook dict 존재
- selected_rulebook과 rulebook이 compact가 아님
- source_file 존재
- candidate_source == real_dashboard_buy_candidates_export
- real_candidate_fallback == False
- should_buy_verified == True
```

### 6.2 live 슬롯과 export 후보 비교

비교 기준:

```text
input live slots candidate_id set
exported candidate_id set
skipped candidate_id set with reason
```

보고:

```text
live_slots_count
exported_count
matched_count
skipped_count
skipped_summary
```

### 6.3 SAFETY guard 우회 여부가 아니라 정규 경로 통과 여부 확인

실제 주문 제출 없이 검증:

```text
- 임시 REAL_BUY_CANDIDATES_PATH와 REAL_BUY_INTENT_PATH 사용
- _get_real_broker는 FakeBroker로 monkeypatch
- direct_orders_enabled=True 테스트 환경
- _candidate_for_real()은 정규 파일 candidates[cid]를 읽는 원래 경로 사용
- fallback patch는 사용하지 않거나, 정규 row가 존재하므로 fallback branch에 들어가지 않는지 확인
```

기대 결과:

```text
- _candidate_for_real(candidate_id)가 정규 row 반환
- row.candidate_source != live_slots_state_fallback
- row.real_candidate_fallback != True
- SAFETY fallback guard 미발동
- FakeBroker.place_buy 1회 호출
- returned status == submitted
- execution_mode == direct_alpaca_live_market_order
```

주의:

```text
실제 Alpaca 주문 제출 없음. FakeBroker만 사용.
```

---

## 7. 구현 경계

2단계에서 수정 허용:

```text
scripts/export_real_dashboard_buy_candidates.py 신규 생성
필요하면 분석 readout/검증 결과 파일 생성
```

2단계에서 수정 금지:

```text
engine/live/real_dashboard_api.py
engine/live/real_dashboard_holding_days_patch.py
data/_system/ops/live_candidate_slots.py
engine/live/elite_shadow_trader.py
청산/exit 관련 코드
SAFETY guard
```

운영 데이터 쓰기 허용 범위:

```text
data/_system/real_dashboard_buy_candidates.json
단, 스크립트가 temp 생성 + 검증 통과 후 os.replace로만 교체한다.
```

---

## 8. 현재 단계 결론

```text
DESIGN_CONFIRMED_FOR_REVIEW
IMPLEMENTATION_NOT_STARTED
STOP_AFTER_STEP_1
```

구현은 아직 하지 않았다.

다음 단계에서 승인되면:

```text
1. scripts/export_real_dashboard_buy_candidates.py 신규 생성
2. dry-run 검증
3. 실제 export 실행
4. full rulebook/selected_rulebook/SAFETY 정규 경로 검증
5. 구현 diff + 검증 readout 작성
6. 커밋
```
