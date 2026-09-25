# 라이브 Event 활성 경로

## 최종 활성 경로

central Stage3 후보 평가는 `use_llm_events`를 호출하지 않는다.

```text
LiveCentralController._evaluate_entity_signal()
  -> _evaluate_stage3_entity_signal()
  -> get_market_context()
  -> ctx.active_events
  -> 11개 has_* flag 직접 생성
  -> evaluate_signal(event_flags=event_flags)
  -> rb.use_event_block=True이면 direct Event 가산
```

## 코드 지점

### 1. central Stage3 평가 진입

`engine/live/central_control.py:511-515`

```python
def _evaluate_entity_signal(self, entity, price):
    return self._evaluate_stage3_entity_signal(entity, float(price))
```

모든 central entity 평가가 `_evaluate_stage3_entity_signal()`로 들어간다.

### 2. MarketContext 로드

`engine/live/central_control.py:544-556`

```python
ctx = get_market_context()
market_score = float(getattr(ctx, "score", 50.0) or 50.0)
```

이 `market_score`는 `engine/market/context.py:602`에서 이미 가격점수와 Event aggregate를 합친 값이다.

```python
final_score = float(np.clip(price_score + event_adj, 0, 100))
```

### 3. 라이브 Event flag 직접 생성

`engine/live/central_control.py:582-599`

```python
active = getattr(ctx, "active_events", {}) or {}
event_flags = {
    "has_war": int("전쟁" in active),
    "has_rate_hike": int("금리정책_인상" in active),
    ...
    "has_fed_statement": int("연준발언" in active),
}
```

이 블록에는 다음이 없다.

- `use_llm_events` 조회
- live config 조회
- environment switch 조회
- 학습 설정 조회

`active_events`가 존재하면 무조건 flag dictionary를 만든다.

### 4. evaluator 전달

`engine/live/central_control.py:601-611`

```python
res = evaluate_signal(
    rb=rb,
    market_score=market_score,
    event_flags=event_flags,
    ...
)
```

### 5. 실제 direct Event 활성 gate

`engine/strategies/evaluator.py:176-195`

```python
if event_flags and getattr(rb, "use_event_block", True):
    event_adj += ...
    event_adj *= rb.event_strength_multiplier

raw_score += event_adj
```

라이브 direct Event가 최종적으로 켜지는 조건은 다음 두 개다.

```text
event_flags가 비어 있지 않음
AND
rb.use_event_block == True
```

`Rulebook.use_event_block` 기본값은 `True`다.

`engine/strategies/rulebook.py:19-21`

```python
use_event_block: bool = True
```

기존 rulebook dictionary에 필드가 없으면 `Rulebook.from_dict()`가 dataclass 기본값 True를 사용한다.

## 일반 LearnedRuleBook 라이브 경로

central controller 외 일반 runner도 같은 결과를 별도 코드로 만든다.

`engine/live/runner.py:496-506`

```python
sig = self.rulebook.evaluate(ticker, price)
```

`engine/strategies/learned_rulebook.py:250-308`

```python
ctx = get_market_context()
active = getattr(ctx, "active_events", {}) or {}
event_flags = {...}
res = evaluate_signal(..., event_flags=event_flags)
```

이 경로도 `use_llm_events`를 보지 않는다.

## next-open 경로와의 차이

`engine/live/scheduled_open_buy_queue.py:397-408`은 유일하게 학습 helper를 사용하며 명시적으로 끈다.

```python
_lookup_signal_context(
    ...,
    use_llm_events=False,
)
```

따라서 next-open D-1 경로는 Event OFF지만, intraday central 및 일반 runner 경로는 별도 직접 flag 생성으로 Event ON이다.
