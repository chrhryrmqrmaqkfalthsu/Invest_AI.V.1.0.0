# Event vs News/NewsTopics 코드 경로 대조

| 항목 | Event | News/NewsTopics |
|---|---|---|
| 원본 소스 | NewsAPI `top-headlines`, 미국 business 헤드라인 | `data/_system/ticker_news_cache/<TICKER>/...`의 Alpha Vantage feed |
| 최초 선택 단위 | 시장 뉴스 전체 | 호출 인자로 받은 단일 `ticker` |
| 구조화된 ticker 정보 | 없음 | 각 기사 `ticker_sentiment[]`에 존재 |
| ticker 필터 지점 | 없음 | `engine/market/ticker_sentiment.py::extract_ticker_data()`에서 `ts.get("ticker") == ticker` 검사 |
| 분류/집계 | LLM이 S&P500 전체 영향으로 event_type 판정 후 event_type별 전역 집계 | 해당 ticker와 일치한 기사만 일별 sentiment/topic으로 집계 |
| 런타임 조회 | 단일 `MarketContext.active_events` | `load_ticker_sentiment(ticker)` 및 `_lookup_lagged_news_context(ticker, ...)` |
| 종목 평가 전달 | `int("실적쇼크" in active)` 등 전역 key 존재 여부 | 평가 대상 ticker로 로드한 sentiment/topic feature |
| 종목 일치 재검사 | 없음 | 소스 집계 단계에서 이미 수행 |

## Event 적용 분기

`engine/strategies/learned_rulebook.py`는 평가 대상 `ticker`를 알고 있지만 Event flag 생성에는 사용하지 않는다.

```python
active = getattr(ctx, "active_events", {}) or {}
event_flags = {
    "has_earnings_shock": int("실적쇼크" in active),
    ...
}
```

`engine/strategies/evaluator.py`도 ticker 또는 article 대상을 받지 않고 flag와 종목별 룰북 계수만 곱한다.

```python
event_adj += event_flags.get("has_earnings_shock", 0) * rb.event_response_earnings_shock
event_adj *= rb.event_strength_multiplier
```

따라서 `실적쇼크`가 전역 active key에 존재하고 해당 종목 룰북이 Event 블록을 사용하면, 기사 대상 기업과 무관하게 그 종목 점수에 반영된다.

## News/NewsTopics의 ticker 필터

`engine/market/ticker_sentiment.py::extract_ticker_data()`는 기사 내부의 `ticker_sentiment` 배열을 순회하면서 평가 대상 ticker와 정확히 일치하는 항목만 채택한다.

```python
for ts in item.get("ticker_sentiment", []):
    if ts.get("ticker") == ticker:
        ...
```

이후 `engine/strategies/learned_rulebook.py::_load_ticker_sentiment(ticker)`와 `_lookup_lagged_news_context(ticker, ...)`가 동일 ticker 자료만 불러온다.

## 의도 명시 차이

Event 경로에는 `impact_score는 S&P500 전체 영향 기준`이라는 명시가 있다. News/NewsTopics 경로에는 ticker별 데이터 소스와 ticker equality 검사 코드가 있다. 두 경로가 다르게 동작하는 근거는 코드에 존재한다.
