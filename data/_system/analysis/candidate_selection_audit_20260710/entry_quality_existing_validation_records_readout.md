# entry_quality / 5일 룩백 기존 백테스트 검증 기록 탐색

범위: 새 백테스트를 실행하지 않고 `data/_system/analysis` 및 관련 코드·git 이력에서 기존 기록만 탐색했다. 코드·설정·주문·기존 데이터는 변경하지 않았다.

탐색 결과 요약:

```text
analysis 파일 탐색 수: 300개
entry_quality / ret_5d / bounce_low5 / dist_high5 / EQ_FILTER 관련 textual hit: 26개
현행 EQ 복합 allow/block 로직의 OOS 검증 기록: 1개
현행 5일 피처 각각의 독립 ablation/OOS 검증 기록: 없음
현행 EQ가 OOS 개선을 입증한 기록: 없음
```

## 1. 최종 결론

```text
EQ/5일 룩백은 과거에 "돌려본 적은 있다".
그러나 정확 frozen replay가 아니었고, 근사 OOS 결과는 개선이 아니라 악화 방향이었다.
따라서 게이트 채택 근거는 없으며, 라이브 NOT_A_GATE 상태는 의도적 보류다.
```

상태를 정확히 구분하면 다음과 같다.

| 질문 | 판정 |
|---|---|
| 현행 `assess_shadow_entry_quality()` 전체 allow/block을 OOS에 적용해 본 기록이 있는가 | 있음, 2026-07-07 `eq_validity_20260708` |
| 정확히 같은 입력으로 완전 재현했는가 | 아니오, `EQ_NOT_REPRODUCIBLE` |
| 현행 함수 로직 자체는 같은 버전인가 | 예. `elite_shadow_entry_quality.py`는 2026-07-01 도입 후 변경 이력 없음 |
| 근사 OOS에서 개선됐는가 | 아니오. portfolio CAGR/Sharpe/MDD 모두 악화 |
| `ret_5d_pct`, `bounce_low5_pct`, `dist_high5_pct`를 각각 따로 검증한 기록이 있는가 | 없음 |
| 최종 채택됐는가 | 아니오. `EQ_FILTER_UNVERIFIED`, live `NOT_A_GATE` |
| 검증됐는데 연결만 안 된 상태인가 | 아니오. 검증 결과와 재현성 모두 채택을 지지하지 않아 의도적으로 배제 |

## 2. 관련 기록 목록

### 2.1 EQ 구현 도입 기록 — 검증 기록은 아님

```text
파일:
engine/live/elite_shadow_entry_quality.py
engine/live/elite_shadow_trader.py

커밋:
0782c35
2026-07-01 13:18:57 UTC
엘리트 쉐도우 신규 진입에 가격 추종성과 고변동 리스크 필터를 추가
```

이때 `shadow_entry_quality_v1`이 도입됐다. 현재 함수가 보는 주요 5일 계열 값은 다음이다.

```text
ret_5d_pct
bounce_low5_pct
dist_high5_pct
```

그 외에도 ret_1d/3d/10d, MA3/5/20 위치, higher close/low, volume ratio, ATR, event-heavy, bottom-fishing, overheat, low-price/high-vol 조건을 함께 쓴다.

중요: git 이력상 `engine/live/elite_shadow_entry_quality.py`는 이 도입 커밋 이후 변경 기록이 없다. 따라서 2026-07-07의 EQ 검증은 현재와 동일한 함수 버전을 import한 기록이다. 다만 입력 복원이 근사였다.

검증 여부:

```text
IS/OOS 비교 없음
채택 판단 없음
분류: IMPLEMENTATION_RECORD_ONLY
```

---

### 2.2 `entry_quality_stops_regime_20260707` — 이름은 entry quality지만 현행 EQ와 다른 필터

```text
디렉토리:
data/_system/analysis/entry_quality_stops_regime_20260707/

주요 파일:
readout.md
summary.json
entry_quality_feature_summary.csv
entry_filter_is_oos.csv
entry_filter_portfolio.csv
entry_filter_candidates.csv
run_entry_quality_stops_regime.py

생성 시각:
2026-07-07 19:38:22 UTC

커밋:
없음 / 현재 git 추적 대상 아님
```

#### 같은 로직인지

```text
현행 EQ와 다름
```

이 연구의 feature summary는 다음을 본다.

```text
std20
atr14_pct
entry_signal_score
score_excess
pre3_ret_pct
```

실제 후보 drop 규칙은 다음이다.

```text
IS worst_mae bottom 20%
AND IS avg_mfe_capture <= median
=> drop_bad_mae_capture
```

즉 `ret_5d_pct`, `bounce_low5_pct`, `dist_high5_pct`와 `assess_shadow_entry_quality().allow`를 재생한 연구가 아니다. 이름에 `entry_quality`가 들어가지만 현행 5일 follow-through EQ와 혼동하면 안 된다.

#### IS/OOS 결과

판정:

```text
ENTRY_FILTER_WEAK
```

OOS trade-level:

| 지표 | baseline | filter 적용 | 변화 |
|---|---:|---:|---:|
| n | 12,915 | 11,062 | -1,853 |
| avg net | 3.24742% | 3.04497% | -0.20245%p |
| win rate | 60.3252% | 60.3960% | +0.0707%p |
| avg MAE | -6.82048% | -6.47992% | +0.34057%p 개선 |
| sum net | 41,940.4 | 33,683.4 | -8,256.99 |

K=20 portfolio:

| 지표 | baseline | filter 적용 | 변화 |
|---|---:|---:|---:|
| CAGR | 72.2678% | 71.8848% | -0.3830%p |
| MDD | -21.9627% | -18.6361% | +3.3266%p 개선 |
| Sharpe | 1.84165 | 1.89463 | +0.05298 |

해석:

- MDD와 Sharpe는 소폭 좋아졌다.
- 평균 수익, 합산 수익, CAGR은 나빠졌다.
- 최종 verdict도 채택이 아니라 `ENTRY_FILTER_WEAK`다.

이 필터는 이후 `gate_keep`의 MAE/MFE 기반 gate source로 사용됐지만, 현행 5일 EQ allow/block의 검증 근거는 아니다.

채택 여부:

```text
별도 MAE/MFE gate로는 일부 사용
현행 5일 EQ 게이트 채택 근거로는 사용 불가
```

---

### 2.3 `eq_logic_trace_20260708` — 현행 EQ 정체 추적, OOS 검증 전 기록

```text
파일:
data/_system/analysis/eq_logic_trace_20260708/readout.md
data/_system/analysis/eq_logic_trace_20260708/slot_contrast.csv
data/_system/analysis/eq_logic_trace_20260708/source_trace.json

커밋:
ebe6392
2026-07-07 23:03:00 UTC
EQ 판정 로직 추적 read-only 분석 산출물 추가
```

#### 같은 로직인지

```text
현행 `assess_shadow_entry_quality()` 로직 자체를 추적
```

결론:

```text
EQ_INDEPENDENT
```

`evaluate_signal()`의 final_score/threshold/should_buy와 별개로, should_buy 이후에 붙는 독립 follow-through 휴리스틱임을 확인했다.

당시 live slot 8개 중:

```text
signal PASS: 8/8
EQ allow: 3/8
EQ block: 5/8
```

하지만 이것은 성과 검증이 아니라 구조와 현재 후보 대비표다.

IS/OOS 결과:

```text
없음
```

채택 판단:

```text
즉시 gate 승격 금지
별도 IS/OOS 또는 portfolio 검증 필요
```

---

### 2.4 `eq_validity_20260708` — 현행 EQ 복합 필터의 유일한 OOS 검증 기록

```text
디렉토리:
data/_system/analysis/eq_validity_20260708/

주요 파일:
readout.md
summary.json
eq_trade_labels_approx.csv
eq_group_performance.csv
eq_portfolio_compare.csv
eq_stat_tests.csv
eq_live_ledger_rows.csv
run_eq_validity.py

커밋:
071698e
2026-07-07 23:21:13 UTC
EQ allow block 유효성 분기형 검증 산출물 추가

생성 시각:
2026-07-07T23:19:52.042638+00:00
seed: 42
```

#### 같은 로직인지

```text
함수 버전: 현행과 동일
입력 재생: 근사
```

검증 스크립트는 실제 `engine.live.elite_shadow_entry_quality.assess_shadow_entry_quality`를 import했다. 현재 EQ 함수 파일은 2026-07-01 이후 변경 이력이 없으므로 allow/block 규칙 자체는 동일하다.

하지만 frozen row에서 정확히 복원하지 못한 입력이 있다.

```text
1. 당시 live 1분 current price가 frozen trade에 저장되지 않음
2. evaluate_signal reasons/components가 저장되지 않음
3. event_heavy와 bottom_fishing 일부가 reason 문자열에 의존
```

따라서 validation mode는 다음이었다.

```text
APPROX_OHLC_SIGNALDATE_CLOSE_NO_EVENT_NEWS_REASONS
EQ_NOT_REPRODUCIBLE
```

#### OOS group 결과

| 그룹 | n | win rate | avg net | sum net | avg MAE | worst MAE |
|---|---:|---:|---:|---:|---:|---:|
| EQ ALLOW | 5,690 | 61.2302% | 3.21646% | 18,301.7 | -6.32582% | -81.8785% |
| EQ BLOCK | 7,225 | 59.6125% | 3.27180% | 23,638.8 | -6.55398% | -52.0930% |

ALLOW가 win rate와 평균 MAE는 조금 나았지만, avg net은 BLOCK보다 낮았다.

통계검정:

```text
OOS allow - block avg net = -0.05535%p
95% CI = [-0.52785, +0.44486]
permutation p = 0.82084
```

즉 ALLOW 우위가 없고 통계적으로도 유의하지 않았다.

#### OOS portfolio 결과

K=20 final_score priority:

| 지표 | EQ 무시 | EQ allow-only | 변화 |
|---|---:|---:|---:|
| total signals | 12,915 | 5,690 | -7,225 |
| realized trades | 726 | 695 | -31 |
| final multiplier | 2.26947 | 2.01207 | 하락 |
| CAGR | 72.2678% | 59.0408% | -13.2269%p |
| MDD | -21.9627% | -25.6421% | -3.6794%p 악화 |
| Sharpe | 1.84165 | 1.77831 | -0.06334 |

근사 frozen 판정:

```text
EQ_FILTER_HURTS_APPROX
```

최종 표준 판정:

```text
EQ_FILTER_UNVERIFIED
```

정확 재현이 불가능하고, 근사 결과는 오히려 반대 방향이며, live ledger에는 EQ block 후보의 counterfactual trade가 없어서 승격 근거가 없다는 결론이었다.

#### 채택 여부

```text
게이트로 채택하지 않음
참조용 보류 / UNVERIFIED
```

이 기록이 현재 질문에 대한 가장 직접적인 답이다.

---

### 2.5 `live_slots_eq_removal_readout` — 검증 결과를 라이브 정책에 반영한 기록

```text
파일:
data/_system/analysis/eq_validity_20260708/live_slots_eq_removal_readout.md

커밋:
fd2400d
2026-07-08 04:43:31 UTC
라이브 후보 슬롯 EQ 판단 배제 및 검증 로직 고정
```

새 백테스트는 아니다. 바로 앞의 `eq_validity` 결과를 운영 코드에 반영한 기록이다.

결론:

```text
EQ_FILTER_UNVERIFIED
EQ_FILTER_HURTS_APPROX
```

적용:

```text
entry_quality_allow = null
entry_quality_score = null
entry_quality_label = EQ_UNVERIFIED_REFERENCE_ONLY
entry_quality_policy = EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
```

후보 자격과 정렬은 다음만 유지했다.

```text
gate_keep
should_buy = final_score >= threshold
final_score priority
SPY DOWN + HIGH_VOL 후순위
```

제거 전후 candidate_id 집합은 동일했다. 즉 EQ가 이미 실제 gate로 연결돼 있던 것을 단순히 끈 것이 아니라, 원래도 후보 자격/정렬에 직접 쓰이지 않았고 오해를 부르는 표시값만 제거·강등한 기록이다.

채택 여부:

```text
명시적 비채택
```

---

### 2.6 후속 운영 감사 기록

#### `pre_live_audit_20260708`

```text
파일:
data/_system/analysis/pre_live_audit_20260708/readout.md
커밋: 19311ed
날짜: 2026-07-08 05:03:46 UTC
```

새 OOS 검증은 없다. 다음 운영 상태를 확인했다.

```text
entry_quality_allow=None
entry_quality_label=EQ_UNVERIFIED_REFERENCE_ONLY
entry_quality_policy=EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
```

판정: 라이브 비활성 상태가 의도대로 유지됨.

#### `candidate_selection_audit_20260710/entry_quality_chase_design_readout.md`

```text
커밋: 67c3e19
날짜: 2026-07-10 10:50:22 UTC
```

새 EQ OOS 검증은 아니다. 현재 코드 소비 경로를 다시 추적해 다음을 확인했다.

```text
live candidate / export / 수동·자동 매수: EQ_TRULY_INACTIVE
shadow virtual ledger / simulator: EQ_PARTIALLY_USED
```

live 18개에 현재 EQ를 가상 적용하면 10개 BLOCK, 3개 SIZE_REDUCE였지만 실제 후보 선정에는 영향이 없음을 확인했다.

---

## 3. 검색됐지만 현행 EQ 검증으로 세면 안 되는 기록

다음 파일들은 관련 단어를 포함하지만, 현행 5일 EQ allow/block의 OOS 검증이 아니다.

| 기록 | 내용 | 현행 EQ 검증 여부 |
|---|---|---|
| `live_slots_tool_20260707/readout.md` | `drop_bad_mae_capture` gate와 슬롯 구성 | 아니오 |
| `pre_live_safety_exit_20260708/readout.md` | 주문 안전·청산 감사, per-trade dataset 참조 | 아니오 |
| `candidate_selection_audit_20260710/readout.md` | 93→26 선별 경로에서 EQ가 NOT_A_GATE임을 확인 | OOS 검증 아님 |
| `chase_gate_readout.md` | 추격 게이트/first_signal 분석 | EQ 검증 아님 |
| `entry_quality_stops_regime_20260707` | MAE/MFE와 pre3 feature 연구 | 이름만 유사, 현행 EQ와 다름 |

## 4. 개별 5일 피처 검증 여부

현재 EQ validity 기록은 composite allow/block을 통째로 재생했다. 다음 피처를 하나씩 끄거나 임계값을 바꾸는 ablation/sweep 기록은 찾지 못했다.

```text
ret_5d_pct 단독
bounce_low5_pct 단독
dist_high5_pct 단독
5일 low/high window 3/5/10일 비교
각 피처 제거 시 OOS CAGR/MDD/Sharpe 변화
```

따라서 다음 문장은 성립하지 않는다.

```text
"5일 룩백 자체가 OOS에서 나쁘다고 확정됐다"
```

확인된 것은 다음뿐이다.

```text
현행 5일 피처를 포함한 composite EQ allow/block을 근사 적용했을 때
OOS portfolio가 악화했고, 정확 재현도 불가능했다.
```

즉 5일 피처 개별 유효성은 `UNTESTED`, composite live gate는 `UNVERIFIED / HURTS_APPROX`다.

## 5. 라이브 비활성과 검증 기록의 괴리 설명

괴리는 없다. “검증은 개선으로 끝났는데 코드 연결만 빠진 상태”가 아니다.

정확한 시간 순서:

```text
2026-07-01
EQ shadow filter 도입
0782c35

2026-07-07 23:03 UTC
EQ가 evaluate_signal과 독립인 별도 휴리스틱임을 추적
즉시 live gate 승격 금지
commit ebe6392

2026-07-07 23:19 UTC
현행 EQ 함수의 approximate frozen OOS 재생
CAGR 72.27 -> 59.04
MDD -21.96 -> -25.64
Sharpe 1.842 -> 1.778
EQ_FILTER_HURTS_APPROX / EQ_FILTER_UNVERIFIED
commit 071698e

2026-07-08 04:43 UTC
라이브 후보 판단에서 EQ 배제를 명시하고 표시값을 UNVERIFIED로 강등
commit fd2400d

2026-07-08 이후
pre-live audit와 2026-07-10 코드 추적에서 NOT_A_GATE 상태 재확인
```

라이브가 비활성인 이유:

1. exact replay 불가.
2. approximate OOS가 개선을 보여주지 못함.
3. portfolio 성과는 세 지표 모두 악화.
4. ALLOW vs BLOCK 평균 수익 우위가 통계적으로 없음.
5. live ledger에는 blocked counterfactual이 없어 실전 검증 불가.
6. EQ는 원래 백테스트 진입 규칙과 독립인 추가 휴리스틱이라, 검증 없이 붙이면 frozen OOS 분포를 바꿈.

따라서 라이브 `NOT_A_GATE`는 미연결 버그가 아니라 의도적 안전 결정이다.

## 6. 최종 판정

```text
현행 EQ composite OOS 테스트 이력: 있음
정확 재현: 없음
근사 OOS 개선 입증: 없음
근사 OOS 결과: 악화
개별 5일 피처 ablation: 없음
라이브 gate 채택: 없음
현재 상태: EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
```

한 문장 요약:

```text
entry_quality/5일 룩백은 과거에 composite 형태로 근사 OOS 검증을 돌렸지만,
채택 근거를 만들지 못했고 오히려 portfolio가 악화해 라이브에서 의도적으로 비활성화됐다.
```
