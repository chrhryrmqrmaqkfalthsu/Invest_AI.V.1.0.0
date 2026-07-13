# Stage 3 시장 snapshot 경로 정렬 및 fail-closed 적용

## 결과

`PASS`

Stage 3 workspace는 이제 repository root의 검증된 시장 snapshot 두 파일만 단일 소스로 읽는다.

```text
/home/g3000kkw/kingmaker/data/_system/market_history.csv
/home/g3000kkw/kingmaker/data/_system/market_history_v2.csv
```

Workspace-local 경로에는 시장 CSV를 생성하거나 복사하지 않는다.

## 수정 파일

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py
```

확인 리포트:

```text
data/_system/analysis/stage23_market_snapshot_alignment_20260713/readout.md
```

## SHA 전후

Stage 3 wrapper 수정 전:

```text
7d4ac62d9d7c7b7313141aa4753e673b1b1ff00837f3af7c1b6e861cacb38da2
```

Stage 3 wrapper 수정 후:

```text
3fb837c3a575d98260e3bc71eb45678440797a8f61188cdff366c93a0f8ebe7d
```

Root 보호 snapshot SHA:

```text
market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

## 1. Root 단일 소스 경로 지정

Wrapper가 repository root를 `.git`과 `data/_system` 기준으로 해석한다.

명시 상수:

```text
RESEARCH_MARKET_HISTORY_SOURCE
RESEARCH_MARKET_HISTORY_V2_SOURCE
RESEARCH_MARKET_CALENDAR_SOURCE
```

실제 해석 경로:

```text
primary: /home/g3000kkw/kingmaker/data/_system/market_history.csv
v2: /home/g3000kkw/kingmaker/data/_system/market_history_v2.csv
calendar: /home/g3000kkw/kingmaker/data/_system/calendars/us_xnys_2020_2027.json
```

Workspace-local 경로 상태:

```text
scripts/research/stage23_rework_20260713/data/_system/market_history.csv: ABSENT
scripts/research/stage23_rework_20260713/data/_system/market_history_v2.csv: ABSENT
```

## 2. Auto-fetch·auto-regenerate 차단

설정:

```text
RESEARCH_MARKET_AUTO_FETCH_ENABLED = False
RESEARCH_MARKET_AUTO_REGENERATE_ENABLED = False
```

Stage 3 process 안에서 다음 함수들을 연구용 snapshot loader로 교체한다.

```text
engine.pipeline.context.get_market_history
engine.market.context.get_market_history
```

기존 재생성 함수는 즉시 에러를 내는 차단 함수로 교체한다.

```text
engine.market.context.build_market_history
→ _blocked_market_history_build()
```

따라서 파일 누락·SHA 불일치·필수 컬럼 결측·NaN/Inf·전부 0·stale 중 하나라도 발생하면 fetch나 재생성을 하지 않고 실행을 중단한다.

Fail-closed 정적 검증:

```text
SHA 불일치: RuntimeError PASS
필수 컬럼 결측: RuntimeError PASS
stale primary snapshot: RuntimeError PASS
auto build 호출: RuntimeError PASS
```

## 3. 필수 컬럼 검증

Primary 필수 컬럼:

```text
date
score
vix
sector_tech
sector_finance
sector_energy
sector_healthcare
sector_consumer
sector_industrials
```

V2 필수 컬럼:

```text
date
event_adjustment
active_events_count
av_sentiment_avg
```

각 numeric 필수 컬럼은 전 행 finite이며 전부 0이 아님을 확인한다.

## 4. 거래일 freshness

기존 calendar-day freshness를 사용하지 않는다.

기준:

```text
뉴욕 기준 as-of 날짜보다 엄격히 이전인 마지막 미국 시장 세션
```

내부 기존 캘린더:

```text
engine.live.us_market_calendar.UsMarketCalendar
allow_api=False
```

따라서 캘린더도 API fetch를 하지 않는다.

2026-07-13 월요일 검증:

```text
snapshot last date: 2026-07-10
expected latest session: 2026-07-10
missing sessions: 0
fresh: true
calendar source: cache
```

2026-07-09를 마지막 날짜로 가정한 stale 검증은 fail-closed RuntimeError가 발생했다.

V2는 기존 Stage 3 auto-refresh 대상이 아니며, SHA로 고정된 이벤트 snapshot으로 처리한다.

```text
v2 last date: 2026-06-05
freshness_applicable: false
freshness_policy: sha_pinned_event_snapshot_no_stage3_auto_refresh
```

## 5. 실행 manifest 기록

기존 `manifest.json`에 다음 객체를 추가한다.

```text
research_market_snapshot
```

기록 항목:

```text
primary 절대경로
primary SHA-256
primary 첫/최종 날짜
primary row count
primary 거래일 freshness 결과
v2 절대경로
v2 SHA-256
v2 첫/최종 날짜
v2 row count
v2 freshness 정책
auto_fetch_enabled=false
auto_regenerate_enabled=false
fail_closed=true
source_mode=repository_root_sha_pinned_single_source
```

Manifest 작성 전에 snapshot 검증이 먼저 수행되므로 검증 실패 상태의 Stage 3 산출물은 시작되지 않는다.

## 정적 검증

```text
py_compile: PASS
wrapper import: PASS
root 경로 해석: PASS
primary SHA: PASS
v2 SHA: PASS
primary 필수 컬럼: PASS
v2 필수 컬럼: PASS
primary row count: 1759
primary last date: 2026-07-10
v2 last date: 2026-06-05
primary freshness: PASS
월요일-금요일 거래일 처리: PASS
auto-fetch 차단 배선: PASS
manifest 확장 배선: PASS
workspace-local 시장 CSV 생성 없음: PASS
```

GA·학습·백테스트는 실행하지 않았다.

## 보호 상태

```text
.env: 불변
market_history.csv: 불변
market_history_v2.csv: 불변
daemon PID 494330: 유지
```

사전 백업:

```text
backup/pre_stage3_market_snapshot_fail_closed_20260713T095138Z.tar.gz
backup/pre_stage3_market_snapshot_fail_closed_20260713T095138Z.manifest.sha256
```
