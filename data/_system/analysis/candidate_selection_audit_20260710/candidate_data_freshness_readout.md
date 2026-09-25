# 후보 생성 시 참조 데이터 시점 추적 readout

범위: 코드·설정·주문·기존 상태 파일 변경 없음. 현재 후보 18개를 읽고, 동일 `evaluate_candidate()` 스택으로 read-only 재평가했다. 시장 컨텍스트는 기존 `market_state.json`을 그대로 읽어 사용했으며, refresh/export/state 저장 함수는 호출하지 않았다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/candidate_data_freshness_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_data_freshness_live18.csv`

분석 기준 시각:

```text
2026-07-10T10:33:41.981233+00:00
2026-07-10 06:33:41 America/New_York, 정규장 전
```

## 1. 최종 판정

```text
DATA_MIXED
```

후보 생성이 며칠 전 고정 스냅샷 하나만 계속 보는 구조는 아니다. 다만 모든 입력이 같은 시점의 실시간 데이터도 아니다.

- `should_buy`의 RSI/MACD/BB/MA/volume 입력: 최신 완료 **일봉** 기반.
- 후보의 표시·주문 기준 `price`: Yahoo 1분봉의 마지막 값 기반.
- 시장·VIX·섹터·이벤트 컨텍스트: 최대 60분 캐시.
- ticker sentiment/news: 로컬 daily CSV에서 signal date 기준 D-1 lag, 최대 7일 age 제한.
- 정규장 밖의 60초 daemon tick: 후보를 재평가하지 않고 기존 candidate pool을 재사용.

현재 18개 모두 기술지표 계산에 사용한 마지막 OHLCV 봉은 `2026-07-09`였다. 분석 시점은 미국 정규장 시작 전이므로 이는 최신 완료 세션 봉이며, 며칠 묵은 daily snapshot은 아니다.

그러나 현재가와 일봉 지표를 섞어서 사용한다. `should_buy`는 현재 1분 가격으로 다시 계산되지 않으므로, 전일 일봉 신호가 True인 상태에서 가격만 크게 상승해도 후보가 남을 수 있다. ANET이 이 경우다.

## 2. 후보 생성 데이터 흐름

```text
build_elite_shadow_report()
  정적 학습 룰 후보 생성
  실시간 OHLCV/현재가 사용 안 함

live_candidate_slots.refresh_slots()
  get_market_context()
  build_elite_shadow_report()
  각 후보 evaluate_candidate(candidate, ctx)
    _load_ohlcv(ticker)
    _latest_price(ticker, df)
    evaluate_signal(rulebook, df, market/sector/VIX/news/event)
  should_buy=True 후보만 candidate_pool에 추가
```

핵심 코드:

- `data/_system/ops/live_candidate_slots.py:377-418`
- `engine/live/elite_shadow_trader.py:395-455`

`build_elite_shadow_report()`는 어떤 룰을 평가 대상으로 올릴지 정한다. 실제 현재 `should_buy`는 `refresh_slots()` 안에서 `evaluate_candidate()`가 다시 계산한다.

## 3. 입력별 소스와 갱신 주기

| 입력 | 실제 소스 | should_buy 영향 | 캐시/갱신 | 현재 시점 판정 |
|---|---|---:|---|---|
| OHLCV 일봉 | adapter → `core.data_loader.load_ohlcv()` → `yf.download()` | 직접 사용 | data_loader 5분 + elite trader outer cache 10분 | 최신 완료 일봉 2026-07-09 |
| RSI/MACD/BB/MA/ATR | 위 일봉 OHLCV에서 `calc_indicators()` | 직접 사용 | OHLCV와 동일 | 최신 완료 일봉 기준 |
| volume surge | 마지막 일봉의 `Volume` | 직접 사용 | OHLCV와 동일 | 2026-07-09 일봉 거래량, 오늘 intraday 거래량 아님 |
| current price | `yf.Ticker.history(period="1d", interval="1m", prepost=True)` 마지막 Close | `should_buy`에는 직접 사용 안 함. display/entry quality/주문 가격에 사용 | 30초 메모리 캐시 | 일부 current session, 일부 이전 세션 stale |
| market/VIX/sector/events | `data/_system/market_state.json` 또는 rebuild | market adjustment/event score에 사용 | 최대 60분 | 분석 시 age 45.4분 |
| ticker sentiment/topic | `data/_system/ticker_sentiment/{ticker}_daily.csv` | 룰이 weight를 가지면 사용 | 10분 메모리 캐시, signal D-1 lag, max age 7일 | 의도적 anti-leak daily lag |
| first_signal_at/price | `live_slots_state.first_seen_signals` | should_buy에 사용 안 함 | fresh evaluation 때 갱신 | 추적용 메타데이터 |

### 중요 구분

RSI 14일, MACD, BB 20일처럼 과거 N일을 보는 것은 정상적인 지표 룩백이다. 이번에 확인한 신선도 문제는 룩백 길이가 아니라 **계산의 마지막 row가 언제인가**다.

현재 마지막 row는 18개 모두 2026-07-09였다. 분석 시각 2026-07-10 06:33 ET 기준으로 마지막 완료 미국 세션은 2026-07-09이므로 daily 데이터 자체는 최신이다.

## 4. 일봉과 현재가가 섞이는 정확한 지점

`engine/live/elite_shadow_trader.py`:

```text
_load_ohlcv(): lines 243-271
  adapter.load_history(years=1)
  calc_indicators(df)
  cache TTL 600 seconds

_latest_price(): lines 274-299
  yfinance 1m prepost history 마지막 Close
  cache TTL 30 seconds

 evaluate_candidate(): lines 395-455
  df = _load_ohlcv()
  price = _latest_price()
  res = evaluate_signal(..., df=df, ...)
```

`evaluate_signal()`에는 `price`가 전달되지 않는다. `price`는 반환값과 entry quality 계산에는 들어가지만, 핵심 `should_buy` 점수는 일봉 `df.iloc[-1]`에서 계산된다.

따라서 구조는 다음과 같다.

```text
should_buy = 전일 완료 일봉 지표 + 시장/뉴스 컨텍스트
candidate price = 별도 1분봉 마지막 가격
```

이것이 `DATA_MIXED` 판정의 핵심이다.

## 5. live 18개 재평가 결과

현재 정규 후보 파일:

```text
data/_system/real_dashboard_buy_candidates.json
updated_at = 2026-07-10T10:01:09.313612+00:00
```

read-only 재평가 결과:

| 항목 | 결과 |
|---|---:|
| live 후보 | 18 |
| evaluate_candidate 성공 | 18 |
| 최신 완료 일봉 기준 should_buy=True | 18 |
| 최신 완료 일봉 기준 should_buy=False | 0 |
| current session 1분봉으로 부분 일봉 합성 가능 | 14 |
| 부분 일봉 재계산 should_buy=True | 14 |
| 부분 일봉 재계산 should_buy=False | 0 |
| current session 1분봉 없음 | 4 |

### 최신 완료 일봉 + 현재 부분 일봉 모두 True

```text
CMC, ACMR, ADMA, ANET, ARKW, BB, BCS, BMI, BN,
BTBT, BTE, BWXT, CBRL, CRS
```

이 14개는 “며칠 전 데이터가 박혀 있어서 True”로 확인되지 않았다. 최신 완료 일봉으로도 True이고, 현재 세션 1분봉을 임시로 오늘의 partial daily bar로 합성해 지표를 다시 계산해도 True였다.

### 최신 완료 일봉 True, 현재 부분 일봉 UNKNOWN

```text
AEIS, ALGT, BGC, BMA
```

이 4개는 Yahoo 1분 데이터에 2026-07-10 현재 세션 거래가 없었다. 마지막 1분 bar가 이전 세션이라 current partial daily 재평가는 `UNKNOWN`으로 남겼다.

| ticker | 마지막 1분 bar UTC | 분석 시점 age | 상태 |
|---|---|---:|---|
| AEIS | 2026-07-09 23:20 | 673.7분 | STALE_NO_CURRENT_SESSION_BAR |
| ALGT | 2026-07-09 22:11 | 742.7분 | STALE_NO_CURRENT_SESSION_BAR |
| BGC | 2026-07-09 21:21 | 792.7분 | STALE_NO_CURRENT_SESSION_BAR |
| BMA | 2026-07-09 21:33 | 780.7분 | STALE_NO_CURRENT_SESSION_BAR |

이것은 **현재가 경로의 staleness**다. 다만 should_buy는 이 1분 가격으로 계산되지 않으므로, 이 stale price가 should_buy=True를 직접 만든 것은 아니다. 이 4개의 intraday 최신 재판정은 데이터 부족으로 UNKNOWN이다.

전체 세부 값은 `candidate_data_freshness_live18.csv`에 저장했다.

## 6. 신호 지속과 데이터 지연 구분

### 신호가 실제로 지속되는 것으로 확인된 개체

```text
14개: CMC, ACMR, ADMA, ANET, ARKW, BB, BCS, BMI, BN,
BTBT, BTE, BWXT, CBRL, CRS
```

판정 라벨:

```text
SIGNAL_PERSISTS_ON_CURRENT_PARTIAL
```

### 데이터 지연 때문에 True로 남았다고 확인된 개체

```text
없음
```

현재 부분 일봉으로 재계산했을 때 False로 바뀐 후보가 0개였다.

### 판정 보류

```text
4개: AEIS, ALGT, BGC, BMA
```

판정 라벨:

```text
SIGNAL_PERSISTS_ON_LATEST_COMPLETED_DAILY_CURRENT_PARTIAL_UNKNOWN
```

현재 세션 1분 거래가 없어 intraday partial 확인이 불가능했다. 불명확하므로 stale-signal이라고 단정하지 않았다.

## 7. ANET 구체 추적

```text
candidate_id: stage3:ANET:fe220620802b
first_signal_at: 2026-07-07T22:22:21.577113+00:00
first_signal_price: 164.50
분석 시 first signal age: 60.19시간
마지막 완료 일봉: 2026-07-09
마지막 완료 일봉 Close: 184.69
최신 1분 bar: 2026-07-10T10:32:22+00:00
최신 1분 가격: 183.50
first signal 대비: +11.55%
```

production 평가:

```text
should_buy=True
score=3.025097
threshold=2.639090
ratio=1.146265
reasons=정배열(+1.31) + RSI(+1.71)
```

현재 1분 데이터를 2026-07-10 partial daily bar로 합성해 재평가:

```text
partial_should_buy=True
partial_score=3.025097
partial_threshold=2.639090
partial_ratio=1.146265
```

ANET 결론:

```text
오래된 7월 7일 데이터가 고정돼 True인 것이 아니다.
7월 9일 최신 완료 일봉으로 다시 계산해도 True이고,
7월 10일 현재 partial bar를 반영해도 True다.
```

따라서 ANET은 **데이터 지연 문제라기보다 신호 지속 + first signal 대비 진입가 상승 문제**다. 이전 추격률 분석과 일치한다.

## 8. first_signal 이후 실제 재평가 여부

현재 18개 모두:

```text
last_seen_at > first_signal_at
```

즉 한 번 통과한 후 평가 없이 영구 보존된 것은 아니다. `refresh_slots()`의 fresh evaluation에서 계속 should_buy=True이면 `last_seen_at`, `last_price`, `last_final_score`가 갱신된다.

또한 first signal이 오래된 후보 중 16개는 현재 사용한 2026-07-09 daily session close가 first_signal_at보다 뒤다. 즉 최초 신호 당시 봉이 아니라 이후 daily bar까지 진행된 상태로 다시 평가됐다. BGC와 BTE는 2026-07-10 premarket에 처음 등록돼 first signal이 현재 latest completed daily bar보다 뒤다.

## 9. 60초 daemon의 실제 동작

실행 중인 process:

```text
venv/bin/python data/_system/ops/live_candidate_slots.py daemon --interval 60
```

하지만 60초마다 항상 재평가하는 것은 아니다.

`refresh_slots()`:

```text
정규장:
  get_market_context()
  build_elite_shadow_report()
  각 후보 evaluate_candidate()
  candidate_pool 재생성

정규장 밖:
  outside_regular_hours_cached_pool
  기존 candidate_pool 재사용
  evaluate_candidate 호출 안 함
```

현재 상태:

```text
last real evaluation: 2026-07-10T10:01:08.341626+00:00
force_evaluate: true
current daemon ticks: REFRESH_SKIPPED / outside regular hours
```

즉 분석 시점의 premarket 60초 tick은 최신가 재계산이 아니라 캐시 pool 재사용이었다. 정규장 시작 후에는 매 tick evaluate_candidate를 다시 호출한다.

정규장 중에도 입력별 신선도는 다르다.

```text
1분 price cache: 30초
OHLCV/indicator outer cache: 10분
market context cache: 60분
```

그리고 일봉 OHLCV는 current intraday tick이 아니라 latest daily row를 사용한다.

## 10. 확인된 데이터 지연 지점

### 10.1 현재가 timestamp age 검사 없음

`_latest_price()`는 Yahoo 1분 history의 마지막 Close를 가져오지만, bar timestamp가 현재 세션인지 또는 몇 분 전인지 검사하지 않는다.

현재 실제 사례:

```text
AEIS, ALGT, BGC, BMA = 이전 세션 마지막 1분 값을 현재 price로 수용
```

이 지점은 display/entry price 신선도 리스크다.

### 10.2 정규장 밖 pool 재사용

`refresh_slots()`는 정규장 밖에서 평가를 생략한다. 따라서 premarket/after-hours 가격 변화가 후보 진입·탈락에 반영되지 않는다.

### 10.3 일봉 신호와 intraday 실행 가격의 시점 불일치

현재 daily data가 오래돼서 문제가 아니라, 전략 점수는 latest completed daily bar에 고정되고 주문 가격은 intraday current price를 사용하는 구조적 시점 차이다.

이 차이는 특히 first signal 이후 가격이 많이 오른 종목에서 추격 리스크를 만든다.

## 11. 종합 결론

```text
후보 생성 데이터 판정: DATA_MIXED
현재 OHLCV stale snapshot 판정: NOT_CONFIRMED
현재 price 일부 stale 판정: CONFIRMED
ANET 신호 지속 판정: CONFIRMED
```

“며칠 전 데이터라 지금은 안 맞는 신호인데 True로 박혀 있다”는 가설은 현재 18개에서 확인되지 않았다.

- 18개 전부 최신 완료 일봉으로 재평가해 True.
- current partial bar 확인 가능한 14개도 전부 True.
- ANET도 current partial 기준 True.
- 4개는 current minute bar가 없어 intraday 판정 UNKNOWN.

따라서 현재 관측상 핵심 문제는 **stale daily snapshot**보다는 다음 두 가지다.

1. daily signal은 지속되는데 실제 진입 가격이 first signal 가격에서 크게 멀어질 수 있음.
2. current price loader가 timestamp age를 검증하지 않아 일부 종목 가격이 이전 세션 값일 수 있음.

이 결과는 추격 게이트를 즉시 적용하라는 결론은 아니다. 다만 추격 리스크가 데이터 지연 착시가 아니라 실제로 “지속되는 daily signal + 더 높은 intraday execution price”에서 생긴다는 근거는 강화됐다.
