# LR8D Full PIT Rerun Design

작성일: 2026-06-10 KST  
상태: 구현 전 설계 초안  
상위 문서:

```text
docs/LR8D_POINT_IN_TIME_NEXT_RUN_DESIGN.md
docs/LR8D_PIT_UNIVERSE_BIAS_PROBE_REPORT.md
docs/LR8D_PIT_EXECUTABLE_RULEBOOK_PROBE_REPORT.md
```

---

## 0. 결론

Full PIT rerun은 정당화됐다. 다만 `pit_executable_rulebook_probe`의 PF 1.45는 full rerun 예상값이 아니다.

숫자의 지위:

```text
fixed16 realistic baseline:
  PF 4.05
  win_rate 71.83%
  return_on_gross 3.27%
  해석: 사후 promoted universe + 2025H2 executable rulebook premium 포함

PIT universe-only:
  PF 2.63
  win_rate 60.56%
  return_on_gross 2.12%
  해석: universe는 시점 정직, executable rulebook은 current promoted

PIT executable source probe:
  PF 1.45
  win_rate 64.00%
  return_on_gross 1.08%
  해석: universe와 executable source를 as_of 허용 label로 제한한 저비용 probe
```

`PF 1.45`는 “진짜 바닥”이라기보다 기존 candidate pool과 `best_expectancy` selection이 가진 과적합 위험을 보여주는 비관적 하한 경고다.

Full PIT rerun의 목적은 다음이다.

```text
시점 정직한 환경에서
T+1 entry + conservative_core exit 조건으로
과적합을 덜 일으키는 robust 룰북 후보 pool을 다시 만들고
as_of별 universe와 executable rulebook을 고정한 뒤
중앙 baseline을 재산출한다.
```

---

## 1. 지금까지 확인된 핵심 사실

### 1.1 기존 학습부는 아직 정직 execution mode를 쓰지 않는다

`engine/learning/backtest.py::run_backtest()`는 현재 T-close entry다.

```text
signal: df.iloc[: i + 1]
entry_price: df.iloc[i]["Close"]
simulate_exit(entry_idx=i)
```

`simulate_exit()`에는 `exit_execution_mode` 인자가 추가되어 있지만, 학습용 `run_backtest()`에는 다음 배선이 없다.

```text
entry_execution_mode
exit_execution_mode
```

따라서 기존 LR8D runner를 그대로 재실행해도 full PIT rerun이 아니다.

### 1.2 기존 RUN 구조는 재사용 가능하다

기존 `scripts/research/run_lr8c_run2_fulluniverse.py`는 다음 부품을 이미 갖고 있다.

```text
85종목 full universe runner
4개 period: 2022 / 2023 / 2024 / 2025H2
shard-count / shard-index 병렬 실행
JSONL append + fcntl.flock
run_key 기반 resume
rulebook / trade artifact persist
post-run survivor report
```

기존 LR8D artifact:

```text
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn.jsonl
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn_rulebooks.jsonl
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_trades.jsonl
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_survivors.jsonl
```

기존 RUN 설정:

```text
population: 40
generations: 50
candidate_mode: qualified_all
max_candidates_per_period: 50
min_trades: 5
min_member_score: 10.0
use_llm_events: false
```

STEP0 timing artifact:

```text
one_ticker_one_period_total: 429.111457 sec
estimated_total_hours_85x4: 40.527193
```

주의:

```text
위 시간은 legacy execution 기준이다.
T+1/conservative_core 학습에서는 재측정이 필요하다.
```

### 1.3 기존 selection은 과적합 위험이 있다

`engine/pipeline/topn_survivor.py`의 member score는 다음 성격이다.

```text
expectancy percentile: 70%
profit factor percentile: 20%
drawdown percentile: 10%
win rate: diagnostic only
```

그리고 label별 대표 후보는 `_best_by_expectancy()`로 고른다.

```text
1. expectancy_pct 높은 순
2. profit_factor 높은 순
3. oos_member_score 높은 순
4. rank_is 낮은 순
```

`pit_executable_rulebook_probe`에서도 이 계열의 `best_expectancy` 규칙을 썼고, 결과는 다음이었다.

```text
trade_count: 71 → 175
PF: 2.63 → 1.45
time_out_total_pnl: +6.04 → -14.45
time_out_loss_count: 6 → 39
```

해석:

```text
단일 OOS label에서 expectancy가 가장 높은 룰북을 고르는 방식은
공격적인 신호 과다와 time_out drag를 유발할 수 있다.
```

따라서 full rerun의 핵심은 단순히 새 GA를 돌리는 것이 아니라, robust selection rule을 사전에 고정하는 것이다.

---

## 2. Full PIT rerun의 범위

### 2.1 하는 것

```text
1. 학습용 run_backtest에 T+1 entry 배선
2. 학습용 run_backtest에 conservative_core exit 배선
3. fold OOS가 fold_end 이후 가격을 보지 않도록 제한
4. 기존 85종목 후보군에 대해 새 execution mode로 GA 재학습
5. period별 top-N candidate / rulebook / trade artifact 재생성
6. robust selection rule로 as_of별 PIT manifest 생성
7. manifest가 executable rulebook artifact ref를 직접 가리키게 함
8. 2024/2025 central PIT baseline 재산출
```

### 2.2 하지 않는 것

```text
live parameters.json 자동 promote 없음
.env 수정 없음
capital allocation sizing engine 구현 없음
N/threshold 결과 기반 튜닝 없음
결과 확인 후 selection rule 변경 없음
```

### 2.3 산출물 이름 후보

```text
run_id: lr8e_pit_cc_20260610
prefix: lr8e_pit_cc
out_dir: data/_system/research/lr8e_pit_cc_20260610
```

---

## 3. 가장 중요한 추가 look-ahead: fold-end exit leakage

기존 `run_backtest()`는 entry decision date를 `start_date/end_date`로 제한하지만, `simulate_exit()`는 전체 dataframe을 훑어 자연청산까지 간다.

문제:

```text
2023 OOS evidence를 as_of=2023-12-31 selection에 쓰려면
2024 가격을 보면 안 된다.

하지만 2023년 말 진입 포지션이 2024년에 청산되도록 두면
2023 OOS score가 2024 가격을 사용하게 된다.
```

Full PIT rerun에서는 fold scoring에 다음 원칙을 둔다.

```text
1. entry decision date는 fold start/end 안에 있어야 한다.
2. T+1 fill date도 fold end 이하여야 한다.
3. exit scan은 fold end 이후 가격을 보지 않아야 한다.
4. fold end까지 자연청산되지 않은 포지션은 fold_end_mark_to_market으로 처리하거나 제외한다.
```

권장 v1:

```text
fold_end_mark_to_market
```

이유:

```text
as_of 시점에는 fold end 종가/고가/저가/시가까지 알고 있다.
열린 포지션을 무시하면 생존 편향이 생긴다.
fold end 가격으로 평가하면 미래 가격을 쓰지 않으면서 open risk를 score에 반영할 수 있다.
```

필수 metadata:

```text
exit_reason = fold_end_mark_to_market
exit_signal_date = fold_end
exit_fill_date = fold_end
exit_execution_mode = conservative_core_fold_bound
```

---

## 4. execution mode 배선 설계

### 4.1 run_backtest 인자

기본값 보존이 최우선이다.

```python
run_backtest(
    ...,
    entry_execution_mode: str = "close",
    exit_execution_mode: str = "base",
    fold_exit_policy: str = "unbounded",
    fractional_shares: bool = False,
)
```

기존 호출은 기본값 때문에 동일해야 한다.

Full PIT runner만 다음 값을 명시한다.

```text
entry_execution_mode = t_plus_1_open
exit_execution_mode = conservative_core
fold_exit_policy = fold_end_mark_to_market
```

### 4.2 T+1 entry 규칙

```text
signal index: i
signal date: df.index[i]
fill index: i + 1
fill price: df.iloc[i + 1]["Open"]
entry ATR/context: signal date 기준
```

금지:

```text
fill_idx >= len(df)
fill_date > fold_end
fill_date > runner end_date
```

### 4.3 conservative_core exit 규칙

학습부에서도 중앙 baseline과 같은 exit semantics를 써야 한다.

```text
exit_execution_mode = conservative_core
same-bar trailing activation exit 금지
same-bar breakeven activation exit 금지
stop_loss / take_profit은 path-independent로 current bar gap-fill 허용
```

---

## 5. Robust selection rule v1

### 5.1 왜 best_expectancy를 버리는가

`best_expectancy`는 한 label에서 가장 잘 맞은 candidate를 고른다.

문제:

```text
OOS label 하나에 과적합된 공격적 룰북을 고를 수 있다.
신호가 과다해지고 time_out 손실이 커질 수 있다.
```

따라서 full PIT rerun은 `best_expectancy`가 아니라 robustness를 우선한다.

### 5.2 ticker universe selection

rule id:

```text
pit_robust_ticker_selection_v1
```

as_of별 허용 label:

```text
as_of=2023-12-31:
  allowed_labels = 2022, 2023
  required_pass_count = 2
  forbidden_labels = 2024, 2025H2

as_of=2024-12-31:
  allowed_labels = 2022, 2023, 2024
  required_pass_count = 3
  forbidden_labels = 2025H2
```

per-label pass:

```text
trade_count >= 5
expectancy_pct > 0.0
profit_factor > 1.0
max_drawdown_pct > -25.0
```

primary ranking:

```text
1. pass_count 높은 순
2. min_expectancy_pct 높은 순
3. min_profit_factor 높은 순
4. worst_drawdown_pct 높은 순
5. avg_expectancy_pct 높은 순
6. avg_profit_factor 높은 순
7. ticker alphabetic
```

주의:

```text
avg_expectancy보다 min_expectancy를 먼저 본다.
한 해 대박으로 평균을 끌어올린 ticker를 피한다.
```

### 5.3 executable rulebook selection

rule id:

```text
pit_robust_executable_selection_v1
```

exact same rulebook hash가 여러 label에 반복되기를 요구하지 않는다.

이유:

```text
GA는 period마다 새 rulebook hash를 만든다.
기존 topn_survivor 주석도 exact-hash consistency가 구조적으로 맞지 않다고 명시한다.
```

v1 executable source:

```text
as_of에 허용된 label 중 가장 최근 label의 qualified candidate를 우선한다.
```

예:

```text
as_of=2023-12-31 → 2023 label candidate 우선
as_of=2024-12-31 → 2024 label candidate 우선
```

candidate gate:

```text
trade_count >= 5
expectancy_pct > 0.0
profit_factor > 1.0
max_drawdown_pct > -25.0
```

candidate ranking:

```text
1. oos_member_score 높은 순
2. max_drawdown_pct 높은 순
3. profit_factor 높은 순
4. expectancy_pct 높은 순
5. rank_is 낮은 순
```

의도:

```text
expectancy 최대화보다 member_score / drawdown / PF를 먼저 본다.
단일 OOS label 과적합 후보를 덜 선택한다.
```

만약 최신 allowed label에 qualified candidate가 없으면:

```text
fallback: 직전 allowed label에서 동일 기준 선택
fallback 사용 여부를 manifest에 기록
```

### 5.4 signal frequency guard

`pit_executable_rulebook_probe`에서 trade_count가 71→175로 폭증했다.

따라서 candidate diagnostic에는 다음을 기록한다.

```text
oos_trade_count
annualized_signal_count
avg_holding_days
time_out_count
time_out_loss_pnl
stop_loss_count
```

v1에서는 signal frequency를 hard gate로 바로 쓰지 않는다. 다만 아래 조건에 걸리면 report warning을 붙인다.

```text
annual_trade_count > 50
or time_out_loss_count / trade_count > 0.25
```

hard gate는 full rerun 결과 분포를 본 뒤 별도 v2에서만 검토한다.

---

## 6. 단계 분할

### Phase 0: 설계/기준 freeze

산출물:

```text
docs/LR8D_FULL_PIT_RERUN_DESIGN.md
```

고정할 것:

```text
execution mode
fold_exit_policy
selection rule id
candidate universe
as_of dates
primary metrics
report format
```

### Phase 1: learning execution mode gate

목표:

```text
run_backtest가 T+1/conservative_core/fold_bound를 정확히 지원한다.
```

테스트:

```text
1. default run_backtest 결과는 기존과 동일
2. t_plus_1_open entry는 signal date와 fill date가 다름
3. fill price는 next open
4. fill_date > fold_end인 entry는 금지
5. conservative_core exit invariant 유지
6. fold_end 이후 가격을 exit에 사용하지 않음
```

### Phase 2: single ticker smoke

대상 후보:

```text
EME
MCK
MELI
```

이유:

```text
EME: 기존 fixed16/PIT 양쪽에서 등장
MCK: PIT top universe의 핵심 winner
MELI: executable probe에서 worst time_out 발생
```

목표:

```text
legacy vs T+1/conservative_core/fold_bound 차이 확인
trade_count 폭증 여부 확인
fold_end_mark_to_market 발생 여부 확인
```

### Phase 3: mini RUN

범위:

```text
3~5종목 × 2 label
population/generations는 full 값보다 작게 smoke용으로만 사용
```

목표:

```text
JSONL persist
rulebook artifact hash 매칭
trade artifact 저장
resume/run_key
post-run scorer
manifest builder
```

주의:

```text
mini RUN 성과는 수익률 결론으로 쓰지 않는다.
인프라 gate다.
```

### Phase 4: STEP0 timing

기존 legacy timing:

```text
one_ticker_one_period_total: 429.111 sec
estimated_total_hours_85x4: 40.527 h
```

Full PIT execution mode에서 같은 STEP0 timing을 다시 측정한다.

산출물:

```text
<NEW_RUN>/lr8e_pit_cc_timing.txt
```

### Phase 5: full 85×4 shard RUN

범위:

```text
85 symbols
2022 / 2023 / 2024 / 2025H2 labels
population=40
generations=50
max_candidates_per_period=50
use_llm_events=false
```

주의:

```text
2025H2는 2024/2025 PIT baseline selection에 사용하지 않는다.
2025H2는 2026 이후 live promotion/stress report용이다.
```

### Phase 6: PIT manifest build

산출물:

```text
pit_manifests/universe_asof_2023-12-31.json
pit_manifests/universe_asof_2024-12-31.json
```

manifest 필수 필드:

```text
as_of_date
trade_start_date
trade_end_date
selection_rule_id
executable_selection_rule_id
allowed_labels
forbidden_labels
selected_tickers
selected_rulebook_refs
fallback_used
per_ticker_robustness_metrics
code_version_or_git_commit
```

### Phase 7: central PIT baseline

실행 원칙:

```text
2024 decision date 신규 진입 → asof_2023 manifest
2025 decision date 신규 진입 → asof_2024 manifest
기존 보유 포지션은 universe에서 빠져도 청산까지 유지
```

필수 지표:

```text
trade_count
active_ticker_count
win_rate
profit_factor
total_return_on_gross_entry_pct
avg_trade_pnl_pct
exit_reason_counts
time_out_total_pnl
time_out_loss_count
stop_loss_count
trailing_pnl
cap binding days
worst_5_trades
yearly metrics
```

---

## 7. 통과 기준

### 7.1 execution integrity

```text
학습부 default mode backward compatibility 통과
T+1 fill invariant 통과
conservative_core same-bar activation invariant 통과
fold_end 이후 가격 미사용 통과
manifest forbidden label violation 0
rulebook artifact missing 0
```

### 7.2 RUN completeness

```text
completed_period_rows == expected_period_rows
rulebook artifact rows > 0
trade artifact rows > 0
shard logs count == expected
resume duplicate run_key 없음
```

### 7.3 성능 해석 기준

Full PIT rerun의 1차 목표는 수익률 향상이 아니다.

```text
1차 목표: live 기대값에 가까운 정직한 기준선 산출
2차 목표: 양의 기대값 유지 여부 확인
3차 목표: 이후 개선 레버 선정
```

성과 해석:

```text
PF >= 2.0:
  robust alpha 가능성 높음. live/paper 운영 설계와 universe 확대 계속.

1.3 <= PF < 2.0:
  약한 양의 기대값. stop/time_out 개선과 selection refinement 필요.

PF < 1.3 또는 return <= 0:
  현재 룰북 family의 edge가 약함. 새 feature/룰북 family/RUN 목적 재검토.
```

이 threshold는 full rerun 결과를 본 뒤 바꾸지 않는다.

---

## 8. capital allocation과의 관계

Full PIT rerun 전에는 entry sizing engine을 구현하지 않는다.

이유:

```text
fixed16 baseline은 사후 universe premium 포함
PIT universe-only에서도 480/600 cap non-binding
PIT executable에서는 cap=120만 일부 binding, 480/600은 여전히 non-binding
```

Full PIT central baseline 이후에만 다음을 재검토한다.

```text
trade_count
active_ticker_count
max_open_positions
cap binding days
power check
capital_reweight_probe
```

---

## 9. 구현 금지사항

```text
.env 수정 금지
parameters.json promote apply 금지
live_universe manifest 교체 금지
결과 확인 후 selection threshold 수정 금지
full RUN 결과를 보고 N/topK 재튜닝 금지
중간 mini RUN 성과로 수익률 주장 금지
```

---

## 10. 최종 작업 순서

1. `run_backtest` execution mode/fold bound 설계와 테스트 추가.
2. default backward compatibility gate.
3. T+1/conservative_core/fold_end unit gate.
4. single ticker smoke.
5. mini RUN infra gate.
6. STEP0 timing.
7. full shard RUN.
8. robust PIT manifest build.
9. central PIT baseline.
10. full PIT report 작성.
11. 그 결과로 live/paper 운영, rulebook family 개선, universe 확장, capital allocation 재검토 중 다음 줄기 결정.

---

## 11. 현재 판단

```text
Full PIT rerun은 필요하다.
하지만 목적은 PF를 올리는 것이 아니라, live 기대값에 가까운 기준선을 확정하는 것이다.
```

PF 1.45 probe는 다음을 의미한다.

```text
기존 candidate pool과 best_expectancy selection은 과적합 위험이 크다.
Full rerun에서는 execution mode와 selection rule을 모두 바꿔야 한다.
```

따라서 다음 개발 시작점은 `run_backtest`가 아니라 GA runner가 아니다. 정확한 첫 단계는 다음이다.

```text
learning backtest execution-mode gate
```

이 gate가 닫히기 전에는 어떤 full RUN도 정직한 RUN으로 인정하지 않는다.
