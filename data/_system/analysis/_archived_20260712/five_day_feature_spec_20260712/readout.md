# path_filter 5일 feature 세트 정확한 추출

- 조사일: 2026-07-12
- 대상:
  - `scripts/research/run_stage2_path_filter.py`
  - `scripts/research/run_stage2_path_filter_hold3.py`
  - commit `f354304581d0e9e90a8d284e232e8ae46b9db231`의 historical `run_range_predictor_stage2_v3.py`
  - 해당 historical predictor가 동적으로 불러오는 commit `3048579f792f0d3d213034c6956f33973cc8130b`의 feature builder
- 코드·라이브 설정·daemon 변경: **0**

## 1. 핵심 결론

두 feature 계보는 목적과 표현 방식이 다르다.

### Path filter

직전 5일 경로를 소수의 해석 가능한 shape feature로 요약한다.

핵심 입력:

- D-6 Close
- D-5~D-1 High/Low/Close

핵심 결과:

- 5개 일별 수익률
- 5일 누적수익률
- 상승·하락일 수
- 최근 상승→하락 전환
- 5일 고점·저점
- 고점 발생 후 경과일
- 5일 range 내 D-1 종가 위치
- 5일 고점 대비 pullback
- 최대 단일 상승일
- 초반 급등 후 최근 2일 fade score

보조 feature로 20일 trend-age proxy와 20일 고점 이격을 사용한다.

### Range predictor

직전 5일을 일자별 lag vector로 펼친다.

각 D-1~D-5에 대해:

- close-to-close return
- open-to-close return
- high-low range
- gap
- volume / prior-20-day average
- Stage2 component 16여 개

을 계산한다. 여기에 D-1 candle shape, 거래량 변화, ATR 대비 range, 이동평균·Bollinger·RSI·MACD·Stochastic 등 D-1 기술지표를 추가한다.

Feature normalization은 raw 값의 고정 scaling이 아니라 rolling-train별 empirical quantile mapping이다.

## 2. Path filter 정확한 feature

정의 함수:

`_path5_features()`

위치:

`scripts/research/run_stage2_path_filter.py:227-288`

### Daily returns

```text
ret[D-k] = 100 * (Close[D-k] / Close[D-k-1] - 1)
```

D-5 수익률 계산을 위해 D-6 Close가 추가로 필요하다.

### Five-day cumulative return

```text
cumulative_ret5_pct
= 100 * (Close[D-1] / Close[D-6] - 1)
```

### High/low and range position

```text
high5 = max(High[D-5:D-1])
low5  = min(Low[D-5:D-1])

close_pos5
= clip((Close[D-1] - low5) / (high5 - low5), 0, 1)
```

`high5 == low5`이면 `0.5`를 사용한다.

### Days since high

```text
days_since_high5 = 4 - argmax(High[D-5:D-1])
```

- 0: D-1이 고점
- 4: D-5가 고점

### Pullback

```text
pullback_from_high5_pct
= max(0, 100 * (high5 / Close[D-1] - 1))
```

### Turn-down

```text
recent_turn_down
= 1 if ret[D-2] > 0 and ret[D-1] < 0 else 0
```

### Surge-fade score

```text
first3_max_up_day_pct = max(ret[D-5], ret[D-4], ret[D-3])
last2_ret_pct = 100 * (Close[D-1] / Close[D-3] - 1)

fade_after_surge_score
= max(0, first3_max_up_day_pct)
  + max(0, -last2_ret_pct)
```

초반 3일 중 가장 큰 상승과 최근 2일 약세를 단순 합산한다.

### 실제 gate에 사용되는 path feature

- `days_since_high5`
- `close_pos5`
- `pullback_from_high5_pct`
- `up_days5`
- `down_days5`
- `recent_turn_down`
- `fade_after_surge_score`
- `single_up_day5_pct`

다음 값은 계산·기록되지만 직접 gate 조건에는 사용되지 않는다.

- `cumulative_ret5_pct`
- `days_since_close_high5`
- `first3_max_up_day_pct`
- `last2_ret_pct`
- raw `high5`, `low5`, `close_d1`

## 3. Path filter 보조 feature

### Signal age proxy

위치:

`scripts/research/run_stage2_path_filter.py:196-214`

최근 20개 row를 뒤에서부터 보며 다음 중 하나가 이어진 연속일수를 센다.

```text
Aligned_bull
OR
(Close >= MA5 AND MACD_hist >= 0)
```

주의: 해당 rulebook의 최초 `should_buy` 발생일부터의 실제 signal age가 아니다.

### 20-day high distance

위치:

`scripts/research/run_stage2_path_filter.py:217-224`

```text
dist_high20_pct
= max(0, 100 * (max(High trailing 20) / Close[D-1] - 1))
```

둘 다 엄밀한 5일 feature는 아니므로 inventory에서 `CONFIRMED_AUXILIARY`로 분리했다.

## 4. hold3 버전 차이

`run_stage2_path_filter_hold3.py`는 feature를 하나도 바꾸지 않는다.

명시적 차이:

```text
max_holding_days GA range = 1..3
```

- feature 세트: 동일
- 계산식: 동일
- 정규화: 동일
- lookahead 규칙: 동일
- fitness: 동일

근거:

- 파일 주석: `run_stage2_path_filter_hold3.py:3-23`
- manifest: `111-139`

## 5. Historical range predictor의 5일 feature

commit `f354304`의 실제 feature builder는 commit `3048579f`의 historical module을 사용한다.

### Raw daily lag feature

D-1~D-5 각각에 대해 다음 5개를 생성한다.

```text
STK_lag{k}_ccret
= 100 * (Close[D-k] / Close[D-k-1] - 1)

STK_lag{k}_intr
= 100 * (Close[D-k] - Open[D-k]) / Open[D-k]

STK_lag{k}_range
= 100 * (High[D-k] - Low[D-k]) / Close[D-k]

STK_lag{k}_gap
= 100 * (Open[D-k] / Close[D-k-1] - 1)

STK_lag{k}_volratio20
= Volume[D-k] / mean(Volume[D-k-20:D-k-1])
```

총 25개다.

### Stage2 lag component

D-1~D-5마다 다음 feature family를 생성한다.

- `ma_align`
- `macd`
- `rsi_zone`
- `bb_near_lower`
- `volume_surge`
- `raw_score`
- `score`
- `score_margin`
- `score_ratio`
- `market_adjustment`
- `market_score_proxy`
- `sector_score_proxy`
- `vix_level_proxy`
- `rsi_distance_mid`
- `bb_position`
- `volume_ratio`

중요한 구현 세부:

- lag1만 market context를 받는다.
- lag2~lag5는 neutral/default market context를 사용한다.
- 따라서 `STAGE2_lag{k}_market_*` 이름은 5개 모두 존재하지만 실제 비중은 lag1과 나머지가 다르다.

### D-1 tight feature

D-1 하나에 대해 추가한다.

- candle body, absolute body
- upper/lower wick
- body/wick 대비 candle range
- candle 내 open/close 위치
- bullish flag
- D-2 high/low 대비 close
- high/low break
- inside/outside bar
- volume ratio 5/10
- volume change 1/3
- range / ATR
- range / prior 5/20-day average range
- Close 대비 MA5/20/60/200 및 Bollinger bands
- MA5 대비 MA20
- RSI, ATR_pct, BB_width, Volume_ratio, MACD, Stochastic, Trend, Momentum 등 raw value와 1일 변화
- optional flow/orderbook raw value와 1일 변화

전체 exact family와 공식은 `feature_inventory.csv`에 기록했다.

### Five-day aggregate

```text
STK_ret5
= 100 * (Close[D-1] / Close[D-6] - 1)

STK_vol5
= mean(100 * (High-Low)/Close over D-5..D-1)

STK_range_pos5
= 100 * (Close[D-1]-low5)/(high5-low5)
```

## 6. Normalization

Path filter는 계산된 percent·ratio·binary 값을 그대로 GA gene threshold와 비교한다.

Range predictor는 각 rolling train split에서 feature별 empirical quantile을 만든다.

Quantile levels:

```text
0, .02, .05, .10, .20, .33333, .50,
.66667, .80, .90, .95, .98, 1
```

- finite sample 50개 미만 feature는 제외
- min == max feature는 제외
- q_low/q_high는 0~1로 clamp
- band width는 최소 0.10, 최대 0.70 quantile 단위로 보정
- train에서 만든 quantile mapping을 final evaluation에 재사용

따라서 ticker 가격 수준이나 거래량 절대값 차이에 비교적 강하다.

## 7. “튐 선별” 적합성

### [추정] Path filter 장점

- 고점이 너무 오래됐는지
- 현재 range 위치가 너무 높거나 낮은지
- 고점 대비 pullback이 적당한지
- 최근 상승 후 꺾였는지
- 초반 급등 후 fade인지

를 단순하고 설명 가능하게 거른다.

과최적화와 운영 해석 위험이 상대적으로 낮다.

### [추정] Range predictor 장점

다음이 추가돼 “갑자기 튈 준비”를 감지하는 정보량은 더 많다.

- 일자별 range 변화
- gap sequence
- volume normalization
- D-1 candle compression/expansion
- ATR 대비 range
- indicator acceleration

하지만 feature 수가 매우 많고 optional market/news/flow까지 포함하므로 overfit 및 재현성 위험도 훨씬 크다.

### [추정] 실용적인 최소 조합

전부 사용하기보다 다음처럼 축소하는 편이 합리적이다.

Path shape:

- `close_pos5`
- `pullback_from_high5_pct`
- `days_since_high5`
- `daily_rets_pct`
- `fade_after_surge_score`

Volatility/volume:

- `STK_lag1..5_range`
- `STK_lag1..5_gap`
- `STK_lag1..5_volratio20`
- `D1_range_vs_ATR`
- `D1_volratio5`
- D-1 candle body/wick/range ratios

단, 이는 코드 추출 결과에 기반한 연구 판단이며 검증된 권고는 아니다.

## 8. 라이브 데이터 가용성

`engine/live/elite_shadow_trader.py::_load_ohlcv()`는:

1. ticker adapter에서 1년 history를 로드하고
2. 실패 시 yfinance 1년 일봉으로 fallback하며
3. 최소 60행을 요구하고
4. `calc_indicators()`를 적용한다.

위치:

`engine/live/elite_shadow_trader.py:244-272`

조사 시점 현재 후보 10개 모두:

- 270개 일봉
- 2025-06-12~2026-07-10
- Open/High/Low/Close/Volume
- MA5/20/60/200
- RSI/ATR/ATR_pct
- BB_width/Bollinger bands
- Volume_ratio
- MACD 계열
- Stochastic
- Trend/Momentum
- Aligned_bull/MACD_golden

을 확보했다.

따라서 순수 OHLCV·표준 기술지표 feature는 신호 시점에 계산 가능하다.

## 9. 가용성 제한과 누수 위험

### D0 gap

Historical range predictor는 다음을 포함한다.

- `STK_gap_d0`
- `MKT_{ETF}_gap_d0`

이들은 완료된 D-1 데이터가 아니라 signal-day open을 사용한다. “신호 직전 5일 완료봉”만 쓰려면 제외해야 한다.

### Incomplete daily candle

시장 중 adapter가 최신 당일 partial daily row를 반환하면 `df.tail(5)`가 D0 미완료봉을 포함할 위험이 있다.

현재 일요일 snapshot은 latest row가 2026-07-10 완료봉이라 문제가 없었다. 실제 production에서는 반드시 previous completed session까지 명시적으로 slice해야 한다.

### Optional flow/orderbook

일반 daily OHLCV에는 다음이 보장되지 않는다.

- unfilled buy/sell
- depth
- imbalance
- tick volume
- execution strength
- taker volume

현재 표준 candidate history column에서는 확인되지 않았다. `NOT_STORED` 또는 optional로 취급해야 한다.

### Quantile spec

현재 후보 10개용 predictor qspec과 individual weight/cut artifact가 없다. Raw feature는 계산 가능하지만 historical predictor와 동일한 pass/fail을 재현할 수는 없다.

## 10. 최종 판정

- Path filter 5일 feature 정의: **완전 추출**
- hold3 feature 차이: **없음**
- Historical range predictor 5일 feature: **추출 완료**
- 기본 OHLCV·기술지표 라이브 가용성: **10/10 확인**
- optional flow/news/market context: **부분 가용 / NOT_STORED**
- D0 open feature: **순수 5일 완료봉 사양에서 제외 필요**
- 현재 live selector 적용: **없음**

세부 산출물:

- `feature_inventory.csv`
- `feature_comparison.csv`
- `availability_check.csv`
- `immutability_check.csv`
- `manifest.sha256`
