# 현재 라이브 후보 선별 경로 + 최근 개편 + 스위치 커버리지

## 최종 운영 판정

`SWITCH_MISSES_PATH`

단, 원인은 **새 코드 경로 누락이 아니라 현재 실행 중인 daemon이 1단계 구현 전 프로세스인 런타임 배포 공백**이다.

소스 코드만 보면 판정은 다음과 같다.

```text
SOURCE_LEVEL = SWITCH_COVERS_CURRENT
```

그러나 현재 실제 selector process는 새 switch를 메모리에 적재하지 않았고, 실제 elite live shadow row도 0건이다. 따라서 운영 기준 최종 판정은 `SWITCH_MISSES_PATH`이며 2단계 OFF 전환은 중단해야 한다.

## 1. 현재 실제 활성 후보 선별 경로

### OS 실측

현재 실행 중인 후보 evaluator process:

```text
PID 337946
live_candidate_slots.py daemon --interval 60
started 2026-07-10 13:30:03 UTC
```

현재 실행 중이지 않은 관련 process:

- `run_live.py`
- `run_s2_auto_live.py`
- `run_elite_shadow_trader.py`
- `run_elite_strategy_sim.py`

실행 중인 dashboard API:

```text
PID 325259
uvicorn api_server_candidate_only:app --port 8001
```

### 현재 실전 후보 경로

```text
cron live_candidate_slots_guard.sh
  -> live_candidate_slots.py daemon
  -> refresh_slots()
  -> build_elite_shadow_report()
  -> elite_shadow_trader.evaluate_candidate()
  -> live_slots_state.json
  -> dashboard API live-slot fallback
  -> manual real buy
```

코드 근거:

- `data/_system/ops/live_candidate_slots.py:28-31`
- `data/_system/ops/live_candidate_slots.py:346-418`
- `engine/live/real_dashboard_holding_days_patch.py:139-171`

현재 실거래 dashboard는 별도 evaluator를 호출하지 않고 live slot candidate snapshot을 소비한다.

### 현재 날짜의 실제 평가 상태

2026-07-11은 미국 시장 비거래일로 gate가 다음을 기록한다.

```text
REFRESH_SKIPPED
reason=not_us_weekday
```

마지막 실제 candidate evaluation:

```text
2026-07-10T19:59:53.002040Z
evaluated=63
buy_signal_count=18
eligible_pool_count=18
```

현재 dashboard pool은 이 결과를 캐시 재사용 중이다.

## 2. run_live scheduler는 7월 8일과 같은가

같다.

현재 코드:

```python
# scripts/run_live.py:191-194
result = runner.tick_market()
```

```python
# scripts/run_live.py:450-453
scheduler.add_market_hours_job(
    func=make_holding_news_tick_market_job(runner),
    ...
)
```

`LiveCentralController.tick_market()`은 scheduler에 등록되지 않는다.

따라서 `run_live.py`를 실행할 경우 ticker 평가 경로는:

```text
Runner._process_ticker()
  -> LearnedRuleBook.evaluate()
```

이다.

현재는 `run_live.py` process 자체가 없다.

## 3. 모드별 현재 진입점

### 실전

현재 실제 active selector:

```text
live_candidate_slots -> elite evaluate_candidate
```

실거래 dashboard와 manual order는 이 candidate snapshot을 소비한다.

`real_dashboard_buy_candidates` exporter와 S2 auto path도 코드상 존재하지만 현재 실행 process나 cron은 확인되지 않았다.

### paper

현재 process 없음.

코드상 일반 paper runner는 `LearnedRuleBook.evaluate()`를 사용한다. 다만 legacy per-ticker BUY는 guard로 차단된다.

`--central-control on`으로 실행되는 next-open path는:

```python
_lookup_signal_context(... use_llm_events=False)
```

로 direct Event가 원래 OFF다.

### virtual

현재 elite shadow/sim process 없음.

실행 시:

```text
run_shadow_tick / run_strategy_sim_tick
  -> elite_shadow_trader.evaluate_candidate
```

로 수렴한다.

`elite_signal_history`와 `elite_pullback_replay`도 elite `_event_flags()`를 import한다.

## 4. 7월 8일 이후 개편 여부

후보·dashboard 관련 기능은 추가됐다.

주요 변경:

- `fd2400d`: live candidate slot source commit
- `75faf9a`: S2 auto dry-run/possible real validation path 추가
- `1b42954`: dashboard live slot fallback 추가
- `e589e57`: live slot daemon cron guard 추가
- `ea2ae57`: dashboard candidate exporter 추가
- `cbc3e77`: dashboard direct buy plumbing 추가
- `e5cd786`: direct Event switch와 shadow 비교 추가

그러나 새 direct Event 변환 root가 추가된 것은 아니다.

신규 exporter와 S2 auto는 모두:

```text
elite_shadow_trader.evaluate_candidate()
```

를 재사용한다.

`run_live.py` scheduler도 7월 8일 이후 central tick으로 이동하지 않았다.

따라서 판정은:

```text
PATH_REFACTORED = false
PATH_EXPANDED_WITH_SHARED_EVALUATOR = true
```

이다.

## 5. source-level switch 커버리지

현재 source에서 current-context `active_events`를 direct Event flag로 만드는 runtime root는 세 곳이다.

1. `engine/live/central_control.py`
2. `engine/strategies/learned_rulebook.py`
3. `engine/live/elite_shadow_trader.py`

세 곳 모두 `live_event_flags(ctx)`를 사용한다.

### elite

```python
# elite_shadow_trader.py:376-378
def _event_flags(ctx):
    return live_event_flags(ctx)
```

실제 평가:

```python
# elite_shadow_trader.py:409-418
event_flags = _event_flags(ctx)
evaluate_signal(... market_score=market_score, event_flags=event_flags)
```

### learned runner

```python
# learned_rulebook.py:282-292
event_flags = live_event_flags(ctx)
evaluate_signal(... market_score=market_score, event_flags=event_flags)
```

### central

```python
# central_control.py:583-594
event_flags = live_event_flags(ctx)
evaluate_signal(... market_score=market_score, event_flags=event_flags)
```

repo-wide grep 결과, live runtime 파일에 과거 인라인 `active_events → has_*` mapping은 남아 있지 않다.

남은 인라인 mapping은 historical market-history builder와 연구 script뿐이다.

따라서 source-level 누락 경로는 없다.

## 6. 현재 runtime에서 switch가 실제 적용되는가

아니다.

현재 live slot daemon 시작:

```text
2026-07-10 13:30:03 UTC
```

구현 commit:

```text
e5cd786
2026-07-11 12:09:28 UTC
```

관련 파일 수정:

```text
engine/live/event_policy.py        2026-07-11 11:16:52 UTC
engine/live/elite_shadow_trader.py 2026-07-11 11:29:46 UTC
```

`live_candidate_slots.py`는 process startup 때 top-level로 다음을 import한다.

```python
from engine.live.elite_shadow_trader import evaluate_candidate
```

현재 daemon은 구현 전부터 실행 중이며 guard log에 이후 재시작 기록이 없다.

따라서 실제 active selector는 old in-memory `evaluate_candidate`를 사용하고 있다. 현재 프로세스에는 `event_policy.py` switch와 shadow logger가 적재되지 않았다.

이것이 최종 `SWITCH_MISSES_PATH` 판정 사유다.

## 7. shadow 로그 유효성

현재 runtime shadow 파일:

```text
shadow_direct_event_20260711.jsonl
12 rows
```

내용:

- central synthetic rows: 10
- runner synthetic rows: 2
- elite_shared actual rows: 0

candidate IDs:

- `AAA_stage2`
- `BBB_stage3`
- `learned:TEST`

`AAA_stage2`, `BBB_stage3`는 test fixture ID다.

현재 실제 live slot path라면 다음 값이 나와야 한다.

```text
mode=elite_shared
path=engine.live.elite_shadow_trader.evaluate_candidate
```

현재 0건이다.

따라서 현재 shadow 파일은 테스트 결과이며 2단계 OFF 판단 근거로 사용할 수 없다.

## 8. 2단계 진행 조건

현재는 진행 불가다.

최소 확인 조건:

1. live slot daemon 시작 시각이 `e5cd786` 이후
2. 정규장 `REFRESH`가 새 process에서 실행
3. shadow JSONL에 `mode=elite_shared` 실제 candidate row 생성
4. candidate ID가 같은 시각 live slot pool과 일치
5. `market_score_on == market_score_off`
6. `invariant_ok=true`
7. 여러 refresh cycle 데이터 축적

이번 조사는 읽기 전용이므로 process 재시작이나 force evaluate는 하지 않았다.

## 최종 정리

| 항목 | 판정 |
|---|---|
| 현재 실제 selector | live candidate slots daemon |
| 실제 evaluator | elite_shadow_trader.evaluate_candidate |
| 7/8 이후 scheduler central 전환 | 없음 |
| 신규 direct Event root | 없음 |
| source-level 3곳 switch coverage | 완전 |
| 현재 active daemon switch 적재 | 안 됨 |
| actual elite shadow rows | 0 |
| 현재 shadow의 2단계 근거 유효성 | 무효 |
| 최종 운영 판정 | `SWITCH_MISSES_PATH` |
| 2단계 OFF 진행 | 중단 |

## 산출물

- `data/_system/analysis/current_live_path_switch_coverage_20260711/current_active_paths.md`
- `data/_system/analysis/current_live_path_switch_coverage_20260711/commits_since_0708.csv`
- `data/_system/analysis/current_live_path_switch_coverage_20260711/switch_coverage.csv`
- `data/_system/analysis/current_live_path_switch_coverage_20260711/runtime_snapshot.csv`
- `data/_system/analysis/current_live_path_switch_coverage_20260711/shadow_log_validity.md`
- `data/_system/analysis/current_live_path_switch_coverage_20260711/readout.md`

운영 코드·설정 변경: 0건
