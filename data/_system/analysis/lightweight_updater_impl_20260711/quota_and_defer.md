# API usage·실패·이월 검증

## 실제 사용량

기존 updater의 권위 usage 파일:

```text
data/_system/ticker_sentiment_update_usage.json
```

실행 전:

```text
count=0
```

실행 후:

```text
count=18
```

시장 API usage:

```text
data/_system/news_cache/_usage.json
current-date count=0
```

무료 정책:

```text
daily_limit=25
market_reserve=2
market_used=0
ticker_used=18
reserve_remaining=2
available=5
```

## 실패 처리

실제 18개 결과:

```text
OK=18
FAILED=0
```

Wrapper는 기존 updater가 append하는:

```text
data/_system/ticker_sentiment_update.log.jsonl
```

의 실행 전 byte offset을 기록하고, 실행 후 추가된 row만 파싱한다.

다음 status는 실패·다음 실행 이월 대상으로 분류한다.

```text
FAILED
ERROR
API_LIMIT
EMPTY_FEED
```

Subprocess 자체가 non-zero이고 ticker별 실패 row가 없으면 선택된 전체 ticker를 이월한다.

## quota 초과 이월

계산식은 기존 updater와 동일하다.

```text
reserve_remaining = max(0, market_reserve - market_used)
available = daily_limit - market_used - ticker_used - reserve_remaining
```

선택 우선순위:

```text
신규 broker holdings
→ 전체 broker holdings
→ 신규 candidates
→ 이전 deferred
→ stale/missing
→ 나머지 candidates
```

`ordered_targets[available:]`는 wrapper state의 `deferred`에 저장돼 다음 실행에서 우선 처리된다.

단위 테스트에서는 ticker usage 21, market usage 0 조건에서:

```text
available=2
A,B selected
C deferred
usage_delta=0 in dry-run
```

을 확인했다.

## 실제 실행 후 상태

```text
known_candidates=18
known_holdings=0
deferred=[]
last_daily_date=2026-07-11
```

Post-fix on-demand dry-run:

```text
target_count=0
usage_before=18
usage_after=18
available=5
deferred=[]
```

## 계측 수정

첫 실제 실행 직후 wrapper가 시장 usage 파일만 읽어 usage 0으로 표시하는 계측 버그를 발견했다.

기존 updater의 실제 구조에 맞춰 다음처럼 수정했다.

```text
market usage: data/_system/news_cache/_usage.json
ticker usage: data/_system/ticker_sentiment_update_usage.json
```

실제 API 호출과 CSV 갱신은 정상적으로 18건 수행됐으며, 권위 ticker usage 파일도 18건을 기록했다.
