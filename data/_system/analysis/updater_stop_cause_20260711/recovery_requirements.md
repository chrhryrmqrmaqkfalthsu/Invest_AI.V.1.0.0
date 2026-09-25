# 밀린 데이터 복구 요건과 부하

## 최소 호출량

현재 저장소 universe 6,174종목을 한 번씩 조회하면 정상 성공 기준 최소 6,174 API calls가 필요하다.

2026-06-02 실제 실행에서는:

- ticker: 6,174개
- API attempt: 6,183회
- 추가 attempt: 9회
- 경과시간: 127.13분
- 실효 처리율: 약 48.56종목/분

재시도, rate-limit notice, network error가 있으면 호출량은 최소치보다 늘어난다.

## Alpha Vantage 공식 제한

2026-07-11 확인 기준 공식 Alpha Vantage Support는 free service를 최대 25 requests/day로 설명한다.

공식 Premium 페이지는 다음 plan을 제시한다.

- 75 requests/min
- 150 requests/min
- 300 requests/min
- 600 requests/min
- 1200 requests/min
- premium은 daily limit 없음

현재 프로젝트 API key의 실제 entitlement는 `.env`를 읽거나 변경하지 않았으므로 확인 불가다. 6월 2일 wrapper의 `--daily-limit 10000`은 로컬 budget guard override일 뿐 provider 계약을 증명하지 않는다.

## 40일 gap 복구 시 날짜 범위

updater 기본값은 다음이다.

```text
--lookback-days 7
--overlap-days 1
```

따라서 현재 그대로 다시 실행하면 최근 7일만 요청하고, 6월 2일 이후 약 40일 gap 전체를 채우지 못한다.

복구에는 최소한 다음 중 하나가 필요하다.

1. `--lookback-days`를 gap 전체보다 크게 지정
2. 명시적 날짜 window를 지원하는 별도 실행 계획
3. 여러 구간으로 나눠 호출하고 각 구간 coverage를 검증

구현은 이번 조사 범위가 아니며 수행하지 않았다.

## 1,000건 응답 상한

현재 updater는 `limit=1000`을 사용한다. Alpha Vantage 공식 NEWS_SENTIMENT 문서는 `time_from/time_to` 범위 지정과 최대 1,000개 결과를 지원한다고 명시한다.

40일 구간에서 특정 대형 ticker의 기사 수가 1,000개를 넘으면 최신 1,000개만 받을 가능성이 있다. 현재 코드는 다음 기능이 없다.

- pagination
- `feed_count == 1000` 감지 후 자동 구간 분할
- coverage 시작/종료 검증

실제로 어느 ticker가 1,000건을 초과하는지는 API를 호출하지 않았으므로 확인 불가다. 안전한 backfill 요건은 짧은 날짜 window 분할과 경계 중복·URL dedupe다.

## 무료 플랜 비용

최소 6,174 calls를 25 calls/day로 처리하면 이론상 최소 247일이다. 다른 Alpha Vantage 작업이 같은 key를 쓰면 더 오래 걸린다.

현재 기본 reserve는 시장 뉴스용 2 calls이므로 이를 유지하면 ticker용 실질 daily capacity는 더 작아질 수 있다.

따라서 free tier에서 전수 6,174종목 즉시 복구는 현실적으로 어렵다.

## premium 이론 시간과 현재 스크립트 한계

provider rate만 적용한 이론 최소시간:

- 75/min: 82.32분
- 150/min: 41.16분
- 300/min: 20.58분
- 600/min: 10.29분
- 1200/min: 5.15분

하지만 현재 script의 기본 `request_interval=0.86초`는 network latency를 제외해도 약 88.49분의 sleep floor를 만든다. 따라서 150/min 이상 plan에서도 현재 코드 그대로라면 provider rate보다 script pacing이 병목이다.

6월 2일 관측 실행은 127.13분이었다.

## 실패 종목 처리

6월 2일 실행에서 153종목이 `aggregate_ticker returned empty rows`로 실패했다. 기존 CSV는 보존됐다.

전수 복구 완료 조건은 단순 process exit가 아니라 다음 검증을 포함해야 한다.

- ticker별 API attempt 여부
- raw cache의 update timestamp
- daily CSV 재집계 성공 여부
- 마지막 coverage 날짜
- 1,000건 상한 도달 여부
- 실패 ticker 별도 retry/제외 판정

## 복구 난이도

- free plan + 6,174종목 전수: `VERY_HIGH`
- premium + 날짜 window 분할 + 검증: `MEDIUM`
- 현재 live 후보·보유 종목만 우선 복구: `LOW`

가장 빠른 안정화 경로는 라이브 평가에 실제 필요한 ticker를 우선 복구하고, 전수 universe는 별도 저우선순위 backfill로 분리하는 것이다.
