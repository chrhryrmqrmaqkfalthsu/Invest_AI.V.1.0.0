# Event 차단 시 매크로 잔존 여부

## `use_event_block=False`

`engine/strategies/evaluator.py`에서 이 설정은 직접 Event component만 0으로 만든다.

```python
if event_flags and rb.use_event_block:
    event_adj = ...
else:
    event_adj = 0.0
```

그러나 `market_score`는 evaluator 호출 전에 이미 `MarketContext.score = price_score + event_adjustment`로 만들어져 있다. `use_event_block`은 MarketContext를 다시 계산하지 않는다.

따라서 Event block을 꺼도 다음은 유지된다.

- `market_score` 안의 aggregate Event 영향
- `market_score`를 사용하는 market_adjustment
- `market_score`를 사용하는 crash bonus gate

단, 룰북이 실제로 이 기능을 켰을 때만 최종 entry score에 남는다.

## 설정별 잔존

| 룰북 설정 | Event block off 이후 매크로 영향 |
|---|---|
| `use_market_entry_adjustment=True` | market_score를 통한 전체 score 배수에 잔존 |
| `crash_buy_enabled=True`이고 threshold 충족 | 고정 +2 crash bonus로 잔존 |
| 둘 다 비활성 또는 조건 미충족 | `ctx.score`에는 남지만 최종 entry score에는 반영되지 않음 |

## `use_llm_events=False`

이 인자는 주로 backtest의 `_lookup_signal_context()`에서 Event flags를 0으로 만드는 용도다.

```python
if use_llm_events:
    event_flags[key] = int(mkt.get(key, 0) or 0)
```

라이브 central Stage3 경로에는 동일한 런타임 switch가 없다. 라이브는 `ctx.active_events`에서 항상 flags를 만들고, 룰북의 `use_event_block`이 직접 Event 기여를 통제한다.

또한 backtest의 `market_score`는 `market_history.score`를 읽는데, 이 컬럼은 가격·VIX 기반이다. 따라서 학습에서 `use_llm_events=False`일 때는 Event flags뿐 아니라 market_score 경로의 뉴스 Event도 사실상 들어가지 않는다.

## CE

CE 룰북:

- `use_event_block=True`
- `use_market_entry_adjustment=False`
- `crash_buy_enabled=True`
- `crash_threshold_score=30.8996124788`

CE 실제 주문 snapshot:

- Event contribution: `+4.62260455`
- market adjustment: `1.0`
- crash bonus: `0`

따라서 그 주문 시점에 `use_event_block=False`였다고 가정하면, 저장된 점수 구성상 +4.62가 제거되고 market_adjustment 또는 crash bonus를 통한 매크로 잔존은 없었을 것이다.

다만 당시 `market_score` 값 자체는 snapshot에 보존되지 않아, 내부 MarketContext가 어떤 aggregate Event 값을 포함했는지는 확인 불가다. 결론은 저장된 final-score 구성에 한정한다.

## Event 과반 9종목 설정

- `use_market_entry_adjustment=True`: BTBT, BMI, ACMR — 3/9
- `crash_buy_enabled=True`: ACMR 제외 8/9
- `use_event_block=True`: 9/9

따라서 Event block을 끄더라도 3종목은 market adjustment를 통해 aggregate macro가 상시 남을 수 있고, 8종목은 낮은 market_score 상황에서 crash bonus로 남을 수 있다.
