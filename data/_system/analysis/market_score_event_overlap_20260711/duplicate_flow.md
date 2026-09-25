# 동일 매크로 Event의 이중 반영 흐름

## 동일 원천

금리·연준·인플레이션·지정학 등 표준 Event key는 한 번의 NewsAPI/LLM 분류에서 생성된 `active_events`를 두 경로가 함께 사용한다.

```text
NewsAPI top-headlines
        │
        ▼
GPT/LLM event_type + impact_score
        │
        ▼
MarketContext.active_events
        │
        ├──────────────────────────────────────┐
        │                                      │
        ▼                                      ▼
각 event total_impact_score 합계          event key 존재 여부
+ TTL decay                                has_rate_hike 등
        │                                      │
        ▼                                      ▼
event_adjustment                         종목별 event_response 계수
        │                                × event_strength_multiplier
        ▼                                      │
price_score + event_adjustment                 ▼
        │                                direct Event component
        ▼                                      │
market_score                                  │
        │                                      │
        ├─ crash bonus gate                   │
        └─ market_adjustment 배수              │
                 │                             │
                 └──────── final score ────────┘
```

## 최종 score에서 계산되는 횟수

동일 표준 Event는 룰북 설정에 따라 최대 세 지점에 영향을 줄 수 있다.

1. **직접 가산**

```python
event_component = sum(flag * event_response_key) * event_strength_multiplier
raw_score += event_component
```

2. **전체 score 곱셈**

```python
market_score = price_score + aggregate_event_impact
market_adjustment = f(market_score, sector_score, vix)
final_score = raw_score * market_adjustment
```

`use_market_entry_adjustment=True`일 때 적용된다.

3. **조건부 crash bonus**

```python
if crash_buy_enabled and market_score <= crash_threshold_score:
    raw_score += 2.0
```

Event aggregate가 market_score를 threshold 아래로 이동시킬 때만 적용된다.

## 같은 정보지만 같은 숫자는 아님

두 경로는 동일 source를 쓰지만 인코딩은 다르다.

- market_score 경로: 기사 impact의 부호·크기를 합산한 연속값
- Event 경로: key가 하나라도 있으면 1인 binary flag와 종목별 반응계수

따라서 완전히 같은 수치를 두 번 더하는 것은 아니다. 그러나 동일 기사·동일 Event classification이 가산 경로와 곱셈/보너스 경로에 동시에 들어가는 **source-level 중복**이다.

## 부분 중복

market_score의 `event_adjustment`는 `active_events`의 모든 key를 합산한다. Event axis는 정확히 일치하는 11개 key만 flag로 변환한다.

예를 들어 현재 state의 `유가급등|지정학_긴장` 같은 복합 key는 market_score aggregate에는 들어가지만, `has_oil_surge` 또는 `has_geopolitical` exact-match flag에는 들어가지 않는다.

따라서 겹치는 표준 key에서는 이중 반영이고, 복합·비표준 key는 market_score에만 반영될 수 있다.
