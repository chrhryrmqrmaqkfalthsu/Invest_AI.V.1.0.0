# 조사 2 — 대시보드 뉴스 vs 판정 뉴스 경로

## 최종 판정

`SEPARATE_LIVE_FEED`

대시보드 뉴스와 평가용 News/NewsTopics는 같은 Alpha Vantage 제공자를 사용하지만, 동일 데이터 파이프라인이 아니다.

대시보드는 후보·보유 종목 일부를 대상으로 `NEWS_SENTIMENT`를 별도 실시간 호출해 `holding_news_sentiment_cache.json`과 `real_dashboard_news_state.json`에 저장한다. evaluator는 이 파일을 읽지 않고, 6월 2일 이후 정지된 `ticker_sentiment/<TICKER>_daily.csv`만 읽는다.

따라서 7월 8일 대시보드에 최신 뉴스가 보였던 것과 판정 News/NewsTopics가 0이었던 것은 서로 모순이 아니다.

## 1. 대시보드 뉴스 소스

코드 경로:

- `engine/live/real_focus_news_refresh.py::refresh_real_focus_news()`
- `engine/live/holding_news_queue.py::fetch_alpha_vantage_ticker_news_score()`
- `engine/live/real_dashboard_api.py::_real_news_state()`

API 호출:

```text
GET https://www.alphavantage.co/query
function=NEWS_SENTIMENT
tickers=<ticker>
sort=LATEST
limit=50
apikey=<ALPHA_VANTAGE_KEY>
```

대상은 실거래 보유 종목과 `live_slots_state.json`의 상위 후보이며, 기본 budget은 12개다. 결과는 최근 3일 직접 관련 기사의 negative risk score와 최대 2개 기사 제목으로 가공된다.

저장 경로:

```text
data/_system/holding_news_sentiment_cache.json
data/_system/real_dashboard_news_state.json
```

대시보드는 `/api/real/news`와 후보·보유 카드에서 이 상태를 표시한다.

## 2. 판정용 News/NewsTopics 소스

코드 경로:

- `scripts/news_downloader/update_ticker_sentiment_recent.py`
- `engine/market/ticker_sentiment.py`
- `engine/strategies/learned_rulebook.py::_load_ticker_sentiment()`
- `_lookup_lagged_news_context()`
- `engine/live/central_control.py::_evaluate_stage3_entity_signal()`

평가 시 읽는 파일:

```text
data/_system/ticker_sentiment/<TICKER>_daily.csv
```

이 파일의 D-1 이하 최신 row를 조회하며, 7일을 초과하면 News=0, NewsTopics={}로 처리한다.

## 3. 같은 소스인지

제공자와 API 함수는 같다.

- 제공자: Alpha Vantage
- 함수: `NEWS_SENTIMENT`

그러나 호출 계약과 저장 구조는 다르다.

| 항목 | 대시보드 | 평가 |
|---|---|---|
| 호출 시점 | focus refresh 실행 시 | 평가 중 호출 안 함 |
| 종목 범위 | 보유+상위 후보 일부 | bulk updater 대상 전 종목 |
| 정렬 | `sort=LATEST` | 날짜 구간 `time_from/time_to` |
| limit | 50 | 1000 |
| 저장 | holding/dashboard JSON | raw gzip + daily CSV |
| feature | 최근 부정기사 위험점수 | 일별 평균 sentiment·topic z-score |
| 소비자 | 대시보드 | evaluate_signal |

## 4. 실제 최신성

`real_dashboard_news_state.json`은 2026년 7월 8일 16:49 UTC에 갱신됐다.

당시 실제 기사 날짜:

- ADMA: 2026-07-08 10:11 UTC
- CMC: 2026-07-08 13:02 UTC
- BMI: 2026-07-07 23:40 UTC
- BTBT: 2026-07-07 21:40 UTC
- ALGT: 2026-07-07 18:41 UTC

따라서 당시 대시보드는 6월 CSV를 화면에 재사용한 것이 아니라, 별도 live API 호출에서 받은 실제 최근 기사를 표시했다.

CE는 같은 시각 API fetch가 있었지만 최근 직접 관련 기사 수는 0이었다. CE에 최신 CE 뉴스가 표시됐다는 근거는 없다.

현재 보존된 dashboard state는 7월 8일 이후 갱신되지 않았다. cache_max_minutes가 180분이므로 2026년 7월 11일 현재 이 파일은 stale이다. 이는 7월 8일 당시 데이터가 최신이었다는 사실과 별개다.

## 5. 연결이 끊긴 지점

대시보드 refresh는 다음 파일만 갱신한다.

```text
holding_news_sentiment_cache.json
real_dashboard_news_state.json
```

이 코드에는 다음이 없다.

- `aggregate_ticker()` 호출
- `save_csv()` 호출
- `ticker_sentiment/<TICKER>_daily.csv` 갱신
- evaluator가 읽는 sentiment cache 무효화

반대로 evaluator는 dashboard JSON을 읽지 않는다.

즉 inbound 뉴스는 들어왔지만 평가 파이프라인으로 연결되지 않는다. 다만 두 데이터의 score 의미와 시간 집계 방식도 다르므로 단순 파일 복사로 연결할 수 있는 구조는 아니다.

## 결론

판정은 `SEPARATE_LIVE_FEED`다.

대시보드에는 별도 실시간 개별주 뉴스 feed가 존재했고, 7월 8일 당시 실제 최신 기사를 수신했다. 그러나 이 feed는 표시·보유위험 감시용이며 Stage3 score의 News/NewsTopics 입력과 완전히 분리돼 있다. 평가용 CSV updater 정지가 지속돼도 대시보드 뉴스는 독립적으로 최신일 수 있다.

## 산출물

- `data/_system/analysis/dashboard_vs_eval_news_20260711/path_comparison.csv`
- `data/_system/analysis/dashboard_vs_eval_news_20260711/dashboard_freshness_snapshot.csv`
- `data/_system/analysis/dashboard_vs_eval_news_20260711/source_trace.md`
- `data/_system/analysis/dashboard_vs_eval_news_20260711/readout.md`

운영 코드·설정·재학습 변경: 0건
