# dashboard-real 후보 20개 표시 수정 readout

요청:

```text
데시보드에 후보 20개 띄워놓도록 수정
```

수정 범위:

```text
/dashboard-real 화면 표시 요청값과 라벨만 수정
후보 산출 로직 수정 없음
SAFETY guard 수정 없음
청산 배관 수정 없음
export 스크립트 수정 없음
정규 후보 파일 수정 없음
주문 제출 없음
```

수정 파일:

```text
api_server_candidate_only.py
engine/live/real_dashboard_candidate20_patch.py
```

백업:

```text
backup/pre_dashboard_real_candidate_20_display_20260709_172628.tar.gz
backup/pre_api_server_candidate_20_patch_20260709_172650.tar.gz
```

---

## 1. 구현 방식

기존 상태:

```text
engine/live/real_dashboard_api.py:_real_candidate_slots_payload(max_slots=8)
/api/real/candidate_slots endpoint는 max_slots query를 지원
/dashboard-real overlay JS는 /api/real/candidate_slots를 query 없이 호출하므로 기본 8개 표시
HTML 라벨도 매수 대기 후보 슬롯 (8)
```

신규 runtime patch:

```text
engine/live/real_dashboard_candidate20_patch.py
```

역할:

```text
1. real_api._real_slot_overlay_js()를 감싸서 fetch URL을 아래로 치환
   /api/real/candidate_slots?max_slots=20

2. /dashboard-real HTML 라벨을 아래로 치환
   매수 대기 후보 슬롯 (8) -> 매수 대기 후보 슬롯 (20)

3. 기타 텍스트의 8개 후보 문구를 20개 후보로 치환
```

설치 위치:

```text
api_server_candidate_only.py
  install_real_dashboard_candidate20_patch()
```

설치 순서:

```text
기존 real-dashboard overlay 관련 patch들이 모두 설치된 뒤 마지막에 candidate20 patch를 설치한다.
이유: 다른 overlay patch가 _real_slot_overlay_js를 다시 감쌀 수 있으므로, 최종 출력 JS에 20개 표시 치환이 적용되도록 마지막 설치가 필요했다.
```

---

## 2. 검증 결과

문법 검사:

```text
venv/bin/python -m py_compile engine/live/real_dashboard_candidate20_patch.py api_server_candidate_only.py
PY_COMPILE_OK
```

API 프로세스 재시작:

```text
8001 api_server_candidate_only 재시작 완료
```

후보 API 검증:

```text
GET /api/real/candidate_slots?max_slots=20
count: 20
```

최종 확인 시점 반환 ticker 20개:

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
```

주의:

```text
검증 중 실계좌 holdings_count가 1에서 2로 바뀌었다.
이번 작업에서 주문 제출은 하지 않았지만, _real_candidate_slots_payload()는 현재 실보유 ticker를 후보에서 제외하므로 후보 순서/구성은 보유 변화에 따라 바뀔 수 있다.
```

/dashboard-real HTML 검증:

```text
GET /dashboard-real
contains_label_20: True
contains_label_8: False
```

/real-slot-overlay.js 검증:

```text
GET /real-slot-overlay.js
contains_max_slots_20: True
contains_label_20: True
contains_label_8: False
```

direct/status 최종 확인:

```text
/api/real/connection:
  ok: True
  broker_mode: alpaca_live
  direct_orders_enabled: True
  holdings_count: 2
  cash: 378.59
  total_value: 628.50
```

주문 관련:

```text
이번 작업에서 place_buy 호출 없음
실주문 제출 없음
```

---

## 3. 중요한 운영 주의점

현재 정규 후보 파일:

```text
data/_system/real_dashboard_buy_candidates.json
candidates: 8
source_section: slots
```

이번 수정은 표시를 20개로 바꾸는 작업이다.

```text
표시 후보 20개 중 9~20번 후보가 정규 후보 파일에 없으면,
매수 버튼 클릭 시 _candidate_for_real() 원본 정규 조회가 실패하고 live_slots_state fallback으로 넘어갈 수 있다.
하지만 fallback candidate는 SAFETY guard에서 broker.place_buy 전에 rejected된다.
```

즉:

```text
화면 표시: 20개 완료
정규 실매수 가능 후보: 현재 정규 파일 기준 8개
9~20번까지 정규 실매수 가능하게 하려면 별도 승인 후 아래가 필요하다.
  scripts/export_real_dashboard_buy_candidates.py --source-section candidate_pool --limit 20 --write
```

이번 요청 범위에서는 정규 후보 파일을 변경하지 않았다.

---

## 4. 최종 판정

```text
DASHBOARD_REAL_CANDIDATE_DISPLAY_20_ENABLED
API_CANDIDATE_SLOTS_MAX20_WORKING
DASHBOARD_LABEL_20_CONFIRMED
OVERLAY_FETCH_MAX20_CONFIRMED
NO_ORDER_SUBMITTED
REGULAR_CANDIDATE_FILE_UNCHANGED_8
```
