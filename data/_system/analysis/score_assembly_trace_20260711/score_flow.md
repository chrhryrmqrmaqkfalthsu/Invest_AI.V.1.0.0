# 최종 score 합산 흐름

## 단일 조립 지점

최종 score 조립은 `engine/strategies/evaluator.py::evaluate_signal()` 한 곳에서 이뤄진다.

```text
기술 컴포넌트
  MA + MACD + RSI + Bollinger + Volume
        │
        ├─ News global
        ├─ NewsTopics (축 내부 clamp)
        │
        ▼
초기 raw_score = sum(components.values())
        │
        ├─ Event = sum(flag × event_response_key)
        │           × event_strength_multiplier
        │
        ├─ crash bonus (+2.0, 조건부)
        │
        ▼
최종 raw_score
        │
        × market_adjustment
        │
        ▼
final_score
        │
        └─ final_score >= signal_threshold → should_buy
```

## 코드 순서

1. MA, MACD, RSI, Bollinger, Volume을 각각 `weight × 조건 충족 여부`로 계산한다.
2. News는 `weight_news_sentiment × signed sentiment`로 계산한다.
3. NewsTopics는 토픽별 `weight × feature`를 합산한 뒤 `news_block_cap`으로 양방향 clamp한다.
4. 위 컴포넌트를 `sum(components.values())`로 더해 초기 `raw_score`를 만든다.
5. Event는 활성 flag별 계수를 합산하고 `event_strength_multiplier`를 곱한 후 `raw_score`에 더한다.
6. 폭락장 보너스가 활성 조건이면 고정 `+2.0`을 더한다.
7. 시장·섹터·VIX 상관 보정값을 학습 가중합으로 만들고 `market_adjustment_strength`로 clamp한다.
8. `final_score = raw_score * market_adjustment`로 전체 점수에 한 번 곱한다.
9. `final_score >= signal_threshold`이면 매수 신호다.

## 분류

최종 판정은 `MIXED_MULTIPLICATIVE`다.

축 내부에서는 가중 가산 구조가 중심이지만, Event에 별도 multiplier가 있고 마지막에 시장보정 배수가 전체 raw score에 곱해진다. 각 축을 모두 독립 gate로 통과해야 하는 구조는 아니다.
