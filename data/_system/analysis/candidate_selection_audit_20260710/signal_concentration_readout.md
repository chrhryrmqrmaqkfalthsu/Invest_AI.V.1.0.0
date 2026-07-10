# 신호 점수 집중도(몰빵) 기반 진입 차단 로직 설계 검증

범위: 라이브 코드·설정·룰북·원본 룰풀·주문을 변경하지 않았다. 재학습도 하지 않았다. frozen 5년 일봉과 기존 frozen 거래 결과를 이용한 연구용 후처리/재생 분석만 수행했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/signal_concentration_performance_mapping.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/signal_concentration_boundary_scan.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/signal_concentration_readout.md`
- 재현 스크립트: `data/_system/analysis/candidate_selection_audit_20260710/run_signal_concentration_analysis.py`

## 1. 최종 판정

```text
REJECT_NO_SIGNIFICANT_IS_BOUNDARY
```

데이터는 “한두 개 지표에 점수가 몰린 신호가 더 나쁘다”는 가설을 지지하지 않았다. 오히려 IS와 OOS 모두 유효 지표 0~1개 신호의 평균 PnL이 유효 지표 2~3개 신호보다 높았다.

따라서 현재 데이터로는 신호 집중도 기반 진입 차단 gate를 후보 생성 경로에 붙이면 안 된다.

## 2. 분석 데이터

```text
원천 거래: data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv
원천 OHLC: data/_system/analysis/ohlc_snapshot_20260707
전체 신호/거래행: 43,972
IS: 31,057
OOS: 12,915
개체: 93 rule_hash
종목: 91
신호 기간: 2021-03-24 ~ 2026-07-01
```

기존 frozen 거래의 `net_pct`, 승패, MAE/MFE, exit_reason을 그대로 성과로 사용했다. 원본 룰북은 읽기만 했고 수정하지 않았다.

## 3. final_score 구성요소 분해

대상 core technical component:

```text
ma_align
macd
rsi
bb
volume
```

각 신호일의 frozen 일봉에 현재 evaluator와 동일한 기술 조건을 적용해 각 component의 raw contribution을 재생했다.

final_score 기여율:

```text
component_final_share
= max(core_component × frozen 로그의 market_adjustment, 0)
  / frozen 로그의 final_score
```

market adjustment는 모든 raw component에 공통으로 곱해지므로 기술지표 간 상대 집중도는 유지된다.

검증:

```text
무작위 component parity sample: 465건
직접 분해값 vs evaluate_signal().components 최대 절대 오차: 0.0
```

즉 5개 기술 component 분해는 evaluator 코드와 일치했다.

뉴스·토픽뉴스·이벤트·폭락매수 보너스는 core technical count에서 제외했다. 이 때문에 유효 지표 0개 신호도 존재할 수 있다. 이는 기술지표 없이 뉴스/이벤트/비기술 점수로 threshold를 넘은 신호를 의미한다.

## 4. “무시 가능 기여” cutoff 결정

처음 탐색한 log-Otsu 분리는 cutoff를 final_score의 `24.6011%`로 잡았다. 그러나 이 값은 “무시 가능”이라고 부르기에는 너무 크고, 5개 지표가 균등하게 20%씩 기여하는 신호도 유효 지표 0개로 분류할 수 있다. 따라서 최종 정의에서 폐기했다.

최종 cutoff는 IS positive component share 분포의 Tukey lower fence로 정했다.

```text
IS positive contribution n: 59,667
Q1: 25.5754%
Q3: 52.4268%
IQR: 26.8514%p
lower fence = Q1 - 1.5×IQR = -14.7017%
```

lower fence가 0 이하이므로 데이터상 별도 “미세 양수 기여” outlier 군이 존재하지 않았다. 최종 cutoff는 다음과 같다.

```text
negligible contribution cutoff = 0%
```

최종 유효 지표 개수 정의:

```text
component_final_share > 0인 core technical component 수
```

즉 실제로 0점인 지표만 제외한다. 양수 기여를 임의로 무시하지 않았다.

## 5. 유효 지표 개수별 성과

### IS

| 유효 지표 수 | n | 평균 PnL | 승률 | 평균 MAE | 평균 MFE |
|---:|---:|---:|---:|---:|---:|
| 0 | 549 | +1.637% | 54.64% | -7.164% | +11.239% |
| 1 | 7,369 | +1.750% | 57.42% | -6.753% | +9.708% |
| 2 | 17,581 | +0.942% | 54.08% | -6.426% | +8.448% |
| 3 | 5,108 | +0.408% | 50.22% | -5.976% | +7.040% |
| 4 | 438 | +0.535% | 50.46% | -5.661% | +6.629% |
| 5 | 12 | -0.083% | 33.33% | -5.622% | +5.109% |

### OOS

| 유효 지표 수 | n | 평균 PnL | 승률 | 평균 MAE | 평균 MFE |
|---:|---:|---:|---:|---:|---:|
| 0 | 314 | +3.706% | 64.01% | -6.200% | +12.008% |
| 1 | 4,010 | +3.765% | 62.17% | -6.486% | +11.443% |
| 2 | 6,560 | +2.867% | 61.14% | -6.408% | +10.323% |
| 3 | 1,816 | +2.362% | 59.58% | -6.012% | +9.166% |
| 4 | 210 | +3.355% | 63.81% | -5.754% | +9.529% |
| 5 | 5 | -6.802% | 20.00% | -11.554% | +0.916% |

유효 지표 5개 그룹은 표본이 IS 12건, OOS 5건뿐이므로 일반화할 수 없다.

핵심 관찰:

```text
IS와 OOS에서 모두 1개 기여 신호의 평균 PnL이 2개·3개 신호보다 높다.
지표 수가 많아질수록 성과가 좋아지는 단조 관계가 없다.
오히려 1 → 2 → 3개 구간은 평균 PnL과 승률이 하락한다.
```

## 6. 데이터 기반 차단 경계 탐색

검사 경계:

```text
유효 지표 수 <= k 차단, k = 0,1,2,3,4
```

IS 판정 조건:

- 차단/유지 양쪽 각각 최소 500행.
- 차단/유지 양쪽 각각 최소 15개 rule_hash.
- candidate_id cluster bootstrap 2,000회.
- 차단군 minus 유지군의 평균 PnL과 승률 95% CI 상단이 모두 0 미만이어야 “성과 붕괴” 인정.
- 가장 작은 k를 선택해 과도한 차단을 방지.

### k=1 결과

IS:

| 그룹 | n | 평균 PnL | 승률 |
|---|---:|---:|---:|
| 차단 예정: count<=1 | 7,918 | +1.742% | 57.22% |
| 유지: count>=2 | 23,139 | +0.816% | 53.15% |

```text
PnL 차이 차단군-유지군: +0.926%p
95% cluster bootstrap CI: [+0.354, +1.540]
승률 차이: +4.076%p
95% CI: [+0.736, +7.513]
```

가설과 정반대다. 0~1개 몰빵 신호를 막으면 성과가 더 좋은 그룹을 제거한다.

OOS에서도 동일했다.

| 그룹 | n | 평균 PnL | 승률 |
|---|---:|---:|---:|
| count<=1 | 4,324 | +3.761% | 62.30% |
| count>=2 | 8,591 | +2.767% | 60.85% |

### k=2 결과

IS:

```text
count<=2: avg PnL +1.191%, win 55.06%
count>=3: avg PnL +0.417%, win 50.20%
PnL 차이 95% CI: [+0.313, +1.176]
승률 차이 95% CI: [+1.469, +7.946]
```

OOS:

```text
count<=2: avg PnL +3.222%, win 61.60%
count>=3: avg PnL +2.443%, win 59.92%
```

이 역시 집중 신호 차단 논리를 지지하지 않는다.

### k=0, 3, 4

- k=0: 0개 그룹이 나쁘다는 유의한 증거 없음.
- k=3: 유지군 IS 450행, OOS 215행으로 최소 500행 미달.
- k=4: 유지군 IS 12행, OOS 5행으로 판정 불가.

최종적으로 IS에서 선택 가능한 붕괴 경계가 하나도 없었다.

```text
selected_boundary = null
verdict = REJECT_NO_SIGNIFICANT_IS_BOUNDARY
```

IS 경계가 없으므로 OOS gate 승격 검증 단계로 넘어가지 않았다. 다만 관측 방향도 OOS에서 동일하게 concentration 차단에 반대였다.

## 7. 왜 이런 결과가 나올 수 있는가

이번 결과는 “몰빵이 항상 안전하다”는 뜻이 아니다. 현재 evaluator 구조에서 유효 지표 수가 단순 품질 척도가 아니기 때문이다.

가능한 구조적 이유:

1. RSI·BB처럼 특정 개체에 강한 단일 신호가 충분한 edge를 가질 수 있다.
2. 여러 기술 조건이 동시에 켜지는 시점은 이미 추세가 진행됐거나 late entry일 수 있다.
3. 유효 지표 0개에는 뉴스·이벤트·시장 관련 점수로 통과한 신호가 포함된다.
4. 지표 수는 각 지표의 개체별 적합성, 가중치 크기, 상관관계를 구분하지 못한다.
5. MA·RSI·BB는 서로 독립적인 증거가 아니라 같은 가격 경로에서 파생된 상관 지표일 수 있다.

따라서 “많이 켜졌으니 더 안전하다”는 규칙을 일반 gate로 만들 수 없다.

## 8. 기존 `elite_entry_concentration.py`와의 구분

`engine/live/elite_entry_concentration.py`는 이번 분석의 지표 집중도와 다른 로직이다.

해당 모듈은 다음을 합산한다.

```text
OOS expectancy/win/fitness
entry_quality score
ret_5d/dist_ma5/dist_high5/volume/ATR
risk cap
```

즉 evaluator의 BB/RSI/MACD/volume/MA component 집중도를 세는 함수가 아니다. 이번 결과를 그 모듈의 검증으로 해석하면 안 된다.

## 9. 부착 지점 설계 — 검증 통과 시에만

이번 판정은 REJECT이므로 실제 부착은 권장하지 않는다. 향후 다른 concentration metric이 별도 OOS에서 통과할 경우의 위치만 제안한다.

### 순수 계산 위치

```text
engine/live/elite_shadow_trader.py::evaluate_candidate()
현재 lines 435-462 부근
```

`evaluate_signal()` 이후 이미 다음이 확보된다.

```text
score
market_adjustment
components
```

여기서 shared pure function으로 concentration metric만 계산해 evaluation 결과에 추가한다. `evaluate_signal()` 자체의 학습/백테스트 정의는 변경하지 않는 편이 안전하다.

예정 인터페이스:

```text
assess_signal_concentration(
    components,
    final_score,
    market_adjustment,
    validated_cutoff,
    validated_count_boundary,
)
```

### 후보 차단 위치

```text
data/_system/ops/live_candidate_slots.py::refresh_slots()
현재 lines 414-418
```

권장 순서:

```text
should_buy 확인
validated concentration gate 확인
pool.append 직전 차단
```

### export 재검증

```text
scripts/export_real_dashboard_buy_candidates.py
현재 lines 483-492
```

refresh와 export가 동일 shared function을 사용해야 후보 파일에서 우회가 생기지 않는다.

### 설계 원칙

- candidate/rulebook 원본 수정 금지.
- final_score 자체에 감점하지 말고 별도 gate 결과로 기록.
- `concentration_allow`, `effective_indicator_count`, `top1_share`, `top2_share`, `policy_version`을 진단 필드로 남김.
- IS에서 정한 cutoff/boundary를 OOS·live에서 재탐색하지 않음.
- 모든 후보 경로가 동일 함수/버전을 사용.

그러나 현재 데이터에서는 validated cutoff/boundary가 없으므로 이 설계를 구현하면 안 된다.

## 10. 최종 결론

```text
유효 지표 수 기반 몰빵 차단 경계: 발견되지 않음
IS 통계 유의 붕괴: 없음
OOS 방향: 몰빵 차단 가설과 반대
최종 결정: REJECT
라이브 부착: 하지 않음
```

현재 데이터가 지지하는 결론은 다음과 같다.

```text
“한두 개 지표에만 의존했다”는 이유만으로 신호를 막으면,
평균 PnL과 승률이 더 높은 신호군을 제거할 가능성이 크다.
```
