# 학습–라이브 market_score 정합성

## 학습용 market_history

`engine/market/context.py::build_market_history()`는 각 날짜의 S&P500 60일 수익률과 VIX로 `score`를 만든다.

```python
score, regime = _score_from_trends(sp500_60d, vix_level)
rec = {
    "score": score,
    "sp500_60d": sp500_60d,
    "vix": vix_level,
    ...
}
```

`market_history_v2.csv`가 존재하면 Event 컬럼을 별도로 merge하고 다음 파생 컬럼을 만든다.

```python
merged["score_with_events"] = (
    merged["score"] + merged["event_adjustment"]
).clip(0, 100)
```

그러나 학습 backtest는 `score_with_events`를 읽지 않는다.

`engine/learning/backtest.py::_lookup_signal_context()`:

```python
cur_market = float(mkt.get("score", market_score))
```

저장소 검색 결과 `score_with_events`는 learning/evaluator에서 사용되지 않는다.

## Event 학습 설정

Stage2와 Stage3 runner는 다음을 고정했다.

```python
"use_llm_events": False
```

따라서 학습 시:

- market_score: 가격·VIX 기반 `market_history.score`
- sector_score: 가격 기반 sector ETF score
- vix: market_history VIX
- Event flags: 전부 0

## market_adjustment는 학습됐는가

그렇다. `market_score_weight`, `sector_strength_weight`, `vix_sensitivity`, `market_adjustment_strength`, `use_market_entry_adjustment`, `crash_buy_enabled`, `crash_threshold_score`는 Rulebook/GA 표현 공간에 존재한다.

즉 GA는 market adjustment와 crash bonus를 학습할 수 있었지만, 입력 market_score는 **가격·VIX 기반**이었다.

Event처럼 market_score 자체가 중립 50으로 강제된 것은 아니다.

## 라이브

라이브 central Stage3는 다음을 사용한다.

```python
market_score = ctx.score
active = ctx.active_events
```

여기서 `ctx.score`는:

```text
price_score + NewsAPI/LLM event_adjustment
```

이고, `active_events`는 다시 직접 Event axis에 사용된다.

## 정합성 판정

| 항목 | 학습 | 라이브 |
|---|---|---|
| 가격·VIX market score | 사용 | 사용 |
| Event aggregate를 market score에 합산 | 사용 안 함 | 사용 |
| Event flags 직접 가산 | 0으로 강제 | 사용 |
| market adjustment | 룰북별 사용 | 룰북별 사용 |
| crash bonus | 룰북별 사용 | 룰북별 사용 |

따라서 market_score의 가격·VIX 부분은 학습–라이브가 정합하지만, Event 부분은 정합하지 않는다.

라이브에서는 학습 때 없던 Event 정보가 두 경로로 동시에 투입된다.

1. event-adjusted market_score
2. direct Event component

이는 `DUPLICATE_MACRO` 판정과 동시에 별도의 학습–라이브 입력 분포 불일치다.

## CE

CE는 `use_market_entry_adjustment=False`여서 시장 배수 파라미터가 최종 entry score에 적용되지 않는다. `crash_buy_enabled=True`지만 실제 주문 snapshot에는 crash bonus가 없었다.

따라서 CE +4.62 사례의 직접 원인은 direct Event component이고, 당시 market_score를 통한 두 번째 수치 기여는 저장된 final-score 구성에서는 확인되지 않는다.

반면 BTBT, BMI, ACMR처럼 `use_market_entry_adjustment=True`인 룰북에서는 라이브 Event가 direct Event와 market multiplier 양쪽에 실제로 들어간다.
