# 진입품질 게이트(q<45)의 후보선정 이관 타당성 조사

- 대상 게이트: `engine/live/elite_shadow_entry_quality.py::assess_shadow_entry_quality()`
- 현재 적용 위치: elite shadow 가상 OPEN 직전
- 조사 기준 현재 pool: `data/_system/live_slots_state.json`의 10개
- 구현 변경: **0**
- 최종 판정: **추가검증 필요 — 현 상태에서 후보선정 이관 비권장**

## 1. q 점수와 임계 45

q는 0~100으로 clamp되는 가산·감점형 휴리스틱이다.

주요 5일 요소:

- 5일 저점 대비 반등: +10/+16/+22
- MA5 위: +14
- MA5 대비 -3% 아래: -10
- 5일 고점 대비 -12% 아래: -8

그 외 1일·3일 수익률, MA20, higher close/low, 전일 고점 돌파, 당일 range 상단, 거래량을 합산한다. 전체 식은 `q_formula_and_threshold.csv`에 기록했다.

`q<45`는 2026-07-01 commit `0782c35`에서 literal 상수로 도입됐다. 45를 선택한 calibration, threshold sweep, 독립 ablation은 **NOT_FOUND**다.

유일한 composite OOS 기록은 `eq_validity_20260708`이며 정확 replay가 아닌 근사다. 결과는 이관을 지지하지 않았다.

- CAGR: 72.2678% → 59.0408%
- MDD: -21.9627% → -25.6421%
- Sharpe: 1.84165 → 1.77831
- ALLOW-BLOCK 평균수익 차이: -0.05535%p
- permutation p=0.82084

현재 라이브 후보 state의 `EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE`는 이 결과를 반영한 의도적 비채택이다.

## 2. 최근 shadow OPEN에서 실제 차단 정도

지속 state에 남은 표본만 집계했다.

- `elite_shadow_state.json`: open score 16개, q<45 skip sample 20개
  - snapshot 비율 20/(16+20)=55.56%
- `elite_strategy_sim_state.json`: open score 28개, skip sample 5개
  - snapshot 비율 15.15%

두 state를 단순 합치면 25/(44+25)=36.23%지만 **실제 운영 차단율로 확정할 수 없다.** 이유:

- open_positions는 생존 snapshot이다.
- skip sample은 상한·누락 가능성이 있다.
- simulator 전략 간 중복이 있다.
- 완전한 attempt event log가 아니다.

따라서 최근 분포는 `CONFIRMED_PERSISTED_SAMPLE`, 실제 전체 차단율은 `NOT_STORED`다.

## 3. 현재 후보 풀 10개 소급 적용

현재 저장가격과 최신 OHLCV로 동일 함수를 replay했다. 이는 historical frozen 값이 아니라 **INFERRED_CURRENT_REPLAY**다.

| 순위 | 티커 | q | q<45 | 결과 |
|---:|---|---:|---|---|
| 1 | ADMA | 32 | 예 | 차단 |
| 2 | CRS | 0 | 예 | 차단 |
| 3 | ALGT | 53 | 아니오 | 통과 |
| 4 | AEIS | 58 | 아니오 | 통과 |
| 5 | ARKW | 37 | 예 | 차단 |
| 6 | CBRL | 75 | 아니오 | 통과 |
| 7 | BTU | 39 | 예 | 차단 |
| 8 | BB | 6 | 예 | 차단 |
| 9 | BN | 65 | 아니오 | 통과 |
| 10 | ACMR | 72 | 아니오 | 통과·size 0.5 |

결과:

- 현재 pool 10 → 5
- 감소율 50%
- q<45 추가 탈락: ADMA, CRS, ARKW, BTU, BB

CRS:

- 최초 신호 replay: q=31
- 현재 replay: q=0
- 둘 다 q<45 차단

최초 q=31은 복원 가격과 재구성 일봉 기반이므로 `PARTIALLY_RECOVERED`다.

## 4. Event OFF·v3·BOIL과의 교차

2026-07-11 교차표에 존재하는 현재 후보 9개 중 Event OFF SURVIVE + v3 PASS + BOIL PASS인데 q가 추가 차단하는 후보:

- ADMA q=32
- CRS q=0 현재 / q=31 최초
- ARKW q=37
- BB q=6

BTU는 해당 2026-07-11 표본에 없어 교차 판정은 `NOT_IN_SAMPLE`이다.

즉 q 게이트는 Event OFF·v3·BOIL과 중복되지 않는 별도 축이지만, 현재 pool 절반을 제거할 만큼 강하다.

## 5. 이중 적용

후보선정에 q를 추가해도 shadow OPEN의 동일 함수가 그대로 남으면 같은 논리를 두 번 평가한다.

- 입력이 같으면 완전 중복
- 선택과 OPEN 사이 가격/OHLCV가 바뀌면 결과 충돌 가능
- 후보선정에서 차단된 후보는 이후 q 회복 기회를 잃음
- 후보선정 통과 후 shadow에서 다시 차단될 수 있음

따라서 향후 검증 후 승격하더라도 한 authoritative hard gate만 두는 편이 일관적이다. 후보선정 hard gate로 승격한다면 shadow 쪽은 audit/size 조절용으로 강등하는 구조가 합리적이다. 이는 `INFERRED_RECOMMENDATION`이다.

## 6. 좋은 개체 동반 탈락 위험

근사 OOS `eq_trade_labels_approx.csv`에서 q<45:

- 25,547행
- 양수 수익 14,360행, 56.21%
- +5% 초과 8,551행
- 평균 net +1.6765%
- median net +1.5272%

상위 사례:

- AMSC q=0, +116.51%
- CAR q=6, +90.69%
- CAN q=0, +90.52%
- BMA q=24, +88.78%

이는 q<45가 부진 개체만 분리하지 못하고 큰 winner도 대량 제거할 수 있음을 보여준다. 다만 이 데이터는 `APPROX_OHLC_SIGNALDATE_CLOSE_NO_EVENT_NEWS_REASONS` 방식이므로 정확 실전 counterfactual은 아니다.

## 7. 판정

### 판정: 추가검증 필요

현 상태에서 후보선정 이관을 권장하지 않는다.

근거:

1. 임계 45의 calibration 근거가 없다.
2. exact historical replay가 불가능했다.
3. 유일한 근사 OOS는 CAGR·MDD·Sharpe를 모두 악화시켰다.
4. q<45군에도 수익 거래와 큰 winner가 다수 존재한다.
5. 현재 pool에서는 50%를 제거해 과도한 축소 위험이 있다.
6. 기존 shadow OPEN 게이트와 이중 적용 문제가 생긴다.

이관 타당성을 다시 판단하려면 최소한 다음이 필요하다.

- exact timestamp price/reasons/components를 저장한 prospective shadow log
- q threshold 25~60 sweep
- hard block vs rank penalty vs size reduction 비교
- Event OFF·v3·BOIL 이후 incremental lift 검증
- blocked 후보의 동일 exit-rule counterfactual 성과

세부 산출물:

- `q_formula_and_threshold.csv`
- `current_pool_q_replay.csv`
- `recent_shadow_q_distribution.csv`
- `risk_and_double_application.csv`
