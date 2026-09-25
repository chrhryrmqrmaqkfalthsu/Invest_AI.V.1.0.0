# EQ(entry quality) 판정 로직 정체 확인

## 결론

판정: **EQ_INDEPENDENT**

`live_candidate_slots.py`에 표시되는 EQ(`HEALTHY/WEAK/STRONG/FAILED`, `allow/block`)는 백테스트 진입 신호인 `evaluate_signal()`과 동일 조건이 아니다. 흐름상 `evaluate_candidate()` 안에 같이 들어 있지만, 실제 순서는 다음과 같다.

```text
build_elite_shadow_report 후보
→ engine.live.elite_shadow_trader.evaluate_candidate(candidate, ctx)
  → engine.strategies.evaluator.evaluate_signal(...)
     - final_score / threshold / should_buy 계산
  → engine.live.elite_shadow_entry_quality.assess_shadow_entry_quality(...)
     - 별도 follow-through / high-risk / event-heavy 품질 판정
→ live_candidate_slots.py는 should_buy=True인 후보만 pool에 넣고,
  entry_quality는 표시 필드로만 복사
```

따라서 현재 `/dashboard-real`의 EQ는 **백테스트 진입 신호와 같은 로직이 아니라, 백테스트 진입 이후에 붙는 별도 실시간 품질 필터**다. 지금 상태에서는 슬롯 게이트로 쓰면 검증된 OOS 진입 분포를 바꾸는 추가 필터가 된다. 기본 방침은 **표시만 하고, 슬롯 진열 게이트에는 넣지 않는 쪽**이 정합적이다.

## 소스 추적

| 단계 | 파일 | 근거 위치 | 의미 |
|---:|---|---|---|
| 1 | `data/_system/ops/live_candidate_slots.py` | line 29, 269, 290-293, 393-406 | `evaluate_candidate()`를 호출하고 `ev.should_buy`가 false면 제외한다. EQ는 `ev.entry_quality`에서 복사해 표시한다. |
| 2 | `engine/live/elite_shadow_trader.py` | line 32, 423-448, 455-462 | `evaluate_candidate()`가 먼저 `evaluate_signal()`을 호출하고, 그 다음 `assess_shadow_entry_quality()`를 호출한다. |
| 3 | `engine/live/elite_shadow_entry_quality.py` | line 16, 35-42, 69-113, 116-185, 188-303 | EQ 본체. `FILTER_VERSION = shadow_entry_quality_v1`. |
| 4 | `engine/strategies/evaluator.py` | line 30-237, 특히 223-225 | 백테스트형 진입 신호: `final_score = raw_score * market_adjustment`, `should_buy = final_score >= rb.signal_threshold`. |
| 5 | `engine/live/elite_shadow_trader.py` | line 808-817 | elite shadow 가상 ledger에서는 EQ block이면 가상 진입을 막는다. |
| 6 | `engine/live/elite_strategy_sim.py` | line 1-9, 243-253, 473-480 | elite strategy sim도 EQ를 별도 entry_quality filter로 사용한다. |

## EQ 판정 로직 요약

EQ 함수 이름은 다음이다.

```text
engine.live.elite_shadow_entry_quality.assess_shadow_entry_quality()
```

버전:

```text
shadow_entry_quality_v1
```

입력은 다음이다.

```text
candidate
일봉 OHLCV + calc_indicators 결과 df
현재가 price
백테스트 진입 신호의 score / threshold / ratio
백테스트 진입 신호의 reasons
백테스트 진입 신호의 components
```

EQ가 보는 주요 지표는 다음이다.

```text
ret_1d_pct
ret_3d_pct
ret_5d_pct
ret_10d_pct
dist_ma3_pct
dist_ma5_pct
dist_ma20_pct
bounce_low5_pct
dist_high5_pct
above_ma3 / above_ma5 / above_ma20
above_prev_high
higher_close
higher_low
up_day
close_position
volume_ratio20
ATR pct
이벤트반응 reason 점수
BB근접/RSI reason 존재 여부
가격 < 5 여부
고변동 여부
과열 여부
follow-through 없음 여부
```

라벨 기준:

```text
q_score >= 75  → STRONG_FOLLOW_THROUGH
q_score >= 60  → HEALTHY_FOLLOW_THROUGH
q_score >= 45  → WEAK_FOLLOW_THROUGH
그 외          → FAILED_FOLLOW_THROUGH
```

follow-through 점수 가산/감산:

```text
5일 저점 대비 bounce >= 15%  +22
5일 저점 대비 bounce >= 8%   +16
5일 저점 대비 bounce >= 4%   +10
1일 수익률 > 3%              +14
1일 수익률 > 0%              +8
3일 수익률 > 5%              +10
3일 수익률 > 0%              +5
MA5 위                       +14
MA20 위                      +8
higher close                 +10
higher low                   +10
전일 고점 돌파               +10
당일 범위 상단 위치 >= 0.70  +8
volume_ratio20 >= 1.5 and ret1 > 0  +10
volume_ratio20 >= 1.1 and ret1 > 0  +6
ret1 < -2%                   -12
MA5 아래 3% 초과             -10
5일 고점 회복 실패           -8
```

block 조건:

```text
q_score < 45
no_follow and q_score < 60
event_heavy and q_score < 60
event_heavy and below MA5 and q_score < 75
bottom_fishing and q_score < 60
overheat and ret_1d_pct <= 0
(low_price or high_vol) and q_score < 60
```

allow이지만 size만 줄이는 조건:

```text
low_price or high_vol → size cap 0.7 또는 0.5
event_heavy and q_score < 75 → size cap 0.6
overheat → size cap 0.5
bucket == A_core and q_score < 60 → size cap 0.5
```

## 백테스트 진입 신호와 EQ 비교

백테스트 진입 신호는 `engine/strategies/evaluator.py:evaluate_signal()` 기준이다. 이 함수는 룰북의 다음 조건/가중치를 합산한다.

```text
MA 정배열 / 인버스 역배열
MACD cross
RSI zone
BB lower/upper proximity
Volume surge
news sentiment
topic news
event flags
crash_buy bonus
market_score / sector_score / VIX 기반 market_adjustment
```

최종 판정은 단순하다.

```text
should_buy = final_score >= rb.signal_threshold
```

반면 EQ는 `should_buy` 이후에 별도로 계산되는 follow-through 품질 점수다. 일부 입력은 `evaluate_signal` 결과의 `reasons/components`를 참고하지만, 핵심 판정 기준은 `ret_1d`, `ret_3d`, `bounce_low5`, `MA5/MA20 회복`, `higher close/low`, `volume_ratio20`, `high_vol`, `low_price`, `event_heavy` 같은 별도 실시간 상태 기반 휴리스틱이다.

따라서 EQ는 `EQ_SAME`이 아니며, 백테스트 진입 조건의 단순 subset/superset도 아니다. 실제 사용상 `should_buy` 뒤에 EQ allow만 진입시키면 결과적으로 subset 필터처럼 작동하지만, 그 subset 기준 자체는 frozen OOS 진입 신호에 포함되어 검증된 조건이 아니다. 분류는 **EQ_INDEPENDENT**가 맞다.

## 현재 8개 슬롯 대조표

현재 슬롯은 `live_candidate_slots.py` 구조상 모두 `evaluate_signal` 기준 `should_buy=True`로 pool에 들어온 후보다. 즉 8개 모두 백테스트 진입 신호 기준 통과 상태다.

| slot | ticker | final_score | threshold | signal pass | EQ allow | EQ label | EQ score | EQ primary reason | case |
|---:|---|---:|---:|---|---|---|---:|---|---|
| 1 | BMI | 15.944364 | 2.429303 | PASS | True | HEALTHY_FOLLOW_THROUGH | 64.0 | event_heavy_size_cap | BACKTEST_PASS_EQ_ALLOW |
| 2 | BMA | 13.470340 | 2.848392 | PASS | False | WEAK_FOLLOW_THROUGH | 46.0 | event_heavy_without_follow_through | BACKTEST_PASS_EQ_BLOCK |
| 3 | BTBT | 11.424443 | 1.911258 | PASS | False | FAILED_FOLLOW_THROUGH | 0.0 | failed_follow_through_q_lt_45 | BACKTEST_PASS_EQ_BLOCK |
| 4 | ADMA | 8.133905 | 2.179291 | PASS | True | STRONG_FOLLOW_THROUGH | 100.0 | passed | BACKTEST_PASS_EQ_ALLOW |
| 5 | CE | 7.195458 | 2.654187 | PASS | True | STRONG_FOLLOW_THROUGH | 76.0 | passed | BACKTEST_PASS_EQ_ALLOW |
| 6 | ALGT | 6.706770 | 2.333673 | PASS | False | FAILED_FOLLOW_THROUGH | 0.0 | failed_follow_through_q_lt_45 | BACKTEST_PASS_EQ_BLOCK |
| 7 | CAMT | 6.488897 | 2.247025 | PASS | False | FAILED_FOLLOW_THROUGH | 0.0 | failed_follow_through_q_lt_45 | BACKTEST_PASS_EQ_BLOCK |
| 8 | ALGT | 5.597293 | 2.293728 | PASS | False | FAILED_FOLLOW_THROUGH | 0.0 | failed_follow_through_q_lt_45 | BACKTEST_PASS_EQ_BLOCK |

요약:

```text
현재 8개 슬롯:
- 백테스트 진입 신호 PASS: 8/8
- EQ allow: 3/8
- EQ block: 5/8
- BACKTEST_PASS_EQ_BLOCK: 5개
- EQ_ALLOW_BUT_BACKTEST_FAIL: 0개, 현재 슬롯 구조상 발생 불가
```

후보 pool 전체 기준:

```text
candidate_pool: 28개
- EQ allow: 8개
- EQ block: 20개

waitlist: 20개
- EQ allow: 5개
- EQ block: 15개
```

## 운영 판단

현재 EQ block 5개는 “백테스트 진입 조건을 통과하지 못해서 막힌 것”이 아니다. 모두 `final_score >= threshold`로 진입 신호는 통과했지만, **별도 follow-through 품질 필터가 block한 케이스**다.

따라서 현재 결론은 다음이다.

```text
EQ는 표시용으로 유지.
EQ allow만 진열하는 슬롯 게이트로 즉시 승격하지 않음.
슬롯 게이트에 넣으려면 EQ allow/block에 대한 별도 IS/OOS 검증 또는 포트폴리오 검증이 필요.
```

## 관련 산출물

```text
data/_system/analysis/eq_logic_trace_20260708/readout.md
data/_system/analysis/eq_logic_trace_20260708/slot_contrast.csv
data/_system/analysis/eq_logic_trace_20260708/source_trace.json
```
