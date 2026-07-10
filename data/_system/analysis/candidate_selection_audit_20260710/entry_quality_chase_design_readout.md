# entry_quality 5일 룩백 실사용 검증 + 추격 게이트 설계 readout

범위: 코드·설정·주문·기존 라이브 상태 변경 없음. `entry_quality` 계산·전달·소비 경로를 코드에서 추적하고, 현재 live 18개 및 frozen OOS 12,915건에 추격률 임계값을 read-only로 시뮬레이션했다. 구현은 하지 않았다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/entry_quality_chase_design_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/entry_quality_chase_design_live18.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/entry_quality_chase_threshold_summary.csv`

## A. entry_quality 실사용 검증

### A-1. 최종 판정

판정은 경로를 나누어야 정확하다.

```text
실거래 후보 생성·정렬·export·수동/자동 매수 경로:
EQ_TRULY_INACTIVE

저장소 전체:
EQ_PARTIALLY_USED
```

`EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE` 라벨은 **실거래 후보 경로의 실제 동작과 일치한다.**

`entry_quality`는 `evaluate_candidate()`에서 계산되지만, live candidate pool에서는 `allow`, `score`, `size_factor`를 의도적으로 버린다. 반면 별도 shadow 가상 ledger와 strategy simulator에서는 실제 진입 차단·사이징에 사용한다. 따라서 저장소 전체를 하나로 보면 `EQ_PARTIALLY_USED`, 이번 질문의 핵심인 live 후보/실매수 경로만 보면 `EQ_TRULY_INACTIVE`다.

### A-2. 5일 룩백 값 계산 지점

파일:

```text
engine/live/elite_shadow_entry_quality.py
```

주요 계산:

| 값 | 코드 위치 | 계산 방식 |
|---|---|---|
| `ret_5d_pct` | lines 69-103, 특히 97 | 현재 price 대비 `closes[-6]` |
| `bounce_low5_pct` | lines 81-103, 특히 102 | 현재 price 대비 최근 5봉 low |
| `dist_high5_pct` | lines 82-103, 특히 103 | 현재 price 대비 최근 5봉 high |
| `volume_ratio20` | lines 88-112 | 마지막 일봉 volume / 직전 20봉 평균 |
| quality score | lines 116-185 | follow-through 점수 0~100 |
| allow/block/size factor | lines 188-299 | Q<45, no-follow, overheat, high-vol 등 |

중요하게도 `_technical_snapshot()`의 `current`는 `price` 인자를 우선 사용한다. 즉 5일 지표는 일봉 히스토리와 별도 현재가를 섞어 계산한다.

### A-3. evaluate_candidate에서의 전달

파일:

```text
engine/live/elite_shadow_trader.py
```

흐름:

```text
lines 421-432: evaluate_signal() 실행
lines 435-437: score / threshold / ratio 확정
lines 440-449: 그 이후 assess_shadow_entry_quality() 계산
lines 450-468: should_buy와 entry_quality를 별도 필드로 반환
```

`entry_quality`는 `should_buy`, `score`, `threshold`, `ratio`가 이미 확정된 뒤 계산된다.

`evaluate_signal()` 자체의 final score 경로는 다음과 같다.

```text
engine/strategies/evaluator.py
lines 58-195: MA/MACD/RSI/BB/volume/news/events component 합산
lines 206-223: market adjustment
line 223: final_score = raw_score * market_adjustment
line 225: should_buy = final_score >= signal_threshold
```

여기에 `entry_quality`, `ret_5d_pct`, `bounce_low5_pct`, `dist_high5_pct`는 들어가지 않는다.

결론:

```text
should_buy 영향: 없음
final_score 영향: 없음
```

### A-4. live candidate pool에서 명시적 폐기

파일:

```text
data/_system/ops/live_candidate_slots.py
```

`public_candidate_row()`:

```text
lines 274-276:
  EQ는 후보 자격·정렬에서 배제한다고 명시

lines 295-300:
  entry_quality_policy = EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
  entry_quality_allow = None
  entry_quality_score = None
  entry_quality_label = EQ_FILTER_UNVERIFIED
  primary_reason = excluded_from_candidate_decision_after_eq_validity_20260708
```

`refresh_slots()`의 실제 통과 조건:

```text
lines 392-398: gate 존재 + gate_keep
lines 399-400: held exclusion
lines 403-415: evaluate_candidate ok + should_buy
line 418: pool append
```

EQ `allow`를 읽는 분기가 없다.

정렬:

```text
live_candidate_slots.py lines 318-319
(priority_group, -final_score, ticker, candidate_id)
```

EQ score는 정렬 키에 없다.

### A-5. gate_keep과의 관계

`gate_keep`은 5일 entry_quality와 무관하다.

```text
data/_system/ops/live_candidate_slots.py lines 172-214
source: entry_quality_stops_regime_20260707/entry_filter_candidates.csv
rule: IS worst_mae bottom 20% AND IS avg_mfe_capture <= median이면 DROP
```

사용 필드:

```text
drop_bad_mae_capture
is_worst_mae_pct
is_avg_mfe_capture
```

`ret_5d_pct`, `bounce_low5_pct`, `dist_high5_pct`를 읽지 않는다.

결론:

```text
gate_keep 영향: 없음
```

### A-6. export·실매수 경로

Export:

```text
scripts/export_real_dashboard_buy_candidates.py
lines 468-505
```

검사 항목은 full candidate, full rulebook validation, `evaluate_candidate().ok`, `should_buy=True`, candidate row validation이다. EQ allow/score를 검사하지 않는다.

수동/direct 매수:

```text
engine/live/real_dashboard_api.py
_candidate_for_real(): lines 676-689
_create_real_buy_intent(): lines 698-784
```

candidate 존재/status/manual flag, notional, broker/current price, fallback safety만 확인한다. EQ를 읽지 않는다.

S2 auto:

```text
engine/live/s2_auto_trader.py
candidate_pool sort: lines 290-298
signal revalidation: lines 313-329
order plan: lines 361-386
submit: lines 421-460
```

정렬은 `priority_group`과 `final_score`, 재검증은 `should_buy`만 사용한다. EQ는 사용하지 않는다.

### A-7. 어디에서는 실제 사용되는가

별도 shadow 가상 거래에서는 EQ가 실제로 작동한다.

```text
engine/live/elite_shadow_trader.py lines 808-826
entry_quality.allow=False이면 virtual open 차단
size_factor로 virtual notional 축소

engine/live/elite_strategy_sim.py lines 243-251, 473-483
entry_quality decision으로 simulation 진입 차단·사이징
```

`engine/live/elite_shadow_entry_quality.py` 파일 상단 lines 3-6도 “실제 broker 주문에는 관여하지 않고 elite shadow 가상 ledger 신규 OPEN 전에만 사용”한다고 명시한다.

따라서 저장소 전체 판정은 `EQ_PARTIALLY_USED`지만, 이 사용은 live 후보·실주문 경로와 분리되어 있다.

### A-8. live 18개 실증 확인

현재 live 18개를 동일 평가 스택으로 재계산하면 EQ 자체는 다음처럼 나온다.

| 가상 EQ 효과 | 개체 수 | 종목 |
|---|---:|---|
| BLOCK | 10 | CMC, BCS, BGC, BMA, BMI, BN, BTE, BWXT, CBRL, CRS |
| SIZE_REDUCE | 3 | ACMR, ADMA, BTBT |
| ALLOW | 5 | AEIS, ALGT, ANET, ARKW, BB |

하지만 실제 live 18개에는 이 10개 BLOCK 후보도 그대로 포함되어 있다. CSV의 `live_eq_consumed=False`가 이를 표시한다.

즉 EQ가 silently used였다면 현재 live 후보 수와 순위가 크게 달라졌어야 하지만, 실제로는 그렇지 않다.

### A-9. CE 및 기존 deny-list 개체 확인

현재 read-only 재평가:

| ticker | should_buy | EQ allow | EQ score | EQ reason |
|---|---:|---:|---:|---|
| CE | False | False | 10 | failed_follow_through_q_lt_45 |
| CDE | True | False | 42 | failed_follow_through_q_lt_45 |
| BKSY | False | False | 2 | failed_follow_through_q_lt_45 |
| BOIL | True | False | 0 | failed_follow_through_q_lt_45 |

CDE와 BOIL은 현재 `should_buy=True`인데 EQ는 block이다. deny-list가 없었다면 live 후보 경로는 EQ block을 무시하고 통과시킬 구조다.

CE가 과거 후보였을 때도 동일하게 `public_candidate_row()`가 EQ 값을 `None/UNVERIFIED`로 치환했으므로, CE의 당시 통과·순위는 5일 EQ의 영향을 받지 않았다. 현재 제외 근거는 candidate deny-list이며 EQ와 별개다.

### A-10. 라벨 최종 판정

```text
EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
= live candidate selection 실제 코드와 일치
```

다만 이름이 `entry_quality`인 파일과 shadow simulator에서 실제 차단이 존재하므로, 운영자가 shadow 결과와 live 후보 정책을 혼동할 위험은 있다. live source policy의 주석과 `None` 치환은 이 혼동을 막기 위한 의도적 분리다.

---

## B. 추격 게이트 설계

### B-1. 설계 목표

후보 목록과 순위는 유지한다. 사용자는 ANET처럼 추격 상태인 후보를 대시보드에서 볼 수 있다. 다만 실제 신규 BUY 직전에 실행 가격과 최초 신호 가격을 비교해 진입만 차단한다.

```text
후보 생성 차단: 하지 않음
export 차단: 하지 않음
대시보드 숨김: 하지 않음
실제 BUY 직전 차단: 권장
```

이 방식은 “신호가 지속되는지”와 “그 가격에 지금 진입해도 되는지”를 분리한다.

### B-2. 추격률 정의

```text
chase_pct = (execution_current_price - first_signal_price)
            / first_signal_price * 100
```

권장 block 조건:

```text
chase_pct >= configured_max_chase_pct
```

음수 또는 임계값 미만이면 chase 측면에서는 PASS다. 다른 safety/gate는 그대로 유지한다.

### B-3. first_signal_price 결측 처리

권장 정책은 fail-closed다.

```text
first_signal_price is None / non-finite / <=0
또는 first_signal_at missing
=> CHASE_REFERENCE_UNKNOWN
=> 후보는 화면에 유지
=> 신규 BUY는 차단
```

이유:

- 기준가가 없으면 추격률을 안전하게 계산할 수 없다.
- 임의로 candidate current price를 first signal price로 대체하면 chase가 0%가 되어 gate가 무력화된다.
- 조용히 PASS시키는 것보다 명시적 UNKNOWN 차단이 안전하다.

향후 수동 override를 허용하려면 일반 매수 버튼과 분리된 명시적 승인 절차가 필요하다. 기본 정책에는 포함하지 않는다.

### B-4. 확정 권장 부착 지점

#### 수동/direct real dashboard

```text
engine/live/real_dashboard_api.py::_create_real_buy_intent()
```

정확한 위치:

```text
line 768: broker.get_current_price(ticker)
lines 769-770: current price 유효성 확인
--- 여기서 chase guard ---
line 771: shares 계산
line 772: broker.place_buy()
```

이 위치의 장점:

- 후보는 대시보드에 계속 보인다.
- export 시점 가격이 아니라 주문 직전 broker 가격으로 계산할 수 있다.
- 차단 시 rejected intent/event에 `first_signal_price`, `execution_price`, `chase_pct`, threshold, reason을 남길 수 있다.
- 기존 candidate selection, ranking, gate_keep, should_buy는 건드리지 않는다.

#### S2 auto 경로

`_create_real_buy_intent()`만 수정하면 S2 auto는 우회한다.

```text
engine/live/s2_auto_trader.py::submit_plan()
lines 438-459
```

auto도 실제 `place_buy()` 직전에 broker 가격을 새로 받아 동일 shared chase guard를 호출해야 한다. `compute_order_plan()`의 evaluation price만 믿으면 계획 생성과 제출 사이 가격 변화를 놓칠 수 있다.

따라서 최종 설계는 다음과 같다.

```text
shared evaluate_chase_guard(candidate, execution_quote, threshold)
  called by _create_real_buy_intent before direct place_buy
  called by s2_auto_trader.submit_plan before auto place_buy
```

shared guard를 쓰지 않으면 한 경로만 막히고 다른 경로로 추격 주문이 나갈 수 있다.

### B-5. 후보 표시용 상태

구현 시 후보 row 자체를 제거하지 않고 다음 진단 필드를 API 응답에 추가하는 방식이 적합하다.

```text
first_signal_price
execution/current_price
price_as_of
price_age_sec
chase_pct
chase_threshold_pct
chase_gate_status = PASS / BLOCK / UNKNOWN
chase_gate_reason
```

단, 화면 표시용 chase 값과 주문 차단용 값은 다를 수 있다. 주문 차단은 반드시 주문 직전 fresh broker quote로 다시 계산해야 한다.

### B-6. 임계값 시뮬레이션 — live 18개

가격 기준:

- current-session 1분 가격이 확인된 14개: 최신 관측 가격 사용.
- current-session 가격이 없던 4개(AEIS, ALGT, BGC, BMA): canonical candidate snapshot을 참고값으로만 사용하고 stale 표시.

| threshold | 차단 수 | 차단 종목 | ANET +11.55% | AEIS +6.60% |
|---:|---:|---|---:|---:|
| +5% | 2 | ANET, AEIS | 차단 | 차단 |
| +8% | 1 | ANET | 차단 | 통과 |
| +10% | 1 | ANET | 차단 | 통과 |
| +15% | 0 | 없음 | 통과 | 통과 |

ANET은 fresh current-session 가격 기준 +11.55%다.

AEIS +6.60%는 기존 canonical snapshot 기준이며, current-session 1분 가격이 확인되지 않은 stale 사례다. 실제 권장 execution guard에서는 숫자 임계값보다 먼저 `PRICE_FRESHNESS_UNKNOWN/STALE`로 차단해야 한다. 표는 요청한 임계값 비교를 위한 reference-only 결과다.

### B-7. frozen OOS 정합성

대상:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv
OOS trades = 12,915
```

백테스트 proxy:

```text
first_signal_price proxy = signal_date Close
execution_current_price proxy = D+1 entry_price
```

| threshold | OOS 차단 수 | 차단률 | 통과 수 |
|---:|---:|---:|---:|
| +5% | 302 | 2.338% | 12,613 |
| +8% | 93 | 0.720% | 12,822 |
| +10% | 55 | 0.426% | 12,860 |
| +15% | 21 | 0.163% | 12,894 |

해석:

- +8% 이상부터 OOS 진입의 1% 미만만 잘린다.
- +10%는 0.426%, +15%는 0.163%다.
- +5%는 2.338%로 작지만 “거의 0”이라고 부르기에는 상대적으로 크다.
- frozen backtest는 trading-bar 기준 전부 D+1 open 진입이므로 며칠 동안 유지된 live 신호를 뒤늦게 사는 상황 자체가 거의 없다.

이 gate의 목적은 백테스트 성과 최적화가 아니다.

```text
라이브 실제 진입가를
백테스트의 D+1 진입 전제에 더 가깝게 제한하는 실행 보호 장치
```

OOS 수치는 threshold를 선택하기 위한 성과 검증이 아니라, 백테스트 전제에서 얼마나 자주 발동하는지 확인한 것이다. 기업행위·가격조정 이상치가 포함될 수 있어 실제 성과 검증에서는 split-adjusted 기준가 처리도 필요하다.

### B-8. threshold 선택 상태

이번 단계에서는 threshold를 확정하지 않는다.

```text
+5 / +8 / +10 / +15 시뮬레이션만 완료
실제 threshold 선택: 별도 OOS 성과 검증 후
라이브 구현: 별도 지시 후
```

### B-9. 트레이드오프

장점:

- 후보 표시와 실행 안전을 분리한다.
- first signal 이후 가격이 멀어진 ANET 유형을 주문 직전에 막는다.
- export 후 가격 변화도 broker quote로 다시 확인할 수 있다.
- candidate generation/selection 성과를 직접 훼손하지 않는다.

단점·주의:

- 강한 momentum winner를 차단할 수 있다.
- first signal이 inactive 후 재활성화될 때 기준가가 reset되는 현재 semantics를 명확히 유지해야 한다.
- split·reverse split 발생 시 raw `first_signal_price`가 무효가 될 수 있다.
- threshold는 아직 성과 검증되지 않았다.
- 모든 BUY 경로가 shared guard를 호출하지 않으면 우회가 생긴다.

---

## C. stale price 부수 문제

확인된 별도 문제:

```text
engine/live/elite_shadow_trader.py::_latest_price()
```

이 함수는 Yahoo 1분 history의 마지막 Close를 가져오지만, bar timestamp가 현재 세션인지 또는 몇 분 전인지 검사하지 않는다. 30초 cache TTL은 “가져온 값의 캐시 시간”일 뿐, 원본 bar 자체의 freshness를 보장하지 않는다.

이전 시점 추적에서 확인된 종목:

| ticker | 마지막 1분 가격 age |
|---|---:|
| AEIS | 약 11.2시간 |
| ALGT | 약 12.4시간 |
| BGC | 약 13.2시간 |
| BMA | 약 13.0시간 |

따라서 chase guard와 별개로 다음이 필요하다.

```text
CURRENT_PRICE_FRESHNESS_GUARD 후보 지점:
execution-time broker quote acquisition immediately before place_buy
```

권장 원칙:

- price와 함께 `as_of` timestamp를 확보.
- 정규장 중 허용 age를 넘으면 `PRICE_STALE` 차단.
- timestamp를 제공하지 못하면 `PRICE_FRESHNESS_UNKNOWN`으로 fail-closed.
- candidate snapshot 또는 `_latest_price()`의 scalar 값만으로 실주문 chase 판정을 하지 않음.

이번 단계에서는 현재가 freshness guard도 구현하지 않았다.

## 최종 결론

```text
A. live 후보/실주문 경로 EQ 판정: EQ_TRULY_INACTIVE
A. 저장소 전체 EQ 판정: EQ_PARTIALLY_USED (shadow virtual/sim only)
A. NOT_A_GATE 라벨과 live 실제 코드: 일치

B. 추격 게이트 권장 위치:
후보 생성이 아니라 주문 직전 shared execution guard
- _create_real_buy_intent before broker.place_buy
- s2_auto_trader.submit_plan before broker.place_buy

B. first_signal_price 결측: UNKNOWN fail-closed
B. threshold: 아직 미확정, 시뮬레이션만 완료
C. stale current price 문제: 별도 freshness guard 필요
```

설계만 완료했으며 코드·설정·주문 경로는 변경하지 않았다.
