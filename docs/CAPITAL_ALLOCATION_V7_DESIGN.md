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

power check 이후 capital allocation의 방향은 다음처럼 수정한다.

```text
확정:
  진입 신호 세기만으로 수익률을 올리는 sizing은 정당화되지 않는다.
  entry_signal_score 단독 배분은 금지한다.
  live_strength도 단조성이 약하므로 바로 sizing engine 구현에 쓰지 않는다.

1차 목표:
  fixed_30 realistic baseline을 중앙 simulator에서 정확히 재현한다.

2차 목표:
  실제 구현 전 offline reweighting probe로 sizing 후보가 noise보다 큰 개선을 낼 수 있는지 확인한다.

3차 목표:
  time_out drag를 키우지 않으면서 trailing winner를 키울 수 있는 후보만 구현한다.
```

즉 v7 capital allocation은 “강신호에 더 준다”가 아니라 다음 가설을 검증하는 연구 단계로 시작한다.

```text
진입 시점 정보만으로 차별화할 수 있는가?
아니면 진입 sizing은 fixed로 두고, universe 확대 / 차기 RUN / post-entry add_buy·리밸런싱이 더 큰 레버인가?
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
두 해 모두 PF 3 이상으로, capital allocation 연구로 넘어갈 수 있다.
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

## 3. 진입 시점 변수 power check

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

계산 가능 파생값:

```text
live_strength = entry_signal_score / entry_signal_threshold
```

단, 현재 trade log에는 `live_strength` 자체는 저장돼 있지 않다. central allocation trade log에는 계산값을 명시적으로 저장해야 한다.

### 3.2 entry_signal_score 단독 검증 결과

`entry_signal_score` 기준 하위 절반과 상위 절반 비교:

```text
약신호 n=35, score 2.0660~3.2676, time_out 12건(34%), trailing 23건, 손실 10건, 평균 +2.51%
강신호 n=36, score 3.2676~4.7284, time_out 16건(44%), trailing 14건, 손실 10건, 평균 +3.41%
차이 +0.90%p
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

### 3.3 market/sector/vix 필드 주의

초기 상·하위 절반 비교에서 다음 필드들이 큰 차이를 보이는 듯했다.

```text
entry_market_adjustment
entry_market_score
entry_sector_score
entry_vix_level
```

하지만 추가 확인 결과, baseline trade log에서는 모두 상수다.

```text
entry_market_adjustment: unique 1, value 1.0
entry_market_score:      unique 1, value 50.0
entry_sector_score:      unique 1, value 50.0
entry_vix_level:         unique 1, value 18.0
```

따라서 이 필드들의 상·하위 절반 차이는 정렬 tie와 row order 때문에 생긴 가짜 신호다.

판정:

```text
현재 baseline trade log의 market/sector/vix 필드는 capital allocation 입력으로 쓸 수 없다.
시장 국면 기반 총노출 조절을 검증하려면 실제 market_history 기반 값이 daily loop에 연결되어야 한다.
```

### 3.4 live_strength 검증 결과

`live_strength = entry_signal_score / entry_signal_threshold` 기준 하위 절반과 상위 절반:

```text
하위 절반 n=35, 평균 +1.92%, time_out 9건
상위 절반 n=36, 평균 +3.98%, time_out 19건
차이 +2.05%p
```

표면적으로는 평균 차이가 2%p를 넘지만, 사분위는 단조적이지 않다.

```text
Q1 n=17, live_strength 1.0195~1.0195, 평균 +0.47%, time_out 3건, 손실 7건, 주요 ticker EME 17건
Q2 n=17, live_strength 1.0195~1.0922, 평균 +3.05%, time_out 6건, 손실 4건
Q3 n=17, live_strength 1.0922~1.2813, 평균 +5.82%, time_out 6건, 손실 2건
Q4 n=20, live_strength 1.2891~1.5407, 평균 +2.58%, time_out 13건, 손실 7건, 주요 ticker MPLX 9건
```

판정:

```text
live_strength는 entry_signal_score보다 약간 낫지만, 단조 관계가 아니다.
최상위 Q4는 time_out이 가장 많고 평균도 Q3보다 낮다.
Q1과 Q4 모두 ticker 구성에 크게 오염되어 있다.
```

따라서 다음은 금지한다.

```text
금지:
  live_strength가 높을수록 자동으로 더 크게 배정한다.
  live_strength_bucket을 구현 전 검증 없이 바로 production candidate로 둔다.
```

허용:

```text
live_strength는 offline reweighting probe의 후보 입력으로만 둔다.
실제 sizing 구현은 probe에서 baseline 대비 noise 이상 개선을 보인 뒤 진행한다.
```

### 3.5 표본 식별력

baseline 분포:

```text
거래수: 71
평균 pnl_pct: +2.96%
표준편차: 6.35%
평균의 표준오차: 약 0.75%
```

판정:

```text
두 sizing 후보의 평균 차이가 약 1.5%p 미만이면 noise와 구분하기 어렵다.
entry_signal_score 차이 +0.90%p는 식별력이 부족하다.
live_strength 차이 +2.05%p는 관찰할 가치는 있으나, 사분위 비단조성과 ticker composition 때문에 구현 전 probe가 필요하다.
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

power check 이후 수정된 해석:

```text
다 굴린다:
  BUY 후보를 단순 score 순위로 대량 탈락시키지 않는다.

안 위험하게 굴린다:
  총노출, 종목별 cap, stop-risk stress, time_out drag cap을 둔다.

약신호는 적게 배정한다:
  원칙으로는 유지한다.
  하지만 baseline 데이터에서는 약신호 축소가 time_out 감소로 직접 이어진다는 증거가 약하다.
  따라서 v1에서는 hard rule이 아니라 probe 대상이다.

분산한다:
  특정 ticker, sector, exit profile에 과집중하지 않는다.
  특히 Q1/Q4 live_strength 결과가 ticker composition에 오염되어 있으므로 concentration guard가 필수다.

룰북/종목 특성을 분리한다:
  live_strength는 현재 신호 세기,
  historical_quality는 룰북 품질,
  time_out_risk는 exit profile 위험,
  concentration은 포트폴리오 위험으로 분리한다.
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
BUY가 아닌 신호는 신규 진입 후보에서 제외
```

초기 bucket은 구현 전 probe 전용이다.

```text
weak:        live_strength < 1.05
normal:      1.05 <= live_strength < 1.10
strong:      1.10 <= live_strength < 1.30
very_strong: live_strength >= 1.30
```

주의:

```text
기존 문서의 1.20 / 1.50 / 2.00 bucket은 현재 16종목 baseline 분포에서는 너무 높다.
현재 baseline의 live_strength 최대값은 약 1.54다.
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

주의:

```text
historical_quality는 아직 baseline trade 결과와 power check가 끝나지 않았다.
entry_signal_score처럼 단독 사용은 금지한다.
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

### 5.4 market_regime_quality

현재 baseline trade log의 `entry_market_score`, `entry_sector_score`, `entry_vix_level`은 상수라 사용할 수 없다.

시장 국면 기반 총노출 조절을 실험하려면 다음 선행 작업이 필요하다.

```text
market_history 기반 daily market_score / regime / vix를 central daily loop에 연결
trade log에 실제 entry_market_score / entry_regime / entry_vix_level 저장
power check 재실행
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

### 6.2 offline reweighting probe

실제 sizing engine 구현 전 반드시 실행한다.

목표:

```text
기존 71거래의 realized pnl에 가상의 notional multiplier를 적용했을 때,
수익률 개선이 noise보다 큰지 확인한다.
```

probe 후보:

```text
fixed_30
entry_signal_score_bucket
live_strength_bucket
historical_quality_bucket
live_strength * historical_quality
capped_floor_and_cap
```

통과 조건 초안:

```text
candidate total_pnl 개선폭 >= baseline gross_entry 기준 +1.5%p
candidate time_out_total_pnl 악화 없음
candidate worst_5 손실 악화 없음
candidate max_ticker_gross_entry_share cap 충족
```

이 probe가 실패하면 variable entry sizing 구현은 보류한다.

### 6.3 live_strength bucket sizing

구현 후보가 아니라 probe 후보로 둔다.

예시 multiplier:

```text
weak:        0.75x base_notional
normal:      1.00x base_notional
strong:      1.10x base_notional
very_strong: 1.00x base_notional
```

중요:

```text
Q4가 가장 좋은 bucket이 아니므로 very_strong을 1.25x로 자동 확대하지 않는다.
```

### 6.4 capped signal_power proportional

기존 v7 문서의 `signal_power`를 사용하되 강한 cap을 둔다.

```text
signal_power = historical_quality * live_strength
raw_weight_i = max(0, signal_power_i)
weight_i = raw_weight_i / sum(raw_weight)
target_notional_i = total_exposure_cap * weight_i
```

주의:

```text
이 방식은 concentration 위험이 가장 크다.
probe에서 ticker concentration과 time_out drag가 악화되면 구현하지 않는다.
```

### 6.5 hybrid floor-and-cap

사용자 원칙인 “다 굴리되, 안 위험하게”에 가장 잘 맞는 후보지만, 구현은 probe 이후다.

공식 후보:

```text
base = 30 USD
multiplier = bucket_multiplier(live_strength) * quality_multiplier(historical_quality)
target_notional = clamp(base * multiplier, min_notional, per_position_cap)
```

초기 multiplier는 보수적으로 시작한다.

```text
bucket_multiplier:
  weak        0.75
  normal      1.00
  strong      1.10
  very_strong 1.00

quality_multiplier:
  bottom 25%  0.90
  middle 50%  1.00
  top 25%     1.10
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

### 9.2 capital_allocation_reweight_probe_gate

목표:

```text
실제 simulator 구현 전, 기존 baseline 거래에 가상 multiplier를 적용해
variable sizing이 식별 가능한 개선 여지가 있는지 확인한다.
```

출력:

```text
reference_fixed_30_metrics
candidate_reweighted_metrics
return_delta_pct_of_gross_entry
time_out_drag_delta
worst_5_delta
concentration_delta
passed
```

통과 조건:

```text
수익 개선폭이 평균 표준오차 × 2 수준보다 커야 한다.
time_out drag와 worst loss가 악화되면 실패다.
```

### 9.3 capital_allocation_sizing_gate

이 게이트는 `reweight_probe_gate`를 통과한 sizing 후보에 대해서만 만든다.

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

### 9.4 concentration guard

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

## 10. 초기 실험 후보

```text
base_notional: 30
min_notional: 10, 15
per_position_cap: 45, 60
total_exposure_cap: 480, 600
probe_mode:
  fixed_30
  entry_signal_score_bucket
  live_strength_bucket
  live_strength_mid_bucket_boost
  historical_quality_bucket
  hybrid_floor_and_cap
historical_quality:
  off
  expectancy_percentile
stop_stress:
  off
  live_hard_stop_guard_daily_low
slippage_bps:
  0, 5, 10, 25
```

첫 구현은 sweep 전체가 아니라 다음 순서로 진행한다.

```text
A. fixed_30 no-op
B. offline reweighting probe
C. probe 통과 후보만 central simulator sizing으로 구현
```

---

## 11. 구현 순서

1. `capital_allocation_noop_gate`를 만든다.
2. no-op에서 `realistic_research_baseline`을 정확히 재현한다.
3. `capital_allocation_reweight_probe_gate`를 만든다.
4. 기존 baseline 71거래에 offline multiplier를 적용해 다음을 검증한다.

```text
entry_signal_score_bucket
live_strength_bucket
historical_quality_bucket
hybrid_floor_and_cap
```

5. probe가 실패하면 variable entry sizing 구현을 보류한다.
6. probe가 통과하면 central trade log에 다음 필드를 추가한다.

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

7. 통과 후보만 실제 simulator sizing으로 구현한다.
8. guard stress와 concentration guard를 추가한다.
9. sizing이 noise 수준이면 다음 레버로 이동한다.

```text
universe 확대
T+1 기준 차기 RUN / 룰북 재선정
post-entry add_buy 또는 winner scaling
market_history 기반 총노출 조절
```

---

## 12. 최종 판단 기준

capital allocation 후보는 다음을 모두 만족해야 live 후보가 된다.

```text
1. no-op 재현 성공
2. offline reweighting probe에서 noise보다 큰 개선 확인
3. 동일 총노출 기준 baseline 대비 수익률 또는 방어 지표 개선
4. time_out drag 악화 없음
5. stop-risk stress에서 붕괴하지 않음
6. 연도별 2024/2025 모두 성과가 극단적으로 무너지지 않음
7. 특정 ticker 과집중 없음
8. turnover/slippage 반영 후에도 우위 유지
```

위 조건을 만족하지 못하면 자동 자본 배분은 연구 모드로 유지하고, 수익률 개선의 주 레버를 다음으로 옮긴다.

```text
universe 확대
차기 RUN에서 T+1/conservative_core 기준 룰북 재선정
post-entry winner scaling
```
