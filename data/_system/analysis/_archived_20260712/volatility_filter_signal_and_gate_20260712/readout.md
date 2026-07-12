# “변동성 예측 필터 + 개체 신호 동시 발생 시 진입” 결합 로직 발굴

- 조사일: 2026-07-12
- 코드·설정·daemon 변경: **0**
- 최종 판정: **RESEARCH_ONLY — 결합 의도는 확인, 실제 AND 진입 구현은 NOT_FOUND**

## 1. 변동성-only 필터의 실체

사용자 기억과 가장 정확히 맞는 코드는 commit `f354304581d0e9e90a8d284e232e8ae46b9db231`의 historical `run_range_predictor_stage2_v3.py`다.

커밋 시각:

`2026-07-04T22:40:53Z`

코드가 직접 밝힌 목적:

- LONG/SHORT 방향 성공을 직접 예측하지 않음
- 다음 날 전체 변동폭이 큰 날을 예측
- `range_pct = high_pct_label + low_mag_pct_label`
- rolling train 상위 30%, 즉 q70 이상을 large-range label로 사용
- **Stage2 방향 신호와 결합하기 전 단계의 변동성 후보 필터**

이 문구는 사용자 기억의 핵심을 뒷받침한다.

```text
변동성 후보 필터
→ 이후 Stage2 방향 신호와 결합 예정
```

하지만 해당 commit에서 실제 구현된 것은 변동성 predictor의 학습·평가까지다. Stage2 `should_buy`와 같은 날짜를 join하는 코드는 없다.

## 2. large-range 필터의 판정식

Target:

```text
next_day_range_pct = next_day_high_pct + next_day_low_magnitude_pct
large_range_threshold = rolling_train_quantile(next_day_range_pct, 0.70)
large_range_label = next_day_range_pct >= large_range_threshold
```

Predictor pass:

```text
predicted_high_bin + predicted_low_bin >= 4
```

이는 “위로 갈지 아래로 갈지”를 선택하는 방향 모델이라기보다 위쪽·아래쪽 예상 폭을 합쳐 전체 움직임 크기가 큰 날을 찾는 모델이다.

## 3. 검증 상태

실험:

`exp_fix_range_predictor_stage2_v3_large_range_q70_20260704_001/`

보존 상태:

- `stage_survivors.jsonl`: 1,044,846 bytes
- `final_survivors.jsonl`: 0 bytes
- final survivor: 0

즉 중간 Stage2 생존자는 있었으나 최종 gate를 통과한 변동성 predictor는 없었다.

판정:

- 구현: `FOUND_RESEARCH_ONLY`
- 최종 검증: `VERIFIED_FAIL`, final survivor 0
- live 승격: 없음

## 4. “신호 AND 변동성 필터” 구현 검색 결과

요청된 결합식은 다음이다.

```text
rulebook_should_buy(ticker, date)
AND volatility_predictor_pass(ticker, date)
→ enter
```

다음 형태를 전수 검색했다.

- 동일 ticker/date join
- signal mask와 predictor mask의 `&`
- `should_buy`와 predictor pass의 AND
- combined/confirm/co-occurrence gate
- predictor artifact lookup 후 candidate filter
- candidate_id 또는 ticker/date exact match

결과: **NOT_FOUND**.

Git 전체 live path 이력에서도 연결·제거 commit은 발견되지 않았다.

검색 대상:

- `engine/live/elite_shadow_report.py`
- `engine/live/elite_shadow_trader.py`
- `data/_system/ops/live_candidate_slots.py`
- `scripts/export_real_dashboard_buy_candidates.py`

## 5. 실제로 존재하는 AND 구조

### A. Large-range predictor 내부 AND

```text
predicted_high_bin + predicted_low_bin >= threshold
```

이는 total range를 만드는 predictor 내부 signal이다.

### B. Current range predictor의 both head

```text
high_signal AND low_signal
```

현재 `head_objective=both`에서 사용된다. 이것도 predictor 내부 결합이다.

### C. Multi-condition GA gene

commit `8a484dea1bf813af5d922c4472ac6ae73a657c8f`:

- 한 gene 안의 여러 feature quantile 조건을 AND
- 모든 조건이 맞아야 해당 HIGH/LOW bin에 vote

이름에 AND가 있지만 외부 rulebook signal과는 무관하다.

### D. Payoff tier overlap

`scripts/research/run_payoff_tier_overlap.py`:

```text
up_prob >= up_threshold
AND low_safe_prob >= low_threshold
AND bad_risk_prob <= bad_safe_threshold
```

같은 날짜의 payoff head를 결합한다. 역시 `should_buy`와의 AND가 아니다.

### E. Stage2 five-day path filter

`scripts/research/run_stage2_path_filter.py`는 실제로:

```text
should_buy
AND NOT path_filter_block
```

구조를 갖는다.

이는 사용자 기억과 작동 방식이 가장 가까운 “개체 신호 확인 gate”다. 그러나 별도 변동성 predictor를 사용하지 않고 D-5~D-1 path gene을 직접 검사한다.

## 6. Payoff two-gene GA의 성격

Payoff two-gene은 순수 변동성-only 필터가 아니다.

```text
UP_score >= up_cut
AND LOW_score >= low_cut
```

- UP gene: 다음 날 상방 reward
- LOW gene: 하방 위험이 작을 가능성

따라서 long 방향 payoff detector다. 변동성 크기만 예측한다고 보기 어렵다.

## 7. 사용자 기억과 실제 코드의 대조

| 사용자 기억 | 실제 확인 | 판정 |
|---|---|---|
| 방향 없이 변동성만 예측 | large-range q70 predictor에 정확히 존재 | 일치 |
| 최근 5일 상태 사용 | 해당 historical predictor가 prior-5-day GA 사용 | 일치 |
| 개체 매수신호와 동시 발생 시 진입 | commit 주석에 “Stage2 방향 신호와 결합 전 단계” 명시 | 의도 일치 |
| 실제 AND 진입 코드 존재 | ticker/date join 또는 should_buy AND predictor 없음 | 불일치 |
| live candidate_pool에 적용 | import/loader/call 없음 | 불일치 |
| 검증 통과 | final survivor 0 | 불일치 |

[추정] 사용자는 `f354304`의 설계 의도와 `stage2_path_filter`의 실제 signal-confirm 구조를 하나의 완성된 결합 gate로 기억했을 가능성이 있다.

## 8. 현재 후보 10개 적용 여부

현재 후보:

- ADMA
- CRS
- ALGT
- AEIS
- ARKW
- CBRL
- BTU
- BB
- BN
- ACMR

현재 candidate row에는 다음 field가 없다.

- volatility predictor score
- large-range threshold
- predicted range bin
- predictor signature
- up_cut/low_cut
- volatility confirmation pass/fail
- same-day co-occurrence flag

현재 선정식은:

```text
upstream KEEP
AND evaluate_candidate.should_buy
→ final_score 정렬
```

이다.

따라서 현재 10개 모두 변동성 predictor와의 AND 확인을 거치지 않았다.

## 9. 라이브 연결 상태

- 현재 연결: 없음
- 과거 연결: `NOT_FOUND`
- 연결 후 제거: `NOT_FOUND`
- research artifact loader: 없음
- current pool field: 없음
- 운영 감사: range/payoff는 `TERMINATED_EXPERIMENT_DATA`

## 최종 판정

### **RESEARCH_ONLY**

더 세부적으로는:

- 변동성-only predictor: **FOUND_RESEARCH_ONLY**
- Stage2 방향 신호와 결합 의도: **FOUND_IN_COMMIT_DOCSTRING**
- 실제 same-day AND 진입 구현: **NOT_FOUND**
- 라이브 연결: **NOT_FOUND**
- 검증: **FAILED_FINAL_GATE**, final survivor 0

따라서 `FOUND_AND_CONNECTED`나 `FOUND_DISCONNECTED`가 아니라, **연구 단계에서 결합 의도까지만 기록되고 구현·승격되지 않은 상태**로 판정한다.

세부 산출물:

- `volatility_filter_spec.md`
- `and_gate_inventory.csv`
- `live_connection_status.csv`
- `immutability_check.csv`
- `manifest.sha256`
