# candidate_pool 전체 export --write readout

요청:

```text
scripts/export_real_dashboard_buy_candidates.py --source-section candidate_pool --limit 26 --write
26개 전부에 대해 final_rulebooks.jsonl에서 full 룰북 조회 → attach → should_buy 재검증 → 통과분만 정규 파일에.
룰북 조회 실패하거나 should_buy False인 건 제외.
temp 검증 → 원자 교체.
이미 보유 중인 ALGT/BCS duplicate 처리 확인.
실주문 금지. export만.
```

실행 범위:

```text
정규 후보 파일 export/write만 수행
실계좌 주문 제출 없음
place_buy 호출 없음
SAFETY guard/청산 배관/후보 산출 로직 수정 없음
```

백업:

```text
backup/pre_candidate_pool_export_write_20260709_175553.tar.gz
```

---

## 1. write 전 상태

정규 후보 파일:

```text
path: data/_system/real_dashboard_buy_candidates.json
regular_candidates_before: 8
updated_at_before: 2026-07-09T16:58:29.612877+00:00
source_section_before: slots
```

write 전 candidate_id:

```text
stage2:ALGT:402f72d48c3c
stage3:ADMA:42437a3ee595
stage3:ALGT:aec5dd5b1dc1
stage3:BCS:5e7da5a74b01
stage3:BMA:0c978464f9dd
stage3:BMI:07d4ee0f7841
stage3:BTBT:363898884d44
stage3:CE:998b0b638c66
```

live state:

```text
live_candidate_pool_count: 26
```

실계좌 상태:

```text
broker_mode: alpaca_live
direct_orders_enabled: True
holdings_count: 2
holdings: ALGT, BCS
open_orders: 0
cash: 378.59
total_value: 627.92
```

---

## 2. export --write 실행

명령:

```text
cd ~/kingmaker && PYTHONPATH=. venv/bin/python scripts/export_real_dashboard_buy_candidates.py \
  --source-section candidate_pool \
  --limit 26 \
  --write \
  --summary-path data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_candidate_pool_write_summary.json
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

temp file:

```text
/home/g3000kkw/kingmaker/data/_system/.real_dashboard_buy_candidates.json.tmp.237036.20260709T175610Z
```

summary file:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/real_dashboard_buy_candidates_candidate_pool_write_summary.json
```

---

## 3. write 후 상태

정규 후보 파일:

```text
path: data/_system/real_dashboard_buy_candidates.json
regular_candidates_after: 26
updated_at_after: 2026-07-09T17:56:00.865392+00:00
source_section_after: candidate_pool
limit_after: 26
exported_count_after: 26
skipped_count_after: 0
file_size: 295079 bytes
```

전후 비교:

```text
before: 8 candidates
candidate_pool source: 26 candidates
after: 26 candidates
net_added: 18 candidates
excluded: 0 candidates
```

신규 추가된 18개:

```text
stage2:CEF:fe84c0ad85d8
stage2:CMC:4f6ee2739add
stage2:FIX:cab7d458767d
stage3:AAP:71dcdeb19ec0
stage3:ACMR:44c1e02681c4
stage3:ADPT:78c31f1ca209
stage3:AEIS:6e26f08a7c6d
stage3:ANET:fe220620802b
stage3:ARKW:296c057b4ef7
stage3:BB:f1bdfe7f8ad9
stage3:BKSY:f1bcc8efea02
stage3:BOIL:9044dc2c67a3
stage3:BWXT:f195725cb792
stage3:CAPR:a51d615a0ff1
stage3:CBRL:677767a0b6a9
stage3:CDE:ceb9fe0512dc
stage3:CIEN:2ed675d30868
stage3:CRS:8695c9ce3320
```

---

## 4. full rulebook / should_buy 검증

post-write 검증:

```text
bad_count: 0
candidate_source == real_dashboard_buy_candidates_export: 26/26
real_candidate_fallback == False: 26/26
rulebook dict len >= 50: 26/26
selected_rulebook dict len >= 50: 26/26
rulebook == selected_rulebook: 26/26
atr > 0: 26/26
should_buy_verified == True: 26/26
full_rulebook_verified == True: 26/26
source_file exists: 26/26
```

제외 후보:

```text
skipped_count: 0
skipped_summary: {}
skipped: []
```

판정:

```text
ALL_26_CANDIDATES_EXPORTED
ALL_26_FULL_RULEBOOK_ATTACHED
ALL_26_SHOULD_BUY_REVERIFIED
NO_CANDIDATE_EXCLUDED
```

---

## 5. write 후 정규 후보 26개

```text
stage2:ALGT:402f72d48c3c
stage2:CEF:fe84c0ad85d8
stage2:CMC:4f6ee2739add
stage2:FIX:cab7d458767d
stage3:AAP:71dcdeb19ec0
stage3:ACMR:44c1e02681c4
stage3:ADMA:42437a3ee595
stage3:ADPT:78c31f1ca209
stage3:AEIS:6e26f08a7c6d
stage3:ALGT:aec5dd5b1dc1
stage3:ANET:fe220620802b
stage3:ARKW:296c057b4ef7
stage3:BB:f1bdfe7f8ad9
stage3:BCS:5e7da5a74b01
stage3:BKSY:f1bcc8efea02
stage3:BMA:0c978464f9dd
stage3:BMI:07d4ee0f7841
stage3:BOIL:9044dc2c67a3
stage3:BTBT:363898884d44
stage3:BWXT:f195725cb792
stage3:CAPR:a51d615a0ff1
stage3:CBRL:677767a0b6a9
stage3:CDE:ceb9fe0512dc
stage3:CE:998b0b638c66
stage3:CIEN:2ed675d30868
stage3:CRS:8695c9ce3320
```

---

## 6. 보유 중복 ALGT/BCS 처리 확인

현재 실보유:

```text
positions: ALGT, BCS
open_orders: 0
```

정규 후보 파일 내 held ticker 포함 여부:

```text
ALGT:
  stage2:ALGT:402f72d48c3c
  stage3:ALGT:aec5dd5b1dc1

BCS:
  stage3:BCS:5e7da5a74b01
```

정규 후보 파일 자체에는 ALGT/BCS가 포함되어 있다.

중복 ticker 확인:

```text
duplicate_tickers_in_regular:
  ALGT: [stage2:ALGT:402f72d48c3c, stage3:ALGT:aec5dd5b1dc1]
```

표시 API 확인:

```text
GET /api/real/candidate_slots?max_slots=26
count: 26
contains_ALGT: False
contains_BCS: False
```

반환 ticker:

```text
BMA
BTBT
BMI
CMC
ADPT
ANET
ACMR
BKSY
CAPR
AAP
FIX
ADMA
BB
CDE
CIEN
BWXT
CEF
ARKW
CRS
CBRL
BOIL
CE
AEIS
None
None
None
```

해석:

```text
정규 후보 파일은 원본 candidate_pool 26개를 모두 보관한다.
하지만 /api/real/candidate_slots는 현재 실보유 ticker를 표시 후보에서 제외한다.
현재 ALGT 2개 candidate와 BCS 1개 candidate가 실보유 ticker라 표시 후보에서 빠지고, max_slots=26을 맞추기 위해 blank row 3개가 반환된다.
```

central_candidates 확인:

```text
GET /api/real/central_candidates
count: 26
contains_ALGT: True
contains_BCS: True
```

운영상 의미:

```text
정규 파일에는 ALGT/BCS가 full 룰북 후보로 포함되어 있으나,
현재 dashboard-real 후보 슬롯 API는 보유 ticker를 숨긴다.
따라서 일반 후보 슬롯 화면에서는 ALGT/BCS 중복 매수 버튼이 보이지 않는 상태다.
```

주의:

```text
정규 후보 파일 자체에서 held ticker를 삭제하거나 제외하지는 않았다.
이번 지시의 범위가 candidate_pool 전체 export였기 때문이다.
```

---

## 7. 주문 안전 확인

```text
실주문 제출 없음
place_buy 호출 없음
open_orders after write: 0
positions after write: ALGT, BCS 기존 2개 유지
```

---

## 8. 최종 판정

```text
CANDIDATE_POOL_EXPORT_WRITE_SUCCESS
REGULAR_CANDIDATES_8_TO_26
EXPORTED_COUNT_26
SKIPPED_COUNT_0
POST_WRITE_VALIDATION_OK
ALL_26_FULL_RULEBOOK_AND_ATR_VALID
ALL_26_SHOULD_BUY_REVERIFIED_TRUE
NO_REAL_ORDER_SUBMITTED
HELD_TICKERS_PRESENT_IN_CANONICAL_BUT_HIDDEN_FROM_CANDIDATE_SLOTS
DUPLICATE_TICKER_ALGT_PRESENT_AS_STAGE2_AND_STAGE3
```
