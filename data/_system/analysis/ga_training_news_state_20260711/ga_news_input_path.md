# GA 학습의 News·NewsTopics·Event 입력 경로

## News/NewsTopics

학습과 라이브 평가는 같은 일별 ticker sentiment CSV loader를 사용한다.

```text
engine/pipeline/context.py::prepare_ticker_context(ticker)
  -> engine.market.ticker_sentiment.load_csv(ticker)
  -> data/_system/ticker_sentiment/<TICKER>_daily.csv
  -> ctx["ticker_sentiment"]
  -> Stage2/Stage3 run_backtest(... ticker_sentiment=...)
  -> engine.learning.backtest::_lookup_signal_context()
  -> D-1 이하 최신 row, max_age_days=7
  -> sentiment_avg + precompute_topic_features()
  -> evaluate_signal(news_sentiment, topic_features)
```

별도 학습 전용 뉴스 데이터셋은 확인되지 않았다. `engine/learning/learner.py`도 동일한 `load_csv`를 import하며, `engine/strategies/learned_rulebook.py` docstring은 라이브가 "the same source used by backtests"를 읽는다고 명시한다.

### global News mask

`use_news_global`은 GA categorical gene이다.

```python
CATEGORICAL_PARAMS["use_news_global"] = [False, True]
```

`engine/strategies/evaluator.py`에서 이 mask가 False이면 `weight_news_sentiment × sentiment_avg`만 0으로 만든다.

```python
s_news = rb.weight_news_sentiment * eff_sent
if not rb.use_news_global:
    s_news = 0.0
```

NewsTopics는 `use_news_global`과 독립이다. `topic_features`가 있으면 15개 `weight_news_<topic>`을 적용하고 `news_block_cap`으로 clamp한다. 따라서 `use_news_global=False`여도 NewsTopics는 학습·평가된다.

## Event

Stage2와 Stage3 학습 runner는 모두 backtest 인자로 다음을 고정한다.

```python
"use_llm_events": False
```

근거:

- `scripts/research/run_stage2.py::base_kwargs()`
- `scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001::base_backtest_kwargs()`

`engine.learning.backtest::_lookup_signal_context()`는 `use_llm_events=False`일 때 `event_flags`를 전부 0으로 유지한다.

```python
event_flags = _zero_event_flags()
...
if use_llm_events:
    event_flags[key] = int(mkt.get(key, 0) or 0)
```

그 결과 GA fitness는 다음 gene의 값에 영향을 받지 않는다.

- `use_event_block`
- 11개 `event_response_*`
- `event_strength_multiplier`

Stage2/Stage3 호출은 `complexity_penalty_per_mask`도 넘기지 않아 기본값 0을 사용한다. 따라서 `use_event_block=True/False`도 complexity penalty를 통해 선택되지 않는다.

## 핵심 차이

- News/NewsTopics: 실제 historical ticker CSV가 존재하는 날에는 fitness에 반영됨.
- Event: 학습 전 기간 flag=0이므로 fitness 기여가 항상 0.

따라서 라이브 룰북의 Event 계수는 historical Event 반응을 학습한 계수로 해석할 수 없다.
