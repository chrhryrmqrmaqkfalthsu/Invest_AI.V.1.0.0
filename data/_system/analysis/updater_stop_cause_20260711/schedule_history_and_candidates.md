# 과거 자동 실행 흔적 및 스케줄 후보

## Git 이력

확인된 도입 커밋:

- `1f6cd1d` — 2026-06-02 10:44 UTC — `AV 티커 감성 최신화 스크립트 추가: 무료 한도와 증분 병합 지원`
- `dbf6ac1` — 2026-06-02 19:25 UTC — `6174종목 뉴스 최신화 후 bulk diagnostic 실행 래퍼 추가`

다음 경로의 전체 Git 이력에서 updater 등록 또는 제거 흔적을 검색했다.

- `config/cron/kingmaker_crontab`
- `scripts/run_live.py`
- `config/systemd`
- `engine/live`
- `scripts`

검색 문자열:

- `update_ticker_sentiment_recent.py`
- `ticker_sentiment`
- `run_full_screening_simple.sh`

결과:

- updater 자체 도입 커밋: 있음
- 일회성 full-screening wrapper 호출: 있음
- cron 등록 이력: 없음
- systemd timer 등록 이력: 없음
- `run_live.py` scheduler 등록 이력: 없음
- 등록됐다가 제거된 커밋: 근거 없음

따라서 `REMOVED_FROM_SCHEDULE`가 아니라 `MANUAL_ONLY_NEVER_AUTOMATED`가 코드·Git 이력에 맞는다.

## 현재 자동 경로와의 구분

현재 cron에는 `scripts/build_sentiment_history.py`가 매일 등록돼 있지만, 이는 `ticker_sentiment/<TICKER>_daily.csv`를 갱신하는 updater가 아니다.

`run_live.py`에는 보유 종목의 최신 부정 뉴스 위험도를 갱신하는 `holding_news_queue`가 있지만 저장 대상은 `holding_news_sentiment_cache.json`이며, 평가용 ticker CSV와 분리돼 있다.

## 자동 실행 후보 — 설계 제안만

### 1순위: 별도 systemd oneshot + timer

권고 이유:

- 거래 프로세스와 API 지연·실패를 격리할 수 있음
- `flock` 또는 systemd 중복 실행 방지 가능
- exit status, 재시도, 로그 보존, timeout 관리가 명확함
- 활성 universe와 backfill job을 별도 unit으로 분리하기 쉬움

권고 역할 분리:

- 일일 증분: 실제 라이브 후보·보유·활성 룰북 ticker 우선
- 전수 backfill: 별도 수동 또는 저우선순위 batch

### 2순위: post-market cron

장점은 단순성이다. 다만 다음 보호가 필요하다.

- `flock` 중복 실행 방지
- provider quota 확인
- 실행 대상 ticker 목록 고정 또는 snapshot
- 종료 상태와 freshness 검증
- 실패 ticker 재시도 queue

### 비권고: `run_live.py` 시장 tick 내부

6,174개 외부 API 호출을 거래 프로세스 안에 넣으면 API latency, rate limit, timeout이 후보 평가·주문 루프와 결합된다. 현재 보유 뉴스 refresh처럼 소수 종목만 다루는 별도 경량 작업이 아니라면 live scheduler 직접 삽입은 안정성상 부적절하다.

## 운영 범위 권고

전 종목 6,174개를 매일 호출하는 것과 라이브 판정에 필요한 종목을 매일 갱신하는 것은 다른 문제다.

안정성·비용 우선 순서는 다음이 적절하다.

1. 현재 live candidate pool, 보유 종목, 실제 활성 Stage3 룰북 ticker
2. 다음 세션 후보 universe
3. 전체 6,174개는 주기적 저우선순위 또는 연구용 backfill

이는 구현 지시가 아니라 자동 실행 지점을 선택할 때의 코드 구조 기반 설계 요건이다.
