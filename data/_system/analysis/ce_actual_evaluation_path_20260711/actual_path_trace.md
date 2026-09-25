# CE 실제 평가·진입 경로

## 판정

`ACTUALLY_OTHER`

CE는 central controller나 일반 Runner가 만든 주문 후보를 소비한 것이 아니라, 별도 운영 helper인 `live_candidate_slots`가 `elite_shadow_trader.evaluate_candidate()`로 평가한 후보를 실거래 대시보드가 fallback으로 받아 주문했다.

## 1. 당시 run_live scheduler

CE 첫 신호 직전 commit `0f53472`과 주문 직전 commit `de5792c`의 `scripts/run_live.py`는 현재와 동일하다.

```python
# scripts/run_live.py:191-194

def tick_market_with_holding_news():
    result = runner.tick_market()
    _refresh_if_due()
    return result
```

```python
# scripts/run_live.py:450-453
scheduler.add_market_hours_job(
    func=make_holding_news_tick_market_job(runner),
    ...
)
```

`make_holding_news_tick_market_job(runner)`의 내부 호출은 `runner.tick_market()`이다.

`LiveCentralController`는 생성되지만 scheduler에 `central_controller.tick_market`이 등록되지 않는다.

```python
central_controller = LiveCentralController(runner, central_config)
```

constructor도 `runner.tick_market`을 치환하지 않는다. runner reference와 config를 저장하고 entity를 로드할 뿐이다.

따라서 7월 7~8일에도 central intraday tick은 dormant였다.

## 2. 별도 live slot process

`data/_system/live_slots_state.json`은 2026-07-07 20:38:49 UTC에 생성됐다.

첫 event:

```text
2026-07-07T20:39:22.288081Z
REFRESH
force_evaluate=true
candidate_count=93
evaluated=80
buy_signal_count=28
```

CE 첫 signal:

```text
first_signal_at=2026-07-07T22:22:21.577113Z
first_final_score=7.195458414225013
```

같은 시각 event log에는 두 번째 강제 refresh가 남아 있다.

```text
2026-07-07T22:22:21.577623Z
REFRESH
force_evaluate=true
```

7월 7일은 장 마감 후라 일반 daemon refresh가 `REFRESH_SKIPPED`를 기록했고, CE 첫 signal은 명시적 force evaluation에서 생성됐다.

정확히 어떤 shell/process가 최초 helper를 시작했는지는 로그에 launcher PID나 command line이 없어 확인 불가다. cron guard는 7월 9일에야 추가됐다. 그러나 state와 event log는 helper 자체가 7월 7일부터 실행 중이었음을 확정한다.

## 3. live slot 평가 코드

7월 8일 04:43 UTC에 처음 commit된 `data/_system/ops/live_candidate_slots.py`는 다음 경로를 명시한다.

```python
ctx = get_market_context()
report = build_elite_shadow_report(...)
...
ev = evaluate_candidate(candidate, ctx=ctx)
```

source metadata도 같은 내용을 저장한다.

```text
signal_evaluator = engine.live.elite_shadow_trader.evaluate_candidate(candidate, ctx=get_market_context())
```

첫 signal 시점의 helper source는 아직 Git에 commit되기 전이므로 exact source snapshot은 복원 불가다. 다만 같은 날 20:40 UTC에 작성된 `live_slots_tool_20260707/readout.md`가 동일 evaluator stack을 명시하고, 7월 8일 commit된 source와 event/state 형식이 일치한다.

주문 시점에는 helper source가 이미 commit돼 있었고 이후 주문 전까지 해당 파일 변경 commit은 없다.

## 4. elite evaluator의 direct Event

CE 첫 signal 시점 commit `0f53472`와 주문 시점 commit `de5792c`의 `engine/live/elite_shadow_trader.py`는 동일하다.

```python
def _event_flags(ctx):
    active = getattr(ctx, "active_events", {}) or {}
    return {
        "has_war": int("전쟁" in active),
        "has_rate_hike": int("금리정책_인상" in active),
        ...
        "has_fed_statement": int("연준발언" in active),
    }
```

```python
res = evaluate_signal(
    ...,
    market_score=market_score,
    event_flags=_event_flags(ctx),
)
```

이 경로는 `use_llm_events`를 읽지 않는다. 따라서 direct Event는 라이브 active events가 있으면 생성되고, 룰북 `use_event_block=True`일 때 가산된다.

## 5. 주문 직전 재평가

CE 주문 직전 live slot event:

```text
2026-07-08T14:27:14.510353Z
REFRESH
force_evaluate=false
candidate_count=93
evaluated=73
buy_signal_count=25
```

CE order snapshot의:

```text
last_seen_at=2026-07-08T14:27:14.509621Z
```

와 같은 refresh다.

즉 7월 7일 최초 cached 후보를 그대로 주문한 것이 아니라, 주문 약 1초 전에 같은 elite evaluator로 다시 계산된 live slot row를 사용했다.

## 6. dashboard fallback과 주문

7월 8일 13:33 UTC commit `1b42954`는 dashboard real candidate lookup에 fallback을 추가했다.

```python
state = real_api._live_slots_state()
row = real_api._find_live_slot_candidate_raw(state, cid)
...
out.setdefault("candidate_source", "live_slots_state_fallback")
out.setdefault("real_candidate_fallback", True)
```

CE intent snapshot:

```text
candidate_source=live_slots_state_fallback
real_candidate_fallback=true
candidate_state_path=data/_system/real_dashboard_buy_candidates.json
source=dashboard-real-detail
execution_mode=direct_alpaca_live_market_order
```

따라서 주문 API는 CE를 새로 평가하지 않고 방금 갱신된 live slot candidate row를 fallback으로 가져와 Alpaca live market order를 제출했다.

## 최종 경로

```text
별도 live_candidate_slots process
  -> build_elite_shadow_report()
  -> elite_shadow_trader.evaluate_candidate()
  -> get_market_context().active_events
  -> _event_flags(ctx)
  -> evaluate_signal()
  -> live_slots_state.json
  -> real-dashboard fallback candidate lookup
  -> dashboard-real-detail
  -> direct Alpaca live market order
```
