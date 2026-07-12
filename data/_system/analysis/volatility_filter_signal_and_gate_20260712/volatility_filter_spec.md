# 변동성-only 필터 사양 확정

## 1. 가장 명확한 변동성-only 구현

가장 직접적인 구현은 Git commit `f354304581d0e9e90a8d284e232e8ae46b9db231`의 historical `scripts/research/run_range_predictor_stage2_v3.py`다.

커밋 시각:

`2026-07-04T22:40:53Z`

커밋 메시지:

`고저폭 GA v3 타깃을 다음날 대형 변동성 범위 예측으로 전환`

코드 주석은 다음을 명시한다.

- 방향성 LONG/SHORT 성공을 직접 맞히지 않음
- 다음 날 전체 변동폭이 큰 날인지 예측
- `range_pct = high_pct_label + low_mag_pct_label`
- rolling train의 `range_pct` 상위 quantile을 large-range label로 사용
- 기본 quantile `0.70`, 즉 train 상위 30%
- Stage2 방향 신호와 결합하기 전 단계의 변동성 후보 필터

따라서 사용자 기억의 “방향은 못 맞히고 움직임 크기만 예측”과 정확히 일치하는 연구 variant다.

### Target

```text
range_pct = next_day_high_pct + next_day_low_magnitude_pct
range_large_threshold_pct = train quantile(range_pct, 0.70)
large_range = range_pct >= range_large_threshold_pct
```

### Predictor signal

```text
predicted_high_bin + predicted_low_bin >= 4
```

HIGH/LOW는 상승·하락 방향 선택이 아니라 다음 날 시가 기준 위쪽 폭과 아래쪽 폭의 크기 bin이다. 두 예측 bin의 합으로 전체 range가 큰 날을 표시한다.

### GA fitness/gate

- precision lower bound
- large-range base rate 대비 lift lower bound
- 최소 signal 수
- signal coverage
- selected range 평균

을 사용한다.

### 검증 결과

실험 디렉터리:

`exp_fix_range_predictor_stage2_v3_large_range_q70_20260704_001/`

- `stage_survivors.jsonl`: 1,044,846 bytes
- `final_survivors.jsonl`: 0 bytes
- final survivor: 0

즉 Stage2 중간 생존자는 있었지만 최종 검증 gate를 통과한 predictor는 없었다.

## 2. 현재 range predictor의 출력

현재 `scripts/research/run_range_predictor_stage2_v3.py`는 large-range 단일 target에서 다시 HIGH/LOW coarse-bin predictor로 변경된 후속 버전이다.

출력:

- `predicted_high_bin`
- `predicted_low_bin`
- `high_signal`
- `low_signal`
- `both_signal`
- 각 head의 dense feature score 및 cut

현재 `head_objective=both`이면 `both_signal`을 사용한다. 그러나 이는 변동성-only large-range label이 아니라 HIGH와 LOW 두 head의 동시 pattern signal이다.

## 3. Payoff two-gene GA의 실체

`scripts/research/run_payoff_two_gene_ga.py`는 순수한 방향無 변동성-only 모델이라고 보기 어렵다.

개별 출력:

- `UP_score`: 다음 날 상방 high ATR 목표의 적합도
- `LOW_score`: 다음 날 하방 위험이 작을 가능성의 적합도
- `up_cut`, `low_cut`: GA individual별 threshold

최종 experimental signal:

```text
UP_score >= up_cut
AND LOW_score >= low_cut
```

Label:

```text
GOOD_SIGNAL = next_high_atr >= good_high_atr
              AND next_low_atr <= good_max_low_atr
```

이는 “크게 움직인다”만 보는 모델이 아니라 상방 reward와 하방 risk를 비대칭으로 결합한 long payoff detector다.

## 4. Payoff tier overlap

`scripts/research/run_payoff_tier_overlap.py`는 same-date 확률을 다음처럼 AND한다.

```text
up_probability >= selected_up_threshold
AND low_safe_probability >= selected_low_threshold
AND bad_risk_probability <= bad_safe_threshold
```

이 역시 내부 payoff-head 결합이다. 외부 rulebook `should_buy`와 결합하지 않는다.

## 5. “AND” 오탐 구분

commit `8a484dea1bf813af5d922c4472ac6ae73a657c8f`의 “멀티컨디션 AND”는 한 GA gene 안에서 여러 feature band가 모두 맞아야 vote하도록 만든 구조다.

예:

```text
STK_lag1_range in q70~q95
AND STK_lag2_ccret in q60~q90
AND D1_close_pos_candle in q80~q100
```

이는 `rulebook should_buy AND volatility predictor`가 아니다.

## 최종 사양 판정

- directionless large-range predictor: **FOUND_RESEARCH_ONLY**
- payoff UP/LOW cut detector: **FOUND_RESEARCH_ONLY**, 다만 순수 변동성-only는 아님
- Stage2 direction signal과 volatility predictor의 same-day AND: **INTENT_FOUND / IMPLEMENTATION_NOT_FOUND**
