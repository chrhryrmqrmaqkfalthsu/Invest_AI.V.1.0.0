# LR8D Point-in-Time Next RUN Design

작성일: 2026-06-10 KST  
상태: 차기 RUN 재설계 초안  
상위 문서: `docs/LR8D_NEXT_RUN_DESIGN.md`  
연결 문서:

```text
docs/CENTRAL_PORTFOLIO_BACKTEST_DESIGN.md
docs/CAPITAL_ALLOCATION_STAGE_CLOSE.md
docs/CAPITAL_ALLOCATION_REWEIGHT_PROBE_SPEC.md
```

---

## 0. 결론

다음 수익률 개선 줄기는 단순한 universe 확대가 아니다.

```text
잘못된 표현:
  universe 확대

정확한 표현:
  T+1/conservative_core 기준 point-in-time 확대 universe 차기 RUN
```

현재 `realistic_research_baseline`은 거래 레벨에서는 정직해졌다.

```text
진입: T close 신호 → T+1 open 체결
청산: conservative_gap_fill + conservative_core path-dependent exit
same-bar trailing/breakeven activation look-ahead 제거
```

하지만 현재 16종목 universe는 2026-06-09에 export된 `lr8d_stage1_20260609` promoted universe다. 이 universe를 2024-01-01부터 백테스트하면, 2024년 초에는 알 수 없었던 “나중에 strict_k3 survivor가 된 종목”을 미리 알고 거래하는 셈이다.

따라서 현재 baseline의 정확한 지위는 다음과 같다.

```text
거래 레벨 look-ahead: 제거됨
exit path-dependent look-ahead: 제거됨
universe selection look-ahead / survivorship bias: 남아 있음
```

이 문서의 목적은 universe-level look-ahead를 제거한 차기 RUN의 범위를 정의하는 것이다.

---

## 1. 현재 확인된 사실

### 1.1 현재 16종목 universe

정의 파일:

```text
data/_system/live_universe_lr8d_stage1_manifest.json
```

핵심 metadata:

```text
promotion_id: lr8d_stage1_20260609
run_id: lr8d_abcd_20260608
combo_id: strict_k3
count: 16
unique_by: selected_rulebook_hash
stress_worst_expectancy_pct_gte: 0.0
worst_drawdown_pct_gt: -25.0
exported_at: 2026-06-09T13:42:04Z
```

대상 종목:

```text
CAKE
CRWD
CW
EME
ETR
HSBC
ITT
KT
LASR
MPC
MPLX
MTB
NBIX
WAB
WELL
WPM
```

판정:

```text
이 16종목은 현 live promoted universe 감사 대상으로는 유효하다.
하지만 2024~2025 수익률 검증용 point-in-time universe는 아니다.
```

### 1.2 룰북은 종목별이다

현재 중앙 포트폴리오 loader는 다음 경로에서 종목별 룰북을 읽는다.

```text
engine/portfolio/noop_gate.py
load_promoted_rulebooks()
→ data/symbols/{ticker}/parameters.json
→ payload["rulebook"]
```

확인 결과:

```text
data/symbols 내 symbol directory: 95개
parameters.json 보유 종목: 93개
lr8d_stage1_20260609 promoted 종목: 16개
manifest ↔ parameters promotion hash mismatch: 0개
```

판정:

```text
새 종목을 넣으려면 해당 종목의 룰북이 필요하다.
universe 확대와 룰북 재평가는 분리할 수 없다.
```

### 1.3 기존 LR8C/LR8D 파이프라인 구조

`run_lr8c_run2_fulluniverse.py`에는 연도별 train/test split이 있다.

```text
2022 split:
  train: data_start ~ 2021-12-31
  test:  2022-01-01 ~ 2022-12-31

2023 split:
  train: data_start ~ 2022-12-31
  test:  2023-01-01 ~ 2023-12-31

2024 split:
  train: data_start ~ 2023-12-31
  test:  2024-01-01 ~ 2024-12-31

2025H2 stress:
  train: data_start ~ 2025-05-31
  test:  2025-06-01 ~ data_end
```

즉 개별 종목/룰북 후보 평가에는 OOS 부품이 이미 있다.

하지만 survivor/promotion 구조는 다음과 같다.

```text
2022/2023/2024 OOS에서 반복적으로 좋은 ticker/rulebook 후보를 찾음
2025H2 stress를 붙임
2026-06-09에 strict_k3 promoted 16종목을 export
그 16종목을 2024~2025 중앙 baseline에 고정 universe로 사용
```

판정:

```text
기존 파이프라인에는 OOS split은 있다.
하지만 포트폴리오 백테스트용 point-in-time universe manifest는 없다.
```

### 1.4 strict_k3 의미

`engine/pipeline/topn_survivor.py` 기준 survivor는 ticker-level grouping이다.

중요 주석:

```text
LR-8D-A change:
- survivor grouping is ticker-level, not exact rulebook_hash-level.
- GA creates new rulebook hashes per year, so exact-hash consistency is structurally incompatible with the training process.
```

strict_k3는 대략 다음 의미다.

```text
일반 OOS 연도 2022/2023/2024 중 최소 3개 연도에서
min_trades / min_member_score / min_expectancy 기준을 통과하는 ticker-level survivor
```

그리고 selected executable rulebook은 stress-period rulebook을 사용한다.

판정:

```text
strict_k3는 사후 전체기간 survivor 선별에는 유용하다.
하지만 2024-01-01부터 거래할 point-in-time universe로는 쓸 수 없다.
```

### 1.5 RUN은 아직 conservative_core/T+1 학습을 쓰지 않는다

`engine/learning/backtest.py`의 현재 진입은 T close다.

```text
sig = evaluate_signal(... df.iloc[: i + 1] ...)
entry_price = df.iloc[i]["Close"]
simulate_exit(... entry_idx=i ...)
```

`simulate_exit()`는 이제 `exit_execution_mode` 인자를 받을 수 있지만, `run_backtest()` 호출부에는 아직 `entry_execution_mode`나 `exit_execution_mode`가 일반 배선돼 있지 않다.

판정:

```text
차기 RUN은 단순히 기존 runner를 다시 돌리는 것으로는 부족하다.
run_backtest 또는 연구용 wrapper에 T+1 entry + conservative_core exit 축을 연결해야 한다.
```

---

## 2. 핵심 리스크: universe-level look-ahead

### 2.1 문제 정의

현재 16종목은 다음 정보가 반영된 후에 선별됐다.

```text
eligible_years: 2022, 2023, 2024
stress/source label: 2025H2
exported_at: 2026-06-09
```

따라서 이 16종목으로 2024~2025 baseline을 만들면 다음 문제가 생긴다.

```text
2024년 초에는 strict_k3 survivor가 될지 알 수 없었던 종목을
2024년 초부터 미리 universe에 넣고 거래한다.
```

이것은 거래 레벨 look-ahead가 아니라 universe selection 레벨 look-ahead다.

### 2.2 baseline 해석 수정

기존 `realistic_research_baseline` 수치:

```text
trade_count: 71
win_rate_pct: 71.8310%
profit_factor: 4.0516
avg_trade_pnl_pct: 2.9639%
total_return_on_gross_entry_pct: 3.2660%
```

수정된 해석:

```text
이 수치는 거래/청산 체결 가정은 정직해졌다.
하지만 사후 선별된 universe survivorship premium을 포함할 수 있다.
```

따라서 이 baseline은 다음 용도로는 유효하다.

```text
현재 live promoted 16종목의 감사/진단
exit look-ahead 제거 영향 분석
capital allocation entry sizing의 음성 probe
```

하지만 다음 용도로는 부족하다.

```text
2024-01-01 시점부터 실제로 운용 가능한 수익률 추정
확대 universe의 live 기대수익 추정
```

---

## 3. 차기 RUN 목표

차기 RUN의 이름 후보:

```text
lr8e_pit_conservative_core
lr8d_pit_conservative_core_rerun
lr8e_point_in_time_universe
```

목표:

```text
1. T+1 entry 적용
2. conservative_gap_fill 적용
3. conservative_core path-dependent exit 적용
4. sell_omen_threshold 0.30~0.70 적용 검증
5. shared trailing hard-stop invariant 적용 여부 결정
6. point-in-time universe selection rule 구현
7. 확대 후보군에서 시점별 manifest 생성
8. 시점별 manifest로 central realistic baseline 재산출
```

핵심 질문:

```text
T 시점에 거래 가능한 universe는 T 이전 데이터만으로 선발됐는가?
```

---

## 4. point-in-time selection 설계

### 4.1 기본 원칙

```text
거래연도 Y의 universe는 Y-1년 말까지의 데이터로만 결정한다.
```

예시:

```text
2024 거래 universe:
  selection_as_of = 2023-12-31
  train/OOS evidence allowed <= 2023-12-31
  2024 데이터 사용 금지

2025 거래 universe:
  selection_as_of = 2024-12-31
  train/OOS evidence allowed <= 2024-12-31
  2025 데이터 사용 금지
```

### 4.2 최소 구현안: yearly manifest

시점별 manifest를 만든다.

```text
data/_system/research/<NEW_RUN>/pit_manifests/universe_asof_2023-12-31.json
  → 2024 trading universe

data/_system/research/<NEW_RUN>/pit_manifests/universe_asof_2024-12-31.json
  → 2025 trading universe
```

각 manifest 필수 필드:

```text
as_of_date
trade_start_date
trade_end_date
selection_rule_id
candidate_source
allowed_evidence_periods
excluded_future_periods
selected_tickers
selected_rulebook_refs
per_ticker_selection_metrics
created_at
code_version_or_git_commit
```

### 4.3 selection evidence

`strict_k3`를 그대로 쓰면 3개 OOS 연도가 필요하므로 2024 시작 시점 universe가 너무 보수적이거나 불가능할 수 있다.

따라서 point-in-time용 selection rule은 별도 정의가 필요하다.

후보:

```text
pit_k2:
  as_of 이전 OOS 연도 중 최소 2개 통과

pit_k1_plus_stress:
  as_of 이전 OOS 연도 1개 이상 통과 + 최근 OOS 양수

pit_score_ranked_topN:
  as_of 이전 OOS evidence 기준 score 상위 N개
  단, min_trades / min_expectancy / max_drawdown gate 통과 필수
```

주의:

```text
selection rule은 RUN 전에 고정한다.
결과를 본 뒤 k/N/threshold를 고르면 universe selection overfit이다.
```

### 4.4 권장 v1 selection rule

초기 v1은 너무 복잡하게 만들지 않는다.

```text
rule_id: pit_k2_or_ranked_topN_v1
candidate_universe: data/symbols parameters 보유 93종목 또는 full universe loader 대상
as_of dates: 2023-12-31, 2024-12-31
min_oos_trades_per_period: 5
min_member_score: 10.0
min_expectancy_pct: 1.0
max_drawdown_pct_gt: -25.0
min_pass_periods:
  as_of=2023-12-31 → 2022/2023 중 2개 통과, 부족하면 ranked topN fallback 여부를 사전 고정
  as_of=2024-12-31 → 2022/2023/2024 중 2개 또는 3개 통과를 별도 비교
stress period:
  2025H2는 2024/2025 trading universe selection에는 사용 금지
```

중요:

```text
2025H2 stress는 2025 거래 universe 선정에 사용할 수 없다.
2025H2는 2026 이후 live promotion 검증이나 별도 stress report에만 사용한다.
```

---

## 5. RUN 파이프라인 변경 범위

### 5.1 기존 부품 재사용 가능

재사용 가능:

```text
run_lr8c_run2_fulluniverse.py의 연도별 train/test split 개념
engine.pipeline.topn_survivor의 OOS candidate scoring
lr8d_postrun_analysis.py의 post-run integrity/report 일부
rulebook/trade JSONL persist 구조
shard 실행 구조
```

### 5.2 반드시 새로 추가하거나 수정할 부분

필수 변경:

```text
1. run_backtest에 entry_execution_mode=t_plus_1_open 연결
2. run_backtest에 exit_execution_mode=conservative_core 연결
3. train/OOS candidate 수집 시 새 execution mode metadata 저장
4. point-in-time manifest builder 추가
5. central portfolio baseline이 연도별 manifest를 읽어 universe를 교체하도록 별도 runner 추가
6. post-run report에 universe-level look-ahead 제거 여부 기록
```

### 5.3 재구축 수준 판단

판정:

```text
완전 백지 재구축은 아니다.
하지만 단순 기존 파이프라인 재실행도 아니다.
```

이유:

```text
OOS split과 survivor scoring 부품은 이미 있다.
하지만 point-in-time manifest와 conservative_core/T+1 학습 배선은 없다.
```

따라서 작업 크기:

```text
중대형 RUN 파이프라인 개편
```

---

## 6. 차기 RUN 게이트 설계

### 6.1 b0: execution mode unit gate

목표:

```text
run_backtest 또는 연구용 wrapper가 T+1 entry + conservative_core exit를 정확히 쓰는지 검증
```

필수 체크:

```text
entry signal date != entry fill date
entry fill = next open
exit_execution_mode = conservative_core
same-bar trailing/breakeven activation no-exit invariant 유지
```

### 6.2 b1: single ticker smoke

대상:

```text
대표 1~3종목: EME, MPLX, NBIX 등
```

목표:

```text
legacy/T-close vs T+1/conservative_core 결과 차이 확인
trade log metadata에 entry_signal_date / entry_fill_date / exit_signal_date / exit_fill_date 기록
```

### 6.3 b2: PIT manifest builder gate

목표:

```text
as_of=2023-12-31 manifest에 2024 데이터가 들어가지 않음
as_of=2024-12-31 manifest에 2025 데이터가 들어가지 않음
```

검증:

```text
manifest.allowed_evidence_periods <= as_of_date
manifest.excluded_future_periods 명시
선정된 rulebook_refs의 train/test 기간이 as_of 이후를 포함하지 않음
```

### 6.4 b3: point-in-time central baseline

목표:

```text
2024 구간은 universe_asof_2023-12-31 사용
2025 구간은 universe_asof_2024-12-31 사용
```

비교:

```text
current fixed 16 baseline vs PIT yearly universe baseline
```

필수 지표:

```text
trade_count
active_ticker_count
yearly_return_pct
profit_factor
win_rate
avg_holding_days
exit_reason_counts
time_out_drag
stop_loss_count
max_ticker_gross_share
cap binding days
```

### 6.5 b4: promotion candidate report

목표:

```text
2026 이후 live 후보로 쓸 수 있는 as_of latest manifest 생성
```

주의:

```text
이 manifest는 2024~2025 PIT backtest에 쓰면 안 된다.
```

---

## 7. 산출물 구조

예상 산출물:

```text
data/_system/research/<NEW_RUN>/topn.jsonl
data/_system/research/<NEW_RUN>/topn_rulebooks.jsonl
data/_system/research/<NEW_RUN>/trades.jsonl
data/_system/research/<NEW_RUN>/pit_manifests/universe_asof_2023-12-31.json
data/_system/research/<NEW_RUN>/pit_manifests/universe_asof_2024-12-31.json
data/_system/research/<NEW_RUN>/pit_central_baseline/candidate_trades.csv
data/_system/research/<NEW_RUN>/pit_central_baseline/summary.json
data/_system/research/<NEW_RUN>/PIT_RUN_REPORT.md
```

---

## 8. 통과 기준

### 8.1 무결성

```text
모든 selected ticker에 parameters/rulebook ref 존재
manifest rulebook hash 검증 통과
train/test 기간이 as_of 이후를 침범하지 않음
sell_omen_threshold enabled 룰북 <= 0.70
conservative_core invariant 테스트 통과
```

### 8.2 성능 해석

PIT baseline은 기존 fixed 16 realistic baseline보다 낮아질 수 있다. 이것은 실패가 아니다.

```text
기존 fixed 16 baseline:
  거래 레벨 정직 + universe 사후선별 bias 포함

새 PIT baseline:
  거래 레벨 정직 + universe selection 정직
```

따라서 1차 목표는 수익률 향상이 아니라 bias 제거다.

통과 기준:

```text
PIT baseline이 산출되고, 연도별 universe가 시점 정직하며, 최소 거래수와 데이터 무결성을 만족한다.
```

성능 기준은 그 다음이다.

---

## 9. capital allocation과의 관계

현재 capital allocation entry sizing 연구는 음성으로 종료됐다.

```text
capital_reweight_probe:
  passed_candidate_count = 0
  implementation_recommended = false
```

이 결론은 current fixed 16 universe 기준이다. PIT 확대 universe에서 다음 조건이 충족되면 다시 probe를 실행할 수 있다.

```text
trade_count 증가
active_ticker_count 증가
cap binding days 증가
동시진입/동시보유 증가
```

재실행 순서:

```text
1. PIT central baseline 산출
2. cap binding / 동시보유 점검
3. power check 재실행
4. capital_reweight_probe 재실행
5. 사전 기준 통과 후보만 sizing engine 구현
```

---

## 10. 최종 작업 순서

1. `run_backtest` 또는 연구용 wrapper에 `entry_execution_mode` / `exit_execution_mode` 배선 설계.
2. T+1/conservative_core single ticker smoke.
3. 기존 LR8D runner를 복제한 새 RUN entrypoint 작성.
4. full candidate universe 범위 확정.
5. point-in-time selection rule 사전 고정.
6. shard RUN 실행.
7. PIT manifest 생성.
8. PIT central baseline 산출.
9. 기존 fixed 16 baseline과 비교하되, 목적이 다르다는 점을 report에 명시.
10. 이후 universe가 충분히 커지고 cap이 binding되면 capital allocation probe 재개.

---

## 11. 현재 결론

```text
현재 16종목 realistic_research_baseline은 유용한 감사 기준이다.
하지만 live 기대수익 추정용 최종 기준선은 아니다.
다음 최종 기준선은 point-in-time universe selection을 포함해야 한다.
```

따라서 다음 줄기는 다음 문장으로 정의한다.

```text
T+1/conservative_core 기준으로 확대 후보군을 재평가하고,
각 거래연도 직전 as_of 정보만으로 universe를 선택하는
point-in-time LR8D 차기 RUN을 설계·실행한다.
```
