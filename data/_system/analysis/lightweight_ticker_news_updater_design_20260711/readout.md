# 후보·보유 한정 ticker 뉴스 updater 재설계 조사

## 최종 판정

`LIGHTWEIGHT_FEASIBLE`

기존 `update_ticker_sentiment_recent.py`는 이미 임의 ticker 목록을 positional 인자로 받아 종목별 Alpha Vantage 조회·raw 병합·daily CSV 재집계를 수행한다. 전체 6,174종목 스크리닝에 구조적으로 종속되지 않는다.

필요한 것은 신규 sentiment 엔진이 아니라 후보·보유 ticker union을 만드는 얇은 orchestration과 스케줄 정책이다.

## 1. 현재 대상 종목

### 후보

소스:

```text
data/_system/live_slots_state.json::candidate_pool
```

현재:

```text
18개
ACMR ADMA AEIS ALGT ANET ARKW BB BCS BMA BMI BN BNTX BTBT BWXT CBRL CMC CRK CRS
```

`slots` 8개와 `waitlist` 10개는 현재 candidate_pool 18개의 부분집합이다.

### 실계좌 보유

권위 소스:

```text
engine.live.real_focus_news_refresh.collect_real_holding_targets()
→ _get_real_broker()
→ broker.get_holdings()
```

현재 Alpaca holdings:

```text
0개
```

로컬 `positions.json`에는 CAKE/MPLX/WPM 3개가 남아 있지만 broker holdings와 불일치한다. 따라서 updater 대상 보유 목록은 broker를 권위값으로 쓰고 로컬 ledger는 reconciliation 참고로만 사용해야 한다.

현재 dedupe target:

```text
18 candidates + 0 holdings = 18 unique tickers
```

## 2. 기존 updater 재사용

`update_ticker_sentiment_recent.py`:

```python
p.add_argument("tickers", nargs="*")
tickers = [t.upper() for t in (args.tickers or DEFAULT_TICKERS)]
```

종목별 경로:

```text
Alpha Vantage NEWS_SENTIMENT 1회
→ ticker별 monthly raw gzip 병합
→ URL/title dedupe
→ aggregate_ticker()
→ ticker_sentiment/<TICKER>_daily.csv
```

따라서 다음 호출 형태가 이미 가능하다.

```text
update_ticker_sentiment_recent.py ACMR ADMA ... CRS
```

전체 screening wrapper는 이 updater에 200개 positional args를 넘길 뿐이며, updater 자체가 universe 파일을 읽는 구조가 아니다.

### 재사용 가능한 부분

- API 요청
- quota accounting
- overlap/lookback 계산
- raw cache 병합과 dedupe
- 실패 재시도 우선순위
- daily CSV 재집계
- usage/failure/run log

### 새로 필요한 부분

- candidate_pool 읽기
- broker holdings 읽기
- union/dedupe
- missing/stale 우선순위
- 신규 후보 on-demand queue
- daily/periodic scheduler wiring

이는 얇은 wrapper 수준이다.

## 3. 기존 real-focus updater와 차이

이미 다음 경량 경로가 존재한다.

```text
engine/live/real_focus_news_refresh.py
```

하지만 현재:

- 후보 최대 8개
- 보유 우선 + 후보 상위 8개
- `holding_news_sentiment_cache.json` 갱신
- `real_dashboard_news_state.json` 갱신
- 판정용 `ticker_sentiment/<TICKER>_daily.csv` 미갱신

이다.

따라서 이 경로는 dashboard/holding-news용이며, `evaluate_signal`의 News/NewsTopics 입력을 살리는 updater 대체재가 아니다.

## 4. Alpha Vantage 한도 재계산

2026-07-11 공식 페이지 기준:

```text
무료: 최대 25 requests/day
Premium: 75/150/300/600/1200 requests/min
Premium: daily limit 없음
```

프로젝트 updater 기본값:

```text
daily_limit=25
market_reserve=2
request_interval=0.86s
```

다른 당일 사용이 없으면 ticker용 최대 가용량은:

```text
25 - reserve 2 = 23 calls/day
```

### 현재 18개

```text
18 calls
최소 interval 합계 15.48초 + network
무료 일 1회 가능
```

### 후보 18 + 보유 5

```text
23 calls
19.78초 + network
무료 한도 경계
재시도·후보 churn 여유 없음
```

### Premium 75 rpm

```text
18개 theoretical 14.4초
30개 theoretical 24초
```

현재 script interval 0.86초를 유지하면 각각 약 15.48초, 25.8초에 network 시간이 더해진다.

## 5. 매 cycle 반복 가능성

### 무료

정규장 60초마다 18개를 호출할 수 없다.

```text
18 calls × 390 cycles = 7,020 calls/market day
```

무료 25/day와 맞지 않는다.

### Premium 75 rpm

18 calls/min은 entitlement 용량상 가능하지만 권장되지 않는다.

이유:

- live evaluator는 D-1 lag 사용
- 같은 거래일 CSV가 오늘 날짜면 updater가 `SKIP_UP_TO_DATE`
- `--force` 반복은 중복 조회
- 매분 뉴스 반영이 당일 신호에 사용되지 않음

합리적 주기:

```text
무료: 일 1회 + 신규 후보를 남은 quota에서 우선 처리
Premium: 일 1회 또는 30~180분 + 신규 후보 on-demand
```

판정용 D-1 정책만 보면 일 1회로 충분하다.

## 6. 신규 후보 gap 처리

현재 소비 로직:

```text
cutoff = signal date - 1 day
newest row <= cutoff
age > 7 days → empty
missing CSV/row → empty
```

결과:

```text
News=0
NewsTopics={}
평가는 계속 진행
```

현재는 뉴스 누락 시 후보를 HOLD시키는 fail-closed 구조가 아니다.

### 최소 호환 설계

1. candidate_pool ticker diff 감지
2. 신규 ticker의 CSV missing/stale 검사
3. API queue 최우선 배치
4. 성공 후 다음 후보 refresh에서 정상 News 사용
5. quota/오류 시 현행처럼 News=0 유지

### 안정성 우선 설계

신규 후보가 missing/stale이면:

```text
news_pending=true
refresh 성공 전 승격 보류
```

를 둘 수 있으나 이는 현행 동작을 바꾸므로 별도 검증이 필요하다.

현재 학습·라이브 정합성 관점에서는 후보 평가 전에 refresh를 시도하는 편이 더 적절하다. 다만 D-1 lag 때문에 당일 기사까지 즉시 score에 들어가지는 않는다.

## 7. 위험과 주의점

- API attempt마다 usage count가 증가하므로 재시도는 추가 quota를 소모한다.
- 무료 23 target은 reserve 2 기준 사실상 한계라 churn/실패 여유가 없다.
- updater는 `last_csv_date == today`면 skip하므로 일중 반복에 적합하지 않다.
- 보유 목록은 broker와 로컬 ledger 불일치 가능성이 있어 broker 우선이어야 한다.
- Alpha Vantage feed가 비어 `aggregate_ticker()`가 empty면 기존 CSV를 보존하고 실패 처리한다.
- 별도 real-focus cache와 ticker_sentiment CSV를 혼동하면 dashboard 뉴스만 최신이고 판정 뉴스는 stale인 상태가 반복된다.

## 최종 설계 판정

```text
LIGHTWEIGHT_FEASIBLE
```

이유:

- 기존 updater가 ticker list 제한 실행 지원
- 종목당 1 request
- 현재 18개는 무료 일일 한도 안
- 판정용 CSV 재집계까지 기존 코드가 수행
- 필요한 변경은 target collector·scheduler wrapper 중심

단, “매 60초 전체 후보 갱신”은 무료·Premium 모두 설계상 불필요하다. 권장 모델은:

```text
daily incremental refresh
+ new candidate priority refresh
+ broker holdings priority
+ quota-aware defer
```

이다.

운영 코드·설정 변경: 0건

## 산출물

- `target_sources.csv`
- `reuse_and_gap_design.md`
- `api_limit_recalculation.csv`
- `readout.md`
