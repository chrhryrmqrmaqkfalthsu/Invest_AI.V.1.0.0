# CE Event +4.62 스냅샷 복원 탐색

## 최종 상태

`UNRECOVERABLE`

2026년 7월 7~8일 당시 운영 `market_state.json` 또는 동등한 `active_events` payload를 복원하지 못했다. CE의 Event 합계 `+4.62260455`는 여러 후보·백업에 남아 있지만, 이를 만든 key별 binary flag와 기사 payload는 어느 보존 계층에도 없다.

## 탐색 범위

- 현재 운영 파일 및 같은 디렉터리 백업
- 전체 저장소의 `*market_state*`, `*active_event*`, candidate raw, dashboard export
- `logs`, `data/_system/logs`, `data/_system`, `backup`, `data/backups`
- 2026년 7월 9~10일 tar 백업의 member 목록과 내부 파일
- `market_history_v2.csv` 및 before/old/bak 계열
- Git path history와 전체 reachable object 목록
- Git reflog·stash
- `git fsck --full --no-reflogs --unreachable`의 blob 185개·commit 12개·tree 58개
- 2026년 7월 7~8일 전후 커밋
- `live_slots_state.json`, `live_slots_events.jsonl`, `central_buy_candidates.json`, `real_dashboard_market_state.json`
- 기존 CE/Event 분석 산출물

전체 세부 목록은 `ticker_updater_ce_event_recovery_20260711_snapshot_search_inventory.csv`에 기록했다.

## 발견 파일

### 1. 현재 운영 market state

`data/_system/market_state.json`

- timestamp: `2026-07-10T19:34:03.640405`
- active event 기사 payload 존재
- CE 진입 후 state이므로 7월 7~8일 당시 증거로 사용 불가

### 2. 과거 단일 market state 백업

`data/_system/market_state.json.bak_event_decay_20260612_171945`

- timestamp: `2026-06-12T16:58:44.032953`
- CE 진입 전 state
- 당시 active set 복원 자료가 아님

### 3. 실거래 대시보드 격리 state

`data/_system/real_dashboard_market_state.json`

- 파일 mtime: 2026-07-07T17:17:33Z
- `active_events={}`
- note와 schema상 paper/live `market_state.json`과 분리된 격리용 state
- CE Event source가 아님

### 4. market history 계열

`market_history_v2.csv`와 before/old/bak 파일에는 날짜별 Event flag·active event 문자열이 있으나 최신 날짜가 `2026-06-05`다. 7월 7~8일 행은 없다. 기사 URL·publishedAt도 저장하지 않는다.

### 5. 7월 9일 tar 백업

다음 archive들을 포함해 `backup/*.tar.gz`와 7월 10일 cleanup archive의 member를 전수 검색했다.

- `pre_entry_timing_consistency_readout_20260709_150304.tar.gz`
- `pre_export_real_dashboard_buy_candidates_impl_20260709_161023.tar.gz`
- `pre_export_write_real_candidates_20260709_165803.tar.gz`
- `pre_slots_waitlist_should_buy_readout_20260709_170329.tar.gz`
- `pre_k20_feasibility_readout_20260709_170751.tar.gz`
- `pre_rulebook_completeness_audit_20260709_174011.tar.gz`
- `pre_candidate_pool_export_write_20260709_175553.tar.gz`

이들 archive에는 `live_slots_state.json`이 남아 있었다. CE 행에는 다음이 확인된다.

- candidate: `stage3:CE:998b0b638c66`
- first signal: `2026-07-07T22:22:21.577113+00:00`
- `first_final_score=7.195458414225013`
- reasons에 `이벤트반응(+4.62)`

그러나 archived `live_slots_state.json` 최상위와 CE candidate 모두 `active_events`, `event_flags`, 기사 payload를 보존하지 않는다. 일부 7월 9일 refresh에서는 Event reason이 `+0.11`로 바뀐 것도 확인되지만, 이 변화만으로 이전 key 조합을 실제 payload처럼 복원할 수는 없다.

`pre_fallback_live_execution_readout_20260709_145759.tar.gz`의 archived `live_slots_events.jsonl`에는 CE의 2026-07-08 주문 intent만 있고 Event payload는 없다.

어느 tar archive에도 운영 `market_state.json`은 포함되지 않았다.

## Git 탐색 결과

다음 운영 파일은 Git path history가 없다.

- `data/_system/market_state.json`
- `data/_system/live_slots_state.json`
- `data/_system/central_buy_candidates.json`
- `data/_system/real_dashboard_market_state.json`

`data/_system/`은 `.gitignore` 대상이다. `git rev-list --all --objects`에서도 운영 state blob은 발견되지 않았다.

추가로 dangling object까지 검사했다.

- unreachable blob: 185개
- unreachable commit: 12개
- unreachable tree: 58개

`active_events` 문자열이 발견된 unreachable blob은 2026-06-05까지의 `market_history_v2`/EVT 연구 자료뿐이었다. unreachable commit tree에도 운영 market state·live slot state·central candidate path가 없었다.

따라서 7월 7~8일 커밋, reflog, stash 또는 dangling object에서 당시 운영 state를 꺼낼 수 없다.

## 남아 있는 CE 증거

확정 가능:

- CE Event contribution: `+4.62260455`
- CE rulebook: `stage3:CE:998b0b638c66`
- event multiplier: `2.3387436247691396`
- CE rulebook의 11개 `event_response_*` 계수
- 최초 후보와 주문 timestamp
- 7월 9일 tar 백업에도 `이벤트반응(+4.62)`가 남아 있었다는 사실

확인 불가:

- 7월 7~8일 당시 active event key
- 각 key의 fresh/decay 여부
- 각 기사의 title, URL, publishedAt
- event별 `total_impact_score`
- 같은 key가 fresh fetch와 이전 state merge 중 어느 쪽에서 왔는지

## 참고용 수학적 조합

저장된 CE 룰북의 11개 binary Event 조합 `2^11`개를 전수 계산했을 때, target과 `1e-6` 이내로 일치한 조합은 하나다.

```text
(rate_hike + rate_cut + inflation + fed_statement)
× 2.3387436247691396
= 4.6226045428543525
```

관측값과 차이는 약 `7.14565e-09`다.

그러나 당시 `event_flags`가 없으므로 이는 **미확정 수학적 가능 조합**이다. 실제 Event source 또는 실제 활성 key로 판정하지 않는다.

## 종결 판정

`UNRECOVERABLE`

현재 저장소와 백업 체계에서 CE +4.62의 실제 event key·기사·fresh/decay 구성을 더 복원할 경로는 확인되지 않았다. 이후 같은 문제를 방지하려면 후보 snapshot에 `active_events`, key별 `event_flags`, 기사 URL·publishedAt, state timestamp를 함께 저장해야 한다.
