# 조사 1 — score 합산 구조

## 최종 판정

`MIXED_MULTIPLICATIVE`

최종 score 조립은 `engine/strategies/evaluator.py::evaluate_signal()` 한 곳에서 수행된다.

핵심 공식은 다음이다.

```python
raw_score = sum(components.values())
raw_score += event_adj
raw_score += crash_bonus  # 조건부
final_score = raw_score * market_adjustment
should_buy = final_score >= rb.signal_threshold
```

따라서 기술신호·News·NewsTopics·Event는 기본적으로 가산되고, Event 내부에는 별도 `event_strength_multiplier`가 있으며, 마지막에 시장보정 배수가 전체 raw score에 곱해진다.

## 축별 결합 방식

- MA/MACD/RSI/BB/Volume: `학습 weight × 조건 충족 여부`
- News: `weight_news_sentiment × signed sentiment`
- NewsTopics: 토픽별 가중합 후 `[-news_block_cap, +news_block_cap]` clamp
- Event: `sum(active flag × event_response_key) × event_strength_multiplier`
- Crash bonus: 조건부 고정 `+2.0`
- Market adjustment: 시장·섹터·VIX 가중합을 clamp한 뒤 전체 raw score에 곱함
- 최종 gate: `final_score >= signal_threshold`

축마다 모두 별도 통과조건을 만족해야 하는 `GATED_PER_AXIS` 구조는 아니다. 조건을 만족한 축만 가산되며, 최종 합계가 threshold를 넘으면 된다.

## 부호·multiplier·clamp 순서

1. 각 기술축 조건을 평가해 양수 weight 또는 0을 만든다.
2. News와 NewsTopics는 양수·음수 모두 가능하다.
3. NewsTopics는 축 내부에서 양방향 cap을 적용한다.
4. 초기 `raw_score`를 단순 합산한다.
5. Event 계수는 양수·음수 모두 가능하며, 합계에 `event_strength_multiplier`를 곱한다.
6. Event 결과를 raw score에 더한다. Event block cap은 없다.
7. crash bonus를 조건부로 더한다.
8. market adjustment를 `[1-strength, 1+strength]` 범위로 clamp한다.
9. 전체 raw score에 market adjustment를 곱한다.
10. 최종 score를 단방향 threshold와 비교한다.

## CE 재현

CE 룰북 `stage3:CE:998b0b638c66`의 활성 기술축은 로그 기준 MACD, RSI, Bollinger였다.

```text
MACD       1.167787881408684
RSI        1.725077524288451
Bollinger  0.8477763470822091
--------------------------------
기술 subtotal 3.740641752779344

Event       4.622604542854353
--------------------------------
raw score   8.363246295633697

market adjustment = 1.0
final score       = 8.363246295633697
```

CE 룰북은 `use_market_entry_adjustment=False`이므로 시장보정 배수는 1.0이다.

최종 검산:

```text
3.740641752779344 + 4.622604542854353
= 8.363246295633697
```

저장된 CE score와 정확히 일치한다.

News와 NewsTopics는 모두 0이었고, MA·Volume·crash bonus도 CE 로그상 활성 기여가 없다.

## 결론

CE의 8.36은 기술점수 3.74와 Event 4.62가 같은 raw score 공간에서 단순 합산된 뒤, 시장보정 없이 그대로 최종 score가 된 값이다. Event가 기술축과 별도 gate를 통과해야 하는 구조는 아니며, 큰 Event 양수 기여가 최종 threshold 통과를 직접 강화한다.

## 산출물

- `data/_system/analysis/score_assembly_trace_20260711/score_flow.md`
- `data/_system/analysis/score_assembly_trace_20260711/axis_combination_table.csv`
- `data/_system/analysis/score_assembly_trace_20260711/ce_reproduction.csv`
- `data/_system/analysis/score_assembly_trace_20260711/readout.md`

운영 코드·설정·재학습 변경: 0건
