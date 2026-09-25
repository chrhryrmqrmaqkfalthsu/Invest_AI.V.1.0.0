# 대시보드 뉴스와 판정 뉴스 경로 추적

## 대시보드 경로

실거래 대시보드의 개별주 뉴스는 `engine/live/real_focus_news_refresh.py`가 만든 별도 상태를 읽는다.

```text
real holdings + live slot candidates
  -> refresh_real_focus_news()
  -> fetch_alpha_vantage_ticker_news_score(ticker)
  -> Alpha Vantage /query
     function=NEWS_SENTIMENT
     tickers=<ticker>
     sort=LATEST
     limit=50
  -> holding_news_sentiment_cache.json
  -> real_dashboard_news_state.json
  -> real_dashboard_api::_real_news_state()
  -> /api/real/news 및 후보/보유 카드 렌더링
```

`real_dashboard_api.py`는 `ticker_sentiment/<TICKER>_daily.csv`를 읽지 않는다. `REAL_NEWS_STATE_PATH = data/_system/real_dashboard_news_state.json`을 직접 읽는다.

## 판정 경로

Stage3 평가용 News/NewsTopics는 다음 경로다.

```text
update_ticker_sentiment_recent.py 별도 실행
  -> Alpha Vantage /query
     function=NEWS_SENTIMENT
     tickers=<ticker>
     time_from/time_to
     limit=1000
  -> ticker_news_cache/<TICKER>/*.json.gz
  -> ticker_sentiment/<TICKER>_daily.csv

central_control::_evaluate_stage3_entity_signal()
  -> provider._lookup_lagged_news_context(ticker, rb, signal_date)
  -> _load_ticker_sentiment(ticker)
  -> ticker_sentiment/<TICKER>_daily.csv
  -> D-1, max-age 7일 lookup
  -> evaluate_signal(news_sentiment, topic_features)
```

## 끊긴 지점

대시보드 refresh는 `holding_news_sentiment_cache.json`과 `real_dashboard_news_state.json`만 갱신한다. 이 경로에는 다음 호출이 없다.

- `aggregate_ticker()`
- `save_csv()`
- `ticker_sentiment/<TICKER>_daily.csv` 쓰기
- `LearnedRuleBook._sentiment_cache` 무효화

반대로 evaluator는 dashboard cache를 읽지 않는다.

따라서 동일 제공자 Alpha Vantage를 쓰지만, **수집 호출·저장 형식·feature 의미·소비자가 완전히 분리된 두 파이프라인**이다.

## 실제 최신성

`real_dashboard_news_state.json`의 마지막 갱신 시각은 `2026-07-08T16:49:46.330058+00:00`이다. 당시 다음 기사들이 실제로 포함됐다.

- ADMA: 최신 기사 2026-07-08 10:11:32 UTC
- CMC: 최신 기사 2026-07-08 13:02:37 UTC
- BMI: 최신 기사 2026-07-07 23:40:44 UTC
- BTBT: 최신 기사 2026-07-07 21:40:55 UTC

따라서 7월 8일 화면의 최신 뉴스는 오래된 CSV를 재표시한 것이 아니라 별도 API 호출에서 받은 실제 최근 기사였다.

단, 현재 보존 파일은 7월 8일 이후 갱신되지 않았다. dashboard cache 정책은 180분이므로 2026-07-11 현재 파일 자체는 stale 상태다.

CE는 7월 8일 16:49:34 UTC에 API fetch가 성공했지만 최근 3일 직접 관련 기사 수가 0이었다. 따라서 CE 카드에 최신 CE 기사 제목이 표시됐다는 증거는 없다.
