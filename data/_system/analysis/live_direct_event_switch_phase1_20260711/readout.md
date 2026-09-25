# 1단계 — 라이브 direct Event 단일 스위치 도입

## 최종 상태

- 정책 기본값: `live.direct_event_enabled: true`
- 실제 ON 평가: 기존 direct Event 동작 유지
- OFF 평가: shadow 계산·JSONL 기록 전용
- 주문·후보·ledger 반영: ON 결과만 사용
- `MarketContext.score`: 변경 없음
- `engine/market/context.py`: 변경 없음
- 학습·backtest·연구 경로: 변경 없음

## 변경 파일

### 운영 설정

- `config/policy.yaml`
  - `live.direct_event_enabled: true` 추가
  - 키 부재·설정 로딩 예외 시 코드 기본값 `True`

### 공통 모듈

- `engine/live/event_policy.py`
  - `live_direct_event_enabled()`
  - `live_event_flags(ctx, enabled_override=None)`
  - 기존 11개 `has_*` mapping 단일화
  - OFF이면 `None` 반환
  - `append_shadow_direct_event_log()`
  - 로그 경로: `data/_system/analysis/shadow_direct_event/shadow_direct_event_YYYYMMDD.jsonl`
  - 파일 lock 기반 append
  - 기록 실패는 실제 평가에 전파하지 않음

### 라이브 평가 지점

- `engine/live/central_control.py`
- `engine/strategies/learned_rulebook.py`
- `engine/live/elite_shadow_trader.py`

세 파일의 인라인 `active_events → has_*` 변환을 `live_event_flags(ctx)`로 교체했다.

각 지점은 다음 순서로 동작한다.

```text
1. 정책값에 따른 실제 event_flags 생성
2. 기존 evaluate_signal 호출로 실제 결과 확정
3. enabled_override=False로 OFF 결과 별도 계산
4. ON/OFF 비교를 JSONL에 기록
5. 실제 후보·주문·ledger에는 2번 결과만 반환
```

## market_score 보존

세 경로 모두 기존 `market_score` 값을 그대로 ON/OFF 평가에 전달한다.

```text
market_score_on == market_score_off
```

다음 파일은 수정하지 않았다.

```text
engine/market/context.py
```

따라서 `price_score + event_adj`로 계산된 `ctx.score`와 이를 사용하는 market adjustment·crash bonus 경로는 유지된다.

## ON 동작 불변 검증

### flag mapping

기존 세 인라인 구현과 동일한 11개 key·삽입 순서·membership 결과를 비교했다.

결과:

```text
PASS
```

### evaluator bit-level 비교

동일 Rulebook·OHLCV·market score·sector·VIX·News·NewsTopics에 대해:

```text
legacy inline flags
vs
live_event_flags(ctx, enabled_override=True)
```

를 비교했다.

검증 항목:

- `SignalResult` dataclass 전체 equality
- `score.hex()` 동일
- `raw_score.hex()` 동일
- `market_adjustment.hex()` 동일

결과:

```text
PASS
```

### elite 실사용 경로

`engine.live.elite_shadow_trader.evaluate_candidate()`를 mock context와 실제 evaluator로 실행하고 기존 inline flag 결과와 비교했다.

검증 항목:

- final score bit-level 동일
- raw score bit-level 동일
- `should_buy` 동일
- components 동일
- shadow logger의 `result_on`이 기존 결과와 동일

결과:

```text
PASS
```

## shadow 불변식

샘플:

```json
{"candidate_id":"stage3:CE:shadow-sample","event_component":4.42,"market_adjustment_on":1.10575,"market_adjustment_off":1.10575,"market_score_on":71.5,"market_score_off":71.5,"score_on":10.858465,"score_off":5.971050000000001,"score_delta":4.887415,"expected_score_delta":4.887415,"invariant_ok":true}
```

검증 결과:

```text
market_score_on == market_score_off
71.5 == 71.5

market_adjustment_on == market_adjustment_off
1.10575 == 1.10575

score_on - score_off
= 10.858465 - 5.971050000000001
= 4.887415

Event component × market adjustment
= 4.42 × 1.10575
= 4.887415
```

모든 invariant가 `true`다.

샘플 파일:

- `data/_system/analysis/live_direct_event_switch_phase1_20260711/shadow_sample.jsonl`

## 테스트 결과

신규 테스트:

```text
8 passed
```

포함 범위:

- 정책 기본값 ON
- 설정 키 누락 시 ON fallback
- 설정 로딩 예외 시 ON fallback
- 11개 flag mapping 기존과 동일
- evaluator bit-level 동일
- shadow invariant
- 세 live caller 공통 helper 경유
- elite 실제 평가 경로 동일성

기존 회귀 테스트:

```text
central rulebook integrity: 2 passed
central control safety: 17 passed
elite shadow state safety: 4 passed
elite mark-to-market: 2 passed
live exit / Telegram ownership: 23 passed
```

통과 합계:

```text
신규 8 + 기존 48 = 56 passed
```

별도 `tests/test_stage3_context_reuse.py`는 이번 변경 파일을 거치기 전에 기존 test stub이 `runner.run_ga`를 monkeypatch하지만 실제 구현이 `_base.run_ga`를 호출해 실패했다. 실패 stack은 `engine/learning/execution_mode_backtest.py`의 dummy object 처리이며 이번 변경 경로와 무관하다. 학습·backtest 파일 diff는 0건이다.

## 정적 검증

```text
git diff --check: PASS
```

세 live caller의 인라인 mapping 잔존 검색:

```text
0건
```

금지 경로 diff:

```text
engine/learning: 0건
engine/market/context.py: 0건
scripts/research: 0건
```

## shadow 로그 필드

필수 요청 필드를 모두 포함한다.

- `candidate_id`
- `mode`
- `path`
- `market_score_on`
- `market_score_off`
- `event_component`
- `score_on`
- `score_off`
- `pass_on`
- `pass_off`
- `threshold`

추가 검증 필드:

- raw scores
- market adjustments
- actual/expected score delta
- invariant booleans

## 롤백 절차

### 전체 롤백

구현 커밋 하나를 revert한다.

```bash
git revert <implementation_commit>
git push
```

이 방식은 정책 키, 공통 helper, 세 caller 변경, shadow 로깅, 테스트를 한 번에 제거한다.

### 정책 키의 의미

```yaml
live:
  direct_event_enabled: true
```

는 현재 actual 평가 동작을 유지한다.

향후 `false`로 바꾸면 direct Event actual 가산이 OFF가 되지만, 이번 1단계에서는 변경하지 않았다.

정책 키를 `true`로 두는 것만으로 실제 평가 동작은 현행 유지되지만 shadow dual evaluation과 로그 기록은 계속된다. shadow 로깅 자체까지 완전히 제거하려면 구현 커밋 revert가 필요하다.

## 운영 영향

- direct Event actual 결과: 변경 없음
- 실거래 후보·주문: 변경 없음
- paper 후보·주문: 변경 없음
- virtual ledger: 변경 없음
- 추가 동작: OFF shadow 평가와 append-only 진단 로그

## 산출물

- `data/_system/analysis/live_direct_event_switch_phase1_20260711/readout.md`
- `data/_system/analysis/live_direct_event_switch_phase1_20260711/shadow_sample.jsonl`
