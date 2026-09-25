# 라이브 후보 개체의 뉴스 소스 취득 경로 추적

## 최종 결론

라이브 후보의 뉴스 입력은 하나의 경로가 아니라 **평가 모드에 따라 두 갈래**다.

1. **intraday/live-slot central Stage3 후보**
   - Event: NewsAPI `top-headlines` 기반 전역 `market_state.json`
   - News/NewsTopics: Alpha Vantage `NEWS_SENTIMENT`를 별도 다운로드해 만든 로컬 ticker CSV
2. **next-open D-1 후보**
   - Event: 사용 안 함 (`use_llm_events=False`, flags 전부 0)
   - News/NewsTopics: 동일한 로컬 Alpha Vantage ticker CSV

CE의 Event `+4.62260455`는 현재 next-open 경로에서는 나올 수 없다. 보존 source와 코드 흐름상 CE는 intraday/live-slot central Stage3 평가의 `market_state.active_events`를 사용한 후보로 정합한다.

## 1. 실제 뉴스 제공자와 호출

### Event

제공자: **NewsAPI**

엔드포인트:

```text
https://newsapi.org/v2/top-headlines
```

파라미터:

```text
country=us
category=business
pageSize=100
apiKey=<NEWSAPI_KEY>
```

코드:

- `engine/market/context.py::_fetch_realtime_news()`
- `requests.get(..., timeout=15)`

가져온 title, description, URL, source, publishedAt를 keyword filter와 GPT 해석에 넘긴다.

### News/NewsTopics

제공자: **Alpha Vantage**

엔드포인트:

```text
https://www.alphavantage.co/query
```

파라미터:

```text
function=NEWS_SENTIMENT
tickers=<ticker>
time_from=<range start>
time_to=<range end>
limit=1000
apikey=<ALPHA_VANTAGE_KEY>
```

코드:

- `scripts/news_downloader/update_ticker_sentiment_recent.py::_fetch_range()`
- `engine/market/ticker_sentiment.py::aggregate_ticker()`

두 경로는 같은 제공자가 아니다.

## 2. 라이브 후보 평가와 호출 관계

### intraday/live-slot central Stage3

`run_live.py`의 시장 시간 job은 기본 60초마다 후보를 평가한다. 그러나 후보마다 NewsAPI를 부르지는 않는다.

Event 취득:

```text
Runner.tick_offmarket() [기본 3600초]
  -> build_market_context(force_refresh=True)
  -> NewsAPI 호출
  -> market_state.json 저장

후보 평가
  -> LiveCentralController._evaluate_stage3_entity_signal()
  -> get_market_context()
  -> 60분 cache 재사용 또는 만료 시 fresh build
```

즉 NewsAPI Event는 전역으로 한 번 받아 모든 후보가 공유한다.

News/NewsTopics 취득:

```text
별도 updater 실행
  -> Alpha Vantage API
  -> ticker_news_cache/<ticker>/*.json.gz
  -> ticker_sentiment/<ticker>_daily.csv

후보 평가
  -> local CSV만 읽음
```

라이브 후보 평가 순간 Alpha Vantage API를 호출하지 않는다.

### next-open D-1

`NextOpenBuyCoordinator.prepare_if_due()`는 매분 실행되며, 기본 설정으로 애프터마켓 이후 60분마다 draft를 재선별하고 개장 10분 전 final queue를 만든다.

이 경로는 `get_market_context()`를 의도적으로 호출하지 않는다. `market_history.csv`에서 D-1 값을 조회하며 `use_llm_events=False`로 Event flag를 전부 0으로 둔다.

## 3. 신선도와 stale 가능성

### Event

`market_state.json` cache TTL은 60분이다. 장외 refresh job 기본 주기도 3600초다.

그러나 fresh NewsAPI 결과만 쓰지 않는다. 직전 active events를 유형별 TTL로 합친다.

- 금리 인상·인하: 10 거래일
- 인플레이션: 10 거래일
- 연준발언: 3 거래일
- 나머지: 5 거래일

따라서 후보의 Event는 **최신 NewsAPI headline + 과거 market_state에서 decay 중인 Event**의 혼합일 수 있다. 코드 경로상 며칠 전 Event가 후보 점수에 남는 것은 확정된다.

다만 CE +4.62의 정확한 기사와 fresh/decay 구성은 7월 7~8일 `market_state.json`이 보존되지 않아 확인 불가다.

### News/NewsTopics

평가 시 실시간 API 호출이 없다. 별도 updater가 로컬 파일을 갱신해야 한다.

`run_live.py` scheduler와 저장소 cron에서 `update_ticker_sentiment_recent.py` 자동 실행은 확인되지 않았다. 현재 확인된 호출 경로는 별도 screening shell 또는 수동 실행이다.

소비 시 정책:

- D-1 이하 row만 사용
- 최대 age 7일
- 7일 초과 시 News=0, NewsTopics={}

따라서 오래된 CSV가 잘못 계속 점수화되기보다는, 갱신 중단 시 뉴스 축이 0으로 사라지는 구조다.

## 4. CE 기준

CE ticker sentiment 파일:

- 파일: `data/_system/ticker_sentiment/CE_daily.csv`
- 파일 mtime: `2026-06-02T19:44:06Z`
- 마지막 데이터 날짜: `2026-05-20`

CE 후보 시점은 7월 7~8일이므로 7일 max-age를 크게 초과한다. 따라서 News=0, NewsTopics=0은 소스 경로상 설명된다.

CE Event는 당시 전역 `market_state.active_events`에서 왔다. current next-open 경로는 Event를 0으로 두므로, +4.62 후보는 next-open 평가가 아니라 intraday/live-slot central Stage3 평가 결과와 일치한다.

확인 불가:

- 7월 7~8일 NewsAPI 원본 기사 목록
- 당시 `market_state.active_events`의 key별 구성
- 각 Event가 fresh headline인지 이전 state의 decay 잔존인지
- +4.62를 만든 정확한 기사 발생시각

## 핵심 판정

- **Event는 후보마다 실시간 호출되지 않는다.** 전역 cache를 공유한다.
- **Event에는 며칠간의 TTL 잔존이 명시적으로 존재한다.** stale Event 진입 가능성은 코드상 확정이다.
- **News/NewsTopics는 라이브 평가 중 API를 호출하지 않는다.** 별도 갱신된 로컬 Alpha Vantage CSV만 사용한다.
- **CE News 소스는 실제로 오래됐고 max-age에 걸려 0이 됐다.**
- **CE +4.62의 정확한 원본 기사는 payload 부재로 확인 불가지만, 해당 값은 전역 market_state Event 경로에서 왔다.**

## 산출물

- `data/_system/analysis/live_candidate_news_source_trace_20260711_source_matrix.csv`
- `data/_system/analysis/live_candidate_news_source_trace_20260711_trigger_and_cache.md`
- `data/_system/analysis/live_candidate_news_source_trace_20260711_entity_flow.md`
- `data/_system/analysis/live_candidate_news_source_trace_20260711_readout.md`

운영 코드·설정 변경: 0건
