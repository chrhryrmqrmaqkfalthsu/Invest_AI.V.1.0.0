# 라이브 direct Event 전 경로 전수 확인 + 단일 스위치 설계

## 전제 확인

이번 설계는 다음 전제를 유지한다.

- 학습 Stage2/Stage3: 현행 `use_llm_events=False` 유지
- 라이브 current-context direct Event만 OFF 가능하게 설계
- `MarketContext.score`에 포함된 Event aggregate 매크로는 유지
- 실제 코드·설정 변경은 이번 단계에서 하지 않음

## 최종 결론

저장소 전체 grep 결과, 현재 `MarketContext.active_events`를 11개 direct Event flag로 변환하는 라이브 구현은 **정확히 3곳**이다.

1. `engine/live/central_control.py:582-610`
2. `engine/strategies/learned_rulebook.py:281-307`
3. `engine/live/elite_shadow_trader.py:375-430`

실전·페이퍼·가상 후보 경로는 이 3곳 중 하나를 직접 사용하거나 재사용한다. 따라서 세 변환을 하나의 공통 helper로 수렴시키고 단일 정책 키를 참조하면 전 경로 일괄 OFF가 가능하다.

권장 단일 키:

```yaml
# config/policy.yaml
live:
  direct_event_enabled: true
```

권장 공통 모듈:

```text
engine/live/event_policy.py
```

기본값은 `true`로 두므로 스위치 도입 자체는 현행 동작을 바꾸지 않는다.

## 1. 전수 grep 결과

Git 추적 Python 전체 검색 건수:

- `event_flags`: 90개 참조
- `active_events`: 110개 참조
- `use_event_block`: 13개 참조
- `evaluate_signal(`: 32개 호출·정의 참조

`active_events → has_*` exact mapping 파일은 전체 7개였다.

### 라이브·가상 runtime

- `central_control.py`
- `learned_rulebook.py`
- `elite_shadow_trader.py`

### 비라이브

- history builder 3개
- Event decay 연구 helper 1개

따라서 current live context를 직접 Event로 변환하는 누락 지점은 위 3개 외에 발견되지 않았다.

## 2. 앞 조사 외 추가 확인 경로

기존에 확인한 central, 일반 runner, 추가매수 재평가, Telegram, elite shadow 외에 다음 경로가 direct Event 영향을 받는다.

- 운영 `live_candidate_slots` daemon
- 실거래 dashboard exporter
- S2 auto dry-run·실주문 직전 재검증
- elite strategy simulation
- elite pullback replay
- elite signal history

이 중 live slots, dashboard export, S2 auto, strategy sim은 별도 flag 변환을 만들지 않고 `elite_shadow_trader.evaluate_candidate()`를 재사용한다.

## 3. 실전 경로

### live candidate slots

cron에 다음 daemon guard가 등록돼 있다.

```text
scripts/live_candidate_slots_guard.sh
→ data/_system/ops/live_candidate_slots.py daemon --interval 60
```

후보 평가는:

```text
live_candidate_slots.refresh_slots()
→ evaluate_candidate(candidate, ctx)
→ elite_shadow_trader._event_flags(ctx)
```

이다.

### 실거래 dashboard export

`export_real_dashboard_buy_candidates.py`는 live slot 후보를 다시 `evaluate_candidate()`로 검증한다. 따라서 동일 direct Event가 후보 pool 생성과 export 재검증 양쪽에 들어간다.

실거래 manual buy API는 export된 후보를 소비하며 자체 score 재평가는 하지 않는다.

### S2 auto

`S2AutoTrader._validate_candidate_signal()`도 실행 계획 직전에 `evaluate_candidate()`를 다시 호출한다. 기본은 fail-closed/dry-run이지만 실주문 gate 활성 시 이 재평가가 실제 주문 전 조건이 된다.

### 일반 run_live

real/live/vts/paper/alpaca_paper는 동일 `Runner._process_ticker()`와 `LearnedRuleBook.evaluate()`를 공유한다.

현재 legacy BUY guard가 개별 ticker 신규 BUY를 차단하지만 direct Event는 ticker signal, add-buy 재평가, reconfirm, Telegram probability에 계속 쓰인다.

## 4. 페이퍼 경로

### next-open

현재 자동 BUY 경로는 이미 direct Event OFF다.

```python
_lookup_signal_context(..., use_llm_events=False)
```

이 경로는 D-1 `market_history.score`를 사용하며 live current-context Event를 사용하지 않는다.

### central intraday

`LiveCentralController.tick_market()`의 direct Event 코드는 존재하지만 현재 `scripts/run_live.py` scheduler에는 연결돼 있지 않다.

`run_live.py`는 controller를 생성하지만 `runner.tick_market`을 `central_controller.tick_market`으로 치환하지 않고, scheduler에 `runner.tick_market`을 등록한다.

따라서 central direct Event 지점은 현재 wiring에서는 dormant다. 그래도 향후 재연결 시 단일 스위치가 적용되도록 공통 helper 대상에 포함해야 한다.

## 5. 가상 경로

다음 경로는 direct Event ON이다.

- elite shadow trader
- elite strategy sim
- S2 auto dry-run
- elite signal history
- elite pullback replay

strategy sim은 현재 후보 평가 외에 `elite_signal_history`도 사용하므로 direct Event가 가상 판단의 두 계층에 들어갈 수 있다.

중앙 backtest `SignalCollector`는 `use_llm_events=False`가 기본이며 live switch 대상이 아니다.

## 6. 단일 스위치 적용 설계

공통 helper가 다음을 담당한다.

```python
live_event_flags(ctx, enabled_override=None)
```

- 정책 key가 true면 현재 11개 flag 반환
- false면 `None` 반환
- shadow 비교에서는 운영 key와 무관한 explicit override 허용

적용 위치:

### central

```text
engine/live/central_control.py:582-610
```

로컬 mapping을 `live_event_flags(ctx)`로 치환한다.

### 일반 runner

```text
engine/strategies/learned_rulebook.py:281-307
```

로컬 mapping을 `live_event_flags(ctx)`로 치환한다.

### elite 공통 경로

```text
engine/live/elite_shadow_trader.py:375-392
```

기존 `_event_flags()`가 공통 helper를 위임하게 한다.

이 한 변경으로 다음이 함께 제어된다.

- live candidate slots
- dashboard export
- S2 auto
- elite shadow
- strategy sim
- pullback replay
- signal history

## 7. market_score 보존

제안은 `event_flags` 생성·전달만 차단한다.

다음 코드는 변경하지 않는다.

```python
ctx.score = clip(price_score + event_adj, 0, 100)
```

따라서 OFF 후에도 다음은 유지된다.

- Event aggregate가 포함된 market_score
- market adjustment
- crash bonus gate
- MarketContext active event 표시
- dashboard의 시장 context

각 경로는 `ctx.score`를 event_flags와 별도로 가져온 뒤 evaluator에 전달한다. `event_flags=None`으로 바꿔도 `market_score`는 변하지 않는다.

예외는 next-open과 중앙 backtest다. 두 경로는 원래 live `ctx.score`를 쓰지 않으며 direct Event도 이미 OFF다. 이번 스위치로 변경하지 않는다.

## 8. 변경하면 안 되는 지점

### MarketContext builder

`engine/market/context.py`에서 `event_adj` 또는 `active_events`를 제거하면 market_score 매크로까지 사라진다. 목표와 맞지 않는다.

### evaluator 전역 hard-off

`engine/strategies/evaluator.py`를 전역 차단하면 backtest·replay·연구까지 모두 바뀐다.

### 룰북 artifact 일괄 변경

`use_event_block=False`를 룰북에 저장하면 live-only 정책이 아니라 룰북 자체 의미와 hash가 바뀐다.

### 학습 switch

Stage2/Stage3의 `use_llm_events=False`는 그대로 유지한다.

## 9. shadow compare

운영 스위치는 ON으로 유지한 채 동일 입력으로 두 결과를 만든다.

```text
ON: event_flags=current flags
OFF: event_flags=None
```

주문·후보·가상 ledger에는 ON 결과만 사용하고 OFF 결과는 append-only 진단 파일에 기록한다.

필수 비교:

- score ON/OFF
- raw score ON/OFF
- Event component
- should_buy 전환
- 후보 순위 및 top-8 slot 변화
- rulebook별 반복 전환

필수 불변식:

- market_score ON == OFF
- market_adjustment ON == OFF
- Event component OFF == 0
- technical·News·NewsTopics component 동일
- score 차이가 Event component × market adjustment와 일치

실전 후보 슬롯, dashboard export, S2 auto, 일반 runner, elite shadow, strategy sim을 각각 `path_id`로 구분해 기록해야 한다.

## 최종 판정

- current-context direct Event flag 변환 지점: **3곳**
- 추가로 발견된 운영·가상 소비 경로: **6개 이상**, 모두 세 변환점 중 하나로 수렴
- 단일 스위치 일괄 제어: **가능**
- 권장 기본값: **ON**
- direct OFF 후 market_score 매크로 보존: **가능**
- 학습 현행 Event OFF 유지: **가능**
- 현재 코드·설정 변경: **0건**

## 산출물

- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/grep_exhaustiveness.md`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/direct_event_activation_points.csv`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/mode_path_mapping.csv`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/mode_path_mapping.md`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/single_switch_design.md`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/market_score_preservation.csv`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/shadow_compare_plan.md`
- `data/_system/analysis/live_direct_event_all_paths_switch_design_20260711/readout.md`
