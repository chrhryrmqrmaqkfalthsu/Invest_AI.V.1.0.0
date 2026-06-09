# Capital Allocation v7 설계 초안

작성일: 2026-06-10 KST  
상태: 구현 전 설계 초안  
상위 문서: `docs/CENTRAL_PORTFOLIO_BACKTEST_DESIGN.md`  
기준 baseline: `conservative_core_exit_gate` candidate, 즉 `fractional + T+1 open + conservative_core`

---

## 0. 결론

`conservative_core_exit_gate`로 산출한 `realistic_research_baseline`은 capital allocation의 기준선으로 사용할 수 있다. 단, 다음 두 경고 딱지를 항상 같이 붙인다.

```text
경고 A: stop-risk 낙관
  conservative_core 기준 stop_loss는 1건뿐이다.
  live hard-stop guard 또는 intraday touch stress에서는 stop 손실이 더 커질 수 있다.

경고 B: time_out drag
  time_out 28건 중 손실거래가 14건이다.
  최악 5거래 중 4거래가 time_out이다.
```

따라서 capital allocation의 1차 목표는 단순히 강신호에 더 배정하는 것이 아니다.

```text
목표:
  time_out drag를 키우지 않으면서 trailing winner를 키운다.
```

---

## 1. realistic baseline 요약

기준 산출물:

```text
data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv
data/_system/research/central_portfolio/conservative_core_exit/summary.json
```

baseline 지표:

```text
trade_count: 71
ticker_count: 10
gross_entry_krw: 1438.8663
total_pnl_krw: 46.9930
total_return_on_gross_entry_pct: 3.2660%
realized_curve_max_drawdown_krw: -3.0260
realized_curve_max_drawdown_pct_of_gross_entry: -0.2103%
avg_trade_pnl_pct: 2.9639%
win_rate_pct: 71.8310%
profit_factor: 4.0516
avg_holding_days: 17.1268
max_holding_days: 30
```

exit 분포:

```text
breakeven_stop: 4
trailing:       37
time_out:       28
take_profit:     1
stop_loss:       1
```

b2-2 invariant:

```text
first_divergence_count: 5
first_ref_reason_counts: trailing 5
first_non_path_dependent_origin_count: 0
invariant_first_divergence_only_path_dependent: true
```

해석:

```text
look-ahead 제거는 의도한 trailing/breakeven path-dependent exit에서만 최초 차이를 만들었다.
stop_loss, sell_omen, time_out 같은 path-independent 사유에서 최초 분기한 ticker는 없다.
```

---

## 2. baseline 건강검진 결과

### 2.1 연도별 강건성

```text
2024: 거래 33, 승률 69.7%, 평균 +3.18%, PF 3.94
2025: 거래 38, 승률 73.7%, 평균 +2.78%, PF 3.26
```

판정:

```text
신뢰 가능.
한 해에만 몰린 성과는 아니다.
두 해 모두 PF 3 이상으로, capital allocation 설계로 넘어갈 수 있다.
```

### 2.2 exit_reason별 손익

```text
breakeven_stop n= 4, 평균 +1.34%, 손실 0건, 최악 +0.90%, 최고 +1.63%
stop_loss      n= 1, 평균 -7.84%, 손실 1건, 최악 -7.84%, 최고 -7.84%
take_profit    n= 1, 평균 +16.66%, 손실 0건, 최악 +16.66%, 최고 +16.66%
time_out       n=28, 평균 +1.12%, 손실 14건, 최악 -10.80%, 최고 +19.71%
trailing       n=37, 평균 +4.46%, 손실 5건, 최악 -4.24%, 최고 +20.74%
```

판정:

```text
trailing은 건강하다.
time_out은 평균은 플러스지만 절반이 손실거래다.
큰 손실은 대부분 time_out에서 발생한다.
```

### 2.3 최악 거래

```text
NBIX   2025-02-10~2025-03-14 -10.80% reason=time_out
EME    2024-11-27~2025-01-08 -9.42%  reason=time_out
NBIX   2024-08-29~2024-10-02 -9.30%  reason=time_out
WELL   2025-03-03~2025-04-10 -7.97%  reason=time_out
WPM    2024-11-25~2024-12-18 -7.84%  reason=stop_loss
```

판정:

```text
큰 손실의 주범은 trailing 실패가 아니라 time_out drag다.
capital allocation은 time_out으로 끌려갈 거래를 키우면 안 된다.
```

---

## 3. 신호세기와 time_out 가설 검증

### 3.1 사용 가능한 로그 필드

`candidate_trades.csv`에는 다음 진입 신호 필드가 이미 있다.

```text
entry_signal_score
entry_signal_raw_score
entry_signal_threshold
entry_market_adjustment
entry_market_score
entry_sector_score
entry_vix_level
```

따라서 capital allocation 입력으로 다음 값을 계산할 수 있다.

```text
live_strength = entry_signal_score / entry_signal_threshold
```

단, 현재 trade log에는 `live_strength` 자체는 저장돼 있지 않다. central allocation trade log에는 계산값을 명시적으로 저장해야 한다.

### 3.2 entry_signal_score 단독 검증 결과

`entry_signal_score` 기준 하위 절반과 상위 절반 비교:

```text
약신호 n=35, score 2.0660~3.2676, time_out 12건(34%), trailing 23건, 손실 10건, 평균 +2.51%
강신호 n=36, score 3.2676~4.7284, time_out 16건(44%), trailing 14건, 손실 10건, 평균 +3.41%
```

사분위 비교:

```text
Q1_최약 n=17, score 2.0660~3.2156, time_out 7건(41%), trailing 10건, 손실 8건, 평균 -0.06%
Q2      n=17, score 3.2156~3.2389, time_out 4건(24%), trailing 13건, 손실 1건, 평균 +5.85%
Q3      n=17, score 3.2676~3.6168, time_out 10건(59%), trailing 7건, 손실 6건, 평균 +2.16%
Q4_최강 n=20, score 3.7025~4.7284, time_out 7건(35%), trailing 7건, 손실 5건, 평균 +3.76%
```

판정:

```text
entry_signal_score 단독으로는 time_out 위험을 단조롭게 설명하지 못한다.
강신호 절반의 time_out 비율이 약신호 절반보다 오히려 높다.
Q2가 가장 좋고 Q3가 가장 나쁘다.
```

따라서 v1 capital allocation에서 다음은 금지한다.

```text
금지:
  target_notional을 entry_signal_score에만 비례시킨다.
  score가 높다는 이유만으로 무제한 과배정한다.
```

---

## 4. capital allocation v1 원칙

사용자 원칙:

```text
1. 다 굴린다.
2. 안 위험하게 굴린다.
3. 약신호는 적게 배정한다.
4. 분산한다.
5. 룰북/종목 특성을 분리한다.
```

baseline 건강검진을 반영한 해석:

```text
다 굴린다:
  BUY 후보를 단순 score 순위로 대량 탈락시키지 않는다.

안 위험하게 굴린다:
  총노출, 종목별 cap, stop-risk stress, time_out drag cap을 둔다.

약신호는 적게 배정한다:
  live_strength가 낮으면 notional을 줄인다.
  단, entry_signal_score 단독이 아닌 threshold 정규화값과 룰북 품질을 함께 본다.

분산한다:
  특정 ticker, sector, exit profile에 과집중하지 않는다.

룰북/종목 특성을 분리한다:
  live_strength는 현재 신호 세기, historical_quality는 룰북 품질, time_out_risk는 exit profile 위험으로 분리한다.
```

---

## 5. 배분 입력값 정의

### 5.1 live_strength

```text
live_strength = entry_signal_score / entry_signal_threshold
```

방어 규칙:

```text
entry_signal_threshold <= 0 이면 live_strength = 0
BUY 후보가 아니면 신규 진입 후보에서 제외
```

초기 bucket:

```text
weak:        live_strength < 1.10
normal:      1.10 <= live_strength < 1.30
strong:      1.30 <= live_strength < 1.60
very_strong: live_strength >= 1.60
```

주의:

```text
기존 문서의 1.20 / 1.50 / 2.00 bucket은 유지 후보로 남기되,
16종목 baseline에서는 실제 분포가 좁을 수 있으므로 sweep으로 확인한다.
```

### 5.2 historical_quality

초기값:

```text
historical_quality = percentile_rank(expectancy_pct within 16 promoted tickers)
```

후보 보강값:

```text
profit_factor_percentile
win_rate_percentile
trade_count_reliability
```

v1에서는 과최적화를 막기 위해 다음처럼 단순화한다.

```text
historical_quality_v1 = expectancy_pct_percentile
```

### 5.3 time_out_risk

v1에서 반드시 기록할 위험 지표:

```text
time_out_count
time_out_loss_count
time_out_loss_rate
time_out_avg_pnl_pct
time_out_worst_pnl_pct
time_out_total_pnl_pct_sum
```

단, 16종목 2년 baseline만으로 ticker별 time_out risk를 강하게 학습하면 과적합 위험이 크다. 따라서 v1에서는 penalty가 아니라 검증/리포트 지표로 먼저 둔다.

v1.5 후보:

```text
time_out_risk_penalty = f(ticker historical time_out_loss_rate, rulebook max_holding_days, low live_strength)
```

---

## 6. 배분 공식 후보

### 6.1 baseline fixed_30

비교 기준:

```text
order_notional = 30 USD
sizing_mode = fractional
entry = T+1 open
exit = conservative_core
```

이 값은 `realistic_research_baseline`과 일치해야 한다.

### 6.2 live_strength bucket sizing

가장 단순한 v1 후보:

```text
weak:        0.50x base_notional
normal:      0.75x base_notional
strong:      1.00x base_notional
very_strong: 1.25x base_notional
```

예시:

```text
base_notional = 30 USD
weak = 15 USD
normal = 22.5 USD
strong = 30 USD
very_strong = 37.5 USD
```

방어 규칙:

```text
min_order_notional 미만이면 주문하지 않음
per_position_cap <= 45 USD 또는 60 USD sweep
총노출 cap <= 480 USD strict 또는 600 USD live_policy sweep
```

### 6.3 capped signal_power proportional

기존 v7 문서의 `signal_power`를 사용하되 강한 cap을 둔다.

```text
signal_power = historical_quality * live_strength
raw_weight_i = max(0, signal_power_i)
weight_i = raw_weight_i / sum(raw_weight)
target_notional_i = total_exposure_cap * weight_i
```

cap:

```text
target_notional_i <= per_position_cap
약신호 target_notional_i <= base_notional * 0.50
normal target_notional_i <= base_notional * 0.75
```

즉 proportional sizing이어도 약신호가 전체 후보 부족 때문에 과대배정되는 것을 금지한다.

### 6.4 hybrid floor-and-cap

사용자 원칙인 “다 굴리되, 안 위험하게”에 가장 잘 맞는 후보:

```text
eligible BUY 후보는 최소 small allocation을 받을 수 있다.
단, live_strength와 historical_quality가 모두 낮으면 minimum만 받는다.
상위 후보도 per_position_cap 이상 받지 못한다.
```

공식:

```text
base = 30 USD
multiplier = bucket_multiplier(live_strength) * quality_multiplier(historical_quality)
target_notional = clamp(base * multiplier, min_notional, per_position_cap)
```

초기 multiplier:

```text
bucket_multiplier:
  weak        0.50
  normal      0.75
  strong      1.00
  very_strong 1.25

quality_multiplier:
  bottom 25%  0.75
  middle 50%  1.00
  top 25%     1.15
```

---

## 7. time_out drag guard

capital allocation 후보는 수익률만 보지 않고 time_out 손실이 커졌는지 반드시 본다.

필수 지표:

```text
time_out_count
time_out_loss_count
time_out_loss_rate
time_out_avg_pnl_pct
time_out_total_pnl_krw
time_out_worst_pnl_pct
time_out_pnl_share = time_out_total_pnl_krw / total_pnl_krw
worst_5_trade_reasons
```

통과 기준 초안:

```text
candidate total_return >= baseline total_return
candidate profit_factor >= baseline profit_factor * 0.90
candidate time_out_loss_count <= baseline time_out_loss_count * 1.10
candidate time_out_worst_pnl_pct >= baseline time_out_worst_pnl_pct - 2.0pp
candidate avg_holding_days <= baseline avg_holding_days * 1.20
```

방어형 후보는 다음을 만족하면 수익률 소폭 하락을 허용할 수 있다.

```text
candidate total_return >= baseline total_return * 0.90
candidate realized MDD 개선
candidate time_out_loss_count 감소
candidate worst_5 손실 개선
```

---

## 8. stop-risk stress

`conservative_core` baseline은 stop_loss가 1건뿐이라 live stop-risk를 과소평가할 수 있다. 따라서 capital allocation 결과는 반드시 다음 stress와 같이 읽는다.

stress 후보:

```text
stress_exit_mode = conservative_core
stress_exit_mode = conservative_core + live_hard_stop_guard_daily_low
stress_slippage_bps = 5, 10, 25
```

필수 비교:

```text
baseline conservative_core vs candidate conservative_core
baseline guard_stress vs candidate guard_stress
```

통과 기준:

```text
candidate가 conservative_core에서만 이기고 guard_stress에서 무너지면 live 후보로 승격하지 않는다.
```

---

## 9. 구현 게이트 설계

### 9.1 capital_allocation_noop_gate

목표:

```text
새 capital allocation simulator가 fixed_30, switch off, kill off, conservative_core 설정에서
realistic_research_baseline과 trade/equity/summary를 재현한다.
```

reference:

```text
conservative_core_exit_gate candidate
```

candidate:

```text
central capital simulator
sizing_mode = fixed_30
switch_enabled = false
kill_rule_enabled = false
exit_execution_mode = conservative_core
entry_execution_mode = t_plus_1_open
```

통과 조건:

```text
trade_count 동일
entry/exit date 동일
entry/exit price 동일 또는 허용오차 이내
pnl 합계 동일 또는 허용오차 이내
exit_reason 분포 동일
```

이 게이트가 실패하면 sizing/switch/kill 구현 금지.

### 9.2 capital_allocation_sizing_gate

목표:

```text
fixed_30 대비 sizing 후보가 time_out drag를 키우지 않고 성과를 개선하는지 검증한다.
```

비교 후보:

```text
fixed_30
live_strength_bucket
capped_signal_power_proportional
hybrid_floor_and_cap
```

summary 필수 필드:

```text
baseline_metrics
candidate_metrics
return_delta
profit_factor_delta
mdd_delta
avg_holding_delta
exit_reason_counts_delta
time_out_drag_summary
stop_risk_stress_summary
per_ticker_exposure_summary
concentration_summary
passed
```

### 9.3 concentration guard

필수 지표:

```text
max_ticker_pnl_share
max_ticker_gross_entry_share
max_single_trade_notional
top3_ticker_exposure_share
```

통과 기준 초안:

```text
max_single_ticker_exposure <= total_exposure_cap * 0.15
max_single_trade_notional <= base_notional * 2.0
상위 3개 ticker gross exposure <= total_exposure_cap * 0.40
```

---

## 10. 초기 sweep 후보

```text
base_notional: 30
min_notional: 10, 15
per_position_cap: 45, 60
total_exposure_cap: 480, 600
sizing_mode:
  fixed_30
  live_strength_bucket
  capped_signal_power_proportional
  hybrid_floor_and_cap
live_strength_bucket:
  conservative: [1.10, 1.30, 1.60]
  original_doc: [1.20, 1.50, 2.00]
historical_quality:
  off
  expectancy_percentile
stop_stress:
  off
  live_hard_stop_guard_daily_low
slippage_bps:
  0, 5, 10, 25
```

첫 구현은 sweep 전체가 아니라 다음 3개만 비교한다.

```text
A. fixed_30 no-op
B. live_strength_bucket with per_position_cap=45, total_cap=480
C. hybrid_floor_and_cap with per_position_cap=45, total_cap=480, expectancy_quality on
```

---

## 11. 구현 순서

1. `capital_allocation_noop_gate`를 만든다.
2. no-op에서 `realistic_research_baseline`을 정확히 재현한다.
3. central trade log에 다음 필드를 추가한다.

```text
entry_signal_score
entry_signal_threshold
live_strength
historical_quality
signal_power
sizing_mode
target_notional
actual_notional
allocation_multiplier
allocation_reason
```

4. `live_strength_bucket` sizing만 먼저 구현한다.
5. baseline 대비 수익률, PF, MDD, time_out drag를 비교한다.
6. guard stress를 추가한다.
7. 그 다음에만 `hybrid_floor_and_cap`과 `capped_signal_power_proportional`을 구현한다.

---

## 12. 최종 판단 기준

capital allocation 후보는 다음을 모두 만족해야 live 후보가 된다.

```text
1. no-op 재현 성공
2. 동일 총노출 기준 baseline 대비 수익률 또는 방어 지표 개선
3. time_out drag 악화 없음
4. stop-risk stress에서 붕괴하지 않음
5. 연도별 2024/2025 모두 성과가 극단적으로 무너지지 않음
6. 특정 ticker 과집중 없음
7. turnover/slippage 반영 후에도 우위 유지
```

위 조건을 만족하지 못하면 자동 자본 배분은 연구 모드로 유지한다.
