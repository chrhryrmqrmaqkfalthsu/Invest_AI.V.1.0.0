# direct Event ON/OFF shadow-compare 계획

## 원칙

운영 스위치는 `true`로 유지한 상태에서 동일 입력을 두 번 평가한다.

```text
ON  = 현재 active_events flag 전달
OFF = event_flags=None
```

두 평가 모두 다음 입력은 완전히 같아야 한다.

- 동일 Rulebook와 rulebook hash
- 동일 OHLCV slice
- 동일 `ctx.score`
- 동일 sector score
- 동일 VIX
- 동일 News/NewsTopics
- 동일 threshold
- 동일 context timestamp

shadow 결과는 주문·후보 pool·가상 ledger에 반영하지 않고 append-only 진단 산출물에만 기록한다.

## 비교 구현 제안

공통 helper에 운영 정책과 별개인 explicit override를 둔다.

```python
flags_on = live_event_flags(ctx, enabled_override=True)
flags_off = live_event_flags(ctx, enabled_override=False)
```

그리고 동일한 `evaluate_signal()` 입력에 각각 전달한다.

```python
result_on = evaluate_signal(..., event_flags=flags_on)
result_off = evaluate_signal(..., event_flags=flags_off)
```

환경 변수나 정책 파일을 평가 중간에 토글하면 다른 thread/process와 race가 생길 수 있으므로 shadow 비교에서는 사용하지 않는다.

## 기록 필드

제안 JSONL 경로:

```text
data/_system/analysis/live_direct_event_shadow_compare/YYYY-MM-DD.jsonl
```

필수 필드:

```text
timestamp
mode
path_id
candidate_id
entity_id
ticker
rulebook_hash
context_timestamp
active_event_keys
market_score_on
market_score_off
sector_score
vix_level
news_sentiment
news_topic_count
use_event_block
score_on
score_off
raw_score_on
raw_score_off
threshold
market_adjustment_on
market_adjustment_off
event_component_on
event_component_off
should_buy_on
should_buy_off
transition
rank_on
rank_off
```

`transition` 값:

- `PASS_PASS`
- `PASS_FAIL`
- `FAIL_PASS`
- `FAIL_FAIL`

정상적인 direct-only 비교에서는 `FAIL_PASS`가 나올 수도 있다. Event component가 음수인 룰북은 Event를 끄면 score가 상승할 수 있기 때문이다.

## 필수 불변식

각 비교 row에서 다음을 검증한다.

1. `market_score_on == market_score_off`
2. `market_adjustment_on == market_adjustment_off`
3. `event_component_off == 0`
4. technical, News, NewsTopics component가 ON/OFF 동일
5. `raw_score_on - raw_score_off == event_component_on` 허용 오차 내 일치
6. `score_on - score_off == event_component_on * market_adjustment` 허용 오차 내 일치

불변식이 깨지면 direct Event 외 입력이 함께 달라진 것이므로 OFF 전환 판단 자료에서 제외한다.

## 모드별 비교 위치

### 실전 후보 슬롯 및 대시보드

`live_candidate_slots.refresh_slots()`에서 현재 pool을 만들 때 ON 결과는 기존 동작에만 사용하고 OFF 결과는 로그에만 기록한다.

확인 항목:

- 후보 pool 크기 변화
- 상위 8 slot 구성 변화
- waitlist 변화
- `PASS_FAIL` 및 `FAIL_PASS` 후보
- final score 순위 변화

Dashboard exporter는 별도의 재검증 단계이므로 exporter에서도 동일 pair를 기록한다. 동일 candidate/context 중복 row는 `path_id`로 구분한다.

### S2 auto

`_validate_candidate_signal()`에서 주문 계획에 사용하는 것은 ON 결과로 유지한다. OFF 결과는 다음만 기록한다.

- 실행 직전 후보가 `PASS_FAIL`인지
- planned notional/shares는 평가하지 않거나 진단 필드로만 계산
- 실제 submit path에는 OFF 결과를 전달하지 않음

### 일반 Runner

`LearnedRuleBook.evaluate()`에 pair 평가 wrapper를 두되 반환값은 ON으로 유지한다.

대상:

- regular ticker scan
- add-buy reevaluation
- reconfirm
- Telegram probability

각 caller의 `path_id`를 전달하거나 caller 로그 context로 구분해야 한다.

### 페이퍼 next-open

현재 direct Event가 이미 OFF다. shadow 비교에서는 다음 consistency check만 수행한다.

- production result와 OFF result 일치
- ON diagnostic을 계산할 경우 historical lagged flags가 아니라 current `active_events`를 섞지 않음

현재 next-open은 live current-context macro 경로가 아니므로 다른 라이브 compare와 같은 표에 섞지 않는 편이 안전하다.

### 가상 shadow·strategy sim

가상 ledger 진입은 ON 결과를 유지하고 OFF 결과를 로그로만 남긴다.

특히 strategy sim은:

- 현재 evaluate_candidate ON/OFF
- `elite_signal_history` ON/OFF

두 단계가 모두 후보 판단에 영향을 줄 수 있으므로 각각 별도 `path_id`로 기록한다.

## 집계 산출물

세션별 CSV 제안:

```text
mode_path_summary.csv
candidate_transitions.csv
rank_comparison.csv
invariant_failures.csv
```

핵심 집계:

- 평가 수
- `PASS_FAIL` 수와 비율
- `FAIL_PASS` 수와 비율
- score delta P50/P90/P99와 최대 절대값
- Event contribution 양수·음수 분포
- top-N 후보 Jaccard
- slot 1~8 교체 수
- ticker별·rulebook별 transition 반복 횟수
- market_score 보존 실패 수

## 전환 판단 절차

1. 모든 실제 운영 evaluator가 shadow row를 남기는지 path coverage 확인
2. market_score·market_adjustment 불변식 위반 0건 확인
3. 실전 후보 pool과 가상 ledger에서 PASS/FAIL 전환 규모 확인
4. direct Event 의존도가 높은 후보를 별도 검토
5. 주문이 없는 shadow 기간 동안 모드별 결과가 누적됐는지 확인
6. 정책 스위치 OFF 전환 후 첫 세션에서 실제 결과가 사전 OFF shadow와 일치하는지 재검증

이번 단계에서는 shadow 로깅이나 dual evaluation을 실제 구현하지 않았다.
