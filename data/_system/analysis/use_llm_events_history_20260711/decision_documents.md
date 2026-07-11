# Event 차단 관련 결정 문서·근거 조사

## 직접 확인된 결정 근거

### 차단 사실

다음 커밋 메시지는 Event 차단이 의도된 변경이었다는 직접 근거다.

```text
d6bd746  LR-8C-FIX LLM 이벤트 차단과 exit 스냅샷 및 트레일링 활성화 개선
86bf54b  LR-8C-FIX LLM 이벤트 차단과 청산 스냅샷·트레일링 활성화 개선
```

`d6bd746`은 실제 LR8C runner에 `use_llm_events=False`를 넣었고, Event flag가 True일 때만 로드되도록 분기했다.

후속 문서·코드는 이 상태를 `stage2 parity`로 고정했다.

```text
cd76748 daily replay:
- default use_llm_events=False
- True는 event_diagnostic 전용
- entry sanity gate에서는 명시 요청 없이는 제외
```

`docs/LR8D_FULL_PIT_RERUN_DESIGN.md`도 기존·후속 full run 설정에 `use_llm_events=false`를 기록한다.

## 과적합 사유 근거

찾지 못했다.

검색 범위:

- `d6bd746`, `86bf54b` commit subject/body/diff
- 직전·직후 LR-8C-FIX 커밋
- Git 전체 commit message
- LR8C/LR8D 보고서
- Event backfill·품질 분석 문서
- `과적합`, `overfit`, `curve-fit`, `noise`, `노이즈`, `불안정` 문자열

차단 커밋과 Event 과적합을 연결하는 문장은 없다.

같은 diff에 `overfitting 방지` 표현이 한 번 존재하지만, 이는 거래 수 5건 미만의 fitness 표본수 페널티를 설명하는 기존 주석이다. Event 사용 여부와 관련 없다.

따라서 다음 근거는 없다.

- Event ON/OFF OOS 비교 결과 때문에 차단했다는 기록
- Event 계수가 과적합됐다는 판단
- Event noise 또는 불안정성을 이유로 차단했다는 설명
- Event 활성 학습 성과가 나빠서 False로 바꿨다는 수치

## 당시 존재한 Event 관련 분석

### EVT-VALUE

`data/_system/research/evtvalue_20260607/EVT_VALUE_REPORT.md`는 차단 직전 Event를 다음처럼 평가했다.

```text
이벤트 블록 기여도는 작지 않다. backfill 가치는 있다.
```

또한 Event 발화일이 66.3%이고 다수 룰북에서 Event contribution이 threshold와 비슷하거나 더 크다고 기록했다.

이 문서는 Event를 과적합 때문에 제거해야 한다고 결론내리지 않았다. 오히려 데이터 backfill 가치가 있다고 판단했다.

### EVT-2C5 품질 분석

`data/_system/research/evt2c5_20260607/EVT2C5_QUALITY_REPORT.md`는 다음 품질 문제를 발견했다.

- 복합 event_type exact-match로 일부 `has_*` 누락
- 신규 구간 Event 밀도 급증
- 2025-10 이후 event_adjustment 평균 약 -21.95

권고는 Event를 끄는 것이 아니라 split normalization을 고친 뒤 Top-N 검증으로 복귀하는 것이었다.

`EVT2C5FIX_REPORT.md`는 이후 `has_*` split 보정을 PASS로 기록했다.

이 품질 문서와 `d6bd746` 차단 커밋 사이에 원인 관계를 명시한 기록은 없다. 시간상 인접하다는 사실만으로 차단 이유라고 추론하지 않는다.

## 결정 문서 판정

```text
Event를 의도적으로 차단했다는 근거: 있음
과적합 때문에 차단했다는 근거: 없음
다른 명시적 이유: 없음
```

따라서 판정은 `INTENTIONALLY_DISABLED_NO_REASON`이다.
