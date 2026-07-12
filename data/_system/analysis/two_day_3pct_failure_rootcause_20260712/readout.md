# 2일 내 +3% 예측 접근 실패 원인 통합 판정

## 최종 판정

# **SHARED_LIMIT**

통합 방식과 후처리 방식의 차이가 성패를 가른다는 증거는 없다. 두 방식 모두 같은 12개 D-5~D-1 가격 path 정보에서 약한 tail signal을 찾지만, entity별·regime별로 안정적으로 재현되는 정보가 부족하다.

현재 문제는 단순한 GA 탐색량이나 gate 설정이 아니라 다음 조합이다.

```text
5일 가격 path만 사용
+ 종목 변동성을 정규화하지 않은 고정 2일 +3% label
+ entity별 분할 학습
+ hard interval AND filter
+ stress/OOS 분포 이동
```

일부 개선 여지는 있지만, 현 문제 정의를 유지한 채 통합↔후처리만 전환하거나 gate만 완화해서 해결할 수준은 아니다.

## 핵심 비교

| 방식 | Survivor | OOS precision | Survivor 범위 OOS coverage | 전체 OOS 잔존율 | OOS HHI |
|---|---:|---:|---:|---:|---:|
| 로그 후처리 | 2/57 | 77.78% | 31.03% | 1.03% | 0.5062 |
| Replay 후처리 | 1/57 | 88.24% | 16.04% | 0.57% | 1.0000 |
| 통합 rolling | 2/50 종목 | 60.00% | 4.96% | 0.20% | 0.5008 |

후처리는 통합보다 정밀도가 높고 coverage 손실이 덜했지만, 로그 survivor AMSC·AVAV가 replay에서는 모두 탈락하고 AEVA로 완전히 교체됐다. 따라서 높은 정밀도는 universe 정의에 안정적이지 않았다.

통합 방식은 GA 원본 크기와 3개 train split을 복원해 AAP·POWI를 찾았지만, 12개 strict AND 때문에 OOS 전체 평가일의 0.20%만 남았다. 두 survivor의 실제 OOS rolling 목표일 TP OFF 거래 19건은 거래당 평균 순수익률 +0.1446%, 순차 복리 +0.8181%였다.

## 공통 원인

### 1. Feature 정보량이 약함

OOS에서 가장 강한 단일 feature도:

- 절대 Pearson 상관: 약 0.10~0.14
- label entropy 설명 비율: 약 1.1~2.4%

수준이었다.

`pullback_from_high5_pct`, `single_up_day5_pct`, `fade_after_surge_score`에는 반복되는 신호가 있다. Train 최고 분위는 OOS에서 약 +8~13%p lift를 보이기도 했다. 그러나 대부분 coverage 7~16%의 tail 신호이며, 여러 feature가 같은 변동성·반전 상태를 중복 표현한다.

### 2. Hard filter가 약한 정보를 지나치게 압축함

- 후처리: 최대 5개 feature AND
- 통합: 12개 feature 전부 strict AND

통합 방식의 survivor OOS coverage가 4.96%까지 내려간 것은 이 구조의 직접 결과다. 약한 단변량 signal을 여러 개 교집합으로 묶으면서 precision보다 signal extinction이 더 빠르게 발생했다.

### 3. Gate 엄격성은 주원인이 아님

Replay 기준:

- Precision gap을 20%p에서 40%p로 완화해도 survivor는 AEVA 1개 그대로였다.
- OOS-only로 바꾸면 5개가 되지만 추가 4개가 stress에서 실패했다.
- 최소 통과표본을 완화하면 3개가 되지만 원래 sample protection을 만족하는 robust survivor는 1개뿐이었다.

Gate를 완화하면 안정적 패턴보다 regime-specific·thin-sample 후보가 늘었다.

### 4. 분포 이동이 큼

Train→OOS 양성률 변화:

- 로그 후처리: +10.29%p
- Replay 후처리: +10.68%p
- 통합: +6.27%p

통합 150개 후보 평균 정밀도도 Train 81.75%에서 Stress 43.83%, OOS 49.86%로 내려갔다. 로그와 replay의 survivor 교집합이 0인 사실도 표본 정의 민감도를 보여준다.

### 5. 고정 +3% label이 종목별 난이도를 섞음

같은 +3%는 저변동 종목과 고변동 종목에서 전혀 다른 사건이다. 현재 12개 feature에는 장기 실현변동성, ATR 정규화, 시장 regime, catalyst, 유동성 shock가 없다.

이 때문에 GA는 실제 공통 구조보다 특정 ticker·기간의 변동성 상태를 gene 구간에 간접적으로 외우기 쉽다. 이는 **[추정]**이지만 train→validation 붕괴와 일치한다.

## 방식 선택에 대한 결론

### METHOD_ISSUE가 아닌 이유

후처리는 통합보다 덜 선택적이지만 robust survivor는 여전히 1~2개다. 통합은 더 극단적으로 신호를 줄였으나, 후처리로 바꾼다고 범용성이 생긴 증거가 없다.

### FIXABLE로 판정하지 않은 이유

GA 확대는 통합 survivor를 0개에서 2개로 늘렸지만, 전체 문제를 해결하지 못했다. Gate 완화도 robust survivor 수를 늘리지 못했다. 단순 population·generation·threshold 조정 범위를 넘어 target·feature·모델 단위를 바꿔야 한다.

## 권고 방향

### 1순위 — 문제 정의 수정

고정 `2일 +3%` 대신 종목·regime별 변동성을 반영한 target을 우선 비교해야 한다.

예:

```text
2일 내 +k × ATR
2일 forward return의 ticker별 상위 분위
시장 regime별 상대수익
2~5일 내 risk-adjusted payoff
```

고정 +3%를 반드시 유지한다면 최소한 ATR·20일 실현변동성·시장 변동성으로 feature와 threshold를 정규화해야 한다.

### 2순위 — per-entity hard filter를 pooled/hierarchical 구조로 변경

57개 rulebook 또는 50개 ticker를 완전히 독립 학습하면 유효 표본이 얇아진다. 공통 price-path 효과는 공유하고 ticker·rulebook·regime별 보정만 두는 구조가 더 적합하다는 것이 **[추정]**이다.

### 3순위 — 통과/차단보다 확률 ranking으로 사용

현재 evidence는 hard block보다 약한 ranking signal에 가깝다.

- strict 12-way AND 제거
- calibrated probability 또는 percentile score
- coverage별 lift curve 보고
- 기존 신호를 전부 차단하지 않고 상위 confidence만 가중

방식이 더 적합하다.

### 유지해야 할 항목

- Stress·OOS 이중 검증
- 최소 표본 보호
- train-only 선택
- prospective shadow 검증

이 항목을 완화하면 survivor 수는 늘지만 기존 자료상 robust evidence는 늘지 않는다.

## 즉시 방식 전환 여부

현재 증거만으로 통합 방식을 후처리 방식으로 교체하거나 후처리를 라이브 hard block으로 승격할 근거는 없다.

후처리는 연구·shadow 관점에서 통합보다 적용과 철회가 쉽고 OOS precision/coverage가 나았지만, survivor가 log→replay에서 완전히 교체됐다. 따라서 방식 전환보다 먼저 target 정규화와 pooled ranking 실험 설계를 확정하는 것이 우선이다.

## 근거 산출물

- `postprocess_attempts_summary.csv`
- `integrated_vs_postprocess.csv`
- `common_failure_diagnosis.md`
- 원천: `entry_filter_2d3pct_20260712/`
- 원천: `entry_filter_2d3pct_replay_20260712/`
- 원천: `entry_filter_survivor_analysis_20260712/`
- 원천: `stage2_3_rediscovery_pilot_20260712/`
- 원천: `pilot_survivor_detail_20260712/`
