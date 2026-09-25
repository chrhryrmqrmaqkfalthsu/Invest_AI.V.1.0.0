# market_score(시장 분석기) vs Event 축 — 매크로 이중 반영 판정

## 최종 판정

`DUPLICATE_MACRO`

라이브 Stage3 후보에서는 동일한 NewsAPI/LLM `active_events`가 두 경로에 동시에 사용된다.

1. 각 Event의 `total_impact_score` 합계가 `event_adjustment`가 되어 가격 기반 market score에 더해진다.
2. 같은 `active_events`의 key 존재 여부가 종목별 `event_response_*` 계수와 multiplier를 거쳐 raw score에 직접 가산된다.

룰북 설정에 따라 event-adjusted market score는 다시 전체 raw score의 market adjustment 배수와 crash bonus 조건에도 사용된다.

따라서 동일 매크로 source는 라이브 최종 score에서 최대 다음 세 번 영향을 줄 수 있다.

- 직접 Event 가산
- market adjustment 곱셈
- crash bonus 조건부 가산

## 1. market_score의 실제 출처

`engine/market/context.py::build_market_context()`는 먼저 가격·변동성 기반 점수를 만든다.

```python
sp500_df = _fetch_index("^GSPC", "6mo")
vix_df = _fetch_index("^VIX", "1mo")
price_score, _ = _score_from_trends(sp500_60d, vix_level)
```

가격점수 입력은 S&P500 60일 수익률과 VIX다.

같은 함수에서 NewsAPI business top-headlines를 LLM으로 분류하고 `active_events`를 만든다.

```python
articles = _fetch_realtime_news(max_articles=100)
raw_event_adj, fresh_active_events, ... = _analyze_news_via_colab(articles)
event_adj, active_events, ... = _merge_active_events_with_decay(...)
final_score = clip(price_score + event_adj, 0, 100)
```

즉 라이브 `ctx.score`는 순수 가격 시장점수가 아니다.

```text
ctx.score = 가격·VIX 점수 + NewsAPI/LLM Event aggregate
```

금리·연준·인플레이션·지정학·실적쇼크 등 Event impact가 이미 market_score에 포함된다.

## 2. Event 축과 같은 source인가

같다.

`engine/live/central_control.py::_evaluate_stage3_entity_signal()`은 하나의 `ctx`에서 다음을 동시에 꺼낸다.

```python
market_score = float(ctx.score)
active = ctx.active_events
```

그리고 같은 evaluator 호출에 전달한다.

```python
evaluate_signal(
    market_score=market_score,
    event_flags=flags_from(active),
    ...
)
```

직접 Event component:

```python
event_adj = sum(flag * rb.event_response_key)
event_adj *= rb.event_strength_multiplier
raw_score += event_adj
```

market adjustment:

```python
market_norm = (market_score - 50) / 50
correlation_adj = market_norm * rb.market_score_weight + ...
final_score = raw_score * market_adjustment
```

따라서 표준 Event key는 같은 기사·같은 LLM 분류 결과를 source로 공유한다.

## 3. 이중 반영의 형태

완전히 같은 숫자를 두 번 더하는 구조는 아니다.

- market_score 경로: 기사 impact의 부호·크기를 연속값으로 합산하고 TTL decay 적용
- Event 경로: key 존재 여부를 binary flag로 바꾸고 종목별 learned coefficient 적용

하지만 source는 동일하다. 동일 금리인상 Event가 발생하면:

```text
금리인상 article
→ active_events["금리정책_인상"]
→ total_impact_score가 market_score를 변경
→ has_rate_hike=1이 direct Event contribution을 생성
```

`use_market_entry_adjustment=True`이면 변경된 market_score가 전체 score 배수까지 바꾼다.

`crash_buy_enabled=True`이고 score가 threshold 아래면 고정 +2도 추가된다.

## 4. 현재 보존 state 실측

현재 보존된 state:

- timestamp: `2026-07-10T19:34:03.640405`
- market score: `71.5`
- event adjustment: `-18.41`
- 추정 가격-only score: 약 `89.91`

활성 표준 key:

- 금리정책_인상
- 금리정책_인하
- 인플레이션
- 지정학_긴장
- 관세

같은 state를 Event 과반 종목 룰북에 적용하면 다음처럼 양쪽 경로가 동시에 움직인다.

| ticker | direct Event | live market adj | price-only market adj | Event로 인한 multiplier 차이 |
|---|---:|---:|---:|---:|
| BTBT | +11.8106 | 1.1045 | 1.0576 | +0.0468 |
| BMI | +5.1708 | 1.4949 | 1.3566 | +0.1383 |
| ACMR | +1.3952 | 1.1066 | 1.0803 | +0.0263 |

이 세 종목은 동일 active_events가 직접 가산과 전체 score 곱셈에 실제로 동시에 반영된다.

나머지 종목은 `use_market_entry_adjustment=False`라 현재 상태에서는 직접 Event만 최종 score에 반영된다. crash threshold는 현재 market score 71.5에서 어느 종목도 충족하지 않았다.

## 5. 부분 중복 예외

market_score의 aggregate는 모든 `active_events` key를 합산한다.

Event axis는 정확히 일치하는 11개 key만 flag로 변환한다.

현재 state의 다음 복합 key는 market_score에는 들어가지만 direct Event flag에는 들어가지 않는다.

```text
유가급등|지정학_긴장
```

따라서 표준 key는 이중 반영되지만 복합·비표준 key는 market_score에만 들어갈 수 있다.

## 6. Event를 끄면 매크로가 남는가

### `use_event_block=False`

직접 Event component는 0이 된다. 그러나 `ctx.score`는 이미 event-adjusted 상태이므로 그대로 유지된다.

따라서 다음 룰북에서는 매크로 영향이 남는다.

- `use_market_entry_adjustment=True`: 전체 score 배수에 잔존
- `crash_buy_enabled=True`이고 threshold 충족: +2 bonus에 잔존

두 조건이 모두 실효성이 없으면 `ctx.score` 메타데이터에는 남아도 최종 entry score에는 남지 않는다.

Event 과반 9종목 중:

- market adjustment 활성: BTBT, BMI, ACMR — 3/9
- crash bonus 활성: ACMR 제외 8/9
- Event block 활성: 9/9

따라서 Event block off는 전체 종목에서 동일한 효과를 내지 않는다.

### CE

CE 룰북:

- `use_market_entry_adjustment=False`
- `crash_buy_enabled=True`
- crash threshold `30.8996`

CE 실제 주문 snapshot:

- Event `+4.62260455`
- market adjustment `1.0`
- crash bonus `0`

따라서 저장된 CE 주문 구성에서는 Event block을 끄면 +4.62가 제거되고, market_score를 통한 별도 매크로 기여는 최종 score에 남지 않는다.

당시 market_score 자체는 보존되지 않았으므로 내부 aggregate Event 값은 확인 불가다.

## 7. 학습–라이브 정합성

학습은 market_score를 사용했지만 라이브와 같은 형태는 아니었다.

`build_market_history()`의 `score`는 S&P500·VIX 가격 기반이다.

`market_history_v2`는 별도로 `score_with_events`를 만들지만, backtest는 다음을 읽는다.

```python
cur_market = float(mkt.get("score", market_score))
```

`score_with_events`는 learning 경로에서 사용되지 않는다.

또한 Stage2/Stage3 학습은:

```python
"use_llm_events": False
```

로 Event flags를 전부 0으로 했다.

따라서 학습:

```text
price/VIX market_score
+ Event flags 0
```

라이브:

```text
price/VIX + Event aggregate market_score
+ direct Event flags
```

이다.

market adjustment와 crash bonus 파라미터는 학습됐지만, 학습 당시 입력 market_score에는 뉴스 Event가 포함되지 않았다. 라이브에서만 동일 Event가 market_score와 direct Event 양쪽에 새로 들어간다.

## 결론

판정은 `DUPLICATE_MACRO`다.

Event를 끄면 동일 source의 직접 가산 중복은 제거된다. 그러나 market_score 내부 aggregate Event는 남는다. 따라서 market adjustment나 crash bonus를 사용하는 룰북에서는 일부 매크로 정보가 유지된다.

다만 direct Event는 key별 종목 반응이고 market_score는 aggregate 시장 impact이므로, Event off를 완전한 무정보손실로 단정할 수는 없다. 특히 CE처럼 market adjustment가 꺼져 있고 crash bonus가 발동하지 않은 개체에서는 Event off 시 최종 score에서 매크로 정보가 사실상 사라진다.

더 근본적인 문제는 현재 룰북이 학습할 때는 가격-only market_score와 Event=0을 사용했는데, 라이브에서는 event-adjusted market_score와 direct Event를 동시에 사용한다는 학습–라이브 불일치다.

## 산출물

- `data/_system/analysis/market_score_event_overlap_20260711/market_score_path.md`
- `data/_system/analysis/market_score_event_overlap_20260711/source_comparison.csv`
- `data/_system/analysis/market_score_event_overlap_20260711/duplicate_flow.md`
- `data/_system/analysis/market_score_event_overlap_20260711/current_snapshot_overlap.csv`
- `data/_system/analysis/market_score_event_overlap_20260711/event_off_residual.md`
- `data/_system/analysis/market_score_event_overlap_20260711/training_live_alignment.md`
- `data/_system/analysis/market_score_event_overlap_20260711/readout.md`

운영 코드·설정·재학습 변경: 0건
