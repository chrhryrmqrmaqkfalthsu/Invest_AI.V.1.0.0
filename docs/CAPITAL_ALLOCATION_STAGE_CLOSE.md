# Capital Allocation v7 Stage Close

작성일: 2026-06-10 KST  
상태: 1차 capital allocation 진입 sizing 연구 종료 판정  
관련 문서:

```text
docs/CAPITAL_ALLOCATION_V7_DESIGN.md
docs/CAPITAL_ALLOCATION_REWEIGHT_PROBE_SPEC.md
```

---

## 1. 결론

현재 `lr8d_stage1_20260609` 16종목, `fixed_30`, `T+1 open`, `conservative_core` baseline 조건에서는 variable entry sizing 구현을 진행하지 않는다.

판정:

```text
variable entry sizing implementation: 보류
capital allocation sizing engine full implementation: 보류
현재 단계 결론: 정직한 음성 결과
```

이 결론은 실패가 아니다. 사전 기준을 고정한 probe가 noise 수준의 개선을 걸러냈고, 과적합 sizing engine 구현을 막았다.

---

## 2. 판단 근거

### 2.1 realistic baseline은 확정됨

기준:

```text
conservative_core_exit_gate candidate
fractional + T+1 open + conservative_core
```

핵심 invariant:

```text
first_divergence_count: 5
first_ref_reason_counts: trailing 5
first_non_path_dependent_origin_count: 0
invariant_first_divergence_only_path_dependent: true
```

따라서 b2-2 exit look-ahead 제거는 의도한 path-dependent trailing/breakeven 축에서만 최초 차이를 만들었다.

### 2.2 진입 신호세기는 충분한 배분 신호가 아님

`entry_signal_score`:

```text
하위 평균 +2.51%, 상위 평균 +3.41%, 차이 +0.90%p
```

baseline 71거래 평균의 표준오차는 약 `0.75%`이므로 `+0.90%p`는 noise와 구분하기 어렵다.

`live_strength = entry_signal_score / entry_signal_threshold`:

```text
하위 평균 +1.92%, 상위 평균 +3.98%, 차이 +2.05%p
```

하지만 사분위는 비단조다.

```text
Q1 평균 +0.47%
Q2 평균 +3.05%
Q3 평균 +5.82%
Q4 평균 +2.58%, time_out 13건
```

따라서 `live_strength`가 높을수록 더 크게 배정하는 단순 sizing은 정당화되지 않는다.

### 2.3 market/sector/vix 필드는 현재 쓸 수 없음

baseline trade log에서 다음 필드는 모두 상수다.

```text
entry_market_adjustment = 1.0
entry_market_score      = 50.0
entry_sector_score      = 50.0
entry_vix_level         = 18.0
```

초기 상·하위 비교에서 보인 차이는 row order/tie가 만든 가짜 신호다. 시장 국면 기반 총노출 조절은 실제 market_history 연결 후 다시 검증해야 한다.

### 2.4 현재 총노출 cap은 binding되지 않음

fixed_30 기준 cap binding rough check:

```text
cap=120 USD: 49/120 이벤트일 binding_or_over (40.8%)
cap=180 USD: 3/120 이벤트일 binding_or_over (2.5%)
cap=240 USD: 0/120
cap=300 USD: 0/120
cap=480 USD: 0/120
cap=600 USD: 0/120
```

현재 baseline의 480/600 USD cap에서는 자본이 부족한 적이 없다. 따라서 지금 조건에서는 “누구에게 더 줄까”라는 capital allocation 문제가 구조적으로 거의 발생하지 않는다.

### 2.5 offline reweight probe 통과 후보 없음

실행 모드:

```bash
venv/bin/python scripts/research/run_central_portfolio_noop_gate.py --mode capital_reweight_probe
```

결과:

```text
candidate_count: 8
eligible_candidate_count: 5
passed_candidate_count: 0
implementation_recommended: false
```

사전 통과 기준:

```text
1. full-sample return_delta >= +1.50%p
2. 2024와 2025 둘 다 return_delta 양수
3. leave-one-ticker-out 모든 경우 return_delta 양수
4. time_out_loss_pnl 악화 없음
5. max_ticker_gross_share <= 20%
```

주요 후보의 full-sample 개선폭:

```text
entry_signal_score_halves_soft:   +0.0107%p
live_strength_bucket_conservative:+0.3049%p
live_strength_monotone_soft:      +0.1727%p
historical_quality_bucket:        -0.0249%p
hybrid_floor_and_cap:             +0.2805%p
```

가장 좋아 보이는 후보도 +0.3049%p로, 사전 기준 +1.50%p에 크게 못 미친다.

---

## 3. 현재 완성된 게이트 모드

`run_central_portfolio_noop_gate.py` 기준 현재 모드:

```text
comparison_infra_v0
engine_noop_v1
fractional_v2
live_current_proxy
tplus1_entry
conservative_core_exit
capital_reweight_probe
```

의미:

```text
comparison_infra_v0: 비교 인프라 self-vs-self
engine_noop_v1: legacy compat daily loop no-op
fractional_v2: fractional sizing 전환
live_current_proxy: live hard-stop guard proxy
 tplus1_entry: T+1 open 진입 게이트
conservative_core_exit: realistic_research_baseline 산출 및 b2-2 invariant 검증
capital_reweight_probe: variable entry sizing 구현 전 오프라인 식별력 검증
```

---

## 4. capital_allocation_noop_gate 상태

`capital_allocation_noop_gate`라는 별도 central simulator 재현 게이트는 아직 없다.

다만 현재 `capital_reweight_probe`는 다음 성격이다.

```text
입력: conservative_core_exit/candidate_trades.csv
simulate_exit 호출: 없음
run_legacy_compat_daily_loop 호출: 없음
새 진입/청산 시뮬레이션: 없음
```

따라서 probe는 baseline simulator를 변경하거나 재해석하지 않는다. baseline 무결성은 보존됐다.

향후 실제 central capital simulator를 만들 경우에는 반드시 별도 no-op 게이트를 먼저 만든다.

```text
필수 선행:
  capital_allocation_noop_gate
  fixed_30 + switch off + kill off + conservative_core
  realistic_research_baseline trade/equity/summary 재현
```

---

## 5. 다음 우선순위

수익률 개선 레버의 우선순위를 다음으로 확정한다.

### 5.1 1순위: universe 확대

이유:

```text
현재 16종목 baseline은 실제 신호 발생 ticker가 10종목뿐이다.
거래 수는 71건으로 작다.
480/600 USD cap에서 자본 경쟁이 발생하지 않는다.
```

universe 확대는 다음 두 효과를 동시에 기대할 수 있다.

```text
1. 신호 후보와 거래 수 증가
2. 동시진입/동시보유 증가로 capital allocation이 실제 의미를 가지는 조건 형성
```

따라서 수익률 개선을 목표로 한다면, 현 16종목에서 entry sizing을 튜닝하는 것보다 universe 확대가 우선이다.

### 5.2 2순위: T+1/conservative_core 기준 차기 RUN

이유:

```text
기존 룰북 expectancy는 T-close 진입 및 same-bar exit look-ahead 프리미엄을 일부 포함했을 수 있다.
conservative_core 기준으로 룰북을 다시 평가해야 정직한 survivor/promoted universe가 나온다.
```

차기 RUN 목표:

```text
T+1 open 진입
conservative_gap_fill
conservative_core path-dependent exit
same-bar trailing/breakeven activation look-ahead 제거
survivor/promoted universe 재선정
```

### 5.3 3순위: post-entry winner scaling / add_buy probe

진입 시점 정보는 배분 신호로 약했다. 하지만 진입 후 가격 경로는 별도 정보다.

따라서 add_buy/winner scaling은 바로 구현하지 않고 별도 probe로 검증한다.

예시 가설:

```text
진입 후 N일 내 unrealized gain이 일정 이상이고 trailing stop이 활성화된 포지션만 추가 배정한다.
time_out 후보처럼 횡보하는 포지션에는 추가 배정하지 않는다.
```

### 5.4 4순위: market_history 기반 총노출 조절

현재 trade log의 market 관련 값은 상수이므로 사용할 수 없다.

선행 조건:

```text
market_history 기반 market_score/regime/vix를 daily loop에 연결
trade log에 실제 entry_market_score/entry_regime/entry_vix_level 저장
power check 재실행
```

---

## 6. 최종 결정

현재 capital allocation entry sizing 줄기는 다음 상태로 닫는다.

```text
상태: 1차 연구 종료
결론: variable entry sizing 구현 보류
이유: 신호 변별력 부족 + cap non-binding + reweight probe 음성
다음 작업: universe 확대 또는 T+1/conservative_core 차기 RUN 설계
```

향후 universe가 커지고 cap이 실제로 binding되면, `capital_reweight_probe`를 같은 기준으로 다시 실행한다. 그때도 사전 기준을 통과한 후보만 sizing engine 구현 대상으로 삼는다.
