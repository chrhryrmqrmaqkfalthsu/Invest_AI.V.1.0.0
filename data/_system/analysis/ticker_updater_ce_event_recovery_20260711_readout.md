# ticker updater 정지 범위 + CE Event +4.62 정체 규명

## 최종 판정

### A. ticker sentiment updater

`UPDATER_STOPPED_WIDESPREAD`

- 확인된 마지막 전수 실행: 2026-06-02 19:25:16~21:32:24 UTC
- 전 종목 CSV: 6,174개
- 2026-07-10 기준 7일 max-age 초과: 6,174개(100%)
- 현재 `live_slots_state` 후보 18개: 18개 모두 stale
- `central_buy_candidates` 2개: 2개 모두 stale
- CE 마지막 뉴스 데이터: 2026-05-20
- CE는 예외가 아니라 전수 정지의 한 사례

`run_live.py`, 저장소 cron, systemd에서 `update_ticker_sentiment_recent.py` 자동 실행 경로는 확인되지 않았다. 실제 호출이 확인된 경로는 `scripts/screening/run_full_screening_simple.sh`다.

### B. CE Event +4.62

`UNRECOVERABLE`

7월 7~8일 당시 운영 `market_state.json` 또는 동등한 `active_events` payload는 저장소, 백업, 로그, Git history/object, dashboard export, 후보 raw에서 복원되지 않았다.

Git에는 다음 운영 파일의 history가 없다.

- `data/_system/market_state.json`
- `data/_system/live_slots_state.json`
- `data/_system/central_buy_candidates.json`
- `data/_system/real_dashboard_market_state.json`

7월 7일 파일인 `real_dashboard_market_state.json`도 확인했지만, 실거래 대시보드 격리용 빈 state이며 `active_events={}`다. paper/live market state와 분리 운영이라고 명시돼 있어 CE source가 아니다.

따라서 +4.62260455의 실제 event key, 기사, fresh/decay 구성은 최종적으로 복원 불가다.

## A. updater 정지 범위 상세

전수 파일 mtime 분포:

- 2026-06-02: 6,021개
- 2026-05-31: 153개

마지막 뉴스 데이터 날짜의 최신값도 2026-06-02다. 2026-07-10 기준 모든 파일이 7일을 초과한다.

현재 live 후보 18종목의 age는 38~44일이며 모두 News=0, NewsTopics={} 처리 대상이다. CAKE/WPM도 각각 마지막 뉴스 2026-06-01로 39일 stale다.

## B. +4.62 참고용 가능 조합

저장된 CE 룰북 binary Event 구조에서 다음 조합은 관측값과 수치상 거의 정확히 일치한다.

```text
rate_hike + rate_cut + inflation + fed_statement
× event_strength_multiplier
= 4.6226045428543525
```

관측값 `4.62260455`와의 차이는 약 `7.15e-09`다.

그러나 당시 `event_flags`가 없으므로 이는 **미확정 수학적 가능 조합**이며 실제 정체로 판정하지 않는다.

## 결론

1. 개별 종목 News/NewsTopics는 CE만이 아니라 라이브 후보 전체에서 사실상 비활성화돼 있었다.
2. 그 결과 후보 점수에서 Event 등 다른 축의 상대 비중이 구조적으로 커질 수 있다.
3. CE +4.62는 전역 Event 경로에서 생성됐다는 것까지만 확정된다.
4. 실제 event key와 원본 기사는 스냅샷 미보존으로 `UNRECOVERABLE`이다.
5. 동일 문제를 다시 조사하려면 향후 `market_state.active_events`, key별 event_flags, 기사 URL/publishedAt를 후보 snapshot에 함께 보존해야 한다.

## 산출물

- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_ticker_sentiment_freshness_summary.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_live_candidate_stale.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_updater_execution_evidence.md`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_market_state_recovery.md`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_readout.md`

운영 코드·설정 변경: 0건
