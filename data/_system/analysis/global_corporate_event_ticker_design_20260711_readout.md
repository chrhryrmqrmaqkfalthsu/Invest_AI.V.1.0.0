# 전역 기업 이벤트 ticker 미검증 — 코드 흐름 기반 판정

## 최종 판정

`DESIGN_MACRO_BY_INTENT`

코드 흐름상 ticker 정보가 존재했다가 중간에 버려진 흔적은 없다. Event 경로는 처음부터 특정 종목 뉴스가 아니라 미국 business top-headlines를 시장 공통으로 수집하고, LLM에 `S&P500 전체 영향`을 평가하도록 지시하며, ticker 없는 `active_events[event_type]` 구조로 집계한다. 이후 evaluator는 그 전역 flag와 각 종목 룰북의 반응계수를 곱한다.

따라서 현재 동작은 `BUG_TICKER_DROPPED`가 아니라 **시장 공통 Event에 대해 종목별 반응계수를 학습하는 설계**로 코드에 명시돼 있다.

다만 `실적쇼크`를 이 전역 taxonomy에 포함한 선택 자체가 타당한지, 어떤 기업의 실적이 S&P500 전체 사건으로 인정되는지에 대한 별도 기준·문서·threshold는 없다. 이는 ticker 필터 누락 버그라기보다 **근거가 부족한 taxonomy/설계 품질 문제**다.

## 1. 뉴스 → active_events ticker 보존 추적

### 뉴스 수집

`engine/market/context.py::_fetch_realtime_news()`는 NewsAPI `top-headlines`에 다음 조건만 전달한다.

- `country=us`
- `category=business`
- `pageSize`

특정 ticker query는 없다. 저장 필드는 title, description, URL, source, publishedAt이며 ticker 필드는 없다.

### LLM 분류

`engine/market/colab_v32.py::interpret_news_with_gpt()`의 JSON 계약에는 ticker/company/symbol 필드가 없다. 대신 다음 문구가 직접 명시된다.

> impact_score는 S&P500 전체 영향 기준 (-10 ~ +10)

출력은 event_type, market_impact, impact_score, confidence, timeframe, affected_sectors, reasoning으로 구성된다.

### active_events 집계

`aggregate_events()`는 event_type을 key로 다음 구조를 만든다.

```python
active_events[event_type] = {
    "match_count": 0,
    "total_impact_score": 0,
    "market_impact": ...,
    "affected_sectors": ...,
    "articles": []
}
```

기사 payload에도 title, source, impact_score, confidence, timeframe, weighted_score, reasoning, URL만 있고 ticker는 없다.

### 보존 여부 결론

구조화된 ticker가 원본 Event 입력에 존재하지 않는다. 따라서 변환 중 ticker가 삭제된 것이 아니라 **처음부터 ticker 없는 시장 이벤트 경로**다.

## 2. evaluator 이벤트 적용 분기

`engine/strategies/learned_rulebook.py`는 평가 대상 ticker를 알고 있지만 Event flag 생성에는 사용하지 않는다.

```python
active = getattr(ctx, "active_events", {}) or {}
"has_earnings_shock": int("실적쇼크" in active)
```

`engine/strategies/evaluator.py`는 다음 계산만 수행한다.

```python
event_adj += event_flags.get("has_earnings_shock", 0) * rb.event_response_earnings_shock
event_adj *= rb.event_strength_multiplier
```

이벤트 기사와 평가 종목을 대조하는 조건문은 없다. `실적쇼크`가 active하면 Event 블록을 사용하는 모든 종목은 동일 flag를 받고, 종목별 차이는 룰북 계수로만 발생한다.

## 3. News/NewsTopics와의 대조

News/NewsTopics 원본인 Alpha Vantage feed에는 `ticker_sentiment[]`가 있다.

`engine/market/ticker_sentiment.py::extract_ticker_data()`는 다음 equality 검사를 수행한다.

```python
for ts in item.get("ticker_sentiment", []):
    if ts.get("ticker") == ticker:
        ...
```

이후 `_load_ticker_sentiment(ticker)`와 `_lookup_lagged_news_context(ticker, ...)`가 해당 ticker의 일별 sentiment와 topic feature만 읽는다.

따라서 차이는 명확하다.

- Event: 시장 헤드라인 → S&P500 영향 → 전역 active key → 종목별 반응계수
- News/NewsTopics: 기사 내부 ticker 일치 검사 → 해당 ticker 자료만 집계·조회

## 4. 설계 의도 근거

확인된 직접 근거:

1. LLM 지시문의 `S&P500 전체 영향 기준`
2. ticker query 없는 미국 business top-headlines 수집
3. ticker 필드가 없는 `active_events` 스키마
4. evaluator 주석 `11개 이벤트 카테고리별 종목 반응 계수 적용`
5. Git 이력상 `실적쇼크`, S&P500 기준, ticker 없는 active_events 구조가 커밋 `f304f9c`에서 함께 도입됨

확인되지 않은 근거:

- `실적쇼크`를 시장 공통 taxonomy에 포함한 이유를 설명하는 상세 문서: 근거 없음
- 시장 전체 실적쇼크로 인정할 기업 규모·지수 비중·영향 threshold: 근거 없음
- ticker 필터를 향후 추가하려 했다는 TODO 또는 이슈: 근거 없음
- ticker가 존재했지만 실수로 삭제됐다는 Git 흔적: 근거 없음

## 판정 선택 이유

### `BUG_TICKER_DROPPED`가 아닌 이유

Event 원본과 LLM 출력 계약에 구조화된 ticker가 없다. 따라서 '있던 ticker를 변환 중 버렸다'는 조건이 성립하지 않는다.

### `AMBIGUOUS_NO_BASIS`가 아닌 이유

Event를 S&P500 전체 영향으로 평가하고 전역 자료구조에 넣는다는 명시적 코드 근거가 있다. ticker 필터 부재만 있고 의도 근거가 없는 상태가 아니다.

### 최종

`DESIGN_MACRO_BY_INTENT`

현재 동작은 코드상 의도된 시장 공통 Event 설계다. 다만 기업 단일 사건인 `실적쇼크`를 매크로 taxonomy에 넣은 설계는 별도 정당화 기준이 없으므로, 운영 안정성 관점에서는 taxonomy 분리 또는 시장 전체 영향 threshold를 재설계할 필요가 있다.

## 산출물

- `data/_system/analysis/global_corporate_event_ticker_design_20260711_news_to_active_events_trace.csv`
- `data/_system/analysis/global_corporate_event_ticker_design_20260711_event_vs_news_paths.md`
- `data/_system/analysis/global_corporate_event_ticker_design_20260711_design_intent_evidence.md`
- `data/_system/analysis/global_corporate_event_ticker_design_20260711_readout.md`

운영 코드·설정 변경: 0건
