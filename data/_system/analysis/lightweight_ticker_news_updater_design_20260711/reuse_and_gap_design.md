# 기존 updater 재사용 범위와 gap 처리

## 재사용 가능 범위

`scripts/news_downloader/update_ticker_sentiment_recent.py`는 positional ticker 인자를 이미 지원한다.

```text
p.add_argument("tickers", nargs="*")
tickers = [t.upper() for t in (args.tickers or DEFAULT_TICKERS)]
```

전체 universe에 종속되지 않는다. `run_full_screening_simple.sh`도 200개씩 positional args로 넘길 뿐이다.

종목당 수행:

```text
NEWS_SENTIMENT API 1회
→ monthly raw cache merge/dedupe
→ aggregate_ticker()
→ data/_system/ticker_sentiment/<TICKER>_daily.csv 저장
```

따라서 후보·보유 union만 만들어 기존 updater에 넘기면 판정용 CSV를 직접 갱신할 수 있다.

## 별도 real-focus updater와 차이

`engine/live/real_focus_news_refresh.py`는 이미 후보·보유 한정 fetch를 구현했지만:

- 후보는 최대 8개만 선택
- `holding_news_sentiment_cache.json`과 `real_dashboard_news_state.json` 갱신
- `ticker_sentiment/<TICKER>_daily.csv`는 갱신하지 않음
- live evaluator의 News/NewsTopics 입력 경로와 분리

따라서 이 경로만 켜도 판정 뉴스는 살아나지 않는다.

## 필요한 얇은 orchestration

신규 핵심 알고리즘은 필요 없다.

```text
candidate_pool tickers
UNION broker.get_holdings tickers
→ dedupe
→ stale/missing 우선 정렬
→ update_ticker_sentiment_recent.py <tickers...>
```

권위 source:

- 후보: `live_slots_state.json::candidate_pool`
- 보유: Alpaca `broker.get_holdings()`

`positions.json`은 broker와 불일치할 수 있으므로 보유 권위값으로 단독 사용하면 안 된다.

## gap 처리

현재 live evaluator는 `lookup_lagged_daily_dict()`를 사용한다.

정책:

```text
D-day signal cutoff = D-1
selected row age > 7 days → {}
missing CSV/row → {}
```

그 결과 신규 후보에 CSV가 없거나 stale이면:

```text
News=0
NewsTopics={}
```

으로 평가가 계속된다. fail-closed/HOLD가 아니다.

권장 설계 순서:

1. 신규 candidate ID/ticker 감지
2. 해당 ticker CSV가 missing/stale이면 fetch queue 최우선
3. fetch 성공 후 candidate 재평가
4. fetch 실패·quota 부족이면 현재 동작을 명시적으로 선택:
   - 현행 유지: News=0으로 평가
   - 더 보수적 설계: news_pending 표시 후 후보 승격 보류

현재 코드와 완전 호환되는 최소안은 News=0 fallback 유지다. 다만 updater 복구 목적상 신규 후보 첫 평가 전에 refresh를 시도하는 편이 학습·라이브 정합성에 더 맞다.

D-1 lag 때문에 당일 여러 번 갱신해도 같은 거래일 신호가 당일 기사까지 사용하지 않는다. 따라서 판정용 CSV 목적에는 매 60초 refresh가 필요하지 않고, 일 1회 + 신규 후보 on-demand가 합리적이다.
