# Event vs News/NewsTopics 경계

## Event
`engine/market/context.py`가 `_fetch_realtime_news(max_articles=100)`로 시장 공통 뉴스 묶음을 가져오고, `engine/market/colab_v32.py`가 각 기사를 S&P500 전체 영향 기준의 11개 Event 유형으로 분류한다. 결과는 ticker별이 아닌 단일 `MarketContext.active_events`에 집계된다. `engine/strategies/learned_rulebook.py`는 active event 이름의 존재 여부만 0/1 flag로 바꾸며 CE ticker와 기사 대상 기업의 일치 여부를 검사하지 않는다.

## News
`engine/strategies/learned_rulebook.py`의 `_load_ticker_sentiment(ticker)`가 ticker별 일별 `sentiment_avg`를 읽는다. evaluator의 `components["news"]`로 들어가며 `use_news_global`이 false이면 0이다.

## NewsTopics
동일 ticker별 sentiment 자료에서 토픽 feature를 만들고, earnings·M&A·life_sciences 등 15개 토픽별 z-score와 CE 룰북의 `weight_news_<topic>`을 곱해 `components["news_topics"]`에 넣는다.

## 경계 판정
개별 종목 뉴스의 정상적인 ticker-aware 경로는 News/NewsTopics다. Event에는 `실적쇼크`라는 기업 사건 유형이 존재하지만 ticker-aware가 아니며 시장 공통 active key로 전역화된다. 따라서 같은 기사가 ticker sentiment feed와 시장 news feed 양쪽에 존재하면 News/NewsTopics와 Event 양쪽에 반영될 구조적 가능성이 있다. 실제 CE 진입 시 중복 여부는 당시 payload가 없어 확인 불가다.
