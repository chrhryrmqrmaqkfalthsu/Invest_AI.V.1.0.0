# CE 후보 선정 로직 및 입력 소스 근본 원인 조사

## 결론

CE가 후보로 선정된 직접 원인은 **악재·하락추세를 걸러내는 하드 게이트가 없는 상태에서, 기술 신호 3개와 Event +4.62가 합산돼 threshold를 크게 넘었기 때문**이다.

관찰된 주문 직전 CE 점수는 다음과 같다.

- MACD: +1.1678
- RSI: +1.7251
- BB: +0.8478
- MA: 0
- Volume: 0
- News: 0
- NewsTopics: 0
- Event: +4.6226
- 총 score: 8.3632
- threshold: 2.6542
- ratio: 3.1510

기술 신호 subtotal만으로도 약 3.7406으로 threshold 2.6542를 넘었다. Event는 후보를 처음부터 가능하게 만든 유일 요소라기보다, 이미 통과 가능한 신호를 매우 강한 신호처럼 증폭했다.

그러나 더 중요한 구조적 사실은 다음이다.

1. MA 정배열이 아니어도 감점이나 차단이 아니라 단순히 0점이다.
2. 부정적 News·NewsTopics·Event를 별도 차단하는 분기문이 없다.
3. Event는 사건의 일반적 악재·호재 의미가 아니라 종목별 학습 계수 부호에 따라 점수를 더하거나 뺀다.
4. CE에서는 당시 Event 합계가 +4.62로 계산돼 long 진입 점수를 강화했다.
5. 회복 가능성·failed follow-through·무회복 국면을 진입 시점에 차단하는 게이트는 관찰된 Stage3 후보 경로에 없다.

## 1. CE 후보 선정 시점

`live_slots_state.json`에 보존된 최초 라이브 후보 기록은 다음과 같다.

- candidate_id: `stage3:CE:998b0b638c66`
- first_signal_at: `2026-07-07T22:22:21.577113+00:00`
- first_signal_price: 48.68
- first_final_score: 7.19545841
- threshold: 2.65418666

다만 최초 신호 순간의 component 전량, active event payload, exact OHLC 마지막 행은 저장되지 않았다. 따라서 최초 신호 시점의 세부 입력을 완전 복원할 수는 없다.

가장 가까운 고신뢰 입력 스냅샷은 2026-07-08 14:27 UTC의 실제 주문 직전 후보 snapshot이다.

- candidate quote: 48.61000061
- score: 8.36324630
- threshold: 2.65418666
- ratio: 3.15096387
- components: `MACD+RSI+BB+Event`

이 값은 point snapshot 소급 추정이 아니라 당시 주문 직전 candidate snapshot에 실제로 저장된 값이다.

## 2. 가격 소스: 세 값은 서로 다른 역할

### 후보 카드·주문 가격

`central_control.py`에서 후보 평가 루프의 `price`는 `runner.broker.get_current_price(ticker)`로 가져온다. 이 값이 SignalResult의 표시 가격, 후보 카드 가격, 주문 의도 snapshot 가격으로 전달된다.

### 기술지표 계산 가격

`evaluate_signal`은 위 broker price를 기술지표 계산에 사용하지 않는다. 별도로 `provider._get_ohlcv(ticker)`를 호출하고, `df.iloc[-1]`의 OHLCV·MA·MACD·RSI·BB·Volume을 사용한다.

즉 후보 선정에는 두 가격 계층이 있다.

1. broker current price: 카드 표시·주문 가격
2. provider OHLCV 마지막 봉: 실제 기술 component 계산

### 대시보드 candidate export와 candle API

`real_dashboard_buy_candidates.json`은 후보가 만들어진 뒤 exporter가 당시 가격을 복사한 산출물이다. exporter가 갱신되지 않으면 값이 멈출 수 있다. 대시보드 candle API는 별도 OHLC 소스이며 `evaluate_signal` 경로에서 직접 참조되지 않는다.

따라서 candidate export의 stale 가격과 candle API 가격 차이는 대시보드 표시 정합성 문제일 수 있지만, **그 export 가격이 기술지표 입력으로 직접 사용됐다는 증거는 없다.**

CE 주문 snapshot은 `candidate_source=live_slots_state_fallback`으로 보존됐고, ANET·BB는 `real_dashboard_buy_candidates_export`였다. 세 종목이 동일한 stale export를 공통 입력으로 사용했다고 볼 근거는 없다.

## 3. stale export 원인 판정

코드와 상태 구조상 `real_dashboard_buy_candidates.json`의 `price`는 실시간 연결 객체가 아니라 export 시점 복사값이다.

- `created_at`/`exported_at`: exporter 실행 시각
- `last_seen_at`: 후보 원천 마지막 관측 시각
- `price`: 그 실행에서 복사된 quote

따라서 파일 갱신이 멈추면 price도 멈춘다. 대시보드 candle API는 계속 새 캔들을 반환할 수 있어 두 값이 달라질 수 있다.

관찰된 stale의 근본 형태는 **선정 엔진이 오래된 candidate export를 기술 입력으로 읽어서가 아니라, 후보 export가 snapshot 산출물인데 대시보드에서 실시간 값처럼 함께 보였던 것**이다.

## 4. 악재 배제 조건 존재 여부

### News

`evaluate_signal`은 `news_sentiment × weight_news_sentiment`를 score에 더한다. 음수면 점수를 깎을 수 있지만 별도 차단은 없다. `use_news_global=false`면 아예 0으로 만든다.

CE 주문 직전 News contribution은 0이었다. 따라서 당시 개별 뉴스 악재가 score를 낮춘 흔적은 없다.

### NewsTopics

토픽별 feature와 학습 weight를 곱해 합산하고 cap으로 제한한다. 역시 음수일 수 있으나 하드 차단은 없다.

CE 주문 직전 NewsTopics contribution은 0이었다.

### Event

active event flags와 룰북의 `event_response_*` 계수를 곱해 합산한 뒤 `event_strength_multiplier`를 곱한다. 사건 이름이 악재라는 이유만으로 자동 음수가 되지 않는다.

CE에서는 결과가 +4.6226이었다. 따라서 당시 활성 이벤트와 CE 룰북 계수 조합은 long 진입을 강화하는 방향이었다.

정확히 어떤 event flag 조합이 +4.6226을 만들었는지는 최초·주문 snapshot에 active event payload가 저장되지 않아 확정할 수 없다. component 결과와 코드 경로는 확정되지만 원본 flags는 미보존이다.

## 5. 하락추세·무회복 필터 존재 여부

관찰된 Stage3 진입 평가 경로에서 MA는 `Aligned_bull`이 true일 때만 양수 점수를 준다. false면 0점이며 reject하지 않는다.

CE는 MA contribution 0인데도 다음 신호로 통과했다.

- MACD cross
- RSI 허용 구간
- lower BB 근접
- Event +4.62

따라서 “정배열 아님”, “하락 중”, “회복 가능성 낮음”을 독립적으로 차단하는 진입 게이트는 이 경로에 없다.

별도의 shadow exit lab은 CE가 이후 `below_ma20`, `below_ma5`, `below_vwap`, `failed_followthrough` 상태였다고 판정해 가상 청산했다. 그러나 이 로직은 후보 선정 evaluator가 아니라 진입 후 exit-side 분석이다. 후보 선정 시점에 같은 판정을 재사용하지 않는다.

## 6. CE vs ANET vs BB

| 종목 | 관찰 snapshot 조합 | News | NewsTopics | Event | MA 정배열 | score/threshold |
|---|---|---:|---:|---:|---|---|
| CE | MACD+RSI+BB+Event | 0 | 0 | +4.62 | 아니오 | 8.36 / 2.65 |
| ANET | MA+MACD+RSI | 0 | 0 | 0 | 예 | 5.03 / 2.64 |
| BB | MA+RSI | 0 | 0 | 0 | 예 | 3.29 / 2.79 |

세 종목은 공통 stale 뉴스 또는 이벤트 소스로 뽑힌 것이 아니다.

- ANET·BB 룰북은 관찰 snapshot에서 `use_event_block=false`였고 Event 0이었다.
- BB는 `use_news_global=false`, ANET은 news weight가 0이어서 News가 실질적으로 작동하지 않았다.
- CE만 Event가 점수의 절반 이상을 차지했다.

공통점은 악재·하락추세 하드 게이트가 아니라 **가중합 score가 threshold를 넘으면 후보가 되는 구조**다.

## 7. 왜 CE가 뽑혔나

코드 기준 근본 원인은 다음 순서다.

1. Stage3 룰북이 CE를 live pool에서 KEEP 상태로 유지했다.
2. OHLCV 마지막 봉에서 MACD·RSI·BB 조건이 켜져 기술 subtotal 3.7406을 만들었다.
3. 정배열은 아니었지만 MA false는 차단이 아니라 0점이었다.
4. News·NewsTopics 악재 반영은 0이었다.
5. Event 반응이 +4.6226으로 더해졌다.
6. 최종 8.3632가 threshold 2.6542를 크게 넘으면서 BUY candidate가 됐다.
7. entry-quality 필드는 `UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE`로 표시돼 실제 차단 기능이 아니었다.
8. failed-followthrough·무회복 판정은 후보 선정 후 exit-side 시스템에만 존재했다.

따라서 관찰된 근본 원인은 단순 가격 stale가 아니다.

> **CE 선정의 직접 원인은 기술 반등형 신호와 강한 양의 Event 점수가 합산된 것이고, 구조적 원인은 악재·하락추세·회복 실패를 독립적으로 거부하는 진입 게이트가 없다는 점이다.**

## 8. 신뢰도와 한계

- 최초 라이브 신호 시각·가격·score는 실제 상태값이다.
- 주문 직전 component·score·threshold·ratio는 실제 candidate snapshot이다.
- 최초 신호 순간 component, active event flags, exact OHLC row는 미보존이다.
- Event +4.62의 계산 결과와 코드 부호는 확정되지만 어떤 event category 조합이었는지는 확정할 수 없다.
- 저장된 이후 shadow exit 분석은 후보 선정 당시 입력이 아니라 사후 상태다.
- 세 종목 비교는 사례 3건이며 통계가 아니다.

## 산출물

- `ce_selection_input_reconstruction.csv`
- `ce_price_source_flow.csv`
- `ce_filter_and_event_logic_audit.csv`
- `ce_anet_bb_selection_path_comparison.csv`
- `ce_candidate_selection_root_cause_readout.md`
