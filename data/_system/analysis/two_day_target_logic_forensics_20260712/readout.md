# "2일내 2~3% 상승 타겟 + 개체별 기준선 + 5일 상태" 로직 발굴

- 조사 대상: 사용자가 지시했던 forward target 기반 개체 선별 로직
- 비교 대상: 현재 Stage2/Stage3 룰북, 5일 path/range/payoff 연구 실험, q<45 shadow OPEN 게이트
- CRS: `stage3:CRS:8695c9ce3320`
- 코드 변경: **0**

## 최종 판정

### 판정: **부분구현**

사용자 의도를 구성하는 세 요소 중 일부는 연구 코드에 구현돼 있다.

1. **5일 상태를 feature로 사용**: 구현됨
2. **개체별로 서로 다른 cut/weight를 학습**: 구현됨
3. **미래 상승폭/고저폭을 target으로 학습**: 구현됨

그러나 현재 저장된 구현은 정확히 “2거래일 내 +2~+3%”가 아니다.

- 주 target horizon은 `next_day`다.
- target 단위는 고정 퍼센트가 아니라 주로 `ATR 배수` 또는 HIGH/LOW coarse bin이다.
- 이 predictor는 연구 실험군에 머물며 현재 Stage2/Stage3 final rulebook이나 live candidate 선정으로 승격된 연결은 `NOT_FOUND`다.
- CRS에 대해 해당 predictor가 생성한 개체·예측값·통과 기록도 `NOT_FOUND`다.

따라서 사용자 의도는 **연구 단계에서 유사 형태로 부분구현됐지만, 정확한 2일 2~3% target과 live 승격은 누락 또는 다른 형태로 변경된 상태**다.

## 1. “2일 내 2~3% 상승” target 검색 결과

현재 공식 Stage2/Stage3 룰북 학습은 forward 2-day binary label을 직접 최적화하지 않는다.

공식 룰북은 다음을 GA 유전자로 학습한다.

- indicator/news/Event 가중치
- RSI, BB, 거래량 등의 개별 cutoff
- `signal_threshold`
- exit gene

그리고 전체 백테스트의 expectancy, win rate, drawdown, fitness 등으로 개체를 평가한다.

근거:

- `engine/strategies/rulebook.py:52-67,149-185`
- `engine/learning/genetic.py:158-175,210-285`
- CRS final artifact: `exp_batch_stage123_2009_20260616_full/tickers/CRS/stage3/final_rulebooks.jsonl:25`

저장소 및 reachable Git history에서 다음 exact target은 발견되지 않았다.

`forward 2 trading days AND return >= 2% or 3%`

판정: `NOT_FOUND`.

### 가장 가까운 구현

가장 가까운 구현은 2026-07-04~06의 range/payoff predictor 실험군이다.

#### Range predictor

`run_range_predictor_stage2_v3.py`는 다음 날 HIGH/LOW 구간을 예측한다.

- target mode: `next_day_hilo...`
- HIGH/LOW coarse bin
- lag feature를 사용
- 원래 5일 lag, 이후 일부 버전은 10일로 확장
- feature별 weight와 dense cut을 개체마다 진화

근거:

- `scripts/research/run_range_predictor_stage2_v3.py:2-12`
- `scripts/research/run_range_predictor_stage2_v3.py:31-58`
- `scripts/research/run_range_predictor_stage2_v3.py:80-101`
- `scripts/research/run_range_predictor_stage2_v3.py:218-239`

#### Payoff predictor

Payoff 계열은 다음과 같은 label을 사용한다.

- `next_high_atr`
- `next_low_atr`
- `PAYOFF_SCORE = next_high_atr - next_low_atr`
- `GOOD_SIGNAL = next_high_atr >= target AND next_low_atr <= risk limit`

근거:

- `scripts/research/run_payoff_two_gene_ga.py:19-20`
- `scripts/research/run_payoff_two_gene_ga.py:107-111`
- `scripts/research/run_payoff_tier_overlap.py:70-73`

5일 wrapper는 lag feature를 5일로 고정하고 HIGH target을 `0.5/0.7/0.8/1.0 ATR` 등으로 바꿔 실험했다.

- `scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_separate_highlow70.py`
- `scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_separate_highlow_soft_stress_validation_target.py`

즉 “미래 상승 가능성을 최근 5일 상태로 선별”이라는 방향은 맞지만, target은 **2일 +2~+3%가 아니라 다음 날 ATR 기반 고저폭**이다.

## 2. 개체별 기준선 구조

현재 시스템에는 서로 다른 의미의 “기준선”이 있다.

### A. 공식 룰북 `signal_threshold`

각 룰북마다 다른 float gene이다.

- 범위: `1.5~4.0`
- GA에서 randomize/mutate
- rulebook JSON에 저장
- live에서 `final_score >= signal_threshold`로 진입 판정
- dashboard ratio는 `score / threshold`

이는 2일 기대상승률 cutoff가 아니다. 현재 composite score의 개체별 진입 기준이다.

CRS:

- threshold: `2.5574757832651467`
- 최초 score: `2.971797614887265`
- ratio: `1.1620042052140767`
- margin: `0.41432183162211844`

### B. Range/payoff predictor의 개체별 cut

연구 predictor에는 다음이 개체마다 다르다.

- feature weight vector
- quantile rule
- dense high/low cut
- bias
- payoff gene cut

이 구조가 사용자 표현의 “개체마다 기준선을 다르게 둔다”에 가장 가깝다.

하지만 이 cut은 공식 rulebook의 `signal_threshold`와 별개이며, live 후보 선정에 연결되지 않았다.

### C. Exit target

CRS의 `take_profit_atr=2.3721276347759157`도 개체별 값이다. 그러나 이는 청산 target이지 2일 상승 label은 아니다.

신호 당시 재구성 ATR을 적용하면 참고상:

- ATR: `23.387070024602597`
- implied move: `55.47711510179928`
- 신호가 대비 약 `9.23295%`
- implied target price: `656.3371004533618`

이는 target horizon이 고정 2일이라는 뜻이 아니다.

## 3. 5일 상태 선별

공식 live candidate 경로에서 5일 상태는 제한적으로만 사용된다.

- MA5가 `MA5 > MA20 > MA60` 정배열에 포함
- Volume_MA5가 거래량 ratio 계산에 사용

공식 후보 단계에는 별도 5일 high/low/range/ret_5d hard selector가 없다.

근거:

- `data/_system/analysis/crs_5d_gate_forensics_20260712/five_day_logic_inventory.csv`

별도 5일 selector는 연구 코드에 존재한다.

- range/payoff lag feature predictor
- Stage2 path filter

하지만 live wiring은 `NOT_FOUND`다.

## 4. q<45 게이트와의 관계

q<45는 이 target predictor와 **완전 별개**다.

q 게이트:

- 현재 가격과 MA5/MA20/5일 high/low 등을 손으로 가산·감점
- 고정 threshold 45
- shadow OPEN 직전 hard block
- forward label 학습 없음
- rulebook 생성 없음
- 개체별 threshold 없음

Range/payoff predictor:

- historical supervised/GA experiment
- 미래 HIGH/LOW/payoff label 사용
- feature weights와 cut이 개체별로 다름
- 연구 artifact 생성

따라서 q<45를 사용자 의도의 구현으로 보는 것은 틀리다.

## 5. CRS 대입

### 공식 룰북 기준

CRS는 다음 이유로 통과했다.

- score `2.971797614887265`
- 개체 threshold `2.5574757832651467`
- margin `+0.41432183162211844`

점수 성분:

- MA 정배열: `+1.073432530260209`
- RSI: `+1.6475902670733407`
- BB: `+1.9410509060649137`
- Event: `-1.6902760885111983`
- 기타: 0

5일 상태:

- MA5: `600.9259887695313`
- 가격의 MA5 대비: `-0.0109836185%`
- 5일 high: `625.989990234375`
- 5일 low: `576.1599731445312`
- 5일 range 위치: `49.5685405094%`
- 5일 return: `-2.5906297919%`

이 값들은 별도 payoff predictor pass 조건으로 사용되지 않았다. 공식 점수에서는 MA 정배열과 Volume_MA5만 반영됐다.

### 기대상승률

CRS artifact에 저장된 기대값:

- `expectancy_pct=12.288218258394188`
- `avg_return_pct=12.288218258394188`

이는 backtest 전체 거래의 평균 성과이며, “앞으로 2일 내 12.29% 오른다”는 예측이 아니다.

정확한 다음 항목은 `NOT_FOUND`다.

- CRS 2-day target probability
- CRS +2% hit probability
- CRS +3% hit probability
- CRS range/payoff predictor individual artifact
- CRS 5-day predictor pass/fail row

## 사용자 의도 대비 현재 구현 상태

| 의도 | 상태 |
|---|---|
| 신호 개체를 대상으로 함 | 연구 실험에서 부분 구현 |
| 지난 5일 상태 사용 | 구현됨 |
| 미래 상승폭 target | next-day ATR/high-low 형태로 구현됨 |
| 2일 horizon | NOT_FOUND |
| +2~+3% 고정 target | NOT_FOUND |
| 개체별 기준선 | 연구 predictor cut 및 공식 signal_threshold로 각각 구현 |
| predictor를 rulebook로 승격 | NOT_FOUND |
| 현재 live 후보선정에 적용 | NOT_FOUND |
| CRS 적용 artifact | NOT_FOUND |

## 결론

사용자가 지시했던 아이디어는 흔적 없이 사라진 것은 아니다.

- 5일 lag 상태
- 미래 high/low/payoff target
- 개체별 cut/weight

이 세 부분은 range/payoff predictor 실험에 남아 있다.

그러나 구현 과정에서 target이 `2일 +2~+3%`에서 `다음 날 ATR 기반 HIGH/LOW`로 바뀌었고, 공식 Stage2/Stage3 rulebook 및 live 후보선정으로 승격되지 않았다.

따라서 최종 판정은:

**부분구현 — 연구 predictor 위치는 확정, exact target과 live/CRS 적용은 NOT_FOUND.**

세부 산출물:

- `target_definition_inventory.csv`
- `individual_threshold_structure.csv`
- `five_day_selection_and_q_relation.csv`
- `crs_application.csv`
