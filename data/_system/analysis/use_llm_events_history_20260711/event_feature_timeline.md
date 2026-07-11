# Event 기능 도입–비활성 타임라인

## 2026-05-27 — Event 기능 도입 및 학습 활성

커밋:

```text
f304f9c
feat: 학습 시스템 + 봇 통합 (walk-forward, 시드 계승, TrainingManager)
```

같은 커밋에서 다음이 함께 도입됐다.

- `event_response_*` 룰북 파라미터
- `event_strength_multiplier`
- NewsAPI/GPT 기반 `active_events`
- 11개 Event taxonomy
- `market_history_v2` Event flag
- backtest의 `has_*` flag 추출 및 `evaluate_signal()` 전달

당시 backtest에는 `use_llm_events` switch가 없었다. `market_history_df`가 존재하면 11개 Event flag를 항상 읽었다.

따라서 최초 설계 상태는 학습 Event 활성이다.

## 2026-05-30 — 라이브도 backtest에 맞춰 Event 활성

커밋:

```text
ea4341e
FIX: LearnedRuleBook이 event_flags를 evaluate_signal에 전달
(백테스트-라이브 불일치 B버그 수정, DIFF 1.39->0 검증완료)
```

이 커밋은 라이브 `LearnedRuleBook`이 `ctx.active_events`를 11개 flag로 변환해 evaluator에 전달하도록 수정했다.

커밋 메시지 자체가 당시 기준을 다음처럼 보여준다.

```text
backtest: Event 사용
live: Event 미전달
→ live를 backtest에 맞추는 것이 수정 방향
```

## 2026-06-07 — Event 활성 상태로 실제 GA 실행

`data/_system/research/lr8c_run2_20260607/run2.log`에는 다음이 남아 있다.

```text
2026-06-07 06:30:47 실행 시작
market_history built
v2 이벤트 컬럼 18개 머지 완료
GA Gen 1...50 실행
LR8C_RUN2 progress ...
```

이 실행은 `d6bd746` 이전 코드에서 시작됐다. 당시 switch가 없었으므로 Event flag는 무조건 backtest에 전달됐다.

로그는 최소 `56/340` period row까지 진행됐다. 따라서 “코드상 가능했을 뿐 실제 학습은 한 번도 Event를 켜지 않았다”는 설명은 성립하지 않는다.

다만 이 초기 실행의 최종 survivor 결과는 별도 경로로 보존되지 않았고, 이후 같은 출력 경로가 비활성 rerun에 사용됐다. 따라서 Event 활성 실행의 성과와 비활성 실행 성과를 완전 비교할 최종 artifact는 확인 불가다.

## 2026-06-08 00:23 — 생산 학습 Event 차단

커밋:

```text
d6bd746
LR-8C-FIX LLM 이벤트 차단과 exit 스냅샷 및 트레일링 활성화 개선
```

변경:

```text
이전: market_history가 있으면 Event flag 항상 사용
이후: use_llm_events가 True일 때만 사용
LR8C runner: use_llm_events=False 명시
```

이 커밋이 의미상 실제 `enabled → disabled` 전환점이다.

## 2026-06-08 00:25 — generic default만 True로 복원

커밋:

```text
86bf54b
LR-8C-FIX LLM 이벤트 차단과 청산 스냅샷·트레일링 활성화 개선
```

`run_backtest()`의 generic default는 False에서 True로 바뀌었다.

```diff
-use_llm_events: bool = False
+use_llm_events: bool = True
```

하지만 LR8C runner의 명시적 False는 유지됐다. 따라서 API 기본값 복원은 생산 학습 Event 재활성화를 뜻하지 않는다.

## 2026-06-08 10:21 이후 — Event 비활성 전수 rerun

8개 shard가 재시작됐고 최종 보고서는 340/340 period row 완료를 기록한다.

완료된 `lr8c_run2_trades.jsonl`의 `entry_event_flags`는 전부 0이었다. 전체 파일에서 `has_*=1`도 발견되지 않았다.

즉 보존된 LR8C 최종 artifact는 Event 비활성 학습 결과다.

## 후속 생산 학습

- `3b545c3`: LR8D smoke에 `use_llm_events=False`
- `a5dd0fa`: Stage3 실행 경로와 테스트에 False 고정
- `194c503`: Stage2 실행 경로와 테스트에 False 고정
- `docs/LR8D_FULL_PIT_RERUN_DESIGN.md`: full run 설정에 `use_llm_events=false`

## 이후 True 사용의 성격

후속 코드에 `use_llm_events=True`가 일부 남아 있다.

- event diagnostic replay
- PIT probe
- synthetic/legacy timing 비교

이 경로들은 생산 Stage2/Stage3 GA 학습을 Event 활성 상태로 돌렸다는 증거가 아니다. 일부 probe는 `market_history_df=None`이라 True여도 Event flag가 0이다.

## 타임라인 결론

```text
처음부터 False: 아님
Event 기능 도입 후 실제 활성 GA 실행: 있음
2026-06-08 생산 학습에서 의도적으로 False 전환: 있음
이후 생산 Stage2/Stage3에서도 False 유지: 있음
```
