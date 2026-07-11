# use_llm_events=False 도입 이력 — 과적합 때문에 껐는가

## 최종 판정

`INTENTIONALLY_DISABLED_NO_REASON`

Event 학습은 처음부터 꺼져 있던 것이 아니다.

Event 기능은 2026-05-27 도입 당시 backtest에서 항상 활성화됐고, 2026-06-07 실제 LR8C GA도 Event가 켜진 코드로 실행됐다. 이후 2026-06-08 커밋 `d6bd746`에서 `use_llm_events` switch가 처음 추가되면서 LR8C 생산 학습 runner에 `False`가 명시됐다.

따라서 실제 이력은 다음이다.

```text
Event 학습 활성
→ 실제 GA 실행
→ 생산 학습에서 명시적으로 비활성
```

하지만 이를 과적합·curve-fit·noise·불안정 때문에 껐다는 커밋·문서 근거는 발견되지 않았다.

## 1. use_llm_events 값 변경 이력

### Event 최초 상태

커밋:

```text
f304f9c  2026-05-27 06:00:27 UTC
feat: 학습 시스템 + 봇 통합 (walk-forward, 시드 계승, TrainingManager)
```

이 커밋에서 Event 룰북 파라미터, LLM Event 분류, `active_events`, historical `has_*` flag와 backtest 전달이 함께 도입됐다.

당시에는 `use_llm_events` 인자 자체가 없었다. backtest는 `market_history_df`가 존재하면 Event flag를 무조건 읽었다.

```python
cur_event_flags = {}
if market_history_df is not None:
    mkt = lookup_market_at_lagged(...)
    for key in EVENT_FLAG_KEYS:
        cur_event_flags[key] = int(mkt.get(key, 0) or 0)
```

의미상 기본값은 항상 True였다.

### switch 최초 도입 및 False 전환

커밋:

```text
d6bd746  2026-06-08 00:23:41 UTC
LR-8C-FIX LLM 이벤트 차단과 exit 스냅샷 및 트레일링 활성화 개선
```

이 커밋에서 `use_llm_events`가 처음 등장했다.

```python
def run_backtest(..., use_llm_events: bool = False):
```

Event flag는 다음 조건부 구조로 변경됐다.

```python
event_flags = _zero_event_flags()
if use_llm_events:
    for key in EVENT_FLAG_KEYS:
        event_flags[key] = int(mkt.get(key, 0) or 0)
```

실제 LR8C GA runner에도 명시적으로 들어갔다.

```python
"use_llm_events": False
```

따라서 이 커밋이 실제 enabled→disabled 전환이다.

### generic default의 즉시 복원

2분 뒤 커밋:

```text
86bf54b  2026-06-08 00:25:09 UTC
LR-8C-FIX LLM 이벤트 차단과 청산 스냅샷·트레일링 활성화 개선
```

에서는 generic `run_backtest()` 기본값만 다시 True로 바뀌었다.

```diff
-use_llm_events: bool = False
+use_llm_events: bool = True
```

그러나 LR8C runner의 명시적 False는 유지됐다. 따라서 생산 학습은 계속 Event 비활성이었다.

## 2. 실제 Event 활성 학습이 있었는가

있었다.

`data/_system/research/lr8c_run2_20260607/run2.log`는 2026-06-07 06:30:47 UTC에 시작됐다. 이는 `d6bd746` 이전이다.

로그에는 다음이 남아 있다.

```text
market_history built
v2 이벤트 컬럼 18개 머지 완료
GA Gen 1...50
LR8C_RUN2 progress ...
```

최소 56/340 period row까지 진행됐다. 당시 코드에는 switch가 없었으므로 historical Event flag가 GA fitness에 실제로 전달됐다.

따라서 `NEVER_ENABLED`는 아니다.

다만 초기 Event 활성 실행의 최종 artifact는 별도 보존되지 않았다. 동일 출력 경로가 이후 Event 비활성 rerun에 사용됐다. 따라서 활성·비활성의 최종 성과 비교는 확인 불가다.

## 3. Event 비활성 rerun

2026-06-08 10:21 UTC 이후 8개 shard가 다시 실행됐다.

최종 결과:

- 340/340 period row 완료
- `lr8c_run2_trades.jsonl`의 `entry_event_flags`가 모두 0
- 전체 artifact에서 `has_*=1` 미발견

즉 현재 보존된 LR8C 최종 룰북은 Event 비활성 학습 결과다.

## 4. 후속 Stage2·Stage3

Event 비활성 정책은 후속 생산 학습에도 이어졌다.

- `3b545c3`: LR8D smoke에 `use_llm_events=False`
- `a5dd0fa`: Stage3 path와 테스트에서 False 확인
- `194c503`: Stage2 runner와 테스트에서 False 확인
- `docs/LR8D_FULL_PIT_RERUN_DESIGN.md`: full run 설정 `use_llm_events=false`

후속 코드의 True 사용은 event diagnostic, PIT probe, synthetic timing 비교에 한정된다. 생산 Stage2/Stage3 GA를 Event 활성으로 실행했다는 증거는 아니다.

## 5. 과적합 때문에 껐는가

근거 없음.

검색한 범위:

- `d6bd746`, `86bf54b` 커밋 메시지·본문·diff
- 직전·직후 LR8C 관련 커밋
- 전체 Git commit message
- LR8C/LR8D 보고서
- Event backfill·품질 문서
- `과적합`, `overfit`, `curve-fit`, `noise`, `노이즈`, `불안정` 표현

확인된 메시지는 오직 다음이다.

```text
LLM 이벤트 차단
```

차단 이유를 설명하는 본문·주석·수치 비교는 없다.

같은 diff에 있는 `overfitting 방지` 문구는 거래 수 5건 미만 fitness 페널티에 관한 일반 주석으로 Event 차단과 무관하다.

## 6. 관련 Event 문서

### EVT-VALUE

차단 직전 문서는 다음처럼 판단했다.

```text
이벤트 블록 기여도는 작지 않다. backfill 가치는 있다.
```

Event를 과적합 때문에 제거해야 한다는 결론은 아니다.

### EVT-2C5

이 문서는 복합 event_type flag 누락과 신규 구간 Event 밀도 급증을 발견했다. 그러나 권고는 Event off가 아니라 normalization fix였다.

후속 C5-FIX도 split flag 보정을 PASS로 기록했다.

이 품질 문제들이 차단 이유였다는 명시적 연결 근거는 없다.

## 판정 선택 이유

### `INTENTIONALLY_DISABLED_OVERFIT` 아님

True→False 변경은 있지만 과적합 사유 근거가 없다.

### `NEVER_ENABLED` 아님

Event는 최초 도입부터 backtest에 활성화됐고 실제 GA 실행 로그도 있다.

### `INSUFFICIENT` 아님

도입·변경·후속 유지 이력은 Git과 실행 로그로 확인된다.

### 최종

`INTENTIONALLY_DISABLED_NO_REASON`

Event 학습은 실제로 활성 상태에서 사용됐다가 2026-06-08 생산 GA에서 의도적으로 차단됐다. 다만 저장소에 남은 근거만으로는 과적합 때문이라고 판정할 수 없다.

## 확인 불가

- 차단을 결정한 사람의 저장소 밖 의사결정
- Event 활성 초기 run의 최종 survivor 성과
- Event ON/OFF를 동일 조건으로 비교한 당시 실험 결과
- 과적합이 구두 또는 외부 채널에서 논의됐는지 여부

## 산출물

- `data/_system/analysis/use_llm_events_history_20260711/value_change_timeline.csv`
- `data/_system/analysis/use_llm_events_history_20260711/change_commit_evidence.md`
- `data/_system/analysis/use_llm_events_history_20260711/event_feature_timeline.md`
- `data/_system/analysis/use_llm_events_history_20260711/actual_run_evidence.csv`
- `data/_system/analysis/use_llm_events_history_20260711/decision_documents.md`
- `data/_system/analysis/use_llm_events_history_20260711/readout.md`

운영 코드·설정 변경: 0건
