# 라이브 Event OFF 전환 지점 후보

목표는 **direct Event 가산만 끄고 `MarketContext.score`의 Event aggregate는 유지**하는 것이다.

## 후보 A — central Stage3 flag 전달 직전 차단

위치:

```text
engine/live/central_control.py:582-610
```

제안 형태:

```text
live-only 설정이 OFF이면 event_flags=None 또는 {}
그 외에는 현재 active_events 변환 수행
```

효과:

- direct Event component: OFF
- `market_score=ctx.score`: 유지
- market adjustment: 유지
- crash bonus: 유지
- News/NewsTopics: 영향 없음
- MarketContext와 dashboard Event 표시: 유지

영향 범위:

- central controller가 평가하는 entity 전체
- 일반 `Runner._process_ticker()`의 `LearnedRuleBook.evaluate()`에는 영향 없음
- next-open은 이미 OFF이므로 추가 영향 없음
- elite shadow/history 별도 경로에는 영향 없음

**central Stage3 후보만 대상으로 할 때 권장 지점이다.**

## 후보 B — central에서 로드한 Rulebook의 existing gate 사용

위치:

```text
engine/live/central_control.py:520 이후
```

현재 evaluator에는 이미 다음 gate가 있다.

```python
if event_flags and rb.use_event_block:
```

따라서 central evaluation용 local `rb`에만 `use_event_block=False`를 적용하면 direct Event만 꺼진다.

효과는 후보 A와 동일하지만, persisted rulebook 의미와 live override 의미가 섞일 수 있다. 구현한다면 원본 entity dictionary를 수정하지 않고 local copy에만 적용해야 한다.

영향 범위:

- 해당 central 평가 호출에만 한정 가능
- candidate snapshot에 원본 rulebook이 저장되면 실제 평가 override와 snapshot field가 불일치할 수 있으므로 별도 provenance 기록이 필요

## 후보 C — 일반 LearnedRuleBook flag 생성 지점 차단

위치:

```text
engine/strategies/learned_rulebook.py:281-307
```

효과:

- 일반 runner, Telegram 평가, add-buy 재평가의 direct Event: OFF
- `market_score=ctx.score`: 유지
- central Stage3는 이 함수를 우회하므로 그대로 ON

영향 범위:

- `Runner._process_ticker()`
- 승인 재평가
- Telegram 수동 평가
- LearnedRuleBook을 직접 호출하는 기타 live 경로

central 후보만 끄려는 목적에는 단독으로 불충분하다.

## 후보 D — persisted Rulebook의 `use_event_block=False`

위치:

```text
각 entity/rulebook artifact의 use_event_block
```

효과:

- direct Event만 OFF
- market_score macro 유지

영향 범위:

- 해당 rulebook을 사용하는 live, replay, backtest, diagnostic 전체
- 룰북별 수정이 필요
- 기존 artifact hash·provenance와 불일치 가능
- 라이브 전용 차단이 아님

이번 목표에는 범위가 넓고 운영 데이터 변경이 필요하므로 비권고다.

## 후보 E — evaluator에서 Event를 전역 차단

위치:

```text
engine/strategies/evaluator.py:179
```

예를 들어 Event 조건을 항상 false로 만들면 direct Event만 사라지고 market_score는 유지된다.

영향 범위:

- live central
- 일반 live runner
- next-open
- backtest
- replay
- diagnostic
- shadow/history

모든 환경의 의미가 바뀌므로 라이브-only 목표에는 비권고다.

## 후보 F — MarketContext의 `active_events` 또는 `event_adj` 제거

위치:

```text
engine/market/context.py:581-626
```

이 지점에서 Event 분석 또는 `active_events`를 제거하면 direct Event flag는 사라진다. 그러나 다음도 함께 사라진다.

- `price_score + event_adj`의 market_score macro
- market adjustment에 들어가는 Event aggregate
- crash bonus gate에 들어가는 Event aggregate
- risk_events/benefit_events 표시

**direct만 끄고 market_score macro를 유지한다는 목표와 반대이므로 제외해야 한다.**

## 권장 변경 지점

### central Stage3 후보만 OFF

1순위:

```text
engine/live/central_control.py:582-610
```

live central 전용 명시적 switch를 두고 `event_flags` 전달만 차단한다.

### 모든 intraday live direct Event를 OFF

central과 일반 runner가 서로 다른 flag 생성 코드를 갖고 있으므로 최소 두 지점을 함께 gate해야 한다.

```text
engine/live/central_control.py:582-610
engine/strategies/learned_rulebook.py:281-307
```

elite shadow/history까지 라이브 표시 정합성을 맞추려면 각 `_event_flags(ctx)` 호출도 같은 shared live switch를 사용해야 한다.

## 권장하지 않는 변경

- `run_backtest(use_llm_events=False)` 기본값 변경: 라이브에 영향 없음
- MarketContext Event 제거: market_score macro까지 제거
- evaluator 전역 hard-off: 학습·replay까지 광범위하게 변경
- persisted rulebook 일괄 변경: live-only 목적보다 범위가 넓음
