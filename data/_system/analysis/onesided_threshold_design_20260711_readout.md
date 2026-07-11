# 신호/threshold 판정의 단방향 구조 — 코드 기반 판정

## 최종 판정

`STRUCTURAL_ONESIDED_ONLY`

최종 매수 score 판정은 구조적으로 하한 하나만 표현할 수 있다.

```python
should_buy = final_score >= rb.signal_threshold
```

`Rulebook`과 `PARAM_RANGES`에는 `signal_threshold` 하나만 있고 score 상한이나 band를 나타내는 필드가 없다. 따라서 GA가 상한을 학습하지 못한 것이 아니라, **현재 학습 표현 공간에 score 상한 자체가 존재하지 않는다.**

다만 모든 축이 단방향인 것은 아니다. RSI는 `rsi_low <= RSI <= rsi_high`의 명시적 양단 band이고, NewsTopics와 시장 보정에는 contribution clamp가 있다. 따라서 엔진 전체가 band를 기술적으로 지원하지 못하는 것은 아니며, 최종 score·Volume·Event 등 특정 축의 스키마가 단방향 또는 무상한으로 설계된 것이다.

## 1. 축별 판정 구조

### 양단 band

- RSI: `rsi_low <= RSI <= rsi_high`
- NewsTopics: 합계를 `[-news_block_cap, +news_block_cap]`으로 clamp
- 시장 보정: `market_adjustment_strength` 범위 안으로 clamp

### 단방향

- Volume: `Volume_ratio >= volume_surge_ratio`
- 폭락 보너스: `market_score <= crash_threshold_score`
- 최종 score: `final_score >= signal_threshold`
- 추가매수 재평가: `score >= threshold * 1.2`
- 신호 강도 분류: score/threshold가 커질수록 weak→medium→strong; 상단 거부 없음

### 방향성 boolean

- MA alignment
- MACD cross
- Bollinger near-lower/near-upper 조건

### 방향 무관 가산

- News global: signed continuous additive
- Event: binary flag × signed coefficient × multiplier

Event에는 contribution 상한이 없다. NewsTopics에는 상한이 있지만 Event는 없다.

## 2. 학습 파라미터 구조

### 양단 표현 가능

RSI는 `rsi_low`, `rsi_high` 두 gene을 모두 학습한다.

```python
"rsi_low":  (20.0, 40.0)
"rsi_high": (60.0, 80.0)
```

### 하한만 표현 가능

최종 score는 `signal_threshold` 하나뿐이다.

```python
"signal_threshold": (1.5, 4.0)
```

다음 필드는 존재하지 않는다.

- `signal_upper_threshold`
- `score_high`
- `score_band_max`
- `max_signal_ratio`

따라서 현 GA는 score 상한을 만들 수 없다.

Volume도 `volume_surge_ratio` 하한 하나뿐이고, Event는 signed coefficients와 multiplier만 있으며 block cap이 없다.

## 3. 과적합 방지 의도 근거

`DESIGN_BY_INTENT_OVERFIT_GUARD`를 뒷받침하는 직접 근거는 찾지 못했다.

확인한 범위:

- evaluator 주석
- Rulebook/GA 파라미터 정의
- learning fitness와 complexity penalty
- 관련 Git 이력
- 과적합 관련 문서와 promotion gate

complexity penalty는 `use_news_global`, `use_event_block`, `use_market_entry_adjustment` 세 mask의 활성 개수만 센다. threshold 개수나 band 사용에 대한 penalty가 아니며 기본값도 0이다.

따라서 “파라미터를 줄여 과적합을 막기 위해 score 상한을 의도적으로 제외했다”는 근거는 없다.

## 4. 학습 부작용 여부

`LEARNING_ARTIFACT_NO_UPPER`로 분류할 수 없는 이유는 GA가 상한을 탐색했지만 못 찾은 것이 아니기 때문이다. 상한 gene과 evaluator 분기가 처음부터 없다.

Git 이력상 단방향 판정은 최초 엔진 도입 커밋 `59b8a47`에서 들어왔고, score band를 제거한 이력이나 상한 실험을 폐기한 기록은 확인되지 않았다.

즉 부작용의 위치는 “학습 결과가 상한을 못 찾음”이 아니라 **학습 표현 구조가 상한을 허용하지 않음**이다.

## 5. 상단 미제어 통과 실측

2026-07-10 보존 live93 스캔에서 현재 `should_buy=True`인 개체는 34개였다.

- ratio >= 1.5: 17개
- ratio >= 2.0: 12개
- ratio >= 3.0: 5개
- ratio >= 4.0: 4개
- ratio >= 5.0: 4개

상위 사례:

| ticker | score | threshold | ratio | 과거 평균 PnL |
|---|---:|---:|---:|---:|
| DDOG | 15.38 | 1.72 | 8.94 | +3.83% |
| BMA | 19.25 | 2.85 | 6.76 | +7.82% |
| BTBT | 11.33 | 1.91 | 5.93 | +1.19% |
| CVNA | 8.74 | 1.50 | 5.82 | -0.83% |
| BMI | 8.29 | 2.43 | 3.41 | +1.03% |

CE 실제 주문 snapshot:

- score: `8.3632462956`
- threshold: `2.6541866644`
- ratio: `3.1509638745`
- upper gate: 없음

따라서 CE가 threshold의 약 3.15배였음에도 통과한 것은 코드와 일치한다.

다만 고 ratio가 항상 나쁘다는 증거는 아니다. DDOG·BMA처럼 과거 성과가 양호한 사례도 있고 CVNA처럼 음수 평균 사례도 있다. 현재 자료만으로 특정 score 상한이 수익성을 개선한다고 단정할 수 없다.

## 판정 선택 이유

### `DESIGN_BY_INTENT_OVERFIT_GUARD` 아님

과적합 방지 목적으로 단방향을 택했다는 직접 근거가 없다.

### `LEARNING_ARTIFACT_NO_UPPER` 아님

상한 gene이 없어 GA가 학습할 수 없다. 학습 실패가 아니라 표현 불가다.

### `AMBIGUOUS_NO_BASIS` 아님

최종 score가 구조적으로 단방향만 표현 가능하다는 코드는 명확하다.

### 최종

`STRUCTURAL_ONESIDED_ONLY`

CE의 높은 score와 Event 편중은 최종 score 상한이 없어 통과할 수 있었던 동일 구조와 연결된다. 그러나 이것만으로 score band 도입이 옳다고 결론낼 수는 없다. RSI처럼 축 내부 band를 두거나 Event block cap을 두는 대안과 최종 score upper gate는 서로 다른 설계 변경이며, 별도 OOS 검증이 필요하다.

## 산출물

- `data/_system/analysis/onesided_threshold_design_20260711_axis_structure.csv`
- `data/_system/analysis/onesided_threshold_design_20260711_learning_parameter_structure.csv`
- `data/_system/analysis/onesided_threshold_design_20260711_intent_evidence.md`
- `data/_system/analysis/onesided_threshold_design_20260711_upper_uncontrolled_pass_cases.csv`
- `data/_system/analysis/onesided_threshold_design_20260711_readout.md`

운영 코드·설정·재학습 변경: 0건
