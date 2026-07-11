# 라이브 Event 활성 지점 특정

## 최종 판정

`LIVE_ON_SWITCH_IGNORED`

정확히는 라이브 central Stage3 경로가 `use_llm_events`를 잘못된 값으로 읽는 것이 아니라, **그 switch를 전혀 읽지 않는다.**

라이브는 `MarketContext.active_events`가 존재하면 자체 코드로 11개 `has_*` flag를 만들고 `evaluate_signal()`에 직접 전달한다. 이후 룰북의 `use_event_block=True` gate가 direct Event 가산을 활성화한다.

따라서 공용 `run_backtest` 기본값을 False로 바꾸거나 학습 runner의 `use_llm_events=False`를 유지해도 라이브 direct Event는 꺼지지 않는다.

## 1. 라이브 Event 활성 코드 경로

central 후보 평가 호출:

```text
engine/live/central_control.py:359-360
  -> _evaluate_entity_signal()
engine/live/central_control.py:511-515
  -> _evaluate_stage3_entity_signal()
```

MarketContext 취득:

```python
# engine/live/central_control.py:544-553
ctx = get_market_context()
market_score = float(getattr(ctx, "score", 50.0) or 50.0)
```

Event flag 직접 생성:

```python
# engine/live/central_control.py:582-597
active = getattr(ctx, "active_events", {}) or {}
event_flags = {
    "has_war": int("전쟁" in active),
    "has_rate_hike": int("금리정책_인상" in active),
    ...
    "has_fed_statement": int("연준발언" in active),
}
```

Evaluator 전달:

```python
# engine/live/central_control.py:601-610
res = evaluate_signal(
    ...,
    market_score=market_score,
    event_flags=event_flags,
)
```

실제 direct Event gate:

```python
# engine/strategies/evaluator.py:176-195
if event_flags and getattr(rb, "use_event_block", True):
    event_adj += ...
    event_adj *= rb.event_strength_multiplier
raw_score += event_adj
```

`Rulebook.use_event_block` 기본값은 True다.

```python
# engine/strategies/rulebook.py:19-21
use_event_block: bool = True
```

결론적으로 라이브 direct Event ON 조건은 다음이다.

```text
ctx.active_events에서 만든 event_flags가 존재
AND
rulebook.use_event_block=True
```

`use_llm_events`는 이 경로에 없다.

## 2. 공용 기본값 True의 영향

커밋 `86bf54b`는 다음 기본값을 복원했다.

```python
# engine/learning/backtest.py
run_backtest(..., use_llm_events=True)
```

이 값은 historical backtest helper가 `market_history`의 Event flag를 읽을지를 결정한다.

```python
if use_llm_events:
    event_flags[key] = int(mkt.get(key, 0) or 0)
```

그러나 central live는 `run_backtest()`나 `_lookup_signal_context()`를 호출하지 않는다. `central_control.py`는 `get_market_context()`와 `evaluate_signal()`을 직접 import한다.

일반 `LearnedRuleBook.evaluate()`도 `run_backtest()`를 사용하지 않고 `ctx.active_events`를 직접 변환한다.

따라서:

```text
86bf54b 기본값 True → central live Event ON 원인 아님
```

이다.

## 3. 경로별 switch 존중 여부

| 경로 | Event flag 생성 | use_llm_events 존중 |
|---|---|---|
| central intraday | `ctx.active_events` 직접 변환 | 아니오 |
| 일반 runner intraday | `LearnedRuleBook.evaluate()` 직접 변환 | 아니오 |
| Telegram/추가매수 재평가 | 일반 LearnedRuleBook 경로 | 아니오 |
| next-open D-1 | `_lookup_signal_context()` | 예, 명시적 False |
| elite shadow/history | 자체 `_event_flags(ctx)` | 아니오 |
| backtest | `_lookup_signal_context()` | 예 |

next-open만 현재 명시적으로 Event OFF다.

```python
# engine/live/scheduled_open_buy_queue.py:397-408
_lookup_signal_context(..., use_llm_events=False)
```

## 4. direct Event만 OFF하는 정확한 지점

### 권장 지점 — central 후보

```text
engine/live/central_control.py:582-610
```

이 지점에서 live central 전용 설정을 확인하고, OFF이면 `event_flags=None` 또는 빈 dictionary를 `evaluate_signal()`에 전달하는 방식이 목표에 가장 정확하다.

이유:

- direct Event 가산만 0
- `market_score=ctx.score`는 그대로 유지
- market adjustment 유지
- crash bonus gate 유지
- News/NewsTopics 유지
- MarketContext의 Event·대시보드 표시 유지
- 학습/backtest에 영향 없음

중요하게도 `ctx.score`는 이미 다음 계산을 거친 값이다.

```python
# engine/market/context.py:602
final_score = np.clip(price_score + event_adj, 0, 100)
```

따라서 flag 전달만 차단하면 사용자가 의도한 **direct Event OFF + market_score macro 유지**가 된다.

## 5. central 외 라이브 영향

central controller는 `LearnedRuleBook.evaluate()`를 거치지 않고 evaluator를 직접 호출한다. 따라서 central 지점만 차단해도 일반 runner 경로는 계속 Event ON이다.

일반 live 평가도 끄려면 별도 지점이 필요하다.

```text
engine/strategies/learned_rulebook.py:281-307
```

이곳에서 `event_flags` 생성을 같은 live-only switch로 gate해야 한다.

영향 경로:

- `Runner._process_ticker()`
- 추가매수 승인 재평가
- Telegram 수동 평가

elite shadow, pullback replay, signal history도 자체 `_event_flags(ctx)` helper를 사용하므로 표시·진단까지 정합성을 맞추려면 공통 live switch를 공유해야 한다.

## 6. 다른 OFF 후보와 side effect

### 룰북 `use_event_block=False`

직접 Event만 꺼지고 market_score는 유지된다. 그러나 해당 룰북을 사용하는 live·replay·backtest 전체에 영향을 주며, artifact 변경과 hash/provenance 문제가 생긴다. live-only 변경 지점으로는 범위가 넓다.

### evaluator 전역 차단

`engine/strategies/evaluator.py:179`에서 hard-off하면 direct Event만 사라지지만 모든 live·backtest·replay·diagnostic에 영향을 준다. 비권고다.

### MarketContext에서 Event 제거

`engine/market/context.py:581-626`에서 `active_events`나 `event_adj`를 제거하면 direct Event와 함께 market_score의 macro 정보도 사라진다. 이번 목표와 맞지 않는다.

### `run_backtest` 기본값 False 변경

central 및 일반 live 경로가 이 함수를 호출하지 않으므로 효과가 없다.

## 권장 변경 범위

### central Stage3 후보만 대상으로 할 때

```text
engine/live/central_control.py:582-610
```

여기에 명시적 live central direct-Event switch를 추가하는 것이 최소 변경 지점이다.

### 모든 intraday live 평가를 대상으로 할 때

최소 다음 두 지점을 같은 switch로 gate해야 한다.

```text
engine/live/central_control.py:582-610
engine/strategies/learned_rulebook.py:281-307
```

진단·shadow 화면까지 동일하게 만들려면 별도 `_event_flags(ctx)` 구현들도 같은 공통 helper/switch를 사용해야 한다.

## 최종 결론

- 판정: `LIVE_ON_SWITCH_IGNORED`
- 라이브 Event ON은 86bf54b의 공용 기본값 True를 상속한 결과가 아니다.
- 라이브 설정에 명시적 `use_llm_events=True`가 있는 것도 아니다.
- central live가 `ctx.active_events`를 무조건 flag로 변환하는 별도 코드가 활성 원인이다.
- direct Event만 끄고 market_score macro를 유지하려면 `central_control.py`의 flag 생성·전달 지점을 차단해야 한다.
- `market.context`를 변경하면 market_score macro까지 제거되므로 목표 지점이 아니다.

## 산출물

- `data/_system/analysis/live_event_activation_point_20260711/live_event_activation_path.md`
- `data/_system/analysis/live_event_activation_point_20260711/default_86bf54b_impact.md`
- `data/_system/analysis/live_event_activation_point_20260711/switch_respect_matrix.csv`
- `data/_system/analysis/live_event_activation_point_20260711/off_transition_candidates.md`
- `data/_system/analysis/live_event_activation_point_20260711/readout.md`

운영 코드·설정 변경: 0건
