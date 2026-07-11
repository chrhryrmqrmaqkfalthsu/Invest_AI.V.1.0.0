# 이전 전제 검증 및 정정

## 정정 대상

이전 분석은 CE를 다음처럼 표현했다.

```text
intraday/live-slot central Stage3
```

이 표현은 `stage3` 룰북 후보라는 사실과 `LiveCentralController` 실행 경로를 혼합했다.

CE의 candidate ID는:

```text
stage3:CE:998b0b638c66
```

이지만, 여기서 `stage3`는 룰북 산출 단계다. 실제 런타임 호출자가 `central_control.py`였다는 뜻은 아니다.

## 검증 결과

### central controller

7월 7~8일 `run_live.py`는 central controller object를 생성했지만 market scheduler에는 `runner.tick_market` wrapper를 등록했다.

`central_controller.tick_market` 등록이나 `runner.tick_market` 치환은 없었다.

따라서 CE 후보가 central controller의 `_evaluate_stage3_entity_signal()`에서 생성됐다는 기존 전제는 틀렸다.

### 일반 Runner

일반 Runner도 `LearnedRuleBook.evaluate()`에서 direct Event를 켠다. 그러나 CE 주문 intent에는 Runner state나 Runner order source를 연결하는 필드가 없다.

보존된 source는 명시적으로:

```text
candidate_source=live_slots_state_fallback
source=dashboard-real-detail
```

이다.

따라서 일반 Runner가 같은 시각 CE를 별도로 평가했을 가능성은 배제할 수 없지만, 해당 평가가 이 주문의 후보 source였다는 근거는 없다.

### 실제 경로

실제 주문 후보 source는:

```text
live_candidate_slots
→ elite_shadow_trader.evaluate_candidate
```

이다.

## 기존 문구별 정정

### 기존

```text
CE는 intraday/live-slot central Stage3 평가 결과를 사용했다.
```

### 정정

```text
CE는 Stage3 룰북 후보였지만 LiveCentralController 경로가 아니라,
live_candidate_slots가 elite_shadow_trader.evaluate_candidate로 계산한 current-context 후보를 사용했다.
```

### 기존

```text
CE +4.62는 central Stage3의 market_state.active_events에서 왔다.
```

### 정정

```text
CE +4.62는 elite_shadow_trader.evaluate_candidate가 동일 MarketContext.active_events를
직접 has_* flags로 변환해 evaluate_signal에 전달한 결과다.
```

## 유지되는 사실

다음 결론은 경로 정정 후에도 유지된다.

- next-open D-1 경로는 Event OFF이므로 CE +4.62 source가 아님
- CE direct Event는 `MarketContext.active_events`에서 생성됨
- live Event flag 생성은 `use_llm_events`를 존중하지 않음
- 학습은 Event OFF, 라이브 elite evaluator는 Event ON
- +4.62는 direct Event component
- 당시 key별 active event payload는 미보존이라 실제 조합은 복원 불가

## 영향받는 이전 산출물

다음 기존 문서의 경로 명칭은 이번 readout으로 정정해야 한다.

- `live_candidate_news_source_trace_20260711_entity_flow.md`
- `live_candidate_news_source_trace_20260711_readout.md`

기존 파일은 과거 분석 기록으로 유지하고, 이번 산출물을 최신 정정 근거로 삼는다.
