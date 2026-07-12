# 실행·무결성 감사

## Phase 1

```text
prepare_ticker_context 호출: AAP, POWI
GA·학습 실행: 0건
market history rows: 1,759
market period: 2019-07-11 ~ 2026-07-10
sector mapping: AAP=tech, POWI=tech
D-1 lookup gate: PASS
market_history.csv SHA 변경: 없음
```

Phase 1 통과 후에만 Phase 2를 실행했다.

## Phase 2 실행

정식 진입점:

```text
scripts/research/run_stage2.py
```

실행 명령 계약:

```text
--ticker AAP / --ticker POWI
--parallel
fitness cache: default off
fitness_mode: swing
use_llm_events: false
```

종목은 순차 실행했다. 각 종목의 독립 train split 3개만 병렬 실행되어 관측 최대 worker는 3개였다.

### AAP

```text
seed_base: 2026061735
train_1: 30 generations, early stop
train_2: 50 generations
train_3: 39 generations, early stop
generated rulebooks: 300
elapsed: 493.0071 sec
stress pass: 3 / 300
train_3 pass: 0 / 3
OOS reached: 0
survivors: 0
```

### POWI

```text
seed_base: 2026062091
train_1: 42 generations, early stop
train_2: 50 generations
train_3: 34 generations, early stop
generated rulebooks: 300
elapsed: 538.6724 sec
stress pass: 27 / 300
train_3 pass: 15 / 27
train_2 pass: 0 / 15
OOS reached: 0
survivors: 0
```

## Read-only market loader 보호막

복구된 market cache의 마지막 거래일이 2026-07-10이라 정식 `get_market_history()`의 달력일 stale 판정이 자동 refresh를 유발할 수 있었다.

원본 코드는 수정하지 않고 다음 런타임 보호막만 사용했다.

```text
data/_system/analysis/official_stage2_2sym_20260712/_runtime/sitecustomize.py
```

동작:

```text
market_history.csv SHA 확인
복구 CSV 직접 read
market_history_v2 이벤트 열 병합
engine.market.context.get_market_history 런타임 binding 치환
engine.pipeline.context.get_market_history 런타임 binding 치환
```

차단한 것:

```text
build_market_history 호출
stale cache 자동 refresh
market_history.csv 쓰기
```

유지한 것:

```text
정식 Stage 2 진입점
정식 Rulebook GA
정식 D-1 market lookup
정식 evaluate_signal market adjustment
정식 early-cut gate
```

## 시장 파일 검증

```text
경로: data/_system/market_history.csv
크기: 276,656 bytes
mtime: 2026-07-12 20:34:41.532766735 UTC
SHA-256: 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
```

Phase 1 전, 두 Stage 2 실행 전후, 최종 검증에서 크기·mtime·SHA가 동일했다.

## 공식 산출물 파싱

```text
AAP rulebooks_all.jsonl: 300행
AAP trades.jsonl: 7,430행
AAP rl_replay_trades.jsonl: 7,430행
AAP survivors.jsonl: 0행
POWI rulebooks_all.jsonl: 300행
POWI trades.jsonl: 5,306행
POWI rl_replay_trades.jsonl: 5,306행
POWI survivors.jsonl: 0행
```

JSON·JSONL 파싱 오류: 0

공식 CSV:

```text
AAP early_cut_log.csv: 300행
AAP period_metrics_all.csv: 1,500행
POWI early_cut_log.csv: 300행
POWI period_metrics_all.csv: 1,500행
```

요약 CSV:

```text
stage2_results.csv: 10행 × 26열
learned_market_genes.csv: 24행 × 16열
pilot_vs_official_comparison.csv: 11행 × 5열
```

CSV 파싱 오류: 0
CSV 행 너비 오류: 0

## 원본·라이브 SHA-256

```text
.env
da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

scripts/research/run_stage2.py
9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c

scripts/research/run_stage3_aggressive.py
8f275ca52745b6b9f92d56e0e24d8043ccef8644b5c5d996217b9c6226e701c0

engine/pipeline/context.py
33740d9032eb838716070b603d39b13fb87e6e883f7ec46583b16038ec34d74d

engine/market/context.py
8b89e472c26e01c0301aa9c658a2ff2d1f355c346278f86b41578c382a1b8252

engine/core/feature_lag.py
a7eb70be2dd5d4c51842d988567915780d6971c7b82ae61ca75210c4583e6ca8

engine/learning/backtest.py
734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e

engine/learning/execution_mode_backtest.py
efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0

engine/learning/genetic.py
89611d799fdca69d7a8e149898f5652f7e4ef5d020349f567919a548bf4361ad

engine/pipeline/stage2_gate.py
b3018f9323fb7f0194990ce726979841b9db5c5a852711dac3fb7a1d3357f15a

engine/strategies/evaluator.py
d7ce157564c3311d95ba73de79f41dfad3d7d1134727dd8a5fa776487cd83584

engine/strategies/rulebook.py
c7b2892f410cd1b25b8090fe26b2b6daaa0aa4bfeaa28555cf4c8b6d12cb15dc

data/_system/ops/live_candidate_slots.py
259d3bec12901591c84cd1ad9aec01612d914c9120c0976b54bb34adfe684dbb

engine/central/signal_collector.py
fc0768235189c5a6f95926d2c4f42aa78401e11b8fa2a8ab95992515a700f497
```

원본·라이브 diff: 0

## Daemon

```text
PID: 494330
STAT: Sl
CMD: data/_system/ops/live_candidate_slots.py daemon --interval 60
```

중단·재시작·설정 변경 없음.

## 사전 백업

```text
backup/pre_official_stage2_2sym_20260712T204738Z.tar.gz
backup/pre_official_stage2_2sym_20260712T204738Z.manifest.sha256
```

Manifest 검증: `OK`

## 최종 프로세스 상태

```text
AAP Stage 2 process: 종료
POWI Stage 2 process: 종료
잔존 Stage 2 worker: 0
Git worktree before final artifact commit: clean
```
