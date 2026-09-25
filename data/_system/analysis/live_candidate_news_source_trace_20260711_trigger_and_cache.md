# 라이브 평가와 뉴스 취득 트리거·캐싱

## 1. Event — intraday/central Stage3 경로

호출 흐름:

```text
scripts/run_live.py
  -> Scheduler.add_interval_job(runner.tick_offmarket, default 3600 sec)
  -> Runner.tick_offmarket()
  -> build_market_context(force_refresh=True)
  -> _fetch_realtime_news(max_articles=100)
  -> NewsAPI /v2/top-headlines
  -> _analyze_news_via_colab()
  -> market_state.json 저장

central candidate evaluation
  -> LiveCentralController._evaluate_stage3_entity_signal()
  -> get_market_context()
  -> market_state.json cache 또는 fresh build
  -> ctx.active_events -> event_flags -> evaluate_signal()
```

`build_market_context(force_refresh=False)`는 `cycle.market_context_cache_min=60` 이내의 `market_state.json`을 그대로 반환한다. 후보별로 NewsAPI를 다시 호출하지 않는다. 전역 MarketContext를 한 번 만들고 모든 종목이 공유한다.

장외 scheduler 기본값은 `--offmarket-tick=3600`초이며 이 job은 `force_refresh=True`로 NewsAPI를 다시 호출한다. 후보 평가 중 `get_market_context()`가 캐시 만료를 감지하면 fresh build를 수행할 수도 있다.

## 2. Event 자체의 stale 보존

fresh NewsAPI 기사만 쓰는 것이 아니다. `build_market_context()`는 fresh active events와 직전 `market_state.json`의 active events를 `_merge_active_events_with_decay()`로 합친다.

보존 TTL:

- 금리정책 인상·인하: 10 거래일
- 인플레이션: 10 거래일
- 연준발언: 3 거래일
- 나머지 Event: 기본 5 거래일

따라서 후보 평가 시 Event는 최신 NewsAPI 호출 결과와 이전 active event의 decay 잔존분이 섞일 수 있다. 코드상 stale 가능성은 존재하며 의도된 TTL 보존이다.

GPT 해석도 URL 또는 제목을 key로 영구 cache한다. 다만 cache hit 시 현재 NewsAPI 응답의 article 객체와 과거 interpretation을 결합하므로, 오래된 기사가 현재 top-headlines에 다시 나타나지 않는 한 GPT cache만으로 active event가 재생성되지는 않는다. 오래된 Event의 직접 보존 경로는 `market_state` decay merge다.

## 3. News/NewsTopics — Alpha Vantage 경로

후보 평가 함수는 Alpha Vantage API를 직접 호출하지 않는다.

```text
별도 실행:
  scripts/news_downloader/update_ticker_sentiment_recent.py
  -> https://www.alphavantage.co/query
  -> function=NEWS_SENTIMENT
  -> tickers=<ticker>
  -> time_from/time_to
  -> raw monthly gzip merge
  -> ticker_sentiment/<ticker>_daily.csv 재집계

라이브 평가:
  LearnedRuleBook._lookup_lagged_news_context(ticker, signal_date)
  -> load_csv(ticker)
  -> local daily CSV
  -> D-1 이하 최신 row 조회
  -> max_age_days=7 초과 시 0/empty
```

`run_live.py` scheduler에는 `update_ticker_sentiment_recent.py` 실행 job이 없다. 저장소의 cron에도 이 updater가 등록돼 있지 않다. 현재 확인된 호출은 별도 screening shell 또는 수동 실행 경로다. 따라서 라이브 프로세스 자체는 News/NewsTopics를 최신화하지 않는다.

## 4. next-open 후보 경로

`NextOpenBuyCoordinator._evaluate_entity_signal_point_in_time()`는 주석과 코드로 `get_market_context()`를 호출하지 않는다고 명시한다.

- `get_market_history()`에서 D-1 시장 row 조회
- `use_llm_events=False`
- Event flags는 전부 0
- News/NewsTopics는 local ticker sentiment CSV의 D-1 lagged row

따라서 현재 next-open 후보 선별은 NewsAPI Event를 사용하지 않는다.
