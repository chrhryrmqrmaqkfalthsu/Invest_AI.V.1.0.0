# 후보·보유 한정 경량 ticker 뉴스 updater 구현

## 최종 상태

- 대상 wrapper 구현 완료
- 후보 source: `live_slots_state.json::candidate_pool`
- 보유 source: broker `get_holdings()` 경로
- `positions.json` 미사용
- 기존 `update_ticker_sentiment_recent.py` 재사용
- 판정용 `ticker_sentiment/<TICKER>_daily.csv` 직접 갱신
- 무료 quota 23회/일 정책 적용
- 초과·실패 ticker 다음 실행 이월
- 하루 1회 + 신규 후보 on-demand cron 설치
- gap 처리: 기존 News=0 fail-open 유지
- 실제 후보 18개 실행: 18/18 성공

## 구현 파일

```text
data/_system/ops/lightweight_ticker_news_updater.py
```

## 대상 수집

### 후보

```text
data/_system/live_slots_state.json
candidate_pool
```

현재 18개 고유 ticker를 수집한다.

### 보유

```text
engine.live.real_focus_news_refresh.collect_real_holding_targets()
→ broker.get_holdings()
```

현재 broker holdings는 0개다.

로컬 `positions.json`은 wrapper source에서 사용하지 않는다.

## 우선순위

```text
신규 broker holdings
→ 전체 broker holdings
→ 신규 candidates
→ 이전 deferred
→ stale/missing CSV
→ 나머지 candidates
```

Ticker는 대문자 normalize 후 중복 제거한다.

## 기존 updater 재사용

실행 명령:

```text
python update_ticker_sentiment_recent.py <selected tickers>
  --daily-limit 25
  --market-reserve 2
  --request-interval 0.86
```

기존 updater가 그대로 담당하는 기능:

- Alpha Vantage `NEWS_SENTIMENT` 호출
- API usage accounting
- 최근 overlap fetch
- raw monthly cache merge/dedupe
- ticker daily aggregation
- `ticker_sentiment/<TICKER>_daily.csv` 원자 갱신
- 실패·API limit 기록

Dashboard/real-focus cache로 대체하지 않는다.

## quota

권위 usage 파일:

```text
market: data/_system/news_cache/_usage.json
ticker: data/_system/ticker_sentiment_update_usage.json
```

계산식:

```text
reserve_remaining=max(0, market_reserve-market_used)
available=daily_limit-market_used-ticker_used-reserve_remaining
```

현재 실제 실행 후:

```text
market_used=0
ticker_used=18
reserve_remaining=2
available=5
```

초과 target은 state의 `deferred`로 저장하고 다음 실행에서 우선 처리한다.

## 신규 후보 on-demand

Wrapper state:

```text
data/_system/lightweight_ticker_news_updater_state.json
```

보존 항목:

- known candidates
- known holdings
- deferred tickers
- last daily date
- last run

`on-demand` 모드는 기존 known candidate 전체를 반복 호출하지 않고 다음만 선택한다.

- 신규 holdings
- current holdings
- 신규 candidates
- deferred

현재 18개를 known으로 저장한 후 post-fix on-demand dry-run은 target 0건이었다.

## 스케줄

Repository cron 정의와 실제 사용자 crontab에 설치했다.

```text
15 10 * * * ... lightweight_ticker_news_updater.py --mode daily
* * * * * ... lightweight_ticker_news_updater.py --mode on-demand
```

두 job은 동일 flock을 사용한다.

```text
/tmp/kingmaker_lightweight_ticker_news.lock
```

따라서 daily와 on-demand가 동시에 updater를 실행하지 않는다.

## gap 처리

신규 후보 뉴스가 missing/stale이면 on-demand refresh를 먼저 시도한다.

실패·quota 부족이면:

```text
deferred에 저장
기존 live evaluator는 News=0, NewsTopics={}로 계속 평가
```

후보 승격을 보류하는 fail-closed 동작은 추가하지 않았다.

따라서 진입 동작은 기존과 같다.

## 실제 1회 실행

실행 시각:

```text
2026-07-11 15:12:01~15:12:28 UTC
```

대상:

```text
candidate=18
holdings=0
selected=18
deferred=0
```

결과:

```text
OK=18
FAIL=0
return_code=0
```

갱신된 판정용 CSV:

```text
ACMR ADMA AEIS ALGT ANET ARKW BB BCS BMA BMI
BN BNTX BTBT BWXT CBRL CMC CRK CRS
```

각 파일의 row 수와 마지막 뉴스 날짜는 `updated_csvs.csv`에 기록했다.

API usage:

```text
before=0
after=18
delta=18
remaining=5
```

## 계측 버그와 수정

첫 실제 실행 직후 wrapper가 시장 usage 파일만 읽어 usage 0으로 표시하는 문제를 발견했다.

기존 updater의 분리 구조에 맞춰 다음을 수정했다.

```text
MARKET_USAGE_PATH
TICKER_USAGE_PATH
```

또한 updater run log 위치를 실제:

```text
data/_system/ticker_sentiment_update.log.jsonl
```

로 수정하고, subprocess 실행 전 byte offset 이후에 append된 ticker row만 읽도록 했다.

실제 API 호출·CSV 갱신 자체는 처음부터 18/18 정상 완료됐고, 권위 ticker usage 파일에 18건이 기록됐다.

Post-fix 검증:

```text
ticker_used=18
available=5
target=0
usage_delta=0
```

## 테스트

```text
PYTHONPATH=. venv/bin/python -m pytest -q tests/test_lightweight_ticker_news_updater.py
```

결과:

```text
7 passed
```

검증 범위:

- 현재 후보 18개 unique 수집
- holdings 우선순위
- 신규 후보 on-demand
- market reserve 포함 quota 계산
- 초과 ticker 이월
- updater log incremental parsing
- 기존 updater 재사용
- `positions.json` 미사용
- fail-open 유지

## 롤백

B 구현 커밋을 revert한다.

```bash
cd ~/kingmaker
git revert <track_B_commit>
git push
```

그 뒤 실제 crontab에서 `KINGMAKER_LIGHTWEIGHT_TICKER_NEWS` block을 제거하거나 이전 repository cron을 다시 설치한다.

## 산출물

- `run_once_summary.json`
- `updated_csvs.csv`
- `quota_and_defer.md`
- `readout.md`

진입 로직 변경: 0건
