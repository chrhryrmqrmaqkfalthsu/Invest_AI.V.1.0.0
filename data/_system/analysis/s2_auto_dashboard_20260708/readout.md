# S2 자동매매 설정 대시보드 구현 완료

- 생성일: 2026-07-08
- 목적: `data/_system/live_auto_config.json`을 웹 대시보드에서 읽고 저장하는 설정 페이지 구현
- 범위: UI/API 계층만 추가. `S2AutoTrader`, `exit_policy`, 자동매매 실행 로직은 수정하지 않음.
- 실주문 발생: **0건**

## 구현 파일

```text
engine/live/s2_auto_dashboard_api.py      신규
api_server_aftermarket.py                 라우트 설치 추가
data/_system/live_auto_config.json        기존 설정 유지, 정규화 저장
data/_system/live_auto_config_change_events.jsonl  신규 변경 이력 로그
```

설정 페이지:

```text
/dashboard-real/auto-settings
```

API:

```text
GET  /api/real/live_auto_config
POST /api/real/live_auto_config
GET  /api/real/live_auto_config_events
```

## 노출 설정 항목

| 항목 | UI | 기본값/상태 | 설명 |
|---|---|---|---|
| `master_enabled` | 체크박스 + 최상단 kill switch | false | 전체 자동매매 중단/허용 |
| `auto_buy_enabled` | 체크박스 | false | 자동매수 허용 |
| `auto_exit_enabled` | 체크박스 | false | 자동청산 허용 |
| `real_orders_enabled` | 체크박스 + 이중확인 | false | 실제 주문 허용 |
| `dry_run` | 체크박스 | true | true면 실주문 제출 불가 |
| `exit.s2_take_profit_enabled` | 체크박스 | false | false면 S2 no-TP |
| `portfolio_K` | 숫자 입력 | 20 | 최대 보유 종목 수 |
| `entry_timing` | select | next_open | next_open만 활성, 그 외 비활성 표시 |
| `total_capital_mode` | 읽기/비활성 입력 | fixed_from_account_at_start | 세션 시작 available cash 고정 |
| `capital_source` | 읽기/비활성 입력 | available_cash | 마진 제외 현금 기준 |
| risk limits | 숫자 입력 | 기존 config값 | 주문 한도, 일일 주문 수, 노출 한도 등 |

## 읽기 전용 상태 표시

설정 페이지 상단에 다음이 표시된다.

```text
현재 계좌 available cash
cash / K 기준 포지션당 금액
브로커 현재 보유 수
후보 슬롯 filled/slot_count + waitlist 수
master_enabled 상태
```

검증 시 조회값:

```text
available cash = 650.37 USD
portfolio_K = 20
position_notional_from_cash = 32.5185 USD
broker holdings_count = 0
candidate slots = 8/8
waitlist = 20
```

즉 사용자가 K를 바꾸면 화면에서 즉시 다음이 재계산된다.

```text
포지션당 금액 = 현재 available cash / K
```

## real_orders_enabled 이중확인 흐름

`real_orders_enabled`를 false에서 true로 켜려면 API가 다음을 요구한다.

```text
real_orders_ack = true
real_orders_phrase = REAL_ORDERS_ENABLED
```

추가로 다음도 모두 만족해야 한다.

```text
dry_run = false
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS env enabled
entry_timing = next_open
```

검증 결과, 이중확인 없이 켜려는 요청은 차단됐다.

```text
POST /api/real/live_auto_config
real_orders_enabled=true
real_orders_ack=false

결과:
HTTP 400
real_orders_enabled requires checkbox and phrase REAL_ORDERS_ENABLED
```

최종 설정은 여전히 OFF 상태다.

```text
master_enabled=false
auto_buy_enabled=false
auto_exit_enabled=false
real_orders_enabled=false
dry_run=true
entry_timing=next_open
portfolio_K=20
```

## 변경 이력 로그

설정 저장 시 다음 파일에 JSONL 이벤트를 남긴다.

```text
data/_system/live_auto_config_change_events.jsonl
```

이벤트 필드:

```text
time
actor
client
changes[]
master_enabled_changed
real_orders_enabled_changed
```

특히 `master_enabled`, `real_orders_enabled` 변경 여부는 별도 boolean으로 기록한다.

검증 이벤트:

```text
actor = validation-same-config-save
changes = []
master_enabled_changed = false
real_orders_enabled_changed = false
```

## 안전장치 UI

구현된 안전장치:

```text
1. master_enabled 최상단 배치
2. “전체 즉시 정지(master=false 저장)” 버튼 제공
3. real_orders_enabled 경고 박스
4. real_orders_enabled 이중확인 체크박스 + 문구 재입력
5. dry_run=true 상태에서는 real_orders_enabled=true 저장 불가
6. direct order env false이면 real_orders_enabled=true 저장 불가
7. K 변경 시 position_notional 즉시 재계산
8. entry_timing은 next_open만 활성
```

## 실거래 켜는 순서

현재 구현은 설정 UI만 제공하며 주문을 실행하지 않는다. 실제 자동매매를 켜려면 다음 순서가 필요하다.

```text
1. /dashboard-real/auto-settings 접속
2. K, risk limit, dry_run 상태 확인
3. master_enabled=true 저장
4. auto_buy_enabled=true 저장
5. auto_exit_enabled=true 저장
6. 충분한 dry-run 관찰
7. dry_run=false 저장
8. real_orders_enabled 체크
9. “실제 돈이 나갑니다” 확인 체크
10. REAL_ORDERS_ENABLED 문구 입력
11. 환경변수 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1 상태 확인
12. 저장
```

현재는 이 절차를 완료하지 않았고, 모든 스위치는 초기 OFF로 유지했다.

## 검증 결과

컴파일:

```text
python -m py_compile engine/live/s2_auto_dashboard_api.py api_server_aftermarket.py
결과: OK
```

페이지:

```text
GET /dashboard-real/auto-settings
HTTP 200
페이지 안에 “S2 자동매매 설정”, “실제 돈이 나갑니다” 경고 존재
```

API:

```text
GET /api/real/live_auto_config
ok=true
cash=650.37
position_notional_from_cash=32.5185
direct_order_env_enabled=false
slots=8/8, waitlist=20
```

이중확인 차단:

```text
real_orders_enabled=true, ack=false 요청
HTTP 400으로 차단
```

최종 스위치:

```text
master_enabled=false
auto_buy_enabled=false
auto_exit_enabled=false
real_orders_enabled=false
dry_run=true
```

## 결론

요구사항대로 자동매매 설정 페이지와 config read/write API를 구현했다. 자동매매 실행 로직은 수정하지 않았고, 실주문은 발생하지 않았다. 실주문 스위치는 이중확인 UI와 서버 검증 없이 켜지지 않는다.
