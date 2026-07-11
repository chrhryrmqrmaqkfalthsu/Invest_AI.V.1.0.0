# ticker sentiment updater 정지 원인 근거

## 최종 원인 판정

`MANUAL_ONLY_NEVER_AUTOMATED`

`update_ticker_sentiment_recent.py`는 2026-06-02에 일회성 full-screening wrapper로 실행됐고, ticker updater 단계 자체는 전 종목을 끝까지 처리했다. 이후 주기 실행을 담당하는 cron, systemd timer, `run_live.py` scheduler 경로는 현재 코드와 Git 이력 모두에서 확인되지 않았다.

## 2026-06-02 실행 상태

확인 파일:

- `logs/full_screening_news.log`
- `data/_system/full_screening_status.json`
- `data/_system/ticker_sentiment_update.log.jsonl`
- `data/_system/ticker_sentiment_update_usage.json`
- `data/_system/ticker_sentiment_update_failures.json`

실행 구간:

- 시작: `2026-06-02T19:25:16+00:00`
- ticker updater 종료: `2026-06-02T21:32:24+00:00`
- 경과: 약 127.13분

처리 결과:

- universe: 6,174종목
- 처리 record: 6,174개
- OK: 5,973개
- `SKIP_UP_TO_DATE`: 48개
- FAIL: 153개
- 마지막 record: `ZZZ`, `FAIL`, 2026-06-02T21:32:24
- API attempt usage count: 6,183회

wrapper 상태는 다음처럼 기록됐다.

```text
news done: fail_batches=30, crash_like_batches=0
```

30개 batch가 rc=1이었던 이유는 각 batch 내부 ticker-level failure가 하나 이상 있었기 때문이다. wrapper는 summary가 있는 rc=1을 crash로 보지 않고 계속 실행하도록 작성돼 있다. 마지막 31번째 batch까지 완료했으므로 ticker updater 자체가 중간 crash로 멈춘 증거는 없다.

153개 실패의 확인된 공통 오류는 주로 다음이다.

```text
RuntimeError: aggregate_ticker returned empty rows; existing CSV preserved
```

이는 종목별 집계 실패이며 전수 실행 중단 원인이 아니다.

## wrapper 전체 종료 상태

뉴스 단계 완료 후 wrapper는 `bulk_swing_diagnostic.py`를 시작했다. `full_screening_status.json`은 `diagnostic started`에서 끝나며 `wrapper done` 기록은 없다.

따라서 full-screening wrapper 전체는 진단 단계에서 중단 또는 미완료였을 가능성이 있다. 그러나 ticker updater 단계는 이미 31개 batch 전체를 처리하고 `news done`으로 기록된 뒤였다. 이 후속 diagnostic 미완료는 6월 2일 이후 뉴스 updater가 재실행되지 않은 원인을 설명하지 않는다.

## 코드 도입 목적

`update_ticker_sentiment_recent.py`의 docstring은 무료 플랜 기본 25 calls/day를 전제로 “운용 후보 종목만 하루 1회 증분 갱신”하는 도구라고 설명한다. 기본 ticker도 6개뿐이다.

6,174종목 전수 실행은 이후 추가된 `scripts/screening/run_full_screening_simple.sh`가 `--daily-limit 10000`과 200개 batch로 일회성 호출한 특수 경로다.

## CRASH 판정이 아닌 이유

- 마지막 ticker까지 처리됨
- `crash_like_batches=0`
- 각 batch summary가 남음
- 뉴스 단계 `done` 기록 존재
- 실패는 ticker-level이며 wrapper가 의도적으로 계속 진행함

따라서 정지 원인을 `CRASH`로 판정할 코드·로그 근거는 없다.
