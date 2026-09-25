# ticker sentiment updater 마지막 실행 및 자동 경로

## 마지막 실행 흔적

확인된 마지막 전수 updater 실행은 `scripts/screening/run_full_screening_simple.sh`가 호출한 `scripts/news_downloader/update_ticker_sentiment_recent.py`다.

- 시작 로그: `2026-06-02T19:25:16+00:00`
- 마지막 배치 종료: `2026-06-02T21:32:24+00:00`
- 로그 파일 mtime: `2026-06-02T21:32:24.988710715+00:00`
- 상태 파일 mtime: `2026-06-02T21:32:25.099710610+00:00`
- 최종 배치: 31
- 최종 배치 summary: `ok=156 skip=1 fail=17`

CSV 파일 mtime도 6,174개 중 6,021개가 2026-06-02, 153개가 2026-05-31로 이 실행 흔적과 일치한다.

## 자동 실행 경로 재확인

확인한 경로:

- `scripts/run_live.py` scheduler
- `engine/live` scheduler 및 runner
- `config/cron/kingmaker_crontab`
- `config/systemd/user`
- 저장소 내 updater 문자열 참조

확인 결과:

- `run_live.py`에서 `update_ticker_sentiment_recent.py`를 실행하는 job: 없음
- 저장소 cron에서 updater 실행: 없음
- systemd service/timer에서 updater 실행: 없음
- updater를 실제 호출하는 확인된 경로: `scripts/screening/run_full_screening_simple.sh`

cron에는 `scripts/build_sentiment_history.py`가 매일 등록돼 있으나, 이는 개별 ticker daily CSV updater와 다른 경로다.

## 판정

`UPDATER_STOPPED_WIDESPREAD`

CE만 정지한 예외가 아니라 전수 ticker sentiment 갱신이 2026-06-02 이후 중단된 상태다.
