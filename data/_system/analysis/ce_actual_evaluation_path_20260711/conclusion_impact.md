# 경로 정정의 결론 영향

## direct Event 활성 결론

유지된다.

central controller가 아니라 elite evaluator였지만 두 경로 모두 같은 구조를 갖는다.

```text
ctx.active_events
→ 11개 has_* flag 직접 생성
→ evaluate_signal(event_flags=...)
→ rb.use_event_block=True이면 direct Event 가산
```

`elite_shadow_trader._event_flags()`도 `use_llm_events`를 참조하지 않는다.

따라서 CE의 Event `+4.62`가 라이브 direct Event ON 때문에 생성됐다는 결론은 변하지 않는다.

## 학습–라이브 불일치 결론

유지된다.

학습 Stage2/Stage3:

```text
use_llm_events=False
Event flags=0
```

CE 실제 라이브 평가:

```text
elite_shadow_trader.evaluate_candidate
_event_flags(ctx.active_events)
Event flags active
```

호출자가 central에서 elite로 바뀌어도 학습 OFF·라이브 ON 불일치는 동일하다.

## market_score 중복 결론

유지된다.

elite evaluator는 동일 `ctx`에서:

```python
market_score = float(getattr(ctx, "score", 50.0))
event_flags = _event_flags(ctx)
```

를 함께 사용한다.

따라서 `ctx.score`에 포함된 aggregate Event와 direct Event flag의 source-level 중복도 그대로다.

CE 룰북은 `use_market_entry_adjustment=False`였으므로 해당 주문의 최종 score에서는 직접 Event +4.62가 핵심이었고 market multiplier는 1.0이었다는 기존 결론도 유지된다.

## Event OFF 대응 영향

### central-only 변경

CE 경로에는 효과가 없다.

```text
engine/live/central_control.py의 event_flags만 차단
```

하면 dormant central 경로만 바뀌며, 실제 CE를 만든 `elite_shadow_trader.evaluate_candidate()`는 계속 Event ON이다.

따라서 이전의 “CE 대응 최소 지점은 central_control”이라는 개별 경로 제안은 정정해야 한다.

### 전 경로 단일 스위치 설계

유지된다.

후속 전수 설계는 다음 3개 변환 지점을 공통 helper로 묶도록 했다.

```text
central_control.py
learned_rulebook.py
elite_shadow_trader.py
```

실제 CE 경로인 `elite_shadow_trader.py`가 포함돼 있으므로 단일 스위치 설계는 CE 대응에도 유효하다.

CE와 실거래 대시보드 후보에 직접 효과를 내려면 최소한 elite 공통 evaluator의 flag 변환을 switch로 제어해야 한다.

## market_score 보존 목표

유지된다.

`elite_shadow_trader.evaluate_candidate()`는 `ctx.score`와 event flags를 별도 인자로 evaluator에 넘긴다.

따라서:

```text
event_flags=None
market_score=ctx.score 유지
```

로 변경하면 direct Event만 꺼지고 market_score의 Event aggregate는 유지된다.

## 최종 영향 판정

| 기존 결론 | 정정 후 상태 |
|---|---|
| CE가 central controller에서 평가됨 | 틀림 — elite live slot 경로 |
| CE가 Stage3 룰북 후보임 | 유지 |
| direct Event ON | 유지 |
| `use_llm_events` switch 무시 | 유지 |
| 학습 OFF·라이브 ON 불일치 | 유지 |
| Event +4.62가 direct component | 유지 |
| active key 조합 복원 불가 | 유지 |
| central-only OFF가 CE를 막음 | 틀림 |
| 전 경로 단일 live switch 필요 | 유지·강화 |
| market_score를 유지하며 direct만 OFF 가능 | 유지 |
