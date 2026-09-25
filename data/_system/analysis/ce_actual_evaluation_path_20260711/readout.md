# CE 실제 평가 경로 재확정 — central dormant 정정 반영

## 최종 판정

`ACTUALLY_OTHER`

CE는 `LiveCentralController`나 일반 `Runner`의 후보를 소비한 것이 아니라, 별도 운영 helper인 `live_candidate_slots`가 `engine/live/elite_shadow_trader.py::evaluate_candidate()`로 계산한 후보를 실거래 대시보드가 fallback으로 받아 주문했다.

정확한 경로:

```text
live_candidate_slots process
  -> build_elite_shadow_report()
  -> elite_shadow_trader.evaluate_candidate()
  -> get_market_context()
  -> ctx.active_events
  -> elite_shadow_trader._event_flags(ctx)
  -> evaluate_signal()
  -> live_slots_state.json
  -> dashboard live-slot fallback lookup
  -> dashboard-real-detail
  -> direct Alpaca live market order
```

## 1. 7월 7~8일 run_live scheduler wiring

CE 첫 signal 직전 commit `0f53472`과 주문 직전 commit `de5792c`의 `scripts/run_live.py`를 확인했다.

두 시점 모두 scheduler는 다음 wrapper를 등록했다.

```python
def tick_market_with_holding_news():
    result = runner.tick_market()
    _refresh_if_due()
    return result
```

```python
scheduler.add_market_hours_job(
    func=make_holding_news_tick_market_job(runner),
    ...
)
```

즉 실제 market tick은 `runner.tick_market()`이었다.

`LiveCentralController` object는 생성됐지만:

- `central_controller.tick_market` scheduler 등록 없음
- `runner.tick_market = central_controller.tick_market` 치환 없음
- controller constructor 내부 monkeypatch 없음

이었다.

`run_live.py`는 2026-06-29 commit `63de23d` 이후 CE 시점까지 scheduler wiring 변경이 없었다. 따라서 central intraday는 현재뿐 아니라 7월 7~8일에도 dormant였다.

## 2. CE 후보 최초 생성 흔적

`data/_system/live_slots_state.json`:

```text
created_at=2026-07-07T20:38:49.714917Z
```

첫 live slot refresh:

```text
2026-07-07T20:39:22.288081Z
force_evaluate=true
candidate_count=93
evaluated=80
buy_signal_count=28
```

CE 첫 signal:

```text
candidate_id=stage3:CE:998b0b638c66
first_signal_at=2026-07-07T22:22:21.577113Z
first_final_score=7.195458414225013
first_signal_price=48.68
```

같은 시각 event log:

```text
2026-07-07T22:22:21.577623Z
REFRESH
force_evaluate=true
```

따라서 CE 최초 signal은 run_live central tick이 아니라 별도 live slot 강제 refresh에서 생성됐다.

7월 7일 당시 helper source는 아직 Git commit 전이었다. 정확한 최초 실행 command와 PID는 보존되지 않아 확인 불가다. 다만 같은 날 작성된 `live_slots_tool_20260707/readout.md`와 state/event log는 evaluator가 `elite_shadow_trader.evaluate_candidate()`였음을 명시한다.

## 3. 주문 시점의 exact 평가 코드

`data/_system/ops/live_candidate_slots.py`는 2026-07-08 04:43 UTC commit `fd2400d`에서 처음 Git에 들어왔다.

주문 전 코드:

```python
ctx = get_market_context()
report = build_elite_shadow_report(...)
...
ev = evaluate_candidate(candidate, ctx=ctx)
```

주문 직전 event:

```text
2026-07-08T14:27:14.510353Z
REFRESH
force_evaluate=false
candidate_count=93
evaluated=73
buy_signal_count=25
```

CE candidate snapshot:

```text
last_seen_at=2026-07-08T14:27:14.509621Z
score=8.363246295633697
raw_score=8.363246295633697
threshold=2.6541866643896674
Event=+4.62
market_score=87.2
```

주문은 그 약 1초 뒤 제출됐다.

즉 CE는 7월 7일 최초 cached signal을 그대로 주문한 것이 아니라, 7월 8일 정규장 중 live slot process가 방금 다시 계산한 후보였다.

## 4. 실제 Event 활성 메커니즘

CE 첫 signal commit과 주문 commit의 `elite_shadow_trader.py`는 동일했다.

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

이 경로에는 `use_llm_events` 확인이 없다.

따라서 CE의 `이벤트반응(+4.62)`은 central controller가 아니라 elite evaluator가 `ctx.active_events`를 직접 flag로 바꿔 생성한 direct Event component다.

## 5. dashboard 주문 source

CE intent에는 다음이 보존돼 있다.

```text
candidate_source=live_slots_state_fallback
real_candidate_fallback=true
source=dashboard-real-detail
execution_mode=direct_alpaca_live_market_order
```

7월 8일 13:33 UTC commit `1b42954`는 실거래 dashboard candidate lookup에 다음 fallback을 추가했다.

```python
state = real_api._live_slots_state()
row = real_api._find_live_slot_candidate_raw(state, cid)
...
out.setdefault("candidate_source", "live_slots_state_fallback")
out.setdefault("real_candidate_fallback", True)
```

따라서 dashboard API는 CE를 central 또는 Runner에서 다시 평가하지 않았다. `live_slots_state`의 직전 평가 row를 가져와 직접 주문했다.

## 6. 일반 Runner 가능성

일반 Runner도 같은 시각 CE를 별도로 평가했을 가능성은 배제할 수 없다. 그러나 해당 평가가 CE 주문 source였다는 근거는 없다.

주문과 연결된 보존 source는 명시적으로 `live_slots_state_fallback`이다.

따라서 판정은 `ACTUALLY_RUNNER`가 아니라 `ACTUALLY_OTHER`다.

## 7. 이전 분석 전제 정정

이전 문구:

```text
CE는 intraday/live-slot central Stage3 평가 결과를 사용했다.
```

정정 문구:

```text
CE는 Stage3 룰북 후보였지만 LiveCentralController 경로가 아니라,
live_candidate_slots가 elite_shadow_trader.evaluate_candidate로 계산한 후보를 사용했다.
```

`stage3`는 룰북 생성 단계이며 central controller 호출 여부를 뜻하지 않는다.

## 8. 기존 Event 결론 영향

### 유지되는 결론

- CE direct Event는 ON이었다.
- live Event flag 생성은 `use_llm_events`를 무시했다.
- 학습 Event OFF·라이브 Event ON 불일치는 유지된다.
- `+4.62`는 direct Event component다.
- `ctx.score`와 Event flags가 같은 MarketContext를 사용한다.
- 당시 active key 조합과 기사 payload는 미보존이라 복원 불가다.

### 정정되는 결론

- CE가 `central_control.py::_evaluate_stage3_entity_signal()`에서 평가됐다는 설명은 틀렸다.
- CE 대응 최소 변경 지점을 central-only로 잡으면 효과가 없다.

### Event OFF 설계 영향

후속 전 경로 단일 스위치 설계는 유지된다. 그 설계는 다음 세 지점을 함께 제어한다.

```text
central_control.py
learned_rulebook.py
elite_shadow_trader.py
```

실제 CE 경로인 `elite_shadow_trader.py`가 포함돼 있으므로 CE 대응에도 맞다.

CE와 실거래 대시보드 후보의 direct Event를 끄려면 elite 공통 evaluator의 `_event_flags(ctx)`를 반드시 단일 스위치에 연결해야 한다.

이 지점에서 `event_flags=None`만 전달하고 `market_score=ctx.score`를 유지하면 direct Event는 사라지고 market_score의 매크로 aggregate는 유지된다.

## 최종 결론

- 당시 central scheduler: dormant
- CE 후보 평가: `live_candidate_slots → elite_shadow_trader.evaluate_candidate`
- CE 주문: `live_slots_state_fallback → dashboard-real-detail → Alpaca live`
- 판정: `ACTUALLY_OTHER`
- direct Event ON 및 switch 무시 결론: 유지
- 학습–라이브 불일치 결론: 유지
- central-only OFF 제안: 정정 필요
- 전 경로 단일 switch 설계: 유지

## 확인 불가

- 7월 7일 최초 helper 실행 command와 PID
- 최초 signal 시점의 uncommitted helper exact source blob
- 일반 Runner가 같은 시각 CE를 별도로 평가했는지 여부
- 당시 active Event key별 payload

## 산출물

- `data/_system/analysis/ce_actual_evaluation_path_20260711/scheduler_wiring_timeline.csv`
- `data/_system/analysis/ce_actual_evaluation_path_20260711/ce_artifact_path_evidence.csv`
- `data/_system/analysis/ce_actual_evaluation_path_20260711/actual_path_trace.md`
- `data/_system/analysis/ce_actual_evaluation_path_20260711/prior_assumption_correction.md`
- `data/_system/analysis/ce_actual_evaluation_path_20260711/conclusion_impact.md`
- `data/_system/analysis/ce_actual_evaluation_path_20260711/readout.md`

운영 코드·설정 변경: 0건
