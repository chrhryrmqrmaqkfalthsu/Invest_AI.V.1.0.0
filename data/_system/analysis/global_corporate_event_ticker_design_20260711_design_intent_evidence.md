# 설계 의도 근거 조사

## 확인된 근거

### 1. Event 분류 계약이 시장 전체 기준

`engine/market/colab_v32.py`의 LLM 지시문은 다음을 명시한다.

> impact_score는 S&P500 전체 영향 기준 (-10 ~ +10)

같은 JSON 계약에는 `ticker`, `symbol`, `company` 필드가 없고, 대신 `affected_sectors`만 존재한다. 이는 Event가 종목별 사건 객체가 아니라 시장·섹터 영향 이벤트로 설계됐다는 직접 근거다.

### 2. 원본 수집도 시장 헤드라인

`engine/market/context.py::_fetch_realtime_news()`는 NewsAPI의 `top-headlines`, `country=us`, `category=business`를 호출한다. 특정 ticker를 query로 전달하지 않는다. 반환 자료에서도 title, description, URL, source, publishedAt만 보존한다.

### 3. active_events 스키마가 최초부터 전역형

`engine/market/colab_v32.py::aggregate_events()`의 `active_events[event_type]`에는 match_count, total_impact_score, market_impact, affected_sectors, articles만 있다. ticker 필드는 없다.

Git blame 결과 이 구조와 S&P500 전체 영향 문구, `실적쇼크` 카테고리는 모두 커밋 `f304f9c`에서 함께 도입됐다. 즉, ticker 필드가 후속 변환 과정에서 삭제된 흔적이 아니라 최초 도입 시점부터 시장 공통 구조였다.

### 4. evaluator도 종목별 반응계수 모델

주석은 Event 계수를 다음처럼 설명한다.

> 11개 이벤트 카테고리별 종목 반응 계수 적용

즉 이벤트 자체는 공통이고, 종목 차이는 각 룰북의 `event_response_*` 및 `event_strength_multiplier`로 표현하는 구조다.

## 확인되지 않은 근거

- `실적쇼크`를 왜 시장 공통 Event taxonomy에 넣었는지 별도 설계 문서나 상세 주석: 근거 없음
- 개별 기업 실적 기사 중 어떤 조건에서 S&P500 전체 Event로 인정해야 하는지 threshold 또는 대상 기업 규모 기준: 근거 없음
- 기사 대상 ticker를 추출한 뒤 해당 종목에만 적용하겠다는 미구현 TODO: 근거 없음
- ticker 필터가 실수로 빠졌다고 명시한 커밋 메시지·이슈·문서: 근거 없음

## 해석 범위

코드상 Event가 시장 공통으로 설계됐다는 근거는 충분하다. 다만 모든 `실적쇼크` 기사를 시장 전체 사건으로 취급하는 taxonomy 선택이 타당하다는 별도 근거는 없다. 이는 현재 판정에서 'ticker dropped 버그'와는 구분되는 설계 품질 문제다.
