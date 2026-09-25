# CE Event stale/뒷북 진입 여부 조사

## 최우선 결론

**시장 context 저장 계층에는 Event 시간 감쇠와 TTL이 존재하지만, 실제 진입 score에는 그 감쇠가 적용되지 않는다.**

구조는 다음과 같다.

1. `engine/market/context.py`는 이전 이벤트의 `total_impact_score`를 경과 거래일에 따라 선형 감쇠한다.
2. 이벤트 유형별 TTL도 있다.
   - 금리 인상·인하·인플레이션: 10거래일
   - 연준발언: 3거래일
   - 그 외 기본값: 5거래일
3. 그러나 `engine/live/central_control.py`는 `active_events`의 감쇠된 impact 값을 전달하지 않는다.
4. 대신 이벤트 이름이 dictionary에 존재하는지만 확인해 `has_rate_hike=1` 같은 binary flag를 만든다.
5. `engine/strategies/evaluator.py`는 이 0/1 flag에 종목별 `event_response_*` 계수와 `event_strength_multiplier`를 곱한다.
6. evaluator에는 이벤트 발생 시각, 경과일, `decay_weight`, `total_impact_score`가 전달되지 않는다.

따라서 이벤트 key가 TTL 안에서 살아 있는 동안에는 context의 `decay_weight`가 1.0에서 0.2로 줄어도 종목의 Event 기여는 같은 계수로 유지된다.

> 판정: **Event entry score에는 실효적인 recency decay가 없다. TTL 만료 시 갑자기 0이 되는 계단형 구조다.**

## 1. 감쇠·TTL 코드 경로

### context 계층

`engine/market/context.py`의 `_decay_previous_event`는 다음을 수행한다.

- 발생 또는 감지 시각에서 현재까지 경과 거래일 계산
- `weight = (ttl - elapsed) / ttl`
- `total_impact_score = original_impact × weight`
- TTL 이상이면 event key 제거

이 자체는 정상적인 선형 감쇠다.

### 신규 감지 시 재설정

`_merge_active_events_with_decay`는 같은 이벤트 유형이 신규 feed에서 다시 감지되면:

- `detected_at`을 현재 시각으로 재설정
- `elapsed_days=0`
- `decay_weight=1.0`
- 기존·신규 기사 목록을 합침

따라서 동일 이벤트 유형 또는 동일 기사가 매 refresh에서 다시 감지되면 TTL 타이머가 계속 리셋될 수 있다. 현재 `market_state.json`에서도 같은 제목과 URL이 여러 번 누적된 흔적이 관찰된다.

이것이 실제로 무기한 지속됐는지는 과거 refresh별 snapshot이 없어 확정할 수 없다. 그러나 코드상 반복 감지가 수명을 연장하는 것은 확정된다.

### live 후보 평가 계층

`engine/live/central_control.py`는 다음과 같이 key 존재만 사용한다.

- `"금리정책_인상" in active` → `has_rate_hike=1`
- `"금리정책_인하" in active` → `has_rate_cut=1`
- 다른 Event 유형도 같은 방식

감쇠된 `total_impact_score`와 `decay_meta.decay_weight`는 사용하지 않는다.

`engine/strategies/evaluator.py`는 binary flag와 rulebook 계수를 곱한다. 따라서 같은 이벤트 key라면 첫날과 TTL 직전 날의 종목별 기여가 동일하다.

## 2. CE 진입 시 활성 이벤트 발생 시점

확정된 값:

- 최초 라이브 후보 시각: `2026-07-07T22:22:21.577113+00:00`
- 최초 후보 가격: 48.68
- 주문 직전 snapshot: `2026-07-08T14:27:15.330072+00:00`
- 주문 직전 Event contribution: +4.62260455
- 주문 직전 score: 8.36324630

확인하지 못한 값:

- 당시 active event category 목록
- 각 event의 최초 detected_at
- 원본 기사 publishedAt
- CE +4.62를 구성한 event_response 계수별 세부 합

후보 snapshot에는 Event 합계와 reason만 남고 원본 `event_flags` 및 `active_events` payload가 저장되지 않았다. 7월 7일 market_state snapshot이나 feed log도 보존돼 있지 않았다.

현재 `market_state.json`은 2026-07-10 19:34 UTC refresh 결과다. 여기에 7월 4일·6일·7일·8일·9일 기사들이 함께 들어 있지만, 이를 이용해 7월 7일 CE의 실제 active set을 역추정할 수는 없다.

> 판정: **CE Event +4.62가 정확히 어느 날짜의 어떤 이벤트에서 왔는지는 확인 불가다.**

## 3. 이벤트 발생일부터 진입까지 주가 반영 여부

이벤트 발생 timestamp가 확인되지 않았으므로 이벤트 발생일 가격과 CE 진입 가격을 대조할 수 없다.

- 최초 후보 가격 48.68은 확인됨
- 실제 평균 체결가 48.5715도 확인됨
- 이벤트 발생일·가격은 미확인

따라서 “호재가 이미 며칠 전에 반영된 뒤 뒤늦게 진입했다”는 CE 개별 사실은 현재 보존 자료로 확정할 수 없다.

다만 시스템 구조상 다음 가능성은 성립한다.

- Event가 TTL 안에 남아 있으면 age와 무관하게 동일 rulebook 기여가 적용됨
- 반복 feed 감지가 있으면 detected_at이 다시 현재로 리셋될 수 있음
- 따라서 이미 가격에 반영된 오래된 사건이 후보 score에 원래 크기로 남을 수 있는 경로가 존재함

이는 코드상 가능성의 확인이지 CE에서 실제로 그 일이 일어났다는 증명은 아니다.

## 4. CE 한정인가, 전체 구조인가

CE 한정이 아니다.

`central_control.py`와 `evaluator.py`의 Event flag·score 계산은 Event block을 사용하는 모든 종목에 공통 적용된다. 종목별로 달라지는 것은:

- `use_event_block`
- 11개 `event_response_*` 계수
- `event_strength_multiplier`

시간 감쇠 누락 자체는 공통이다.

최근 보존 후보 중 Event가 score의 과반을 차지한 사례는 9개가 확인됐다.

| 종목 | Event 비중 |
|---|---:|
| BMA | 85.20% |
| BNTX | 81.53% |
| BTBT | 79.46% |
| CMC | 77.21% |
| BWXT | 73.35% |
| BMI | 64.99% |
| BGC | 61.42% |
| CE | 55.24% |
| ACMR | 54.77% |

이 목록은 서로 다른 snapshot 소스의 관찰값이며, 같은 시점·같은 event set으로 통제된 표본은 아니다. 구조적 노출 범위를 보여주는 목록일 뿐 성과 분석이 아니다.

## 5. 현재 market_state에서 확인된 추가 위험

현재 `market_state.json`의 일부 active event에는 같은 기사 제목·URL이 여러 차례 반복 누적돼 있다.

또한 2026-07-10 refresh 상태에 2026-07-04, 7월 6일, 7월 7일 기사 등이 포함돼 있다. 하지만 `decay_meta.detected_at`은 7월 10일로 재설정돼 있고 `decay_weight=1.0`인 유형이 있다.

이는 merge 코드의 신규 감지 reset 동작과 일치한다. 다만 해당 기사들이 실제 매 refresh마다 동일 feed에서 재탐지됐는지에 대한 refresh history는 보존되지 않아 개별 재설정 과정을 완전히 재현할 수 없다.

## 최종 판정

### 확정

1. context에는 선형 decay와 TTL이 있다.
2. live 진입 evaluator는 decay weight와 decayed impact를 사용하지 않는다.
3. evaluator는 active event key를 binary 1로 처리한다.
4. 따라서 TTL 내 Event 기여는 시간에 따라 줄지 않는다.
5. 동일 유형 신규 감지는 TTL 시계를 현재로 리셋한다.
6. 이 구조는 Event를 사용하는 모든 종목에 공통이다.
7. CE 외에도 Event가 score의 과반인 최근 후보가 여러 개 존재한다.

### 확인 불가

1. CE +4.62를 만든 실제 event category 조합
2. 각 이벤트의 최초 발생·감지 timestamp
3. Event 발생일부터 CE 진입일까지의 정확한 경과일
4. 해당 호재가 가격에 이미 반영됐는지에 대한 CE 개별 판정

## 한 줄 결론

> **CE 개별 Event가 며칠 전 호재였는지는 payload 부재로 확인할 수 없다. 그러나 시스템은 Event age를 진입 score에 감쇠 반영하지 않으며, key가 TTL 안에 있는 동안 동일한 종목별 Event 점수를 계속 얹는다. 반복 감지 시 TTL도 리셋될 수 있어 stale Event가 뒷북 진입을 강화할 구조적 경로는 확정된다.**

## 산출물

- `ce_event_decay_logic_audit.csv`
- `ce_event_timestamp_trace.csv`
- `ce_event_to_entry_price_trace.csv`
- `event_majority_recent_candidates.csv`
- `ce_event_stale_entry_readout.md`
