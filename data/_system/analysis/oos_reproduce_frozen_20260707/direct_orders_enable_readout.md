# direct_orders_enabled 활성화 + 사전 안전 확인 readout

작업 범위:

```text
1단계: 직접 주문 활성화 전 read-only 안전 확인
2단계: SAFE_TO_ENABLE일 때만 direct_orders_enabled=True 전환
```

금지 준수:

```text
실주문 제출 없음
place_buy 호출 없음
SAFETY guard 수정 없음
청산 배관 수정 없음
정규 후보 파일 수정 없음
export 스크립트 수정 없음
live_candidate_slots 계산 로직 수정 없음
.env 수정/조회 없음
```

수정 파일:

```text
scripts/dashboard_guard.sh
```

수정 목적:

```text
8001 dashboard API 프로세스 시작 시 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1 환경변수를 주입한다.
.env를 건드리지 않고, 기존 cron/guard 재시작 경로에서 동일하게 direct_orders_enabled=True가 유지되도록 한다.
```

백업:

```text
backup/pre_enable_direct_orders_20260709_171604.tar.gz
```

---

## 1. 켜기 전 안전 확인

### 1.1 direct_orders_enabled 현재값 및 제어 위치

전환 전 API 값:

```text
/api/real/connection.direct_orders_enabled = False
/api/real/account.direct_orders_enabled = False
/api/real/central_candidates.direct_orders_enabled = False
```

제어 코드:

```text
engine/live/real_dashboard_api.py:32
  DIRECT_ORDER_ENV = "KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS"

engine/live/real_dashboard_api.py:107-108
  def _direct_orders_enabled() -> bool:
      return str(os.environ.get(DIRECT_ORDER_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}
```

제어 방식 판정:

```text
환경변수 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS 로 제어된다.
현재 확인 범위에서 direct_orders_enabled를 바꾸는 API endpoint는 발견되지 않았다.
.env는 민감 파일이므로 수정하지 않았다.
```

운영 프로세스 상태:

```text
전환 전 8001 process:
  /home/g3000kkw/kingmaker/venv/bin/python3 venv/bin/uvicorn api_server_candidate_only:app --host 0.0.0.0 --port 8001
  started: 2026-07-09 13:24:57
```

중요 확인:

```text
청산 배관 커밋 cbc3e77은 2026-07-09 16:55:24에 반영됐다.
전환 전 실행 중인 8001 process는 그 이전에 시작되어 있었으므로, 활성화 시 현재 코드를 로드하도록 재시작이 필요했다.
```

### 1.2 정규 후보 파일 상태

파일:

```text
data/_system/real_dashboard_buy_candidates.json
```

확인 결과:

```text
updated_at: 2026-07-09T16:58:29.612877+00:00
candidates: 8
source_section: slots
```

후보별 검증:

| candidate_id | ticker | candidate_source | fallback | rulebook_len | selected_len | atr | ok |
|---|---|---|---:|---:|---:|---:|---:|
| stage2:ALGT:402f72d48c3c | ALGT | real_dashboard_buy_candidates_export | False | 88 | 88 | 6.386689045625623 | True |
| stage3:ADMA:42437a3ee595 | ADMA | real_dashboard_buy_candidates_export | False | 88 | 88 | 0.36327608448120496 | True |
| stage3:ALGT:aec5dd5b1dc1 | ALGT | real_dashboard_buy_candidates_export | False | 88 | 88 | 6.386689045625623 | True |
| stage3:BCS:5e7da5a74b01 | BCS | real_dashboard_buy_candidates_export | False | 88 | 88 | 0.6898167819891539 | True |
| stage3:BMA:0c978464f9dd | BMA | real_dashboard_buy_candidates_export | False | 88 | 88 | 4.56109647340232 | True |
| stage3:BMI:07d4ee0f7841 | BMI | real_dashboard_buy_candidates_export | False | 88 | 88 | 5.3530176350470295 | True |
| stage3:BTBT:363898884d44 | BTBT | real_dashboard_buy_candidates_export | False | 88 | 88 | 0.1801082004378081 | True |
| stage3:CE:998b0b638c66 | CE | real_dashboard_buy_candidates_export | False | 88 | 88 | 2.2478600659746704 | True |

판정:

```text
REGULAR_CANDIDATES_OK
bad_count=0
```

### 1.3 SAFETY guard 및 청산 배관 코드 상태

SAFETY guard 근거:

```text
engine/live/real_dashboard_api.py:734-764
  real_candidate_fallback=True 또는 candidate_source == live_slots_state_fallback 이면
  broker.place_buy 전 rejected 반환
```

청산 배관 근거:

```text
engine/live/real_dashboard_holding_days_patch.py:217-246
  _real_buy_preflight_metadata(candidate)
  selected_rulebook / ATR 검증

engine/live/real_dashboard_holding_days_patch.py:311-381
  _wire_real_buy_reconciliation(...)
  FILLED -> BuyReconciliationService.reconcile(...)
  PENDING/PARTIAL/SUBMITTED -> PendingOrderManager.track_order(...)

engine/live/real_dashboard_holding_days_patch.py:384-490
  _patch_direct_buy_reconciliation_wiring()
```

patch 설치 경로:

```text
api_server_candidate_only.py:16
  from engine.live.real_dashboard_holding_days_patch import install_real_dashboard_holding_days_patch

api_server_candidate_only.py:23
  install_real_dashboard_holding_days_patch()
```

별도 import 검증:

```text
before_patch_create_name: _create_real_buy_intent
after_patch_create_name: patched_create_real_buy_intent
direct_env_name: KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS
patch_has_reconciliation_helper: True
```

판정:

```text
SAFETY_GUARD_PRESENT
RECONCILIATION_WIRING_PRESENT_IN_CURRENT_CODE
API_PROCESS_RESTART_REQUIRED_TO_LOAD_CURRENT_CODE
```

### 1.4 실계좌 현재 상태

전환 전 GET 확인:

```text
/api/real/connection:
  ok: True
  broker_mode: alpaca_live
  account_source: alpaca_live
  direct_orders_enabled: False
  holdings_count: 0
  cash: 629.14
  total_value: 629.14

/api/real/account:
  direct_orders_enabled: False
  holdings_count: 0
  orders_today: 0
  cash: 629.14
  total_value: 629.14
  invested: 0.0

/api/real/positions:
  count: 0

/api/real/open_orders:
  count: 0
```

판정:

```text
NO_EXISTING_LIVE_HOLDINGS
NO_OPEN_ORDERS
NO_UNEXPECTED_EXPOSURE
```

### 1.5 1단계 최종 판정

```text
SAFE_TO_ENABLE
```

조건부 설명:

```text
안전 조건은 모두 충족했다.
단, 전환 전 실행 중인 8001 process가 청산 배관 코드 반영 이전에 시작되어 있었으므로,
2단계에서는 환경변수만 바꾸는 것이 아니라 8001 process를 재시작해 현재 코드와 환경변수를 함께 로드해야 한다.
```

---

## 2. 활성화 작업

### 2.1 제어 위치 반영

수정 파일:

```text
scripts/dashboard_guard.sh
```

변경 내용:

```text
DIRECT_ORDER_ENV="KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS"
DIRECT_ORDER_VALUE="1"
```

uvicorn 시작 명령:

```text
env "PYTHONPATH=$BASE" "${DIRECT_ORDER_ENV}=${DIRECT_ORDER_VALUE}" nohup "$UVICORN" api_server_candidate_only:app --host 0.0.0.0 --port 8001 ...
```

의미:

```text
cron/guard가 8001 dashboard API를 재시작할 때마다 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1이 프로세스 환경에 들어간다.
```

초기 시도 이슈:

```text
처음에는 동적 환경변수 지정 문법 오류로 API 재시작이 실패했다.
즉시 env "KEY=VALUE" 형식으로 수정했고, 이후 guard 재시작 성공.
```

### 2.2 전환 후 반영 확인

새 프로세스:

```text
/home/g3000kkw/kingmaker/venv/bin/python3 /home/g3000kkw/kingmaker/venv/bin/uvicorn api_server_candidate_only:app --host 0.0.0.0 --port 8001
```

전환 후 API 값:

```text
/api/real/connection:
  ok: True
  broker_mode: alpaca_live
  direct_orders_enabled: True
  holdings_count: 0
  cash: 629.14
  total_value: 629.14

/api/real/positions:
  count: 0

/api/real/open_orders:
  count: 0

/api/real/central_candidates:
  direct_orders_enabled: True
  manual_buy_enabled: True
  candidates count: 8
  updated_at: 2026-07-09T16:58:29.612877+00:00
```

판정:

```text
DIRECT_ORDERS_ENABLED_TRUE_CONFIRMED
NO_LIVE_ORDER_SUBMITTED
NO_HOLDINGS_AFTER_ENABLE
NO_OPEN_ORDERS_AFTER_ENABLE
```

---

## 3. 최종 상태

```text
direct_orders_enabled: True
broker_mode: alpaca_live
holdings_count: 0
open_orders_count: 0
regular_candidates_count: 8
candidate_source: real_dashboard_buy_candidates_export only
fallback candidates in regular file: 0
```

운영상 의미:

```text
이제 /dashboard-real에서 정규 후보 매수 버튼을 누르면 실계좌 Alpaca LIVE market buy 경로로 진입할 수 있다.
정규 후보는 selected_rulebook/ATR preflight를 통과해야 하며, fallback compact 후보는 기존 SAFETY guard로 broker.place_buy 전에 rejected된다.
FILLED 주문은 청산 배관을 통해 PositionManager.register_entry까지 연결되고, PENDING/PARTIAL은 PendingOrderManager metadata에 selected_rulebook/ATR/context가 보존된다.
```
