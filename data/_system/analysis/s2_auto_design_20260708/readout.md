# S2 자동매매 구현 — 1단계 설계안

- 생성일: 2026-07-08
- 단계: 구현 전 승인용 설계
- 제약: 코드·환경변수·상태파일 수정 없음. read-only 조사 + 설계 문서만 작성.
- 목적: 라이브 후보 슬롯 기반 자동 매수 + 자동 S2 청산 구현 전 연결 경로와 안전장치 확정.

## 0. 설계 결론

추천 구조는 기존 후보 슬롯 도구를 건드리지 않고, 별도 S2 자동매매 컨트롤러를 추가하는 것이다.

```text
live_candidate_slots.py          후보 생성/정렬 담당
→ S2AutoTrader/S2AutoController  신규 자동매수·청산 오케스트레이터
→ Alpaca broker                 실주문 제출
→ BuyReconciliationService       BUY 체결 후 PositionManager 등록
→ PositionManager + ExitPolicy   자동 S2 청산
```

핵심 원칙:

```text
1. /api/real/manual_buy_intent를 자동매매 실행 경로로 쓰지 않는다.
   - 현재 이 경로는 PositionManager 등록이 끊겨 있다.
   - 주문 cap/서버측 이중확인도 부족하다.

2. 자동매수 기본은 next_open이다.
   - 백테스트는 signal_date 다음 거래일 시가 진입이다.
   - intraday 즉시매수는 별도 검증 전까지 OFF.

3. 자동청산은 PositionManager + shared ExitPolicy 단일 경로로 둔다.
   - legacy fallback은 실계좌에서 fail-closed.
   - take_profit은 설정값 하나로 끈다.

4. K 기본값은 20으로 둔다.
   - 검증 정본은 S2 K=20 final_score priority가 안정적이었다.
   - 현재 화면 8칸은 display_slots일 뿐, portfolio_K와 분리한다.
```

## 1. 매수 → 포지션 등록 경로 설계

현재 문제:

```text
/api/real/live_slot_buy
→ 실제 주문 없음, held_exclusions 기록만 함

/api/real/manual_buy_intent
→ env ON이면 Alpaca 주문 가능
→ PositionManager.register_entry 호출 없음
→ S2 자동청산 대상이 되지 않음
```

2단계 신규 후보 파일:

```text
engine/live/s2_auto_config.py
engine/live/s2_auto_trader.py
scripts/run_s2_auto_live.py
```

신규 상태/로그 후보:

```text
data/_system/live_auto_config.json
data/_system/live_auto_state.json
data/_system/live_auto_events.jsonl
data/_system/live_auto_order_intents.jsonl
```

자동매수 실행 흐름:

```text
S2AutoTrader.tick()
1. live_auto_config.json 로드
2. kill switch 확인
3. master/auto_buy/real_orders/operator arm 확인
4. broker 연결 확인
5. broker holdings ↔ PositionManager ↔ held_exclusions reconcile
6. live_candidate_slots candidate_pool 읽기
7. candidate_id 기준 full candidate/rulebook 재구성
   - build_elite_shadow_report(stage2_limit=60, stage3_limit=80)
8. 진입 직전 evaluate_candidate 재확인
   - KEEP gate
   - should_buy=True
   - EQ 미사용
   - final_score priority 유지
9. entry_timing 정책에 따라 실행
   - 기본 next_open queue
   - intraday_immediate는 UNVERIFIED/OFF
10. 주문 수량 계산
11. SafetyLayer.check_order 통과
12. PendingOrderManager intent 생성
13. broker.place_buy(... MARKET)
14. 체결 또는 pending 추적
15. BuyReconciliationService.reconcile()
16. PositionManager.register_entry(rulebook snapshot, ATR, market context)
17. live_auto_state/events 기록
```

`PositionManager.register_entry()`에 필요한 값:

```text
ticker, filled_price, filled_shares, rulebook, atr_value, entry_market_context
```

따라서 자동매수 intent에는 최소 다음을 snapshot으로 저장해야 한다.

```text
candidate_id
ticker
selected_rulebook 전체
rulebook_hash_short
preflight_atr
entry_market_context
signal_score / threshold / final_score
entry_timing / execution_session
```

## 2. next-open 기본 설계

기존 코드에 이미 다음 패턴이 있다.

```text
engine/live/scheduled_open_buy_queue.py
목적: D-1 close selection → D open BUY queue
```

자동매수 기본값:

```json
{
  "entry_timing": "next_open",
  "allow_intraday_immediate": false
}
```

설계 흐름:

```text
D 후보 확정
→ 다음 정규장 execution_session queue item 생성
→ 다음 open window에서 주문 실행
```

이렇게 해야 백테스트 T+1 시가 진입 가정과 가장 가깝다.

## 3. 실주문 이중확인 게이트 설계

현재 위험 경로:

```text
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1
+ POST /api/real/manual_buy_intent
→ Alpaca live MARKET BUY
```

자동매매에서는 4중 게이트를 사용한다.

```text
1. live_auto_config.master_enabled == true
2. live_auto_config.real_orders_enabled == true
3. 환경변수 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS == 1
4. operator arm token 유효
   - operator_armed == true
   - armed_until_utc > now
   - confirmation_phrase 일치
```

추가 필수 게이트:

```text
kill switch 없음
market open 확인
SafetyLayer.check_order 통과
pending lock 없음
cash/cap 통과
```

## 4. S2 청산 경로 + take_profit 토글 설계

현재 take_profit 잔존 위치:

| 위치 | take_profit | 비고 |
|---|---|---|
| `position_manager.py:_legacy_exit_reason()` | fixed/hybrid에 있음 | fallback 시 위험 |
| `core/exit_policy.py:evaluate_exit()` | fixed/hybrid에 있음 | shared ExitPolicy 경로 |
| `elite_shadow_trader.py` | fixed/hybrid에 있음 | shadow ledger |
| `elite_strategy_sim.py` | fixed/hybrid에 있음 | sim ledger |
| `exit_policy_adapter.py:legacy_live_decision()` | fixed/hybrid에 있음 | 진단/비교 |

검증된 S2 기준:

```text
s2_exit_reason take_profit count = 0
analysis_exit_reason take_profit count = 0
```

실제 자동청산 단일 경로는 다음으로 고정한다.

```text
PositionManager.check_exits()
→ _check_one()
→ _evaluate_policy()
→ engine/core/exit_policy.py:evaluate_exit()
→ broker.place_sell(... MARKET)
```

권장 구현:

```text
engine/core/exit_policy.py
- ExitExecutionConfig에 take_profit_enabled: Optional[bool] 추가
- 기본은 기존 호환을 위해 True 또는 None
- S2AutoTrader/exit_policy_adapter에서 config.s2_take_profit_enabled 전달
```

핵심 로직 설계:

```text
take_profit_enabled = config.exit.s2_take_profit_enabled
target_hit = take_profit_enabled and high >= position.target_price
```

S2 기본값:

```json
{
  "s2_take_profit_enabled": false
}
```

주의:

```text
현재 live 핵심 경로의 익절은 고정 3%가 아니라 ATR 기반 target_price다.
따라서 토글을 켜면 기존 live rulebook의 take_profit_atr target을 사용한다.
정말 고정 3% 익절이 필요하면 fixed_take_profit_pct를 별도 추가해야 한다.
```

legacy 처리:

```text
자동매매 실계좌에서는 legacy fallback 금지.
ExitPolicy 실패 시 조용히 legacy로 매도하지 않고 block/alert.
방어적으로 legacy에도 같은 no-TP toggle을 적용 가능하지만, 권장 기본은 legacy 미사용이다.
```

shadow/sim 처리:

```text
elite_shadow_trader / elite_strategy_sim은 실제 자동청산 경로로 쓰지 않는다.
혼선을 줄이려면 이후 별도 작업으로 no-TP 옵션 또는 legacy 표시를 추가한다.
```

## 5. S2 트리거 대조표

| 트리거 | S2 검증 | 설계상 live 경로 | 일치 여부 |
|---|---|---|---|
| stop_loss | 있음 | ExitPolicy stop_hit | MATCH |
| trailing | 있음 | ExitPolicy trailing_hit | MATCH |
| sell_omen | 있음 | ExitPolicy sell_omen_hit | MATCH |
| timeout/time_out | 있음 | ExitPolicy timeout_hit | MATCH |
| breakeven_stop | 있음 | ExitPolicy breakeven_hit | MATCH |
| take_profit | 없음 | config로 OFF | MATCH when OFF |

기본값은 `s2_take_profit_enabled=false`다.

## 6. K 설정 설계

화면 슬롯 수와 포트폴리오 K를 분리한다.

```json
{
  "display_slots": 8,
  "portfolio_K": 20
}
```

의미:

```text
- 화면은 상위 8개 표시.
- 자동매매는 candidate_pool/waitlist 포함 정렬 pool에서 K=20까지 채울 수 있음.
- 사용자가 K=8을 원하면 바꿀 수 있지만 K8_UNVERIFIED 경고 표시.
```

권장 기본값:

```text
portfolio_K = 20
```

## 7. 자본 분배 설계

기본 공식:

```text
base_notional = total_capital_usd / portfolio_K
shares = base_notional / entry_price
```

이미 보유 중인 슬롯이 있을 때:

```text
active_positions = broker holdings 또는 PositionManager positions
pending_buys = pending order count
remaining_capacity = portfolio_K - active_positions - pending_buys
available_budget = min(account_cash - cash_buffer, total_capital_usd - current_exposure)
order_notional = min(base_notional, available_budget / max(remaining_capacity, 1))
```

정책:

```text
- 기존 포지션 전체 재균등화 없음.
- 신규 진입만 목표 슬롯 금액으로 진입.
- 잔여현금은 다음 신규 슬롯에 사용.
- Alpaca fractional share 기준 6자리 반올림/절삭.
- SafetyLayer min_notional/min_fractional/max cap 통과 필수.
```

## 8. 안전장치 설계

필수 안전장치:

```text
실주문 이중확인
주문 한도 cap
일일 최대 주문 수
브로커 실제 보유 ↔ held_exclusions ↔ PositionManager 동기화
VM 재부팅 시 영구 상태 복구
kill switch
pending order lock
same ticker one-position rule
```

권장 cap:

```json
{
  "max_order_notional_usd": 100.0,
  "max_daily_orders": 3,
  "max_daily_buy_notional_usd": 300.0,
  "max_total_exposure_usd": 650.0,
  "min_cash_buffer_usd": 10.0,
  "one_position_per_ticker": true
}
```

reconcile 정책:

```text
- broker에 보유 ticker가 있으면 같은 ticker 신규 BUY 금지.
- candidate_id가 held_exclusions에 있으면 신규 BUY 금지.
- broker 보유는 있는데 PositionManager에 없으면 CRITICAL_ORPHAN_POSITION.
- PositionManager에는 있는데 broker에 없으면 reconcile/unregister 후보.
```

영구 경로:

```text
data/_system/live_auto_state.json
data/_system/live_auto_events.jsonl
data/_system/live_auto_order_intents.jsonl
data/_system/positions.json
data/_system/pending_orders.json
```

kill switch 설계:

```json
{
  "path": "data/_system/live_auto_kill_switch",
  "scope": "block_new_buys_allow_risk_reducing_exits"
}
```

사용자가 원하면 `block_all_orders`로 바꿀 수 있다.

## 9. 설정 파일 설계

설정 파일 후보:

```text
data/_system/live_auto_config.json
```

초기 기본값은 모두 OFF다. 상세 스키마는 `config_schema.json`에 별도 저장했다.

핵심 설정:

```text
master_enabled
auto_buy_enabled
auto_exit_enabled
real_orders_enabled
dry_run
entry_timing
portfolio_K
total_capital_usd
s2_take_profit_enabled
max_order_notional_usd
max_daily_orders
operator_armed / armed_until_utc / confirmation_phrase
kill_switch.path / scope
```

## 10. 2단계 구현 후보 파일

| 파일 | 작업 | 이유 |
|---|---|---|
| `engine/live/s2_auto_config.py` | 신규 | config load/validation/approval gate |
| `engine/live/s2_auto_trader.py` | 신규 | 자동매수/청산 오케스트레이터 |
| `scripts/run_s2_auto_live.py` | 신규 | daemon/one-shot 실행 진입점 |
| `engine/core/exit_policy.py` | 수정 | take_profit_enabled 토글 |
| `engine/live/exit_policy_adapter.py` | 수정 | config의 S2 no-TP를 ExitExecutionConfig로 전달 |
| `engine/live/position_manager.py` | 최소 수정 | legacy fallback fail-closed/no-TP 방어 |
| `engine/live/real_dashboard_api.py` | 선택 수정 | 상태 표시/arm/kill switch 표시, executor 아님 |
| `data/_system/ops/live_candidate_slots.py` | 가능하면 미수정 | 후보 공급 역할 유지 |

## 11. 구현 전 사용자 승인 항목

2단계 전 확정 필요:

```text
1. portfolio_K: 기본 20으로 승인할지, 8로 바꿀지
2. total_capital_usd: 실제 운용 총액
3. entry_timing: next_open 기본 유지 여부
4. s2_take_profit_enabled=false 승인 여부
5. kill_switch scope 선택
6. max_order_notional_usd / max_daily_orders
7. 실주문 arm 방식: phrase만 쓸지 별도 approval token 둘지
8. chart/manual exit plan을 S2 자동매매 포지션에 허용할지 여부
```

## 권장 기본값

```text
portfolio_K = 20
entry_timing = next_open
auto_buy_enabled = false initially
auto_exit_enabled = true only after dry-run passes
real_orders_enabled = false initially
dry_run = true initially
s2_take_profit_enabled = false
allocation_mode = equal_weight_fixed_slot
kill_switch.scope = block_new_buys_allow_risk_reducing_exits
max_order_notional_usd = min(total_capital_usd / K, user cap)
```

## Phase 2 구현 순서 제안

```text
1. live_auto_config loader + validation
2. S2 no-TP ExitPolicy 토글 + unit/dry-run test
3. S2AutoTrader dry-run 모드
4. broker holdings ↔ positions ↔ held_exclusions reconcile
5. next-open queue 기반 dry-run 자동매수
6. BUY reconciliation → PositionManager 등록 검증
7. auto_exit dry-run 검증
8. real_orders_enabled=false 상태로 paper/live dry-run 관찰
9. 사용자 승인 후 소액 real_orders_enabled ON
```

## 승인 필요

이 문서는 설계안이다. 구현은 아직 하지 않았다.

2단계 구현 전 승인 문구 예시:

```text
S2 자동매매 2단계 구현 진행. 기본값은 K=20, next_open, no-TP, dry_run 우선으로 한다.
```
