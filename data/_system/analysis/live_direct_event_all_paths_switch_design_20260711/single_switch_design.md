# 단일 라이브 direct Event 스위치 설계

## 목표

- 학습 Stage2/Stage3의 `use_llm_events=False` 유지
- 라이브·페이퍼·가상 current-context direct Event만 제어
- `MarketContext.score`에 포함된 Event aggregate 매크로는 유지
- 스위치 도입 시 기본값은 ON으로 두어 현행 동작 보존

## 권장 단일 키

`config/policy.yaml`에 다음 비민감 운영 키를 두는 방식이 가장 일관적이다.

```yaml
live:
  direct_event_enabled: true
```

기본값을 명시하지 못했거나 키가 없는 기존 환경에서도 `True`로 해석한다.

권장 이유:

- 실전 runner, cron daemon, dashboard exporter, 가상 trader가 모두 같은 저장소 정책 파일을 읽을 수 있음
- `.env`에 비밀이 아닌 기능 스위치를 섞지 않음
- Git diff와 배포 이력이 남음
- 도입 커밋에서는 `true`이므로 즉시 score나 주문 동작이 바뀌지 않음

장시간 실행 프로세스는 정책 값을 시작 시 캐시할 수 있으므로 실제 OFF 전환 시 관련 daemon·runner를 계획적으로 재시작해야 한다. shadow 비교는 아래의 명시적 override를 사용해 운영 키를 바꾸지 않고 수행한다.

## 권장 공통 모듈

신규 공통 위치 제안:

```text
engine/live/event_policy.py
```

책임:

1. `live.direct_event_enabled` 읽기
2. `active_events`를 11개 `has_*` flag로 변환
3. OFF이면 `None` 반환
4. shadow 비교용 explicit override 지원

제안 인터페이스:

```python
EVENT_KEY_MAP = {
    "has_war": "전쟁",
    "has_rate_hike": "금리정책_인상",
    "has_rate_cut": "금리정책_인하",
    "has_geopolitical": "지정학_긴장",
    "has_tariff": "관세",
    "has_export_ban": "수출규제",
    "has_earnings_shock": "실적쇼크",
    "has_oil_surge": "유가급등",
    "has_banking_crisis": "은행위기",
    "has_inflation": "인플레이션",
    "has_fed_statement": "연준발언",
}


def live_direct_event_enabled() -> bool:
    return bool(config.get("live.direct_event_enabled", True))


def live_event_flags(ctx, *, enabled_override: bool | None = None) -> dict | None:
    enabled = live_direct_event_enabled() if enabled_override is None else bool(enabled_override)
    if not enabled:
        return None
    active = getattr(ctx, "active_events", {}) or {} if ctx is not None else {}
    return {flag: int(event_name in active) for flag, event_name in EVENT_KEY_MAP.items()}
```

## 적용해야 하는 실제 변환 지점

### 1. central Stage3

현재:

```text
engine/live/central_control.py:582-610
```

제안:

```python
event_flags = live_event_flags(ctx)
```

`market_score` 취득과 전달은 현재 그대로 둔다.

### 2. 일반 LearnedRuleBook

현재:

```text
engine/strategies/learned_rulebook.py:281-307
```

제안:

```python
event_flags = live_event_flags(ctx)
```

이 한 지점이 다음 호출을 함께 제어한다.

- 일반 runner ticker scan
- 추가매수 재평가
- 기존 포지션 reconfirm
- Telegram probability 재평가

### 3. elite 공통 evaluator

현재:

```text
engine/live/elite_shadow_trader.py:375-392
```

기존 `_event_flags(ctx)`가 공통 helper를 위임하도록 한다.

```python
def _event_flags(ctx):
    return live_event_flags(ctx)
```

이 한 지점이 다음 경로를 함께 제어한다.

- live candidate slots
- real dashboard exporter
- S2 auto real/dry-run 재검증
- elite shadow trader
- elite strategy sim
- elite pullback replay
- elite signal history

장기적으로 private `_event_flags`를 제거하고 호출자가 `live_event_flags()`를 직접 import하는 편이 구조상 명확하지만, 최소 변경은 wrapper 위임이다.

## 변경하지 말아야 하는 지점

### `engine/market/context.py`

변경 금지 대상:

```python
final_score = clip(price_score + event_adj, 0, 100)
```

이곳을 바꾸면 `ctx.score`의 매크로가 사라져 목표와 반대다.

### `engine/strategies/evaluator.py`

전역 hard-off를 넣지 않는다.

이곳을 변경하면 라이브뿐 아니라 backtest, replay, 연구 경로까지 일괄 변경된다. 기존 `rb.use_event_block` gate는 그대로 둔다.

### 룰북 artifact

`use_event_block=False`를 룰북 파일에 일괄 기록하지 않는다.

그렇게 하면 live-only 정책이 아니라 룰북 의미 자체가 바뀌고 hash·provenance가 변경된다.

### 학습 `use_llm_events`

Stage2/Stage3의 명시적 `False`를 그대로 유지한다. 새 라이브 스위치는 학습 인자와 별개다.

## 중복 코드 제거 효과

현재 동일 11개 mapping이 세 파일에 복사돼 있다.

```text
central_control.py
learned_rulebook.py
elite_shadow_trader.py
```

공통 helper로 수렴시키면 새로운 live 경로가 임의로 직접 flag dict를 만들 가능성을 줄일 수 있다.

권장 회귀 가드:

- `active_events → has_*` literal mapping이 `engine/live/event_policy.py` 외 live 파일에 나타나면 테스트 실패
- `evaluate_signal(event_flags=...)`를 쓰는 새 live 코드가 공통 helper 또는 explicit OFF를 사용하지 않으면 테스트 실패

## 전환 절차 설계

1. 공통 helper와 정책 키를 `true`로 도입
2. ON 기준 기존 score parity 확인
3. shadow comparator에서 ON/OFF 동시 계산
4. 모드별 score·통과·순위 차이 검토
5. 관련 runner/daemon을 중지 또는 drain
6. 정책 키를 `false`로 전환
7. 관련 프로세스 재시작
8. `direct_event_enabled=false`, Event component 0, market_score 동일 로그 확인

이번 조사에서는 위 변경을 실제 적용하지 않았다.
