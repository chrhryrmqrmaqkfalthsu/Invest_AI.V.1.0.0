# 전체 후보 룰북 완전성 전수 점검 — READ-ONLY

목적:

```text
live 후보로 뜨는 전체 후보 중 full 룰북이 없거나 불완전한 것을 전수 목록화한다.
매수 가능한 후보와 불가능한 후보를 명확히 가른다.
```

엄수 사항:

```text
read-only 점검
코드 수정 없음
정규 후보 파일 수정 없음
export --write 없음
학습/재학습 없음
주문 제출 없음
```

산출물:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/live_candidate_rulebook_completeness_audit_readout.md
data/_system/analysis/oos_reproduce_frozen_20260707/live_candidate_rulebook_completeness_audit.csv
```

백업:

```text
backup/pre_rulebook_completeness_audit_20260709_174011.tar.gz
```

---

## 1. 분류 기준

이번 점검은 “현재 직접매수 가능한 orderable payload” 기준으로 분류했다.

```text
RULEBOOK_COMPLETE:
  data/_system/real_dashboard_buy_candidates.json에 해당 candidate_id row가 있고,
  candidate_source=real_dashboard_buy_candidates_export,
  real_candidate_fallback=False,
  selected_rulebook/rulebook dict가 full key count >= 50,
  필수 필드 존재,
  Rulebook.from_dict 복원 가능,
  ATR > 0.
  즉 현재 _candidate_for_real → _create_real_buy_intent 정규 경로에서 직접 매수 가능한 후보.

RULEBOOK_INCOMPLETE:
  정규 후보 파일 row는 있으나 selected_rulebook/rulebook/ATR/export field 중 일부가 불완전.

RULEBOOK_MISSING:
  live candidate_pool에는 있으나 정규 후보 파일에는 없음.
  이 경우 dashboard-real 매수 클릭 시 fallback compact candidate로 잡힐 수 있고,
  현재 SAFETY guard가 broker.place_buy 전에 차단한다.
```

full rulebook 완전성 필수 조건:

```text
full rulebook key count >= 50
required keys:
  signal_threshold
  rsi_low
  rsi_high
  event_response_war
  vix_sensitivity
  stop_loss_atr
  take_profit_atr
  trailing_atr
  max_holding_days
Rulebook.from_dict(dict) 복원 성공
```

근거 코드:

```text
scripts/export_real_dashboard_buy_candidates.py:27-38
  FULL_RULEBOOK_MIN_KEYS = 50
  FULL_RULEBOOK_REQUIRED_KEYS = (...)

engine/live/elite_shadow_trader.py:205-240
  _load_rulebook_for_candidate(candidate)

engine/live/real_dashboard_holding_days_patch.py:217-246
  _real_buy_preflight_metadata(candidate)
```

---

## 2. 현재 점검 대상 수

요청서에는 “전체 27개”라고 되어 있었지만, 점검 시점 최신 상태 파일 기준은 다음과 같다.

```text
live_slots_state.updated_at: 2026-07-09T17:39:17.316634+00:00
last_refresh: 2026-07-09T17:39:17.316212+00:00
candidate_pool_count: 26
regular_candidate_file_count: 8
```

따라서 이번 전수 점검 판정은 현재 live candidate_pool 26개 기준이다.

---

## 3. 최종 집계

```text
전체 후보: 26
RULEBOOK_COMPLETE: 8
RULEBOOK_INCOMPLETE: 0
RULEBOOK_MISSING: 18
```

fixable 집계:

```text
ALREADY_ORDERABLE: 8
FIXABLE_BY_RELOAD: 18
NOT_FIXABLE_NEEDS_RETRAIN: 0
```

해석:

```text
정규 후보 파일에 full 룰북으로 올라와 즉시 직접매수 가능한 후보는 8개다.
나머지 18개는 원본 final_rulebooks/survivors에는 full rulebook이 완전하게 존재하지만,
현재 real_dashboard_buy_candidates.json에 로드되지 않아 직접매수 경로에서는 fallback compact 후보로 취급된다.
따라서 재학습이 필요한 후보는 없다.
채우기 작업 규모는 18개 후보를 정규 후보 파일에 reload/export 하는 수준이다.
```

---

## 4. 후보별 3분류 표

| rank | ticker | candidate_id | classification | direct_buy_ready_now | fixable | source_rulebook_len | 원인 |
|---:|---|---|---|---|---|---:|---|
| 1 | BMA | stage3:BMA:0c978464f9dd | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 2 | BTBT | stage3:BTBT:363898884d44 | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 3 | BMI | stage3:BMI:07d4ee0f7841 | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 4 | BCS | stage3:BCS:5e7da5a74b01 | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 5 | CMC | stage2:CMC:4f6ee2739add | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 6 | ALGT | stage3:ALGT:aec5dd5b1dc1 | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 7 | ADPT | stage3:ADPT:78c31f1ca209 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 8 | ANET | stage3:ANET:fe220620802b | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 9 | ACMR | stage3:ACMR:44c1e02681c4 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 10 | BKSY | stage3:BKSY:f1bcc8efea02 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 11 | CAPR | stage3:CAPR:a51d615a0ff1 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 12 | AAP | stage3:AAP:71dcdeb19ec0 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 13 | FIX | stage2:FIX:cab7d458767d | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 14 | ADMA | stage3:ADMA:42437a3ee595 | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 15 | BB | stage3:BB:f1bdfe7f8ad9 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 16 | CDE | stage3:CDE:ceb9fe0512dc | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 17 | CIEN | stage3:CIEN:2ed675d30868 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 18 | BWXT | stage3:BWXT:f195725cb792 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 19 | CEF | stage2:CEF:fe84c0ad85d8 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 20 | ARKW | stage3:ARKW:296c057b4ef7 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 21 | ALGT | stage2:ALGT:402f72d48c3c | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 22 | CRS | stage3:CRS:8695c9ce3320 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 23 | CBRL | stage3:CBRL:677767a0b6a9 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 24 | BOIL | stage3:BOIL:9044dc2c67a3 | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |
| 25 | CE | stage3:CE:998b0b638c66 | RULEBOOK_COMPLETE | YES | ALREADY_ORDERABLE | 88 | 정규 후보 파일에 full selected_rulebook/rulebook/atr 존재 |
| 26 | AEIS | stage3:AEIS:6e26f08a7c6d | RULEBOOK_MISSING | NO | FIXABLE_BY_RELOAD | 88 | 정규 후보 파일 미포함/compact fallback만 존재; 원본 full rulebook은 완전 |

---

## 5. 원인 규명

### 5.1 RULEBOOK_COMPLETE 8개

후보:

```text
BMA
BTBT
BMI
BCS
ALGT stage3
ADMA
ALGT stage2
CE
```

원인/상태:

```text
real_dashboard_buy_candidates.json에 full selected_rulebook/rulebook이 들어 있다.
각 rulebook/selected_rulebook key count는 88이고 ATR도 유효하다.
현재 직접매수 preflight를 통과할 수 있는 orderable payload다.
```

### 5.2 RULEBOOK_MISSING 18개

후보:

```text
CMC
ADPT
ANET
ACMR
BKSY
CAPR
AAP
FIX
BB
CDE
CIEN
BWXT
CEF
ARKW
CRS
CBRL
BOIL
AEIS
```

원인:

```text
섹터 누락 아님.
애초에 학습 안 된 종목도 아님.
원본 final_rulebooks.jsonl 또는 stage2 survivors.jsonl에는 full rulebook이 있고, 모두 key count 88로 완전하다.
현재 문제는 정규 매수 후보 파일(real_dashboard_buy_candidates.json)이 source_section=slots 기준 8개만 export되어 있어,
이 18개가 orderable payload에 로드되지 않은 것이다.
즉 compact/live candidate_pool row만 화면에 있고, 직접매수 경로에서는 fallback compact 후보로 들어가 SAFETY guard에 막히는 상태다.
```

fixability:

```text
18개 전부 FIXABLE_BY_RELOAD
NOT_FIXABLE_NEEDS_RETRAIN: 0개
```

필요 작업 규모:

```text
재학습 없음.
원본 full rulebook을 정규 후보 파일에 다시 export/reload하는 작업 18개 후보 규모.
구체적으로는 별도 승인 후 candidate_pool 기준 export --write가 필요하다.
예:
  scripts/export_real_dashboard_buy_candidates.py --source-section candidate_pool --limit 20 --write
또는 전체 26개를 대상으로 하려면 --limit 26 이상.
```

---

## 6. 최종 판정

```text
CURRENT_LIVE_CANDIDATE_POOL_COUNT=26
RULEBOOK_COMPLETE=8
RULEBOOK_INCOMPLETE=0
RULEBOOK_MISSING=18
FIXABLE_BY_RELOAD=18
NOT_FIXABLE_NEEDS_RETRAIN=0
```

결론:

```text
full 룰북 자체가 없거나 학습이 안 된 후보는 발견되지 않았다.
불가능 후보 18개는 원본 룰북 부재가 아니라 정규 후보 파일에 아직 로드되지 않은 것이 원인이다.
따라서 채우기 작업은 재학습이 아니라 정규 후보 export/reload 문제다.
```
