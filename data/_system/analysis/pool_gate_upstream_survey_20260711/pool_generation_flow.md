# 룰북 pool 생성 경로도

## 전체 흐름

```text
Stage2/Stage3 학습·백테스트
  ↓
scripts/research/run_stage2.py / run_stage3_aggressive.py
  ↓
exp_batch_stage123_2009_20260616_full/tickers/<TICKER>/stage2/survivors.jsonl
exp_batch_stage123_2009_20260616_full/tickers/<TICKER>/stage3/final_rulebooks.jsonl
  ↓
scripts/research/run_stage23_batch.py
  ├─ Stage2 survivors를 central_index.jsonl에 append
  └─ Stage3 final/profile/validation artifact를 central_index.jsonl에 append
  ↓
engine/live/elite_shadow_report.py
  ├─ collect_stage2_elite()
  │   ├─ central_index.jsonl 읽기
  │   ├─ source_file/source_row_index로 survivors.jsonl 원본 룰북 복원
  │   ├─ OOS/fitness/trade/DD/anti-pattern 필터
  │   ├─ elite_score 정렬
  │   └─ ticker별 1개 dedup, 최대 60
  ├─ collect_stage3_elite()
  │   ├─ */stage3/final_rulebooks.jsonl 전수 읽기
  │   ├─ metrics/anti-pattern 필터
  │   ├─ elite_score 정렬
  │   └─ ticker별 1개 dedup, 최대 80
  ├─ stage2 + stage3 결합
  ├─ candidate_denylist 적용
  └─ bucket/elite_score 재정렬
  ↓
build_elite_shadow_report(...).candidates
  ↓
data/_system/ops/live_candidate_slots.py::refresh_slots()
  ├─ KEEP gate
  ├─ held exclusion
  ├─ 현재 v3·BOIL shadow hook
  ├─ evaluate_candidate()
  ├─ should_buy=true만 pool append
  └─ live_slots_state.json::candidate_pool 원자 저장
  ↓
slots / waitlist / dashboard / S2 auto / 실거래 후보 조회
```

## 학습 artifact 생성 근거

`scripts/research/run_stage23_batch.py`는 기존 단일 ticker Stage2/Stage3 스크립트를 subprocess로 호출한다.

Stage2 완료 후:

```text
survivors.jsonl
rulebooks_all.jsonl
period_metrics_all.csv
trades.jsonl
```

을 artifact로 기록하고, `build_stage2_central_index_rows()`가 survivor별 `rulebook_hash`, `source_file`, `source_row_index`, metrics를 `central_index.jsonl`에 append한다.

Stage3 완료 후:

```text
final_rulebooks.jsonl
validation_results.jsonl
stage3_profile_catalog.jsonl
exit_trades.jsonl
```

을 기록한다.

## live candidate 생성 근거

`engine/live/elite_shadow_report.py`:

```text
ROOT = exp_batch_stage123_2009_20260616_full
CENTRAL_INDEX = ROOT / central_index.jsonl
TICKERS_ROOT = ROOT / tickers
```

Stage2 candidate ID:

```text
stage2:<ticker>:<rulebook_hash[:12]>
```

Stage3 candidate ID:

```text
stage3:<ticker>:<rulebook_hash[:12]>
```

`build_elite_shadow_report()`가 Stage2와 Stage3 candidate를 매 호출마다 다시 조립한다.

## candidate_pool 저장

`live_candidate_slots.py::refresh_slots()`:

```text
report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80)
candidates = report.candidates
...
evaluate_candidate(candidate)
...
state.candidate_pool = sorted passing rows
save_state(state)
```

권위 live 저장 위치:

```text
data/_system/live_slots_state.json::candidate_pool
```

이 파일은 룰북 원본 저장소가 아니다. candidate ID, ticker, score, threshold, price, first-signal metadata 등 라이브 평가 결과를 저장한 파생 state다.

## 생성 주기

Guard가 daemon을 다음과 같이 실행한다.

```text
live_candidate_slots.py daemon --interval 60
```

Daemon은 60초 loop지만:

- 정규장 `allow_decision=true`: 매 loop마다 artifact를 다시 읽고 candidate report·signal pool 재생성
- 장외: 기존 cached candidate_pool 재사용, artifact 재생성·재평가 안 함

따라서 pool은 1회 고정이 아니라 정규장 주기적 재생성이다.
