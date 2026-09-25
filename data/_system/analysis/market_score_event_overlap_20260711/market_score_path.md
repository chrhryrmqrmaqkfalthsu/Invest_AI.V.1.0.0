# market_score / market_adjustment 계산 경로

## 1. 라이브 MarketContext 생성

`engine/market/context.py::build_market_context()`는 가격·변동성 경로와 뉴스 Event 경로를 모두 실행한다.

### 가격 기반 score

```python
sp500_df = _fetch_index("^GSPC", "6mo")
vix_df = _fetch_index("^VIX", "1mo")
price_score, _ = _score_from_trends(sp500_60d, vix_level)
```

`_score_from_trends()` 입력은 다음 두 가지다.

- S&P500 60거래일 수익률
- 현재 VIX

```python
sp500_score = clip((sp500_60d + 10) * 2.5, 0, 50)
vix_score = clip(50 - (vix - 10) * 1.67, 0, 50)
total = clip((sp500_score + vix_score * 0.5) * (100 / 75), 0, 100)
```

이 부분은 가격 기반이며 LLM 또는 `active_events`를 사용하지 않는다.

### 뉴스 Event 조정

같은 `build_market_context()` 안에서 다음 경로가 이어진다.

```python
articles = _fetch_realtime_news(max_articles=100)
raw_event_adj, fresh_active_events, ... = _analyze_news_via_colab(articles)
event_adj, active_events, ... = _merge_active_events_with_decay(...)
final_score = clip(price_score + event_adj, 0, 100)
```

즉 라이브 `MarketContext.score`는 가격점수만이 아니다.

```text
market_score = clip(price_score + active_events aggregate impact, 0, 100)
```

Event aggregate impact는 NewsAPI 기사 → GPT/LLM 이벤트 분류 → event_type별 `total_impact_score` 합계 → TTL decay merge를 거친 값이다.

## 2. evaluator의 market_adjustment

`engine/strategies/evaluator.py::evaluate_signal()`은 전달된 `market_score`를 다음처럼 사용한다.

```python
market_norm = (market_score - 50) / 50.0
sector_norm = (sector_score - 50) / 50.0
vix_norm = (18 - vix_level) / 10.0

correlation_adj = (
    market_norm * rb.market_score_weight
    + sector_norm * rb.sector_strength_weight
    + vix_norm * rb.vix_sensitivity
)

market_adjustment = 1.0 + clamp(
    correlation_adj * rb.market_adjustment_strength,
    -rb.market_adjustment_strength,
    +rb.market_adjustment_strength,
)

if not rb.use_market_entry_adjustment:
    market_adjustment = 1.0

final_score = raw_score * market_adjustment
```

따라서 라이브에서는 `market_score`에 이미 들어간 Event aggregate가 `market_norm`을 통해 전체 raw score에 곱해질 수 있다.

## 3. crash bonus

동일 `market_score`가 폭락장 보너스 gate에도 사용된다.

```python
if rb.crash_buy_enabled and market_score <= rb.crash_threshold_score:
    raw_score += 2.0
```

Event aggregate가 market_score를 충분히 낮추면 직접 Event 가산과 별도로 crash bonus 조건까지 바꿀 수 있다.

## 4. 라이브 호출부

`engine/live/central_control.py::_evaluate_stage3_entity_signal()`은 동일 MarketContext 객체에서 두 입력을 함께 꺼낸다.

```python
market_score = float(ctx.score)
active = ctx.active_events
```

그리고 같은 `evaluate_signal()` 호출에 다음을 동시에 전달한다.

- `market_score=ctx.score`
- `event_flags=flags_from(ctx.active_events)`

따라서 라이브 Stage3 후보에서 두 경로는 단일 MarketContext를 공유한다.
