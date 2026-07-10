# 추격 매수 방지 게이트 존재 여부 확인 readout

범위: 코드·데이터·설정·주문 변경 없음. 실주문/재학습/direct order 설정 변경 없이, 라이브 후보 선정·수동매수·자동매수·frozen backtest 산출물을 읽어서 추적했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/chase_gate_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/chase_gate_live18_scan.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/chase_gate_backtest_entry_gap_stats.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/chase_gate_backtest_oos_gap_top_samples.csv`

## 1. 최종 판정

| 경로 | 판정 | 근거 |
|---|---|---|
| 후보 선정: `build_elite_shadow_report -> refresh_slots -> evaluate_candidate -> export` | `CHASE_GATE_ABSENT` | `first_signal_at/first_signal_price`는 pool 통과 후 기록/복사되는 메타데이터다. `final_score >= threshold`, gate_keep, held exclusion 외에 최초 신호가 대비 현재가 상승률 컷이 없다. |
| 수동/직접 매수: `_create_real_buy_intent` | `CHASE_GATE_ABSENT_AT_EXECUTION` | 후보 존재/status/manual flag/notional/current price만 확인 후, direct enabled면 broker market buy로 간다. first_signal 대비 상승률 체크가 없다. |
| S2 auto 후보 실행: `s2_auto_trader.compute_order_plan/submit_plan` | `CHASE_GATE_ABSENT_AT_EXECUTION` | candidate_pool top row를 다시 `evaluate_candidate`로 should_buy 검증하지만 first_signal 대비 상승률 체크가 없다. |
| next-open queue 구형/중앙 경로 | `PARTIAL_OPENING_GAP_GUARD_ONLY` | `scheduled_open_buy_queue`에는 opening price guard가 있으나, 기준은 `reference_price`/전일종가 대비 current premium이다. first_signal_at/first_signal_price 기반 추격률 게이트는 아니다. |

요약: 현재 실전 후보 파일과 대시보드 매수 경로 기준으로는, **며칠 전 최초 신호 가격보다 이미 오른 후보가 계속 buy candidate로 남고, 수동/직접 매수까지 갈 수 있는 구조**다.

## 2. 후보 선정 경로 코드 확인

### 2.1 `refresh_slots()`

`data/_system/ops/live_candidate_slots.py` 기준:

- lines 381-382: `build_elite_shadow_report(...)` 후보를 가져와 상위 `max_candidates`를 사용.
- lines 392-418: gate 확인 순서:
  - gate_map 존재
  - `gate_keep=True`
  - held exclusion 아님
  - `evaluate_candidate(...).ok=True`
  - `should_buy=True`
  - 이후 `pool.append(public_candidate_row(...))`
- lines 419-450: 그 다음에야 `first_seen_signals`를 갱신하고 `first_signal_at`, `first_signal_price`, `first_final_score`를 row에 붙임.
- line 451: sort 후 `candidate_pool`에 저장.

따라서 `first_signal_at/price`는 후보를 통과시킨 뒤 붙는 메타데이터다. 이 값으로 후보를 막는 분기는 없다.

### 2.2 `evaluate_candidate()`

`engine/live/elite_shadow_trader.py` 기준:

- lines 403-408: OHLCV와 현재 price 확인.
- lines 421-432: `evaluate_signal()` 호출.
- lines 435-459: score/threshold/ratio 계산.
- lines 440-462: `assess_shadow_entry_quality()`를 계산해 반환.

여기에서도 first signal price 대비 chase 계산은 없다. `evaluate_signal()`은 현재 df 기반 점수/threshold 판단이고, 최초 신호 시점 대비 상승률은 입력으로 받지 않는다.

### 2.3 entry_quality 계열 확인

`engine/live/elite_shadow_entry_quality.py`에는 `ret_5d_pct`, `bounce_low5_pct`, `dist_high5_pct`, `overheat` 같은 가격 추종성/과열 관련 지표가 있다. 그러나 `live_candidate_slots.public_candidate_row()` 주석과 필드가 다음처럼 되어 있다.

```text
EQ(entry_quality)는 EQ_FILTER_UNVERIFIED 판정으로 후보 자격·정렬에서 배제한다.
entry_quality_allow = None
entry_quality_label = EQ_REFERENCE_LABEL
entry_quality_primary_reason = excluded_from_candidate_decision_after_eq_validity_20260708
```

즉 entry_quality는 현재 라이브 후보 선정 gate가 아니다. 이전 결론인 `EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE`와 일치한다.

### 2.4 export

`scripts/export_real_dashboard_buy_candidates.py` 기준:

- lines 446-448: `build_elite_shadow_report()`로 full candidate 재조회.
- lines 473-490: full_candidate 존재, full rulebook validation, evaluate_candidate ok, should_buy true만 확인.
- lines 493-505: candidate row validation 후 export.

여기에도 first_signal 대비 상승률 차단은 없다.

## 3. 수동/직접 매수 경로 확인

`engine/live/real_dashboard_api.py::_create_real_buy_intent()` 기준:

- lines 676-689: `_candidate_for_real()`은 candidate 존재, status, manual_buy_enabled만 확인.
- lines 698-731: intent row 생성. candidate price는 snapshot 필드로 저장.
- lines 734-772: direct order enabled면 fallback candidate 차단, broker/current price 확인 후 market buy.

`first_signal_at`과 `first_signal_price`는 candidate_snapshot에 들어갈 수 있지만, 매수 전 차단 조건으로 쓰이지 않는다.

판정: `CHASE_GATE_ABSENT_AT_EXECUTION`.

## 4. 자동매수 경로 확인

`engine/live/s2_auto_trader.py` 기준:

- lines 290-298: `candidate_pool()`은 live_slots_state의 candidate_pool을 정렬해 가져온다.
- lines 313-329: `_validate_candidate_signal()`은 full candidate 재조회 후 `evaluate_candidate()`와 `should_buy`만 재검증한다.
- lines 361-384: pool[0] 기준 가격과 수량을 산출한다.
- lines 421-459: safety layer 후 market buy를 낸다.

여기에도 first_signal 대비 상승률 체크는 없다.

별도 next-open queue인 `engine/live/scheduled_open_buy_queue.py`에는 opening price guard가 있다.

```text
current_price <= reference_price * (1 + max_premium_pct / 100)
```

하지만 이것은 전일종가/reference_price 대비 개장 프리미엄 방지다. 이번 질문의 핵심인 “최초 신호 시점 가격 대비 현재가 상승률” 제한은 아니다.

## 5. 백테스트 진입 구조와 가격 괴리 통계

분석 대상:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv
snapshot_dir: data/_system/analysis/ohlc_snapshot_20260707
```

계산 방식:

- `signal_date`의 frozen OHLC `Close`를 신호 발생가 proxy로 사용.
- `entry_price`는 frozen trade row의 실제 entry_price 사용.
- `entry_vs_signal_close_pct = entry_price / signal_date_close - 1`.
- calendar lag와 trading-bar lag를 분리.

결과:

| scope | n | mean gap | median | p90 | p95 | >5% | >10% | calendar lag dist | trading lag dist |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| ALL | 43,972 | +0.107% | +0.060% | +1.887% | +2.857% | 680 | 126 | `{1:34201, 2:426, 3:8034, 4:1311}` | `{1:43972}` |
| IS | 31,057 | +0.080% | +0.040% | +1.750% | +2.592% | 378 | 71 | `{1:24250, 2:217, 3:5697, 4:893}` | `{1:31057}` |
| OOS | 12,915 | +0.172% | +0.105% | +2.242% | +3.422% | 302 | 55 | `{1:9951, 2:209, 3:2337, 4:418}` | `{1:12915}` |

해석:

- 사용자가 언급한 lag 분포 `1일 34,201 / 2일 426 / 3일 8,034 / 4일 1,311`은 calendar lag 기준으로 재현됐다.
- 하지만 trading-bar lag는 전부 1이다. 즉 백테스트는 구조상 **신호일 D 다음 거래일 D+1 open 진입**이다. 3~4 calendar days는 주말/휴장으로 인한 달력 지연이지, 신호가 여러 날 지속된 뒤 늦게 들어가는 구조가 아니다.
- 다만 D close -> D+1 open gap은 존재한다. OOS median은 +0.105%, p95는 +3.422%, >10% gap은 55건이다.
- `CAPR` OOS 샘플처럼 +371.7% outlier가 있다. 이는 split/가격 조정 이슈 가능성이 높아 보이며, 평균보다 median/p90/p95를 더 신뢰하는 것이 안전하다.

## 6. 현재 live 18개 추격 상태

기준 파일:

```text
data/_system/real_dashboard_buy_candidates.json
updated_at = 2026-07-10T10:01:09.313612+00:00
```

요약:

| 항목 | 값 |
|---|---:|
| live 후보 | 18 |
| first_signal_at 24h 초과 | 14 |
| first_signal 대비 +5% 이상 | 2 |
| first_signal 대비 +10% 이상 | 1 |

+10% 이상:

```text
ANET: first_signal_price 164.50 -> current 184.22 = +11.99%, age 59.65h
```

+5% 이상:

```text
ANET: +11.99%, age 59.65h
AEIS: +6.60%, age 59.65h
```

상위 추격률:

```text
ANET +11.99%
AEIS +6.60%
BTBT +4.88%
ALGT +4.23%
BB +3.39%
ARKW +2.25%
CBRL +1.03%
```

이 명단은 `chase_gate_live18_scan.csv`에 전체 18개로 저장했다.

## 7. 종합 결론

판정:

```text
CHASE_GATE_ABSENT
CHASE_GATE_ABSENT_AT_EXECUTION
```

현재 구조에서는 다음이 가능하다.

```text
1) 후보가 최초 신호 시점에 candidate_pool에 들어온다.
2) first_signal_at/first_signal_price는 기록된다.
3) 며칠 뒤 가격이 이미 상승해도, 현재 evaluate_signal이 should_buy=True이면 후보에 계속 남는다.
4) export 후 수동/직접매수 또는 S2 auto 경로에서 first_signal 대비 상승률을 다시 차단하지 않는다.
```

백테스트는 trading-day 기준 D+1 open 진입이라 “며칠 지난 신호를 현재가로 따라 사는 구조”는 아니다. 라이브는 후보가 며칠 유지될 수 있고, 현재 후보 중 실제로 ANET은 최초 신호가 대비 +11.99% 상태로 남아 있다. 따라서 리스크는 백테스트 곡선 자체보다 **진입가 괴리**에서 발생한다.

## 8. 추격률 게이트 부착 후보 지점, 구현 안 함

이번 세션에서는 구현하지 않았다. 실제 적용 전에는 OOS 검증이 필요하다.

후보 지점:

1. `data/_system/ops/live_candidate_slots.py::refresh_slots()`
   - 위치: `first_seen_signals`를 row에 붙인 뒤, `pool = sort_candidate_pool(pool)` 전.
   - 장점: 후보 pool/export/수동매수까지 한 번에 차단.
   - 주의: 첫 진입 당일은 `first_signal_price=current_price`로 기록되므로 통과시키고, 이후 refresh부터 chase 계산 가능.

2. `scripts/export_real_dashboard_buy_candidates.py::build_export_payload()`
   - 위치: `live_row` 기반 `build_candidate_row()` 전.
   - 장점: 정규 후보 파일로 나가는 마지막 안전망.
   - 단점: live candidate_pool/UI 슬롯에는 남을 수 있음.

3. `engine/live/real_dashboard_api.py::_candidate_for_real()` 또는 `_create_real_buy_intent()`
   - 위치: broker current price 조회 후 `place_buy()` 전.
   - 장점: 수동/직접매수 직전 최종 차단.
   - 단점: 후보 목록에는 계속 보임.

4. `engine/live/s2_auto_trader.py::_validate_candidate_signal()` 또는 `compute_order_plan()`
   - 위치: current `evaluate_candidate()` 후 price 산출 직후.
   - 장점: auto order plan 직전 차단.

5. `engine/live/scheduled_open_buy_queue.py::_opening_price_guard_allows()` 확장
   - 현재 전일종가/reference_price 가드가 있으므로, queue row에 first_signal_price를 싣는 설계가 필요하다.
   - 단, 이것은 next-open queue 전용이고 real dashboard isolated candidate path와는 별개다.

## 9. 주의

이 결과는 “추격 매수 가능 구조인지”를 확인한 것이다. 바로 `10% 컷` 같은 규칙을 라이브에 넣자는 결론이 아니다. BB+RSI gate 사례처럼 그럴듯한 컷도 OOS에서 성과를 깎을 수 있으므로, 실제 chase gate는 별도 frozen OOS 검증 후 판단해야 한다.
