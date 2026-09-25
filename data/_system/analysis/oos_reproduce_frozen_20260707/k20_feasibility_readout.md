# K=20 실현 가능성 확인 — READ-ONLY

범위:

```text
oos_reproduce_frozen backtest K/top-N 설정 확인
$629 자본을 20개 후보에 균등배분할 때 주문 가능성 계산
현재 정규 후보 8개 + waitlist 상위 12개 = 후보 20개 기준
Alpaca asset metadata fractionable/tradable read-only 조회
```

금지 준수:

```text
read-only
주문 제출 없음
--write 실행 없음
코드/데이터/설정 수정 없음
.env 직접 조회/출력 없음
```

최종 판정:

```text
BACKTEST_GLOBAL_K_NOT_APPLIED
BACKTEST_TOP_N_RANKING_NOT_APPLIED
BACKTEST_PER_SIGNAL_INDEPENDENT_TRADES
K20_CAPITAL_PER_CANDIDATE_31_45_USD
K20_INTEGER_ONLY_FEASIBLE_FOR_7_OF_20
K20_FRACTIONAL_REQUIRED_FOR_13_OF_20
ALPACA_ASSET_FRACTIONABLE_TRUE_FOR_ALL_19_UNIQUE_TICKERS
MIN_ORDER_NOTIONAL_UNDER_1USD_ZERO
```

---

## 1. oos_reproduce_frozen backtest K값 확인

대상 코드:

```text
data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py
```

확인된 설정:

```text
POSITION_BUDGET = 10000.0
COMMISSION_RATE = 0.003
WARMUP = 200
candidate_count = 93
stage_counts = {'stage3': 80, 'stage2': 13}
```

핵심 코드:

```text
run_ohlc_freeze_rebuild.py:359
  for i in range(WARMUP, len(df) - 1):

run_ohlc_freeze_rebuild.py:385-392
  sig = evaluate_signal(...)
  if not sig.should_buy:
      continue

run_ohlc_freeze_rebuild.py:400
  shares = POSITION_BUDGET / entry_open

run_ohlc_freeze_rebuild.py:401-418
  simulate_exit(..., shares, POSITION_BUDGET, fractional_shares=True, disable_add_buy=True, ...)

run_ohlc_freeze_rebuild.py:452-460
  for idx, cand in enumerate(candidates, 1):
      r, m = run_candidate(cand, context_cache)
      rows.extend(r)
```

판정:

```text
설정 K값: NOT_APPLIED / NOT_FOUND
매 시점 상위 N종목 선택: NOT_APPLIED / NOT_FOUND
동시 보유 수 제한: NOT_APPLIED / NOT_FOUND
```

해석:

```text
oos_reproduce_frozen은 포트폴리오 레벨에서 매일 상위 K개만 진입시키는 backtest가 아니다.
93개 candidate를 각각 독립적으로 순회하고, 각 candidate/day에서 should_buy=True이면 POSITION_BUDGET=10000.0 기준 독립 trade를 생성한다.
따라서 이 frozen backtest에서 “K=20” 또는 “상위 20개까지 진입”이라는 설정은 존재하지 않는다.
```

### 1.1 참고 — 실제 관측 동시 active trade 수

K 제한이 없기 때문에 실제 겹쳐 열린 trade 수는 매우 크다.

OOS CSV 관측치:

```text
OOS rows: 12915
OOS max_active_trades: 758
OOS max_active_trades day: 2025-04-30
OOS avg_active_trades: 360.3884
OOS p95_active_trades: 534.5
OOS max_active_unique_tickers: 87
OOS max_active_unique_tickers day: 2026-03-31
OOS avg_active_unique_tickers: 69.8784
OOS p95_active_unique_tickers: 83.0
```

판정:

```text
backtest 성과는 K=20 동시보유 제약을 반영한 결과가 아니다.
```

---

## 2. K=20 자본 배분 계산

입력:

```text
capital = 629.00 USD
K = 20
per_candidate_allocation = 629.00 / 20 = 31.45 USD
```

대상 20개:

```text
정규 파일 8개 후보 + waitlist 상위 12개 후보
```

주의:

```text
20개 candidate 기준이다.
ALGT는 stage2/stage3 두 candidate가 있어 unique ticker 수는 19개다.
동일 ticker 중복 진입을 허용하면 ALGT에 2개 candidate allocation이 갈 수 있다.
```

요약:

```text
20 후보 중 정수 1주 이상 가능: 7개
20 후보 중 정수 1주 불가, 소수점 필요: 13개
allocation < 1 USD: 0개
Alpaca asset fractionable=False: 0개
Alpaca asset tradable=False: 0개
```

---

## 3. 후보별 주문 가능성 — $31.45 균등배분

| # | source | candidate_id | ticker | price | $31.45 기준 수량 | 정수주 가능 | 정수주 수량 | 소수점 필요 |
|---:|---|---|---|---:|---:|---:|---:|---:|
| 1 | regular_file | stage3:BMI:07d4ee0f7841 | BMI | 146.369995 | 0.214866 | False | 0 | True |
| 2 | regular_file | stage3:BMA:0c978464f9dd | BMA | 88.830002 | 0.354047 | False | 0 | True |
| 3 | regular_file | stage3:BTBT:363898884d44 | BTBT | 1.682100 | 18.696866 | True | 18 | False |
| 4 | regular_file | stage3:ADMA:42437a3ee595 | ADMA | 9.125000 | 3.446575 | True | 3 | False |
| 5 | regular_file | stage3:CE:998b0b638c66 | CE | 46.610001 | 0.674748 | False | 0 | True |
| 6 | regular_file | stage3:BCS:5e7da5a74b01 | BCS | 27.275000 | 1.153071 | True | 1 | False |
| 7 | regular_file | stage2:ALGT:402f72d48c3c | ALGT | 114.440002 | 0.274816 | False | 0 | True |
| 8 | regular_file | stage3:ALGT:aec5dd5b1dc1 | ALGT | 114.440002 | 0.274816 | False | 0 | True |
| 9 | waitlist_top12 | stage3:ADPT:78c31f1ca209 | ADPT | 21.860001 | 1.438701 | True | 1 | False |
| 10 | waitlist_top12 | stage2:CMC:4f6ee2739add | CMC | 60.970001 | 0.515827 | False | 0 | True |
| 11 | waitlist_top12 | stage3:ANET:fe220620802b | ANET | 184.410004 | 0.170544 | False | 0 | True |
| 12 | waitlist_top12 | stage3:ARKG:50b05b8de94f | ARKG | 43.154999 | 0.728768 | False | 0 | True |
| 13 | waitlist_top12 | stage3:BKSY:f1bcc8efea02 | BKSY | 25.120001 | 1.251990 | True | 1 | False |
| 14 | waitlist_top12 | stage2:FIX:cab7d458767d | FIX | 1789.239990 | 0.017577 | False | 0 | True |
| 15 | waitlist_top12 | stage3:BB:f1bdfe7f8ad9 | BB | 11.468600 | 2.742270 | True | 2 | False |
| 16 | waitlist_top12 | stage3:CDE:ceb9fe0512dc | CDE | 16.105000 | 1.952810 | True | 1 | False |
| 17 | waitlist_top12 | stage3:CIEN:2ed675d30868 | CIEN | 466.559998 | 0.067408 | False | 0 | True |
| 18 | waitlist_top12 | stage3:BWXT:f195725cb792 | BWXT | 188.630005 | 0.166729 | False | 0 | True |
| 19 | waitlist_top12 | stage2:CEF:fe84c0ad85d8 | CEF | 41.285000 | 0.761778 | False | 0 | True |
| 20 | waitlist_top12 | stage3:ARKW:296c057b4ef7 | ARKW | 148.092697 | 0.212367 | False | 0 | True |

정수주만 허용할 경우 가능한 후보:

```text
BTBT 18주
ADMA 3주
BCS 1주
ADPT 1주
BKSY 1주
BB 2주
CDE 1주
```

정수주만 허용할 경우 0주가 되는 후보:

```text
BMI
BMA
CE
ALGT stage2
ALGT stage3
CMC
ANET
ARKG
FIX
CIEN
BWXT
CEF
ARKW
```

---

## 4. Alpaca fractionable/tradable 조회 결과

조회 방식:

```text
Alpaca TradingClient.get_asset(symbol) read-only
주문 제출 없음
```

결과 요약:

```text
19 unique tickers 조회 성공
tradable=True: 19/19
fractionable=True: 19/19
fractionable=False: 0/19
```

| ticker | tradable | fractionable | status | exchange | asset_class |
|---|---:|---:|---|---|---|
| ADMA | True | True | AssetStatus.ACTIVE | AssetExchange.NASDAQ | AssetClass.US_EQUITY |
| ADPT | True | True | AssetStatus.ACTIVE | AssetExchange.NASDAQ | AssetClass.US_EQUITY |
| ALGT | True | True | AssetStatus.ACTIVE | AssetExchange.NASDAQ | AssetClass.US_EQUITY |
| ANET | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| ARKG | True | True | AssetStatus.ACTIVE | AssetExchange.BATS | AssetClass.US_EQUITY |
| ARKW | True | True | AssetStatus.ACTIVE | AssetExchange.BATS | AssetClass.US_EQUITY |
| BB | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| BCS | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| BKSY | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| BMA | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| BMI | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| BTBT | True | True | AssetStatus.ACTIVE | AssetExchange.NASDAQ | AssetClass.US_EQUITY |
| BWXT | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| CDE | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| CE | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| CEF | True | True | AssetStatus.ACTIVE | AssetExchange.ARCA | AssetClass.US_EQUITY |
| CIEN | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| CMC | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |
| FIX | True | True | AssetStatus.ACTIVE | AssetExchange.NYSE | AssetClass.US_EQUITY |

판정:

```text
현재 20 후보의 19 unique ticker 중 Alpaca metadata상 소수점 매수가 안 되는 종목은 없다.
```

---

## 5. broker 코드 기준 소수점/최소 주문 제약

대상 코드:

```text
engine/live/broker/alpaca.py
```

핵심 코드:

```text
AlpacaBroker._qty(shares):
  q = round(float(shares), 6)
  if q <= SHARE_EPS: raise BrokerError

AlpacaBroker._submit_order(...):
  if fractional qty and order_type != MARKET:
      raise BrokerError('fractional qty requires market order + DAY TIF')
  common = {'qty': qty, 'time_in_force': TimeInForce.DAY}
  MarketOrderRequest(**common) for market order
```

현재 dashboard-real 직접 매수 코드:

```text
shares = notional / price
broker.place_buy(... order_type=OrderType.MARKET ...)
```

판정:

```text
코드 기준으로 소수점 매수는 market order + DAY TIF 경로에서 허용된다.
현재 20 후보는 모두 Alpaca fractionable=True라 소수점 필요 종목도 broker metadata상 매수 가능하다.
```

최소 주문금액:

```text
코드 내 explicit minimum notional guard: NOT_FOUND
_positive_float(notional)는 >0만 요구
AlpacaBroker._qty는 qty > 1e-6만 요구
```

이번 $31.45 allocation 기준:

```text
allocation_under_1usd: 0/20
qty <= 1e-6 예상: 0/20
```

판정:

```text
최소 주문금액 미달 후보: 0개
```

주의:

```text
Alpaca 계정/주문 정책의 실시간 최소 notional rule은 broker submit 시 최종 판단될 수 있다.
다만 현재 코드와 asset metadata 기준으로는 $31.45 균등배분이 최소 주문금액 미달로 보이는 후보는 없다.
```

---

## 6. 소수점 매수가 안 되는 종목 / 최소 주문금액 미달 종목

소수점 매수가 안 되는 종목:

```text
없음
```

근거:

```text
Alpaca get_asset(symbol).fractionable=True for all 19 unique tickers
```

최소 주문금액 미달 종목:

```text
없음
```

근거:

```text
per-candidate allocation = $31.45
allocation_under_1usd = 0/20
broker code minimum is qty > 1e-6 and notional > 0, both satisfied by all 20 candidates at current prices
```

정수주만 가능하다고 가정하면 매수 불가 후보:

```text
BMI
BMA
CE
ALGT stage2
ALGT stage3
CMC
ANET
ARKG
FIX
CIEN
BWXT
CEF
ARKW
```

그러나 현재 Alpaca asset metadata 기준 이 후보들도 fractionable=True이므로, market DAY fractional buy 경로에서는 매수 가능으로 분류된다.

---

## 7. 최종 결론

```text
1. oos_reproduce_frozen backtest는 K=20 포트폴리오 backtest가 아니다.
   K/top-N/동시보유 제한 없이 candidate별 should_buy 신호를 독립 trade로 생성했다.

2. frozen backtest는 POSITION_BUDGET=10000.0, fractional_shares=True 구조다.
   따라서 $629 계좌에서 20개 균등 소액 분산 실행은 backtest와 자본/체결 단위가 다르다.

3. $629를 20개 candidate에 균등배분하면 후보당 $31.45다.

4. 정수주만 허용하면 20개 중 7개만 1주 이상 가능하고 13개는 0주가 된다.

5. 현재 Alpaca asset metadata 기준 19 unique ticker 모두 fractionable=True/tradable=True라, 소수점 매수 불가 종목은 없다.

6. 현재 코드/금액 기준 최소 주문금액 미달 후보도 없다.

7. 단, 20개 후보 중 ALGT가 stage2/stage3로 중복되어 unique ticker는 19개다.
   ticker 중복 진입을 허용할지/차단할지는 K=20 실행 정책에서 별도로 결정해야 한다.
```
