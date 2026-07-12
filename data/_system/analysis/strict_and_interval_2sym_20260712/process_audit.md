# Strict-AND interval Phase 3 실행 감사

## 실행 순서

```text
Phase 1 설계·schema
→ Phase 2 evaluator·GA·daily execution
→ 구조 smoke test
→ Phase 1·2 commit/push
→ Phase 3 structural preflight
→ AAP·POWI 6-worker GA
→ 결과 검증
→ reporting field correction
```

## Structural preflight

```text
초기 랜덤 Rulebook: 1,000
초기 invalid: 0
교배·변이 Rulebook: 1,000
교배·변이 invalid: 0
편측 또는 NaN interval: 0
```

Preflight가 통과한 뒤에만 본 GA를 실행했다.

## Phase 3 실행

```text
entrypoint:
scripts/research/redesign_workspace_20260712/phase3_strict_interval_2sym.py

종목: AAP, POWI
작업 수: 6
ProcessPoolExecutor max_workers: 6
관측 worker: 6
잔존 worker: 0
```

각 작업은 workspace의 다음 모듈만 동적 로드했다.

```text
engine/strategies/rulebook.py
engine/strategies/evaluator.py
engine/learning/genetic.py
engine/learning/execution_mode_backtest.py
```

정식 원본 모듈은 수정하지 않았다.

## Market history 보호

각 worker에서 read-only market loader를 적용했다.

```text
market_history.csv SHA 검증
CSV read
market_history_v2 이벤트 메모리 병합
stale refresh 차단
market_history.csv write 0건
```

실행 전후 SHA-256:

```text
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
```

## 설정

```text
population: 36
max generations: 12
early stop: 5
mutation rate: 0.25
mutation strength: 0.15
technical feature lag: D-5
market/news context lag: D-1
entry: D+1 open
max holding: 7
workers: 6
```

## Reporting correction

초기 산출물에서 `best_train_fitness` 표시만 post-period fitness로 덮였다. GA의 `fitness_history`에 기록된 세대별 best 최댓값으로 선택 시점 train fitness를 복원했다.

```text
GA 재실행: 없음
후보 선택 변경: 없음
interval 변경: 없음
coverage·precision·trade·gate·verdict 변경: 없음
```

교정 전 결과는 다음 백업에 보존됐다.

```text
backup/pre_phase3_reporting_correction_20260712T224034Z.tar.gz
```

## 원본 불변 SHA-256

```text
engine/strategies/rulebook.py
c7b2892f410cd1b25b8090fe26b2b6daaa0aa4bfeaa28555cf4c8b6d12cb15dc

engine/strategies/evaluator.py
d7ce157564c3311d95ba73de79f41dfad3d7d1134727dd8a5fa776487cd83584

engine/learning/genetic.py
89611d799fdca69d7a8e149898f5652f7e4ef5d020349f567919a548bf4361ad

engine/learning/execution_mode_backtest.py
efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0

scripts/research/run_stage2.py
9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c

scripts/research/run_stage3_aggressive.py
8f275ca52745b6b9f92d56e0e24d8043ccef8644b5c5d996217b9c6226e701c0
```

원본 diff: 0

## 라이브 불변

```text
.env
da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

live_candidate_slots.py
259d3bec12901591c84cd1ad9aec01612d914c9120c0976b54bb34adfe684dbb

signal_collector.py
fc0768235189c5a6f95926d2c4f42aa78401e11b8fa2a8ab95992515a700f497
```

Daemon:

```text
PID 494330
STAT Sl
running
```
