# CE Event +4.62 스냅샷 복원 탐색

## 최종 상태

`UNRECOVERABLE`

7월 7~8일 당시 `market_state.json` 또는 동등한 `active_events` payload를 복원하지 못했다.

## 탐색 범위

- 현재 파일 및 같은 디렉터리 백업
- 전체 저장소에서 `*market_state*`, `*active_event*`, candidate raw, dashboard export 파일명 탐색
- `data/logs`, `logs`, `data/_system`의 JSON/JSONL/CSV/log
- Git path history
- Git 전체 object 목록
- 2026-07-07~2026-07-08 전후 커밋
- `live_slots_state.json`, `central_buy_candidates.json`, `real_dashboard_market_state.json`
- 기존 CE/Event 분석 산출물

## 발견 파일

1. `data/_system/market_state.json`
   - 현재 파일 시각: 2026-07-10T19:34:03Z
   - CE 진입 후 파일이므로 당시 증거가 아님

2. `data/_system/market_state.json.bak_event_decay_20260612_171945`
   - 2026-06-12 백업
   - CE 진입 전이므로 당시 active set이 아님

3. `data/_system/real_dashboard_market_state.json`
   - 파일 mtime: 2026-07-07T17:17:33Z
   - 실거래 대시보드 격리용 파일
   - `active_events={}`이며 note에 paper/live `market_state.json`과 분리 운영이라고 명시
   - CE Event source 복원 자료가 아님

## Git 결과

다음 운영 파일은 Git history가 없다.

- `data/_system/market_state.json`
- `data/_system/live_slots_state.json`
- `data/_system/central_buy_candidates.json`
- `data/_system/real_dashboard_market_state.json`

`git rev-list --all --objects`에서도 운영 `market_state.json` blob은 발견되지 않았다. 발견된 것은 후속 분석 CSV 경로뿐이다. 따라서 7월 7~8일 커밋에서 당시 운영 state를 꺼낼 수 없다.

## 남아 있는 CE 증거

확정 가능:

- CE Event contribution: `+4.62260455`
- CE rulebook multiplier: `2.3387436247691396`
- CE rulebook event coefficients
- candidate/order timestamp

확인 불가:

- 당시 active event key
- 각 key의 fresh/decay 여부
- 기사 title, URL, publishedAt
- event별 total_impact_score

## 참고용 수학적 조합

binary flag 합산 구조와 저장된 CE 계수만 기준으로 target과 거의 정확히 일치하는 조합은 다음이다.

```text
rate_hike + rate_cut + inflation + fed_statement
= 4.6226045428543525
```

관측값과 차이는 약 `7.15e-09`다.

그러나 당시 `event_flags`가 없으므로 이는 **수학적 가능 조합일 뿐이며 미확정 추측**이다. 실제 source 판정에 사용하지 않는다.
