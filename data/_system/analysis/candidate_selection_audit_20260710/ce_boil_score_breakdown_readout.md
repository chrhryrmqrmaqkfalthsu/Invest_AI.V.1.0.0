# CE·BOIL 기준선 통과 원인 분석 readout

범위: 코드·데이터·설정·주문 변경 없이 현재 `data/_system/real_dashboard_buy_candidates.json`, `data/_system/live_slots_state.json`, evaluator/sector 관련 코드만 읽어 추적했다. 수치 기준은 사용자가 지적한 CE `2.68`, BOIL `2.74`가 들어 있는 `real_dashboard_buy_candidates.json`의 현재 export 값이다.

## 결론 요약

| 항목 | CE | BOIL |
| --- | --- | --- |
| candidate_id | `stage3:CE:998b0b638c66` | `stage3:BOIL:9044dc2c67a3` |
| final_score | `2.678767` | `2.738598` |
| threshold | `2.654187` | `2.183173` |
| ratio | `1.009261` | `1.254412` |
| 통과 성격 | 극단적 턱걸이 | 비교적 여유는 있으나 BB+RSI 단순 통과 |
| 결정적 tipping 요소 | `events +0.105913`가 없으면 fail | `RSI +0.515996`가 없으면, 보정 후에도 거의 fail |
| sector 판정 | `SECTOR_DEFAULT_FALLBACK` | `SECTOR_DEFAULT_FALLBACK` |
| sector 기여분 | `0.000000` | `-1.041373` |
| sector 제거 시 final_score | `2.678767` | `3.779972` |
| sector 제거 시 통과 여부 | PASS | PASS |

핵심 결론: **CE·BOIL의 통과 원인은 섹터 fallback이 점수를 올려서가 아니다.** CE는 `use_market_entry_adjustment=False`라 섹터가 최종 점수에 전혀 반영되지 않는다. BOIL은 `sector_name=tech`, `sector_score=100`, `sector_strength_weight=-0.543990` 조합 때문에 오히려 점수가 약 `-1.041` 깎인다. 실제 문제는 섹터가 아니라 **진입지표가 너무 단순하게 threshold를 넘기는 구조**다. CE는 RSI+BB만으로 threshold 바로 아래까지 가고, 작은 이벤트 점수가 턱걸이를 만든다. BOIL은 BB 2.0점 + RSI 0.516점만으로 이미 raw 기준 threshold를 넘는다.

## 1. 점수 생성 코드 경로

| 기능 | 파일·라인 | 확인 내용 |
| --- | --- | --- |
| 기술 지표 점수 산출 | `engine/strategies/evaluator.py:64-174` | MA, MACD, RSI, BB, volume, news, topic news를 컴포넌트로 계산 |
| 이벤트 점수 추가 | `engine/strategies/evaluator.py:176-197` | event_flags와 rulebook event_response를 더해 `components.events` 생성 |
| 시장/섹터/VIX 보정 | `engine/strategies/evaluator.py:206-223` | `raw_score * market_adjustment = final_score` |
| threshold 판정 | `engine/strategies/evaluator.py:225` | `should_buy = final_score >= rb.signal_threshold` |
| live candidate 평가 | `engine/live/elite_shadow_trader.py:395-470` | rulebook 로드, OHLCV 로드, `sector_strength.get(rb.sector_name, 50.0)`로 sector_score 설정 후 `evaluate_signal` 호출 |

보정식:

```text
market_norm = (market_score - 50) / 50
sector_norm = (sector_score - 50) / 50
vix_norm    = (18 - vix_level) / 10
correlation_adj = market_norm*market_score_weight + sector_norm*sector_strength_weight + vix_norm*vix_sensitivity
market_adjustment = 1 + clamp(correlation_adj * market_adjustment_strength, -strength, +strength)
final_score = raw_score * market_adjustment
```

단, `use_market_entry_adjustment=False`면 `market_adjustment=1.0`으로 강제된다.

## 2. CE 점수 분해

### CE 컴포넌트 점수

| 구성요소 | 기여점수 | final_score 기준 역할 | 제거 시 final_score | 제거 시 통과 여부 |
| --- | ---: | --- | ---: | --- |
| RSI | `1.725078` | 핵심 진입지표 | `0.953690` | FAIL |
| BB | `0.847776` | 핵심 진입지표 | `1.830991` | FAIL |
| events | `0.105913` | **턱걸이 tipping 요소** | `2.572854` | FAIL |
| MA align | `0.000000` | 기여 없음 | `2.678767` | PASS |
| MACD | `0.000000` | 기여 없음 | `2.678767` | PASS |
| volume | `0.000000` | 기여 없음 | `2.678767` | PASS |
| news | `0.000000` | 기여 없음 | `2.678767` | PASS |
| news_topics | `0.000000` | 기여 없음 | `2.678767` | PASS |

CE 계산:

```text
raw_score = RSI 1.725078 + BB 0.847776 + events 0.105913 = 2.678767
market_adjustment = 1.000000
final_score = 2.678767
threshold = 2.654187
margin = +0.024581
```

CE의 결정적 문제는 margin이 `+0.024581`뿐이라는 점이다. `events +0.105913`만 빠져도 `2.572854`로 threshold 아래다. 즉 CE는 강한 다중 확인으로 통과한 것이 아니라 **RSI+BB가 threshold 바로 아래까지 만들고, 작은 이벤트 점수가 통과를 완성한 턱걸이 후보**다.

### CE 섹터 보정

| 항목 | 값 |
| --- | ---: |
| sector_name | `tech` |
| sector_score | `100.0` |
| sector_strength_weight | `-0.620862` |
| sector_norm | `1.000000` |
| sector_term | `-0.620862` |
| market_adjustment_strength | `0.106842` |
| use_market_entry_adjustment | `False` |
| 실제 final_score 내 sector 기여분 | `0.000000` |
| sector 제거 시 final_score | `2.678767` |
| sector 제거 시 통과 여부 | PASS |

CE는 sector_name이 fallback tech이지만, 해당 룰은 `use_market_entry_adjustment=False`라 최종 점수에는 시장/섹터/VIX 보정이 전혀 들어가지 않는다. 따라서 CE 통과의 직접 원인은 섹터 왜곡이 아니라 RSI+BB+작은 이벤트 점수다.

## 3. BOIL 점수 분해

### BOIL 컴포넌트 점수

| 구성요소 | 기여점수 | final_score 기준 역할 | 제거 시 final_score | 제거 시 통과 여부 |
| --- | ---: | --- | ---: | --- |
| BB | `2.000000` | 최대 진입지표 | `0.561649` | FAIL |
| RSI | `0.515996` | **tipping 요소** | `2.176950` | FAIL |
| MA align | `0.000000` | 기여 없음 | `2.738598` | PASS |
| MACD | `0.000000` | 기여 없음 | `2.738598` | PASS |
| volume | `0.000000` | 기여 없음 | `2.738598` | PASS |
| events | `0.000000` | 기여 없음 | `2.738598` | PASS |
| news | `0.000000` | 기여 없음 | `2.738598` | PASS |
| news_topics | `0.000000` | 기여 없음 | `2.738598` | PASS |

BOIL 계산:

```text
raw_score = BB 2.000000 + RSI 0.515996 = 2.515996
market_adjustment = 1.088475
final_score = 2.738598
threshold = 2.183173
margin = +0.555425
```

BOIL은 raw_score만으로도 이미 threshold를 통과한다. 다만 BB 단독으로는 `2.000000 * 1.088475 = 2.176950`이라 threshold `2.183173`보다 약 `0.006223` 낮다. 즉 **RSI 0.516점이 최종 통과를 확정하는 tipping 요소**다. volume, MACD, MA, news, events 확인은 모두 0점이다.

### BOIL 시장/섹터/VIX 보정

| 항목 | 값 |
| --- | ---: |
| market_score | `77.6` |
| sector_score | `100.0` |
| vix_level | `15.97` |
| market_score_weight | `0.828391` |
| sector_strength_weight | `-0.543990` |
| vix_sensitivity | `1.000000` |
| market_adjustment_strength | `0.760862` |
| use_market_entry_adjustment | `True` |
| market_norm | `0.552000` |
| sector_norm | `1.000000` |
| vix_norm | `0.203000` |
| market_term | `0.457272` |
| sector_term | `-0.543990` |
| vix_term | `0.203000` |
| correlation_adj | `0.116282` |
| market_adjustment | `1.088475` |

BOIL의 보정 분해:

| 보정 시나리오 | final_score | threshold 통과 여부 | 설명 |
| --- | ---: | --- | --- |
| 전체 보정 포함 | `2.738598` | PASS | 현재 값 |
| 시장/섹터/VIX 보정 전 raw | `2.515996` | PASS | 보정이 없어도 통과 |
| 섹터 term만 제거 | `3.779972` | PASS | sector fallback 제거 시 오히려 점수 상승 |
| 현재 섹터 term의 final 기여분 | `-1.041373` | N/A | fallback tech/100 + 음수 sector weight가 BOIL 점수를 깎음 |

BOIL은 섹터 fallback이 점수를 올린 것이 아니다. `sector_name=tech`로 인해 `sector_score=100`이 들어가고, 여기에 음수 `sector_strength_weight=-0.543990`가 곱해져 sector_term은 `-0.543990`다. 이 term이 최종 점수를 약 `-1.041373` 낮춘다. 그럼에도 BB+RSI raw 점수와 시장/VIX 양수 보정 때문에 통과한다.

## 4. 섹터 fallback 판정

### 섹터명 생성 코드

| 코드 | 의미 |
| --- | --- |
| `engine/learning/learner.py:38-52` | `_detect_sector_name(meta.name)`가 `meta.name` 문자열 키워드로 sector_name을 정함 |
| `engine/learning/learner.py:52` | 어떤 키워드도 매칭되지 않으면 무조건 `tech` 반환 |
| `engine/pipeline/context.py:234-238` | 새 파이프라인 context도 동일 helper를 사용해 base_rulebook.sector_name 지정 |
| `engine/strategies/rulebook.py:90` | Rulebook 기본값도 `sector_name="tech"` |
| `engine/live/elite_shadow_trader.py:416-417` | 평가 시 `ctx.sector_strength.get(rb.sector_name, 50.0)`로 sector_score를 가져옴 |

중요: 현재 코드에는 미국 개별 종목의 GICS/업종 같은 **권위 있는 실제 섹터 조회 경로가 없다.** `sector_name`은 `meta.name` 문자열 키워드 매칭 결과이며, 미매칭이면 기본값 `tech`다.

### CE·BOIL 판정

| ticker | meta.name | adapter 판정 | code sector_name | 판정 | 근거 |
| --- | --- | --- | --- | --- | --- |
| CE | `Celanese Corporation` | `USStockAdapter` | `tech` | `SECTOR_DEFAULT_FALLBACK` | `_detect_sector_name` 키워드 미매칭 → `return "tech"` |
| BOIL | `ProShares Ultra Bloomberg Natural Gas` | `USStockAdapter` | `tech` | `SECTOR_DEFAULT_FALLBACK` | `BOIL`이 `US_ETF_TICKERS`에 없어 USStockAdapter로 감지되고, `_detect_sector_name`에 `gas`/`natural gas` 키워드가 없어 `tech` fallback |

BOIL은 특히 문제가 크다. 이름상 천연가스 ETF 성격인데 `US_ETF_TICKERS` 목록에 없어 ETF로도 분류되지 않았고, energy 키워드에도 `gas`가 없어 `tech`로 떨어진다.

### sector_score=100의 출처

`sector_score=100`은 종목 자체의 실제 섹터 점수가 아니라, fallback된 `sector_name=tech`에 대해 시장 컨텍스트의 `sector_strength["tech"]`를 조회한 값이다.

관련 코드:

- `engine/market/context.py:363-370`: `SECTOR_ETFS = {"tech": "XLK", ...}`
- `engine/market/context.py:373-382`: 각 sector ETF의 60일 수익률을 `clip((ret_60d + 10) * 5, 0, 100)`으로 점수화
- `engine/market/context.py:578-620`: `sector_strength`를 market context에 주입
- 현재 `data/_system/market_state.json`: `sector_strength.tech = 100.0`

즉 CE와 BOIL의 `sector_score=100`은 “CE/BOIL의 실제 업종이 강하다”가 아니라 **둘 다 tech로 fallback되었고, 현재 XLK 기반 tech sector_strength가 100이기 때문에 생긴 값**이다.

## 5. live 후보 26개 섹터 fallback 비율

기준: 현재 `live_slots_state.candidate_pool` 26개. 동일 ticker라도 stage2/stage3가 각각 후보이면 별도 candidate entry로 집계했다.

| 분류 | 개수 | 비율 | 설명 |
| --- | ---: | ---: | --- |
| `SECTOR_DEFAULT_FALLBACK` | 18 | 69.2% | `_detect_sector_name` 키워드 미매칭 후 기본값 `tech` 반환 |
| `KEYWORD_MATCH_HEURISTIC` | 8 | 30.8% | meta.name 키워드가 매칭됨. 단, 이것도 실제 업종 API 조회는 아님 |
| `SECTOR_REAL` | 0 | 0.0% | 코드상 권위 있는 실제 섹터 조회 경로 없음 |
| `UNKNOWN` | 0 | 0.0% | meta.name 조회 실패 없음 |

fallback 후보 18개:

```text
BMA, BMI, BCS, CMC, ALGT(stage3), ANET, ACMR, CAPR, AAP, FIX, CDE, CIEN, CEF, ARKW, ALGT(stage2), CBRL, BOIL, CE
```

키워드 매칭 후보 8개:

```text
BTBT, ADPT, BKSY, ADMA, BB, BWXT, CRS, AEIS
```

주의: 키워드 매칭도 완전한 실제 섹터가 아니다. 예를 들어 `technology` 문자열이 이름에 있으면 tech가 되고, `energy` 문자열이 이름에 있으면 energy가 되는 수준이다. 따라서 “실제 섹터 조회” 관점에서는 26개 모두 권위 있는 섹터 매핑이 아니다.

## 6. 세 가지 요인 종합

### 1) 턱걸이 통과

| ticker | margin | 판단 |
| --- | ---: | --- |
| CE | `+0.024581` | 매우 심각. 사실상 threshold 경계선 오차 수준 |
| BOIL | `+0.555425` | CE보다는 여유 있지만, 단순 BB+RSI 구조로 통과 |

CE는 명백한 턱걸이다. BOIL은 ratio 1.25라 CE보다 여유가 있지만, 다중 확인이 아니라 BB+RSI 두 항목에 의존한다.

### 2) 진입지표만 반영

| ticker | 작동한 진입지표 | 작동하지 않은 확인지표 | 문제 |
| --- | --- | --- | --- |
| CE | RSI, BB, 작은 events | MA, MACD, volume, news, topic news | 이벤트 0.106점이 없으면 fail인 약한 통과 |
| BOIL | BB, RSI | MA, MACD, volume, events, news, topic news | volume=0인데 HIGH_VOL 종목/레버리지성 상품이 BB+RSI만으로 통과 |

CE/BOIL 둘 다 “거래하면 안 되는 후보”를 막을 만한 live 실전 손실/exit 품질/volume confirmation gate가 이 점수식 안에 없다.

### 3) 섹터 fallback 왜곡

| ticker | fallback 여부 | 점수 왜곡 방향 | 현재 통과에 기여했나? |
| --- | --- | --- | --- |
| CE | fallback tech | 최종 기여 0. `use_market_entry_adjustment=False` | 아니오 |
| BOIL | fallback tech | 최종 기여 `-1.041373`. 점수를 깎음 | 아니오. 오히려 억제 |

섹터 fallback은 데이터 품질 문제로 확정된다. 특히 CE와 BOIL이 실제 업종 맥락이 아닌 tech/XLK 100을 참조하는 것은 잘못이다. 하지만 이번 두 후보의 threshold 통과에 한정하면, 섹터 fallback이 “점수를 올려서” 통과시킨 것은 아니다. CE는 보정 비활성화라 영향 0이고, BOIL은 음수 섹터 가중치 때문에 점수가 낮아졌다.

## 7. 기준선의 어디가 잘못됐는가

1. **threshold가 raw 진입 신호의 단순 합에 너무 민감하다.** CE는 margin이 `+0.024581`에 불과하다. 이런 후보는 ratio/margin gate가 없으면 계속 살아남는다.
2. **BB+RSI 중심 단순 신호가 충분한 확인 없이 통과한다.** BOIL은 volume, MACD, MA, event/news가 모두 0인데도 BB+RSI만으로 통과했다.
3. **섹터 매핑은 품질 문제가 있다.** CE/BOIL 모두 `SECTOR_DEFAULT_FALLBACK`이고, 26개 중 18개(69.2%)가 fallback이다. 다만 이번 두 종목의 현재 통과 원인은 섹터 가산이 아니라 진입지표/threshold 구조다.
4. **실전/exit 품질이 점수식에 없다.** 이전 readout의 결론처럼 실전 손실 되먹임은 `ABSENT`이고, rule_hash별 실제 exit 분포도 현재 should_buy 점수에는 직접 들어오지 않는다.

## 최종 판정

- CE 통과 원인: **턱걸이 threshold + RSI/BB + 작은 events**. 섹터 영향은 0.
- BOIL 통과 원인: **BB 2.0 + RSI 0.516 + 시장/VIX 양수 보정**. 섹터 fallback은 오히려 감점.
- CE·BOIL의 sector_name은 둘 다 **SECTOR_DEFAULT_FALLBACK**.
- sector_score=100은 실제 CE/BOIL 업종 강도가 아니라 fallback `tech`가 XLK 기반 `sector_strength.tech=100`을 참조한 결과다.
- 섹터 fallback은 반드시 고쳐야 하는 데이터 품질 문제지만, 이번 CE·BOIL의 기준선 통과를 직접 만든 주범은 **섹터 가산이 아니라 threshold/margin 부족과 단순 진입지표 통과 허용**이다.
