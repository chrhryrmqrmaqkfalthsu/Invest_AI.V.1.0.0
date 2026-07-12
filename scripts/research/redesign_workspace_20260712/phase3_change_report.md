# Strict-AND redesign Phase 3 변경·실행 보고

## 신규 실행 코드

```text
scripts/research/redesign_workspace_20260712/phase3_strict_interval_2sym.py
SHA-256: 4623a134a794480909bc6dab330469df0332565c571cabea16368a623afcef20

scripts/research/redesign_workspace_20260712/phase3_correct_reporting.py
SHA-256: 27c2e572cef347cecdab638198494e345f04cda20344226f1b40aeafe484be11
```

## 실행 전 구조 게이트

```text
초기 개체 1,000개 invalid: 0
교배·변이 개체 1,000개 invalid: 0
편측·NaN interval: 0
```

## 실행 설정

```text
AAP·POWI × train split 3개 = 6작업
max workers = 6
population = 36
max generations = 12
feature lag = D-5
context lag = D-1
entry = D+1 open
max holding = 7
fitness = mean(pnl_pct / max(holding_days, 1))
```

## 결과

```text
판정: STRICT_AND_NO_SURVIVOR
Train gate: 6/6
Stress gate: 2/6
OOS gate: 3/6
Survivor: 0/6
Stress 평균 coverage: 26.4768%
Stress pooled precision: 32.4701%
OOS 평균 coverage: 17.6432%
OOS pooled precision: 51.6605%
Signal extinction: 0/6
```

## 기존 floored pilot 대비

```text
Stress precision: -10.79%p
OOS precision: -6.40%p
OOS coverage: -0.81%p
Stress gate: 0/6 → 2/6
OOS gate: 1/6 → 3/6
Survivor: 0/6 → 0/6
```

## Reporting correction

최초 결과의 `best_train_fitness` 표시가 후속 period backtest의 in-place 갱신값으로 기록됐다. `fitness_history`의 세대별 best 최댓값으로 표시값을 복원했다.

```text
후보 선택 변경: 없음
interval 변경: 없음
coverage·precision·trade·gate·verdict 변경: 없음
GA 재실행: 없음
```

## 결과 경로

```text
data/_system/analysis/strict_and_interval_2sym_20260712/
```

Result manifest SHA-256:

```text
f1ed2bc84a35f27373abcbe88068a5ed6bed9df78f0375553dd172cd81303987
```

## 원본 불변

정식 `rulebook.py`, `evaluator.py`, `genetic.py`, `execution_mode_backtest.py`, `run_stage2.py`, `run_stage3_aggressive.py`는 Phase 0 SHA와 동일하고 diff 0이다.
