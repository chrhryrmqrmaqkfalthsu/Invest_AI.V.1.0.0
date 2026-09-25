# 실전·페이퍼·가상 경로 매핑

## 실전

### 실거래 대시보드 후보

```text
cron live_candidate_slots daemon
  -> build_elite_shadow_report()
  -> evaluate_candidate()
  -> elite_shadow_trader._event_flags(ctx)
  -> live_slots_state.json
  -> dashboard exporter가 evaluate_candidate()로 재검증
  -> real_dashboard_buy_candidates.json
  -> manual buy intent/order
```

후보 생성과 dashboard export 재검증 모두 direct Event ON이다. 수동 매수 API 자체는 score를 다시 계산하지 않고 이미 검증된 후보 파일을 소비한다.

### S2 auto

```text
live_slots_state candidate_pool
  -> S2AutoTrader._validate_candidate_signal()
  -> evaluate_candidate(full, ctx=get_market_context())
  -> direct Event ON 재검증
  -> dry-run plan 또는 gate 활성 시 실주문
```

S2 auto는 기본 fail-closed지만, 실주문 gate가 활성화되면 동일 direct Event 재검증이 주문 직전 조건이 된다.

### 일반 run_live real/live/vts

`Runner.tick_market()`이 모든 ticker에서 `LearnedRuleBook.evaluate()`를 호출한다. 이 함수는 `ctx.active_events`를 직접 flag로 바꾼다.

현재 `install_legacy_buy_guard()`가 구 개별 ticker BUY 주문을 차단하지만, direct Event는 signal 통계·재평가·reconfirm·Telegram 보조값에 계속 들어간다.

KIS `vts`도 factory상 동일 GuardedKis/Runner 코드 경로를 사용한다.

## 페이퍼

### 일반 paper/alpaca_paper runner

실전과 같은 `Runner` 및 `LearnedRuleBook.evaluate()`를 사용하므로 direct Event ON이다. 구 개별 BUY는 현재 guard로 차단된다.

### next-open 자동 BUY

현재 자동 BUY 경로는 `NextOpenBuyCoordinator`이며:

```python
_lookup_signal_context(..., use_llm_events=False)
```

로 direct Event가 이미 OFF다. 이 경로는 live `ctx.score`가 아니라 D-1 `market_history.score`를 사용한다.

### central intraday 코드

`LiveCentralController.tick_market()`과 `central_control.py`의 direct Event 변환 코드는 존재한다.

그러나 현재 `scripts/run_live.py`는 controller를 생성한 뒤 `runner.tick_market`을 `central_controller.tick_market`으로 치환하지 않는다. scheduler에는 여전히 `runner.tick_market`이 등록된다.

따라서 central intraday direct Event 경로는 **현재 run_live wiring에서는 dormant**다. next-open coordinator는 controller의 entity pool을 이용하지만 자체 point-in-time evaluator를 사용하며 Event OFF다.

## 가상

### elite shadow

`run_shadow_tick()`은 `evaluate_candidate()`를 사용해 가상 포지션을 연다. direct Event ON이다.

### elite strategy simulation

`run_strategy_sim_tick()`도 `evaluate_candidate()`를 사용한다. 추가로 `elite_signal_history`를 gate 판단에 사용하므로 현재-context Event가 두 평가 계층에 들어갈 수 있다.

### signal history / pullback replay

두 경로 모두 `elite_shadow_trader._event_flags(ctx)`를 import해 현재 MarketContext Event를 사용한다. 과거 slice를 평가하지만 Event context는 현재 값으로 고정된다.

### S2 auto dry-run

실주문과 같은 `evaluate_candidate()` 재검증을 사용하므로 direct Event ON이다. 주문만 제출하지 않는다.

### 중앙 backtest

`engine/central/signal_collector.py`는 `use_llm_events=False`가 기본이며 current `active_events`를 사용하지 않는다. 단일 라이브 스위치 대상이 아니다.

## 공통 수렴점

모드별 호출자는 많지만 current `active_events`를 direct flag로 만드는 구현은 다음 세 곳뿐이다.

```text
central_control.py
learned_rulebook.py
elite_shadow_trader.py
```

따라서 세 지점을 공통 helper로 수렴시키면 실전·페이퍼·가상 current-context direct Event를 단일 정책으로 제어할 수 있다.
