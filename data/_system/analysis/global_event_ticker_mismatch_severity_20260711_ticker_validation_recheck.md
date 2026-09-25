# ticker 일치 검증 부재 재확인

## Event 경로

- `engine/market/context.py`는 시장 공통 기사 묶음을 분석해 단일 `MarketContext.active_events`를 만든다.
- `engine/strategies/learned_rulebook.py`는 `ctx.active_events`의 key 존재 여부를 `has_earnings_shock` 등 11개 binary flag로 바꾼다.
- 이 과정에서 평가 대상 `ticker`와 Event 기사 대상 기업 ticker를 비교하는 로직은 없다.
- `engine/strategies/evaluator.py`는 전달된 flag와 룰북의 `event_response_*` 계수를 곱해 Event 기여를 계산한다.

따라서 `실적쇼크`가 전역 `active_events`에 존재하면 Event 블록을 사용하는 모든 평가 대상이 동일한 `has_earnings_shock=1`을 받는 구조다. 다만 실제 특정 시점에 몇 종목이 평가됐고 양의 기여가 발생했는지는 해당 시점 payload가 있어야 실측할 수 있다.

## News/NewsTopics 경로

- `engine/strategies/learned_rulebook.py`의 `_load_ticker_sentiment(ticker)`는 평가 대상 ticker를 명시적으로 인자로 받는다.
- News는 해당 ticker의 sentiment 값을 사용한다.
- NewsTopics도 해당 ticker의 topic feature를 사용한다.

즉 News/NewsTopics는 ticker-aware이고 Event는 ticker-unaware다.
