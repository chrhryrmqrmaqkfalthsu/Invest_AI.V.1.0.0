# ticker updater 정지 범위 + CE Event +4.62 정체 규명

## 최종 판정

### A. ticker sentiment updater

`UPDATER_STOPPED_WIDESPREAD`

- 확인된 마지막 전수 실행: `2026-06-02T19:25:16Z`~`2026-06-02T21:32:24Z`
- 전 종목 CSV: 6,174개
- 2026-07-10 기준 7일 max-age 초과 또는 날짜 부재: 6,174개(100%)
- 7일 이내 fresh CSV: 0개
- 현재 `live_slots_state` 후보 18개: 18개 stale
- `central_buy_candidates` 2개: 2개 stale
- CE 마지막 뉴스 데이터: 2026-05-20, 기준일 대비 51일
- CE는 예외가 아니라 전수 정지의 한 사례

`run_live.py`, 실제 user crontab, 저장소 cron, systemd timer/service에서 `update_ticker_sentiment_recent.py` 자동 실행 경로는 확인되지 않았다. 실제 전수 호출이 확인된 경로는 `scripts/screening/run_full_screening_simple.sh`다.

### B. CE Event +4.62

`UNRECOVERABLE`

7월 7~8일 당시 운영 `market_state.json` 또는 동등한 `active_events` payload는 현재 파일, 일반 백업, tar archive, 로그, Git reachable/unreachable object, reflog·stash, dashboard export, 후보 raw에서 복원되지 않았다.

7월 9일 tar 백업의 `live_slots_state.json` 여러 개에는 CE의 `이벤트반응(+4.62)`가 남아 있었다. 그러나 이 파일들도 Event 합계만 보존하고 `active_events`, `event_flags`, 기사 URL·publishedAt는 보존하지 않는다.

따라서 +4.62260455의 실제 Event key, 기사, fresh/decay 구성은 최종적으로 복원 불가다.

## A. updater 마지막 실행

확인된 마지막 전수 run:

- 로그: `logs/full_screening_news.log`
- 시작: `2026-06-02T19:25:16+00:00`
- 종료: `2026-06-02T21:32:24+00:00`
- 31개 batch
- 마지막 batch summary: `ok=156 skip=1 fail=17`

상태 로그 `data/_system/ticker_sentiment_update.log.jsonl`은 총 6,238개 record를 포함하며 마지막 record timestamp가 `2026-06-02T21:32:24`다. 이후 실행 흔적은 없다.

CSV file mtime 분포:

- 2026-06-02: 6,021개
- 2026-05-31: 153개

마지막 뉴스 날짜의 최신값도 2026-06-02다.

## B. 전 종목 신선도

기준일: `2026-07-10`

| age 구간 | CSV 수 |
|---|---:|
| 마지막 뉴스 날짜 없음 | 153 |
| 0~7일 | 0 |
| 8~30일 | 0 |
| 31~45일 | 3,803 |
| 46~60일 | 1,403 |
| 61~90일 | 454 |
| 91~180일 | 251 |
| 181~365일 | 52 |
| 366일 이상 | 58 |

따라서 6,174개 전부가 News=0, NewsTopics={} 처리 대상이다.

## C. 라이브 후보 영향

`data/_system/live_slots_state.json`의 current candidate pool 고유 ticker는 18개다.

- stale: 18
- fresh: 0
- stale 비율: 100%
- 마지막 뉴스 age 범위: 38~44일

`central_buy_candidates.json`의 CAKE와 WPM도 마지막 뉴스 날짜가 각각 2026-06-01로 39일 stale다.

판정: `UPDATER_STOPPED_WIDESPREAD`

이는 CE 한정 정지가 아니다. 현재 라이브 후보 전체에서 종목별 News/NewsTopics 축이 max-age 규칙 때문에 사실상 0으로 비활성화돼 있다.

## D. CE +4.62 스냅샷 탐색

탐색한 계층:

1. 현재 `market_state.json`
2. `market_state.json.bak_event_decay_20260612_171945`
3. `real_dashboard_market_state.json`
4. `market_history_v2.csv`와 before/old/bak
5. 일반 백업 디렉터리
6. 7월 9~10일 tar archive member 및 내부 파일
7. logs와 archived logs
8. live slot·central candidate·dashboard raw
9. Git path history와 reachable objects
10. Git reflog·stash
11. Git dangling blob 185개·commit 12개·tree 58개
12. 기존 CE/Event 분석 산출물

결과:

- 7월 7~8일 운영 `market_state.json`: 없음
- 동시점 `active_events` payload: 없음
- market history 최신 날짜: 2026-06-05
- 7월 9일 archived live slot state: CE +4.62 합계만 있음
- Git 운영 state history/object: 없음
- dangling object의 active event 자료: 2026-06-05 이전 연구 데이터뿐

따라서 `UNRECOVERABLE`로 종결한다.

## E. 참고용 수학적 가능 조합

CE 룰북 11개 binary Event flag 조합 `2^11`개를 계산했을 때 관측값과 `1e-6` 이내인 조합은 하나였다.

```text
rate_hike + rate_cut + inflation + fed_statement
× event_strength_multiplier
= 4.6226045428543525
```

관측값 `4.62260455`와 차이는 약 `7.14565e-09`다.

그러나 이는 **미확정 수학적 가능 조합**이다. 당시 payload가 없으므로 실제 key 조합이나 기사 정체로 판정하지 않는다.

## 결론

1. ticker sentiment updater는 2026-06-02 이후 전수 갱신이 중단됐다.
2. 2026-07-10 기준 6,174개 CSV 전부가 7일 max-age를 충족하지 못한다.
3. 현재 라이브 후보 18개와 central 후보 2개도 모두 stale다.
4. CE는 예외가 아니라 전역 updater 정지의 한 사례다.
5. 종목 뉴스 축이 0이 되면서 Event 등 다른 축의 상대 비중이 커질 수 있다.
6. CE +4.62는 전역 Event 경로의 합계라는 것까지만 확정된다.
7. 실제 Event key·기사·fresh/decay 구성은 `UNRECOVERABLE`이다.

## 산출물

- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_ticker_sentiment_freshness_summary.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_ticker_sentiment_freshness_distribution.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_live_candidate_stale.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_updater_execution_evidence.md`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_snapshot_search_inventory.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_market_state_recovery.md`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_event_possible_combinations.csv`
- `data/_system/analysis/ticker_updater_ce_event_recovery_20260711_readout.md`

운영 코드·설정 변경: 0건
