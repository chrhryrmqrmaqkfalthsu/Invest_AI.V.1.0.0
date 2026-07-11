# 현재 활성 후보 선별 경로

## OS에서 실제 실행 중인 프로세스

2026-07-11 조사 시점에 후보 평가와 관련해 실행 중인 프로세스는 다음 하나다.

```text
PID 337946
/home/g3000kkw/kingmaker/venv/bin/python
/home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60
started: 2026-07-10 13:30:03 UTC
```

함께 실행 중인 dashboard API:

```text
PID 325259
uvicorn api_server_candidate_only:app --host 0.0.0.0 --port 8001
started: 2026-07-10 10:49:15 UTC
```

현재 실행 중인 다음 프로세스는 확인되지 않았다.

- `scripts/run_live.py`
- `scripts/run_s2_auto_live.py`
- `scripts/run_elite_shadow_trader.py`
- `scripts/run_elite_strategy_sim.py`

따라서 현재 OS에서 실제로 후보를 주기 평가하는 경로는 `live_candidate_slots` 하나다.

## 설치된 cron

실제 사용자 crontab에는 다음 guard가 설치돼 있다.

```text
* * * * * live_candidate_slots_guard.sh
@reboot live_candidate_slots_guard.sh
```

`dashboard_guard.sh`도 1분마다 실행되지만 dashboard/API 상태를 감시할 뿐 후보 score를 계산하지 않는다.

## 실전 후보 경로

현재 실거래 dashboard가 소비하는 후보 source는 다음 경로다.

```text
live_candidate_slots.py daemon
  -> build_elite_shadow_report()
  -> elite_shadow_trader.evaluate_candidate(candidate, ctx)
  -> live_slots_state.json
  -> api_server_candidate_only / dashboard fallback lookup
  -> manual real buy intent
```

코드 근거:

- `data/_system/ops/live_candidate_slots.py:28-31`
  - `evaluate_candidate`와 `get_market_context` import
- `data/_system/ops/live_candidate_slots.py:346-418`
  - `refresh_slots()`가 정규장에 `evaluate_candidate(candidate, ctx=ctx)` 호출
- `engine/live/real_dashboard_holding_days_patch.py:139-171`
  - isolated candidate가 없으면 `live_slots_state` 후보를 fallback으로 사용

현재 주말이므로 daemon은 실제 평가를 하지 않고 캐시된 pool을 재사용 중이다.

마지막 실제 `REFRESH`:

```text
2026-07-10T19:59:53.002040Z
candidate_count=82
evaluated=63
buy_signal_count=18
eligible_pool_count=18
```

현재 slot/pool의 `last_seen_at`도 `2026-07-10T19:59:53.001495Z`다.

## run_live 경로

현재 `run_live.py` 프로세스는 실행 중이지 않다.

코드상 scheduler wiring은 7월 8일과 동일하다.

```python
# scripts/run_live.py:191-194
result = runner.tick_market()
```

```python
# scripts/run_live.py:450-453
scheduler.add_market_hours_job(
    func=make_holding_news_tick_market_job(runner),
    ...
)
```

따라서 `run_live.py`를 실행하면 market tick은 일반 `Runner.tick_market()`을 타며, ticker 평가 지점은:

```text
Runner._process_ticker()
  -> self.rulebook.evaluate(ticker, price)
  -> LearnedRuleBook.evaluate()
```

이다.

코드 근거:

- `engine/live/runner.py:496-509`

그러나 `install_legacy_buy_guard()`가 일반 per-ticker BUY 주문을 차단한다. 현재 `run_live.py`의 기본 `--central-control` 값도 `off`다.

## paper 경로

현재 paper runner 프로세스는 없다.

코드상 가능한 경로는 두 가지다.

1. 일반 paper runner
   - `Runner._process_ticker()`
   - `LearnedRuleBook.evaluate()`
   - direct Event switch 대상
   - 일반 BUY는 legacy guard로 차단

2. `--central-control on` + next-open
   - `NextOpenBuyCoordinator`
   - `scheduled_open_buy_queue.py:397-417`
   - `_lookup_signal_context(... use_llm_events=False)`
   - direct Event는 원래 OFF

`LiveCentralController.tick_market()`은 현재도 scheduler에 직접 등록되지 않는다.

## 가상 경로

현재 별도 elite shadow/sim 프로세스는 없다.

코드상 가상 진입점:

- `scripts/run_elite_shadow_trader.py`
  - `run_shadow_tick()`
  - `elite_shadow_trader.evaluate_candidate()`
- `scripts/run_elite_strategy_sim.py`
  - `run_strategy_sim_tick()`
  - `elite_shadow_trader.evaluate_candidate()`
- `elite_signal_history.py`
  - elite `_event_flags()` 재사용
- `elite_pullback_replay.py`
  - elite `_event_flags()` 재사용

이들 모두 소스 기준으로 elite 공통 switch 경로에 수렴한다.
