# use_llm_events 변경 커밋 근거

## 최초 등장

`use_llm_events`라는 이름은 커밋 `d6bd746`에서 처음 등장한다.

```text
commit: d6bd74695584e3d7ba0ef099397bfbf9a5476fc2
date: 2026-06-08T00:23:41Z
subject: LR-8C-FIX LLM 이벤트 차단과 exit 스냅샷 및 트레일링 활성화 개선
```

이 커밋은 `engine/learning/backtest.py`에 switch를 추가했다.

```python
def run_backtest(..., use_llm_events: bool = False):
    """Run point-in-time backtest. LLM-derived event flags are disabled by default."""
```

context lookup도 다음처럼 바뀌었다.

```python
event_flags = _zero_event_flags()
if use_llm_events:
    for key in EVENT_FLAG_KEYS:
        event_flags[key] = int(mkt.get(key, 0) or 0)
```

동시에 실제 LR8C GA runner에 명시적 False가 들어갔다.

```python
def base_kwargs(ctx):
    return {
        ...
        "use_llm_events": False,
    }
```

## 변경 전 상태

`d6bd746`의 부모 커밋에서는 switch가 없었다. `market_history_df`가 있으면 11개 Event flag를 항상 추출했다.

```python
cur_event_flags = {}
if market_history_df is not None:
    mkt = lookup_market_at_lagged(...)
    for key in (...11 keys...):
        cur_event_flags[key] = int(mkt.get(key, 0) or 0)
```

따라서 의미상 변경은 다음이다.

```text
변경 전: Event enabled unconditionally when market_history exists
변경 후 LR8C runner: use_llm_events=False, flags forced zero
```

이는 실제 True→False에 해당한다.

## 2분 뒤 default 변경

직후 커밋 `86bf54b`는 generic function default를 다시 True로 바꿨다.

```text
commit: 86bf54bb51118f37be181085dff048b2eef9beb1
date: 2026-06-08T00:25:09Z
subject: LR-8C-FIX LLM 이벤트 차단과 청산 스냅샷·트레일링 활성화 개선
```

```diff
- use_llm_events: bool = False
+ use_llm_events: bool = True
```

하지만 `run_lr8c_run2_fulluniverse.py`의 명시적 `use_llm_events=False`는 유지됐다. 따라서 generic API default는 True로 복원됐지만 실제 LR8C 학습은 계속 비활성이었다.

## 과적합 이유 검색

다음 표현을 커밋 메시지, diff, 인접 커밋, 문서에서 검색했다.

- 과적합 / overfit
- curve-fit
- noise / 노이즈
- 불안정
- Event 제외 사유

`d6bd746`, `86bf54b`, 인접 LR-8C-FIX 커밋에는 Event 차단 이유를 과적합으로 설명하는 문구가 없다.

해당 diff에 존재하는 유일한 `overfitting` 문구는 거래 수가 5건 미만일 때 fitness를 낮추는 일반 표본수 주석이며, Event 차단과 무관하다.

따라서 확인 가능한 것은 "의도적으로 차단했다"는 사실까지다. 왜 차단했는지는 커밋 근거가 없다.
