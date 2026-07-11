# shadow 로그 유효성 판정

## 현재 파일

```text
data/_system/analysis/shadow_direct_event/shadow_direct_event_20260711.jsonl
rows: 12
```

경로별 row:

```text
central / engine.live.central_control._evaluate_stage3_entity_signal: 10
runner / engine.strategies.learned_rulebook.evaluate: 2
elite_shared / engine.live.elite_shadow_trader.evaluate_candidate: 0
```

## 실제 live slot 로그인가

아니다.

현재 row의 candidate ID:

- `AAA_stage2`
- `BBB_stage3`
- `learned:TEST`

`AAA_stage2`, `BBB_stage3`는 `tests/test_live_central_rulebook_integrity.py`의 synthetic entity ID다.

실제 현재 후보 selector가 사용하는 elite path의 다음 값은 한 건도 없다.

```text
mode=elite_shared
path=engine.live.elite_shadow_trader.evaluate_candidate
```

## live slot event와 시간 대조

shadow row가 기록된 2026-07-11 11:32~12:08 UTC 동안 live slot daemon은 토요일 gate 때문에 다음만 기록했다.

```text
REFRESH_SKIPPED
outside regular hours; reused cached candidate pool
```

예:

```text
2026-07-11T12:08:40.196340Z REFRESH_SKIPPED
```

따라서 같은 시각 shadow row는 실제 live slot 평가에서 나온 것이 아니다.

## 프로세스 재시작 여부

현재 live slot daemon:

```text
started: 2026-07-10 13:30:03 UTC
```

스위치 구현 commit:

```text
e5cd786
2026-07-11 12:09:28 UTC
```

관련 파일 수정 시각도 daemon 시작 이후다.

```text
engine/live/event_policy.py       2026-07-11 11:16:52 UTC
engine/live/elite_shadow_trader.py 2026-07-11 11:29:46 UTC
```

`live_candidate_slots.py`는 모듈 시작 시점에 다음을 top-level import한다.

```python
from engine.live.elite_shadow_trader import evaluate_candidate
```

guard log에는 2026-07-10 13:30 이후 재시작 기록이 없다. healthy process는 guard가 그대로 유지한다.

따라서 현재 daemon 메모리에는 스위치 구현 전 `evaluate_candidate`가 적재돼 있다고 판정한다.

## 현재 후보 pool의 생성 시점

마지막 실제 평가:

```text
2026-07-10T19:59:53.002040Z
```

이는 스위치 구현 전이다.

2026-07-11에는 캐시 재정렬만 수행됐고 평가가 없었다. 따라서 현재 dashboard slot/pool도 스위치와 shadow 비교를 거친 결과가 아니다.

## 2단계 근거로 사용할 수 있는가

현재 상태에서는 사용할 수 없다.

이유:

1. 실제 active selector daemon이 새 코드를 로드하지 않음
2. 실제 elite path shadow row 0건
3. 현재 파일은 synthetic test row뿐임
4. 주말이라 새 코드가 로드돼도 정규 평가 row가 쌓이지 않는 시점임

## 유효성 확보 조건

분석 기준으로 다음이 모두 확인돼야 한다.

1. live slot daemon 시작 시각이 `e5cd786` 이후
2. 정규장 `REFRESH` event 존재
3. 동일 시간대 shadow JSONL에 `mode=elite_shared` row 존재
4. 실제 candidate ID가 live slot pool ID와 일치
5. `market_score_on == market_score_off`
6. `invariant_ok=true`
7. 여러 refresh cycle에서 row가 반복 축적

이번 조사는 읽기 전용이므로 daemon 재시작이나 강제 평가를 실행하지 않았다.

## 판정

```text
CURRENT_SHADOW_INVALID_FOR_PHASE2
```
