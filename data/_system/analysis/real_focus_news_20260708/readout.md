# 실거래 후보/보유 종목 뉴스 갱신 전환 완료

- 생성일: 2026-07-08
- 목적: 개별주 뉴스 API 한도를 페이퍼 후보 선정이 아니라 실거래 후보 슬롯/실계좌 보유 종목에 우선 사용

## 변경 요약

```text
1. paper/next-open candidate_news_guard의 신규 AlphaVantage fetch 기본 차단
2. 실거래 후보 슬롯 + Alpaca live 보유 종목 전용 뉴스 갱신기 추가
3. real_dashboard_news_state.json 갱신
4. /api/real/news에서 실거래 후보 뉴스 확인
5. real-focus 뉴스 데몬 실행
```

## 페이퍼/기존 후보 뉴스 호출 차단

수정 파일:

```text
engine/live/candidate_news_guard.py
```

기존 `scheduled_open_buy_queue.py`는 후보 선정 중 `check_candidate_news_guard()`를 통해 개별주 뉴스를 새로 fetch할 수 있었다.

이번 변경 후 기본값:

```text
CANDIDATE_NEWS_GUARD_ALLOW_FETCH=0 기본
```

효과:

```text
- paper/next-open 후보 선정은 fresh cache만 읽음
- 신규 AlphaVantage ticker news fetch는 하지 않음
- API 한도를 더 이상 paper 후보 뉴스 가드가 쓰지 않음
```

검증:

```text
candidate_news_guard_fetch_enabled() = False
check_candidate_news_guard(... allow_fetch=True, api_key=dummy).source = paper_candidate_news_fetch_disabled
```

## 실거래 전용 뉴스 갱신기

추가 파일:

```text
engine/live/real_focus_news_refresh.py
data/_system/ops/real_focus_news_refresh.py
```

대상 우선순위:

```text
1. Alpaca live 실제 보유 종목
2. /dashboard-real 후보 슬롯 8개
```

현재 실계좌 보유는 0개라 후보 슬롯만 대상이다.

현재 대상 ticker:

```text
BMI, BMA, BTBT, CE, ADMA, ALGT, CAMT
```

ALGT는 stage2/stage3 중복 후보라 뉴스 대상 ticker는 1개로 dedupe된다.

## 실제 갱신 결과

실행 명령:

```text
./venv/bin/python data/_system/ops/real_focus_news_refresh.py refresh --budget 12
```

결과:

```text
ok = true
dry_run = false
selected_count = 7
fetched_count = 7
errors = {}
```

갱신 파일:

```text
data/_system/real_dashboard_news_state.json
data/_system/holding_news_sentiment_cache.json
data/_system/real_focus_news_events.jsonl
```

요약:

| ticker | score | fresh | article_count | source |
|---|---:|---|---:|---|
| ADMA | 0.926794 | true | 7 | alphavantage_real_focus_news |
| ALGT | 0.0 | true | 5 | alphavantage_real_focus_news |
| BMA | 0.481707 | true | 1 | alphavantage_real_focus_news |
| BMI | 0.640588 | true | 9 | alphavantage_real_focus_news |
| BTBT | 0.0 | true | 1 | alphavantage_real_focus_news |
| CAMT | 0.381979 | true | 2 | alphavantage_real_focus_news |
| CE | 0.0 | true | 0 | alphavantage_real_focus_news |

## 데몬 실행

실행 중:

```text
./venv/bin/python data/_system/ops/real_focus_news_refresh.py daemon --interval 1800 --budget 12
```

로그:

```text
logs/real_focus_news_refresh.log
```

최근 로그:

```text
ok=True dry_run=False selected=['BMI', 'BMA', 'BTBT', 'CE', 'ADMA', 'ALGT', 'CAMT'] fetched=0 errors=0
```

`fetched=0`인 이유는 방금 fetch한 캐시가 fresh라서 추가 API 호출을 하지 않았기 때문이다. 기본 캐시 freshness는 180분이다.

## 대시보드 API 확인

확인:

```text
GET /api/real/news
```

결과:

```text
selected_tickers = ['BMI', 'BMA', 'BTBT', 'CE', 'ADMA', 'ALGT', 'CAMT']
dry_run = false
entries = 7
fresh = true
```

## 최종 상태

```text
paper 개별주 뉴스 신규 fetch: OFF
real candidate/holding 뉴스 갱신: ON
real-focus 뉴스 데몬: ON, 30분 주기
실계좌 보유 종목: 현재 0개
실거래 후보 뉴스: 7개 ticker 갱신 완료
```
