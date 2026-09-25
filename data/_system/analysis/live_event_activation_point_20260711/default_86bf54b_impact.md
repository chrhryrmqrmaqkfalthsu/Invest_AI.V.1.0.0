# 86bf54b 공용 기본값 복원의 라이브 영향

## 결론

커밋 `86bf54b`의 `run_backtest(..., use_llm_events=True)` 기본값 복원은 라이브 central Stage3 활성 원인이 아니다.

## 커밋 변경 범위

`86bf54b`는 `engine/learning/backtest.py`의 공용 backtest 함수 기본값만 바꿨다.

```diff
- use_llm_events: bool = False
+ use_llm_events: bool = True
```

이 값은 `_lookup_signal_context()`에서 historical `market_history`의 `has_*` 컬럼을 읽을지 결정한다.

```python
if use_llm_events:
    for key in EVENT_FLAG_KEYS:
        event_flags[key] = int(mkt.get(key, 0) or 0)
```

## central Stage3와 연결되지 않는 이유

`engine/live/central_control.py`는 다음을 직접 import한다.

- `get_market_context`
- `evaluate_signal`
- `Rulebook`

`run_backtest` 또는 `_lookup_signal_context`를 import하거나 호출하지 않는다.

central Stage3는 `ctx.active_events`를 자체적으로 flag로 바꿔 `evaluate_signal()`에 바로 전달한다.

따라서 다음 변경은 central Stage3 Event를 끄지 못한다.

- `run_backtest` 기본값을 False로 변경
- 학습 runner의 `use_llm_events=False` 유지
- backtest helper의 Event 분기 변경

## 일반 live runner와 연결되지 않는 이유

`engine/strategies/learned_rulebook.py`도 `run_backtest`를 import하지 않는다. `get_market_context()`와 `evaluate_signal()`을 직접 사용한다.

따라서 일반 `Runner._process_ticker()` → `LearnedRuleBook.evaluate()` 경로도 86bf54b 기본값과 무관하다.

## next-open 예외

`scheduled_open_buy_queue.py`는 backtest helper `_lookup_signal_context()`를 직접 사용하고 `use_llm_events=False`를 명시한다.

이 경로는 공용 함수 기본값을 상속하지 않는다. 명시적 False가 우선한다.

## 판정

```text
86bf54b generic default=True
        │
        ├─ backtest caller가 값을 생략할 때 영향 가능
        ├─ central Stage3 live: 연결 없음
        ├─ LearnedRuleBook live: 연결 없음
        └─ next-open: explicit False이므로 영향 없음
```

따라서 라이브 Event ON은 `LIVE_ON_VIA_DEFAULT`가 아니다.
