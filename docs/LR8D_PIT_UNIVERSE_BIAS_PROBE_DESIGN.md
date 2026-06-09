# LR8D PIT Universe Bias Probe Design

작성일: 2026-06-10 KST  
상태: 구현 전 0단계 설계  
상위 문서: `docs/LR8D_POINT_IN_TIME_NEXT_RUN_DESIGN.md`

---

## 0. 결론

차기 RUN 전체 재학습에 들어가기 전에, 먼저 universe survivorship bias의 크기를 측정한다.

```text
목표:
  현재 16종목 고정 realistic baseline 대비
  point-in-time universe만 적용했을 때 성과가 얼마나 하락하는지 측정한다.
```

이 probe는 룰북을 새로 학습하지 않는다. 기존 LR8D 산출물의 연도별 OOS 후보 정보를 재사용해 `as_of`별 universe manifest를 만들고, 기존 `conservative_core` baseline runner에 주입한다.

핵심 질문:

```text
현재 baseline의 win_rate 71.8%, PF 4.05 중
사후 universe selection premium이 얼마나 섞여 있는가?
```

---

## 1. 왜 이 probe가 먼저인가

기존 `realistic_research_baseline`은 거래 레벨 look-ahead는 제거했다.

```text
entry: T close signal → T+1 open fill
exit: conservative_gap_fill + conservative_core
same-bar trailing/breakeven activation look-ahead 제거
```

하지만 universe는 2026-06-09에 export된 `lr8d_stage1_20260609` promoted 16종목이다.

```text
eligible_years: 2022, 2023, 2024
selected_rulebook_source_label: 2025H2
exported_at: 2026-06-09
```

따라서 2024~2025 baseline은 다음 편향을 포함할 수 있다.

```text
2024년 초에는 알 수 없었던 strict_k3 survivor 종목을
2024년 초부터 미리 알고 거래한다.
```

이것은 universe-level look-ahead다.

RUN 재학습보다 먼저 PIT universe bias를 측정해야 하는 이유:

```text
1. 현재 baseline이 얼마나 부풀려졌는지 먼저 알아야 한다.
2. 룰북 재학습 없이도 기존 OOS 산출물로 측정 가능하다.
3. survivorship bias가 크면 이후 모든 수익률 해석 기준이 바뀐다.
4. survivorship bias가 작으면 full RUN 재학습 범위를 더 보수적으로 잡을 수 있다.
```

---

## 2. 기존 데이터 재료 확인

### 2.1 사용할 수 없는 요약 파일

`data/symbols/{ticker}/parameters.json`에는 promoted 결과 요약이 있다.

예: EME

```text
promotion.selection.eligible_years: [2022, 2023, 2024]
promotion.selection.avg_expectancy_pct
promotion.selection.min_expectancy_pct
promotion.selection.worst_year_member_score
promotion.selection.stress_worst_expectancy_pct
```

하지만 `parameters.json`만으로는 PIT manifest를 만들기에 부족하다.

이유:

```text
이미 최종 promoted 16종목만 반영된 사후 요약이다.
후보 universe 전체의 label별 candidate 정보를 담지 않는다.
```

`lr8d_abcd_survivors.jsonl`도 aggregate survivor summary이므로 PIT as_of별 세부 후보를 만들기에는 부족하다.

### 2.2 사용할 핵심 재료

핵심 입력 파일:

```text
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn.jsonl
```

확인된 구조:

```text
topn_rows: 340
labels:
  2022: 85 rows
  2023: 85 rows
  2024: 85 rows
  2025H2: 85 rows
unique_tickers: 85
period_count_distribution:
  모든 85 ticker가 2022 / 2023 / 2024 / 2025H2 row 보유
```

각 row 구조:

```text
ticker
label
year
is_stress
run_key
split
candidates[]
```

각 candidate 구조:

```text
rulebook_hash
rank_is
train_period
test_period
oos_metrics.expectancy_pct
oos_metrics.max_drawdown_pct
oos_metrics.profit_factor
oos_metrics.trade_count
oos_metrics.win_rate
oos_member_score
```

따라서 `lr8d_abcd_topn.jsonl`은 PIT manifest builder의 1차 재료로 충분하다.

### 2.3 label별 통과 가능 후보 수 rough check

기준:

```text
trade_count >= 5
expectancy_pct >= 1.0
oos_member_score >= 10.0
```

확인 결과:

```text
2022: rows 85, with_candidate 85, pass_tickers 54
2023: rows 85, with_candidate 84, pass_tickers 72
2024: rows 85, with_candidate 85, pass_tickers 81
2025H2: rows 85, with_candidate 85, pass_tickers 66
```

판정:

```text
as_of=2023-12-31용 2022/2023 evidence가 충분하다.
as_of=2024-12-31용 2022/2023/2024 evidence도 충분하다.
```

---

## 3. strict_k3 실제 정의와 PIT 변환

### 3.1 기존 strict_k3 정의

`engine/pipeline/topn_survivor.py::evaluate_survivors()` 기준 strict_k3는 다음 구조다.

```text
combo_id: strict_k3
survivor_k: 3
group_by: ticker
min_trades: 5
min_member_score: 10.0
min_expectancy_pct: 1.0
min_stress_expectancy_pct: 0.0
```

일반 OOS label:

```text
2022
2023
2024
```

stress label:

```text
2025H2
```

일반 label pass 조건:

```text
trade_count >= 5
oos_member_score >= 10.0
expectancy_pct >= 1.0
```

일반 label별로 통과 후보 중 expectancy가 가장 높은 candidate를 그 label의 대표로 삼는다.

survivor 조건:

```text
eligible_year_count >= survivor_k
```

즉 strict_k3는 2022/2023/2024 중 3개 label 모두에서 pass해야 한다. “연속” 조건이 별도로 있는 것이 아니라, 사용 가능한 일반 label 3개 중 3개를 통과하는 구조다.

stress 조건:

```text
2025H2에서 min_trades / min_member_score 통과 후보가 있어야 함
stress_avg_expectancy_pct >= 0.0
selected executable rulebook = stress row 중 expectancy가 가장 높은 candidate
```

중요:

```text
기존 promoted 16종목의 executable rulebook은 stress label, 즉 2025H2 source일 수 있다.
```

### 3.2 PIT에서 strict_k3를 그대로 쓸 수 없는 이유

PIT as_of에서는 2025H2 stress를 사용할 수 없다.

```text
as_of=2023-12-31:
  2024, 2025H2 사용 금지

as_of=2024-12-31:
  2025H2 사용 금지
```

또한 as_of=2023-12-31에는 일반 label이 2022/2023 두 개뿐이다. strict_k3의 `survivor_k=3`을 그대로 적용하면 2024 거래 universe가 비어버린다.

따라서 이 probe에서는 strict_k3의 철학을 유지하되, as_of 시점에 사용 가능한 label 수에 맞춰 다음처럼 변환한다.

```text
strict_k3 원칙:
  사용 가능한 일반 OOS label을 모두 통과한 ticker를 우선한다.

PIT v0 변환:
  as_of=2023-12-31 → 2022/2023 둘 다 통과해야 함
  as_of=2024-12-31 → 2022/2023/2024 셋 다 통과해야 함
```

stress gate는 PIT v0에서 금지한다.

```text
2025H2 stress는 2024/2025 trading universe selection에 사용하지 않는다.
```

---

## 4. probe 범위

### 4.1 이 probe가 하는 것

```text
1. 기존 lr8d_abcd_topn.jsonl을 label별로 파싱한다.
2. as_of=2023-12-31에서는 2022, 2023 OOS evidence만 사용한다.
3. as_of=2024-12-31에서는 2022, 2023, 2024 OOS evidence만 사용한다.
4. 사전 고정 selection rule로 ticker를 선발한다.
5. PIT manifest를 생성한다.
6. 2024 구간은 as_of=2023-12-31 manifest로 거래한다.
7. 2025 구간은 as_of=2024-12-31 manifest로 거래한다.
8. current fixed 16 baseline과 성과를 비교한다.
```

### 4.2 이 probe가 하지 않는 것

```text
룰북 재학습 없음
T+1/conservative_core 학습 재실행 없음
sell_omen threshold 재최적화 없음
새 GA RUN 없음
capital allocation sizing 없음
```

### 4.3 중요한 제한

이 probe는 universe selection bias의 크기를 빠르게 측정하기 위한 0단계다.

단, 기존 executable rulebook을 그대로 쓰면 다음 편향이 남을 수 있다.

```text
selected executable rulebook이 2025H2 source일 수 있음
rulebook 자체가 T-close / legacy exit 조건에서 산출됐을 수 있음
```

따라서 이 probe의 지위는 다음과 같다.

```text
측정 대상:
  universe survivorship bias의 1차 크기

아직 제거하지 못한 것:
  rulebook-level rerun bias
  conservative_core/T+1 학습 재평가 필요성
```

그래도 이 probe는 유용하다.

```text
사후 선별 16종목을 PIT 선별 universe로 바꾸는 것만으로
baseline이 얼마나 흔들리는지 먼저 볼 수 있다.
```

---

## 5. 사전 고정 PIT selection rule v0

selection rule은 결과를 보기 전에 고정한다.

### 5.1 rule id

```text
rule_id: pit_all_available_labels_top16_v0
```

이름의 의미:

```text
as_of 시점에 사용 가능한 일반 OOS label을 모두 통과한 ticker 중
strict_k3와 같은 정렬 철학으로 top 16을 고른다.
```

### 5.2 as_of dates

```text
as_of=2023-12-31 → 2024 trading universe
as_of=2024-12-31 → 2025 trading universe
```

### 5.3 allowed / forbidden labels

```text
as_of=2023-12-31:
  allowed_labels = [2022, 2023]
  required_pass_count = 2
  forbidden_labels = [2024, 2025H2]

as_of=2024-12-31:
  allowed_labels = [2022, 2023, 2024]
  required_pass_count = 3
  forbidden_labels = [2025H2]
```

### 5.4 per-label pass condition

각 ticker/label의 candidates 중 하나라도 아래를 만족하면 해당 label pass로 본다.

```text
trade_count >= 5
oos_member_score >= 10.0
expectancy_pct >= 1.0
```

`max_drawdown_pct > -25.0`은 기존 strict_k3 survivor 함수의 per-label eligibility에는 들어있지 않다. 따라서 PIT v0 selection에도 per-label pass 조건으로 넣지 않는다.

단, drawdown은 tie-breaker와 manifest diagnostic에는 기록한다.

### 5.5 label 대표 candidate 선택

label별 pass candidate가 여러 개면 기존 `evaluate_survivors()`와 동일하게 expectancy가 가장 높은 candidate를 대표로 고른다.

정렬:

```text
1. expectancy_pct 높은 순
2. profit_factor 높은 순
3. oos_member_score 높은 순
4. rank_is 낮은 순
```

### 5.6 ticker ranking

required_pass_count를 충족한 ticker가 16개보다 많으면 다음 기준으로 정렬해 상위 16개를 고른다.

기존 `evaluate_survivors()`의 sort key 철학을 최대한 유지한다.

```text
1. eligible_label_count 높은 순
2. min_expectancy_pct 높은 순
3. avg_expectancy_pct 높은 순
4. avg_profit_factor 높은 순
5. avg_rank_is 낮은 순
6. ticker alphabetic
```

stress_avg_expectancy_pct는 PIT v0에서 사용할 수 없으므로 제외한다.

### 5.7 N 고정

primary probe:

```text
N = 16
```

이유:

```text
current fixed 16 baseline과 universe size를 맞춰 survivorship bias를 직접 비교한다.
```

sensitivity는 별도 report로만 허용한다.

```text
N = 30
N = all_pass
```

단, N=30/all_pass 결과가 좋아도 primary 결론을 바꾸지 않는다.

---

## 6. executable rulebook 문제

PIT manifest에는 ticker만 있으면 부족하다. 중앙 baseline runner는 실제 `Rulebook`이 필요하다.

v0 probe에서는 다음 두 가지를 분리한다.

### 6.1 universe-only probe

목표:

```text
PIT ticker selection이 current fixed 16 대비 어떤 영향을 주는지 빠르게 측정한다.
```

실행 방식:

```text
선정된 ticker의 현재 data/symbols/{ticker}/parameters.json rulebook을 사용한다.
```

제한:

```text
rulebook 자체는 point-in-time이 아닐 수 있다.
따라서 이 결과는 full PIT baseline이 아니라 universe-only bias probe다.
```

### 6.2 full PIT rerun

목표:

```text
ticker selection뿐 아니라 executable rulebook도 as_of 이전 데이터로만 만든다.
```

필요 작업:

```text
run_backtest T+1/conservative_core 배선
as_of별 train 종료 rulebook 생성
PIT manifest에 rulebook artifact ref 저장
central runner가 manifest의 rulebook ref를 직접 로드
```

판정:

```text
v0 probe는 6.1만 한다.
6.2는 결과를 본 뒤 full RUN 재설계 단계에서 수행한다.
```

---

## 7. conservative_core runner 주입점

현재 loader:

```text
engine/portfolio/noop_gate.py
load_promoted_rulebooks(manifest_path: Path = MANIFEST_PATH)
```

`load_promoted_rulebooks()` 자체는 manifest path 인자를 받을 수 있다.

하지만 현재 호출부는 대부분 기본값을 쓴다.

```text
run_engine_noop_gate_v1 → load_promoted_rulebooks()
run_fractional_gate_v2 → load_promoted_rulebooks()
run_live_current_proxy_baseline → load_promoted_rulebooks()
run_tplus1_entry_gate → load_promoted_rulebooks()
run_conservative_core_exit_gate → ng.load_promoted_rulebooks()
```

따라서 PIT probe에는 작은 주입점이 필요하다.

필수 변경:

```text
run_conservative_core_exit_gate(manifest_path: Path | None = None)
또는 별도 run_pit_universe_bias_probe() 생성
```

권장:

```text
기존 conservative_core_exit_gate는 그대로 둔다.
새 run_pit_universe_bias_probe()에서 manifest별 rulebooks를 명시 로드한다.
```

---

## 8. 산출물

예상 출력:

```text
data/_system/research/central_portfolio/pit_universe_bias_probe/
  universe_asof_2023-12-31.json
  universe_asof_2024-12-31.json
  candidate_trades.csv
  summary.json
  comparison_vs_fixed16.json
```

summary 필수 필드:

```text
gate: pit_universe_bias_probe
selection_rule_id
source_topn_path
current_fixed16_baseline_ref
as_of_manifests
fixed16_metrics
pit_metrics
survivorship_bias_delta
trade_count_delta
active_ticker_count_delta
exit_reason_delta
time_out_drag_delta
cap_binding_summary
passed
```

---

## 9. 판정 기준

이 probe는 수익률이 좋아야 통과하는 게 아니다.

통과 기준:

```text
1. as_of manifest가 생성됨
2. forbidden_labels가 manifest evidence에 섞이지 않음
3. 2024 거래는 as_of_2023-12-31 universe만 사용
4. 2025 거래는 as_of_2024-12-31 universe만 사용
5. current fixed16 대비 성과 차이가 summary에 기록됨
```

성과 해석:

```text
PIT 성과가 fixed16보다 낮아짐:
  survivorship bias가 있었다는 의미. 실패가 아니라 중요한 측정 결과.

PIT 성과가 fixed16과 비슷함:
  universe survivorship bias가 작을 가능성. full RUN 우선순위 재평가.

PIT 성과가 fixed16보다 좋아짐:
  현 promoted 16종목이 2024~2025에는 최적 universe가 아니었다는 의미.
  단 rulebook-level look-ahead가 남아 있으므로 live 기대수익으로 해석 금지.
```

---

## 10. 결과 해석 threshold

수익률만 보지 않고 win_rate/PF/trade_count/active_ticker/time_out도 같이 본다.

사전 해석 기준:

```text
bias small:
  PIT win_rate가 fixed16 대비 -5pp 이내이고
  PIT profit_factor가 fixed16 대비 -20% 이내이며
  total_return_on_gross_entry_pct가 -1.0pp 이내

bias material:
  PIT win_rate가 fixed16 대비 -5pp 초과 하락하거나
  PIT profit_factor가 fixed16 대비 -20% 초과 하락하거나
  total_return_on_gross_entry_pct가 -1.0pp 초과 하락

bias severe:
  PIT win_rate <= 60% 또는
  PIT profit_factor <= 2.0 또는
  total_return_on_gross_entry_pct <= 0
```

이 기준은 probe 결과를 본 뒤 바꾸지 않는다.

---

## 11. 다음 구현 순서

1. `lr8d_abcd_topn.jsonl` parser 작성.
2. `pit_all_available_labels_top16_v0` selection 함수 작성.
3. `universe_asof_2023-12-31.json`, `universe_asof_2024-12-31.json` 생성.
4. manifest integrity gate 작성.
5. conservative_core daily loop가 연도별 universe를 바꿔 쓰는 runner 작성.
6. current fixed16 realistic baseline과 비교.
7. 결과에 따라 full T+1/conservative_core rerun 범위 결정.

---

## 12. 최종 판단

PIT universe bias probe는 차기 RUN의 0단계다.

```text
무거운 full RUN 재학습 전에,
가장 큰 미측정 편향인 universe survivorship bias를 먼저 잰다.
```

이 probe가 끝나야 다음 둘 중 하나를 결정할 수 있다.

```text
A. survivorship bias가 크다
   → full PIT rerun이 필수. 현재 baseline 수익률은 live 기대값으로 해석 금지.

B. survivorship bias가 작다
   → full RUN 재학습 범위는 T+1/conservative_core 룰북 개선 중심으로 축소 가능.
```
