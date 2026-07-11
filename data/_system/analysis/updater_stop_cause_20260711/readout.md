# 조사 3 — ticker updater 정지 원인 + 복구 요건

## 최종 판정

### 정지 원인

`MANUAL_ONLY_NEVER_AUTOMATED`

### 복구 난이도

- 6,174종목 전수, free plan: `VERY_HIGH`
- premium plan과 날짜 구간 분할·검증 사용: `MEDIUM`
- 현재 라이브 후보·보유 종목 우선 복구: `LOW`

## 1. 6월 2일 실행은 crash였는가

아니다.

`run_full_screening_simple.sh`의 뉴스 updater 단계는 2026년 6월 2일 19:25:16 UTC에 시작해 21:32:24 UTC에 마지막 종목까지 처리했다.

결과:

| 항목 | 값 |
|---|---:|
| universe | 6,174 |
| 처리 record | 6,174 |
| OK | 5,973 |
| SKIP_UP_TO_DATE | 48 |
| FAIL | 153 |
| API attempt | 6,183 |
| 경과시간 | 127.13분 |
| 실효 처리율 | 48.56종목/분 |

wrapper 상태에는 다음이 남아 있다.

```text
news done: fail_batches=30, crash_like_batches=0
```

30개 batch의 exit code 1은 ticker-level failure가 포함됐기 때문이다. wrapper는 batch summary가 존재하면 crash가 아닌 부분 실패로 분류하고 다음 batch를 계속 실행한다. 실제로 31번째 batch의 마지막 `ZZZ`까지 처리됐다.

153개 실패는 주로 다음 오류였다.

```text
RuntimeError: aggregate_ticker returned empty rows; existing CSV preserved
```

따라서 updater 자체가 crash해 6월 2일에 멈춘 것은 아니다.

뉴스 단계 이후 시작된 bulk diagnostic은 완료 기록이 없다. full-screening wrapper 전체는 diagnostic 단계에서 중단됐을 수 있지만, ticker updater 단계는 이미 완료된 뒤였다.

## 2. 왜 이후 다시 실행되지 않았는가

주기 실행 경로가 없기 때문이다.

Git 이력:

- `1f6cd1d`: updater 스크립트 최초 추가
- `dbf6ac1`: 6,174종목 일회성 full-screening wrapper 추가

다음 위치의 현재 코드와 전체 Git 이력을 확인했다.

- `config/cron/kingmaker_crontab`
- `scripts/run_live.py`
- `config/systemd`
- `engine/live`
- 관련 shell과 scheduler

확인 결과:

- cron 등록: 없음
- systemd timer 등록: 없음
- `run_live.py` scheduler 등록: 없음
- 과거 등록 후 제거된 이력: 없음
- 확인된 실행 경로: 일회성 `run_full_screening_simple.sh`

따라서 판정은 `REMOVED_FROM_SCHEDULE`가 아니라 `MANUAL_ONLY_NEVER_AUTOMATED`다.

## 3. 자동 실행 지점 후보

### 1순위: 별도 systemd oneshot + timer

거래 프로세스와 API 지연·rate limit·실패를 격리할 수 있고, 중복 실행 방지·timeout·재시도·로그 관리가 가장 명확하다.

### 2순위: post-market cron

단순하지만 최소한 다음 보호가 필요하다.

- `flock`
- quota 확인
- 대상 universe snapshot
- freshness 사후 검증
- 실패 ticker retry queue

### 비권고: `run_live.py` 시장 tick 내부

6,174개 API 호출을 주문·후보 평가 프로세스에 결합하면 latency와 provider 장애가 거래 루프에 전파된다. 소수 활성 ticker 증분 갱신이 아닌 전수 작업은 별도 프로세스로 분리하는 편이 안정적이다.

이번 조사에서는 설계 후보만 식별했으며 구현·설정 변경은 하지 않았다.

## 4. 밀린 데이터 복구 요건

현재 updater 기본 설정은 `--lookback-days 7`이다. 그대로 재실행하면 최근 7일만 가져오므로 6월 2일 이후 약 40일 gap을 완전히 채우지 못한다.

복구에는 다음이 필요하다.

1. gap 전체를 포함하는 lookback 또는 명시적 날짜 window
2. 구간 경계 overlap과 URL dedupe
3. ticker별 raw cache 및 daily CSV 재집계 검증
4. 실패 153종목 별도 처리
5. `limit=1000` 도달 ticker의 날짜 window 분할

Alpha Vantage NEWS_SENTIMENT는 `time_from/time_to`를 지원하며 결과 상한은 1,000건이다. 40일 동안 1,000건을 넘는 ticker가 있는지는 API를 호출하지 않았으므로 확인 불가다.

## 5. API 부하

한 ticker당 한 번 성공 요청이라는 최소 가정에서 전수 복구는 6,174 calls가 필요하다.

Alpha Vantage 공식 free limit인 25 requests/day 기준 이론상 최소 **247일**이다. 현재 key의 실제 plan은 `.env`를 읽지 않았으므로 확인 불가다.

공식 premium plan의 이론 최소시간:

| plan | provider 기준 최소시간 |
|---:|---:|
| 75 requests/min | 82.32분 |
| 150 requests/min | 41.16분 |
| 300 requests/min | 20.58분 |
| 600 requests/min | 10.29분 |
| 1200 requests/min | 5.15분 |

다만 현재 updater의 `request_interval=0.86초`는 network latency를 제외해도 약 88.49분의 하한을 만든다. 실제 6월 2일 실행은 127.13분이었다.

따라서 높은 premium plan을 사용하더라도 현재 pacing을 그대로 두면 provider rate가 아닌 script interval이 병목이다.

## 결론

1. 6월 2일 ticker updater는 전 종목을 끝까지 처리했으며 crash가 아니다.
2. 이후 중단은 자동화가 제거됐기 때문이 아니라 처음부터 정기 스케줄에 등록되지 않았기 때문이다.
3. full universe 복구는 free plan에서 현실적으로 매우 어렵다.
4. 우선순위는 현재 라이브 후보·보유·활성 룰북 ticker의 신선도 복구다.
5. 전수 backfill은 거래 프로세스와 분리된 systemd timer/oneshot 또는 post-market batch가 적합하다.
6. 기본 7일 lookback과 1,000건 응답 상한 때문에 단순 재실행만으로는 40일 gap 완전 복구를 보장할 수 없다.

## 산출물

- `data/_system/analysis/updater_stop_cause_20260711/stop_cause_evidence.md`
- `data/_system/analysis/updater_stop_cause_20260711/schedule_history_and_candidates.md`
- `data/_system/analysis/updater_stop_cause_20260711/backfill_load_estimate.csv`
- `data/_system/analysis/updater_stop_cause_20260711/recovery_requirements.md`
- `data/_system/analysis/updater_stop_cause_20260711/readout.md`

운영 코드·설정·재학습 변경: 0건
