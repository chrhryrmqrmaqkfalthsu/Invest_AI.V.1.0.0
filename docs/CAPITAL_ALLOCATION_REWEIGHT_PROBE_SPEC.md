# Capital Allocation Reweight Probe Spec

작성일: 2026-06-10 KST  
상태: 구현 및 1차 실행 완료  
연결 문서: `docs/CAPITAL_ALLOCATION_V7_DESIGN.md`  
실행 모드: `run_central_portfolio_noop_gate.py --mode capital_reweight_probe`

---

## 1. 목적

이 probe는 실제 capital allocation simulator를 구현하기 전에, 기존 `realistic_research_baseline` 71개 거래에 가상의 notional multiplier를 적용해 variable entry sizing이 식별 가능한 개선 여지가 있는지 확인한다.

중요한 제한:

```text
이 probe는 새 진입/청산 경로를 재시뮬레이션하지 않는다.
기존 거래 로그의 realized pnl을 동일 총 gross entry exposure 기준으로 재가중한다.
따라서 통과하더라도 sizing engine 구현 전 선행 증거일 뿐이며, live 후보가 아니다.
```

---

## 2. feasibility 점검 결과

기준 파일:

```text
data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv
```

필수 필드:

```text
entry_date: 있음
exit_date: 있음
ticker: 있음
pnl_pct: 있음
entry_signal_score: 있음
entry_signal_threshold: 있음
```

동시 진입:

```text
총 진입일: 63일
2종목 이상 동시진입한 날: 8일
```

동시 보유:

```text
이벤트 날짜 수: 120
평균 동시보유: 3.17
최대 동시보유: 6
2종목 이상 보유 이벤트일: 112
5종목 이상 보유 이벤트일: 13
```

총노출 cap binding rough check, fixed_30 기준:

```text
cap=120 USD: 이벤트일 49/120 binding_or_over (40.8%)
cap=180 USD: 이벤트일 3/120 binding_or_over (2.5%)
cap=240 USD: 이벤트일 0/120 binding_or_over (0.0%)
cap=300 USD: 이벤트일 0/120 binding_or_over (0.0%)
cap=480 USD: 이벤트일 0/120 binding_or_over (0.0%)
cap=600 USD: 이벤트일 0/120 binding_or_over (0.0%)
```

판정:

```text
probe는 필드상 가능하다.
하지만 현재 480/600 USD cap에서는 총노출이 전혀 binding되지 않는다.
따라서 현재 baseline 조건에서 capital allocation은 '한정자본을 나눠담는 문제'가 아니라
'동일 총 gross exposure에서 거래별 notional을 재가중하는 문제'로 먼저 검증해야 한다.
```

---

## 3. 사전 고정 통과 기준

multiple testing을 막기 위해 probe 통과 기준은 후보 결과를 해석하기 전에 고정한다.

implementation-eligible 후보가 sizing engine 구현으로 넘어가려면 다음을 모두 만족해야 한다.

```text
1. full-sample return_delta_pct_of_gross_entry >= +1.50%p
2. 2024와 2025 개별 연도 return_delta가 모두 양수
3. leave-one-ticker-out 모든 경우에서 return_delta가 양수
4. time_out_loss_pnl_krw가 baseline보다 악화되지 않음
5. max_ticker_gross_share_pct <= 20.0%
```

해석:

```text
+1.50%p는 baseline 71거래 평균의 표준오차 약 0.75%의 2배다.
이 기준 미만의 개선은 noise와 구분하기 어렵다.
```

---

## 4. 사전 고정 후보

control:

```text
fixed_30_control
entry_day_equal_control
```

implementation-eligible 후보:

```text
entry_signal_score_halves_soft
live_strength_bucket_conservative
live_strength_monotone_soft
historical_quality_bucket
hybrid_floor_and_cap
```

exploratory only:

```text
equal_ticker_gross_exploratory
```

`equal_ticker_gross_exploratory`는 full-period realized ticker distribution을 사용하므로 좋아 보여도 구현 후보가 아니다.

---

## 5. 1차 실행 결과

실행 명령:

```bash
cd ~/kingmaker
venv/bin/python scripts/research/run_central_portfolio_noop_gate.py --mode capital_reweight_probe
```

산출물:

```text
data/_system/research/central_portfolio/capital_allocation_reweight_probe/probe_results.csv
data/_system/research/central_portfolio/capital_allocation_reweight_probe/summary.json
```

요약:

```text
trade_count: 71
ticker_count: 10
candidate_count: 8
eligible_candidate_count: 5
passed_candidate_count: 0
implementation_recommended: false
```

주요 후보 결과:

```text
fixed_30_control
  return_delta +0.0000%p

entry_signal_score_halves_soft
  return_delta +0.0107%p
  min_yearly_delta -0.0437%p
  min_leave_one_ticker_out_delta -0.0181%p
  time_out_loss_pnl 악화
  passed: false

live_strength_bucket_conservative
  return_delta +0.3049%p
  min_yearly_delta +0.1414%p
  min_leave_one_ticker_out_delta +0.2300%p
  time_out_loss_pnl 개선
  max_ticker_gross_share 21.93%
  passed: false

live_strength_monotone_soft
  return_delta +0.1727%p
  min_yearly_delta +0.0993%p
  min_leave_one_ticker_out_delta +0.1205%p
  time_out_loss_pnl 개선
  max_ticker_gross_share 23.09%
  passed: false

historical_quality_bucket
  return_delta -0.0249%p
  min_yearly_delta -0.0424%p
  min_leave_one_ticker_out_delta -0.0457%p
  time_out_loss_pnl 악화
  max_ticker_gross_share 27.54%
  passed: false

hybrid_floor_and_cap
  return_delta +0.2805%p
  min_yearly_delta +0.1253%p
  min_leave_one_ticker_out_delta +0.2287%p
  time_out_loss_pnl 개선
  max_ticker_gross_share 23.61%
  passed: false

 equal_ticker_gross_exploratory
  return_delta -0.0435%p
  min_yearly_delta -0.1735%p
  min_leave_one_ticker_out_delta -0.4193%p
  time_out_loss_pnl 악화
  passed: false
```

---

## 6. 결론

1차 probe에서는 sizing engine 구현을 추천할 후보가 없다.

```text
implementation_recommended: false
passed_candidate_count: 0
```

가장 좋아 보이는 `live_strength_bucket_conservative`도 full-sample 개선폭이 +0.3049%p에 불과하다. 이는 사전 기준 +1.50%p에 한참 못 미친다. 또한 max ticker gross share가 21.93%로 concentration guard 20%를 넘는다.

판정:

```text
현재 16종목 / 71거래 / fixed_30 / conservative_core baseline에서는
variable entry sizing으로 수익률을 유의미하게 끌어올릴 근거가 부족하다.
```

따라서 다음 단계는 variable entry sizing engine 구현이 아니다.

우선순위:

```text
1. capital_allocation_noop_gate로 fixed_30 realistic baseline 재현성 확보
2. 수익률 개선 레버는 universe 확대 또는 T+1/conservative_core 기준 차기 RUN으로 이동
3. post-entry winner scaling/add_buy는 별도 probe 설계 후 검토
4. market_history 기반 총노출 조절은 실제 market_score 연결 후 다시 power check
```
