# 2거래일 내 +3% 예측 접근의 공통 실패 원인 진단

## 1. 분석 범위

재학습 없이 다음 기존 산출물을 표준화·재집계했다.

- 로그 기반 후처리 필터: `entry_filter_2d3pct_20260712/`
- replay 기반 후처리 필터: `entry_filter_2d3pct_replay_20260712/`
- replay gate 민감도: `entry_filter_survivor_analysis_20260712/`
- 통합 rolling 파일럿: `stage2_3_rediscovery_pilot_20260712/`
- 통합 survivor 거래: `pilot_survivor_detail_20260712/`
- 과거 exact 구현 검색: `two_day_3pct_ga_selector_discovery_20260712/`
- 관련 비정확 predictor 감사: `two_day_target_logic_forensics_20260712/`, `payoff_predictor_status_20260712/`

과거 검색 결과 exact “D-5~D-1 → 2거래일 내 +3%”의 독립 실행 artifact는 이번 로그·replay 시도 이전에는 발견되지 않았다. 다음 날 +2%, ATR/payoff, range predictor는 target이 달라 숫자 비교에서 제외했다.

## 2. 세 접근의 표준화 결과

| 방식 | 전체 label 표본 | 전체 양성률 | OOS 양성률 | Survivor | OOS 통과/전체 OOS | Survivor 범위 coverage | OOS 정밀도 | OOS HHI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 로그 후처리 | 3,430 | 43.18% | 49.20% | 2/57 | 9/870 = 1.03% | 31.03% | 77.78% | 0.5062 |
| Replay 후처리 | 18,226 | 39.06% | 48.16% | 1/57 | 17/2,992 = 0.57% | 16.04% | 88.24% | 1.0000 |
| 통합 rolling | 75,900 | 45.15% | 50.15% | 2/50 종목, 2/150 split 후보 | 25/12,600 = 0.20% | 4.96% | 60.00% | 0.5008 |

후처리 방식은 survivor 종목 안에서는 정밀도를 올렸지만 전체 universe의 0.57~1.03%만 남겼다. 통합 방식은 12개 feature 전부 strict AND를 사용해 OOS 전체 일별 평가의 0.20%만 남겼다.

통합 survivor AAP·POWI의 실제 OOS rolling 목표일 TP OFF 거래는 19건이었다.

- 거래당 평균 순수익률: +0.1446%
- 순차 복리: +0.8181%
- 승률: 63.16%
- 보유 중 +3% 도달률: 52.63%

이 복리는 두 종목 거래를 단순 순차 결합한 값이며 포트폴리오 CAGR이 아니다. 후처리 시도에는 동일 exit policy의 실제 수익 자료가 저장되지 않아 직접 수익 비교는 불가능하다.

## 3. 양성비율과 통과율의 관계

### 3.1 원래 label은 희귀 사건이 아님

전체 양성률은 39.06~45.15%, OOS 양성률은 48.16~50.15%다. 즉 “2일 내 +3%”는 이 고변동성·신호 중심 universe에서 극히 희귀한 사건이 아니다.

문제는 양성을 찾는 것 자체보다, 약 40~50% baseline에서 정밀도를 안정적으로 더 올리면서 충분한 신호 수를 유지하는 것이다.

### 3.2 정밀도 개선은 대부분 신호 대량 삭제와 교환됨

- 로그 후처리: survivor 범위 OOS baseline 58.62% → 77.78%, +19.16%p. 대신 29건 중 9건만 유지.
- Replay 후처리: 70.75% → 88.24%, +17.48%p. 대신 106건 중 17건만 유지.
- 통합 방식: 50.20% → 60.00%, +9.80%p. 대신 504일 중 25일만 유지.

따라서 필터가 완전히 무작위로 신호 수만 줄인 것은 아니다. 일부 tail 구간에서 실제 lift가 있다. 그러나 개선량에 비해 coverage 손실이 크고, survivor가 1~2개 entity에 집중된다.

## 4. 5일 feature와 2일 +3% label의 정보량

세 universe의 같은 12개 D-5~D-1 feature에 대해 모델을 새로 학습하지 않고 다음 기술통계를 계산했다.

- binary label과의 Pearson point-biserial 상관
- Spearman 상관
- train 10분위 경계를 고정한 단변량 mutual information
- train에서 가장 양성률이 높았던 분위가 stress/OOS에서 유지되는지

### 4.1 OOS 단변량 효과 크기

| Universe | OOS 최대 절대 Pearson | OOS 최대 절대 Spearman | OOS 최대 단변량 MI | Label entropy 대비 비율 |
|---|---:|---:|---:|---:|
| 로그 후처리 | 0.1338 | 0.1691 | 0.0240 bit | 2.40% |
| Replay 후처리 | 0.1407 | 0.1635 | 0.0231 bit | 2.31% |
| 통합 일별 | 0.1032 | 0.1211 | 0.0109 bit | 1.09% |

Stress에서는 최대 MI 비율이 약 1.9~5.0%, train에서는 약 1.35~3.01%였다. OOS에서는 세 universe 모두 더 낮아졌다.

상관의 p-value는 큰 표본에서 매우 작지만 효과 크기는 작다. 통계적 유의성과 실전 분류력은 다르다.

### 4.2 반복적으로 신호가 있는 feature

세 universe에서 가장 일관된 방향은 다음이었다.

- `pullback_from_high5_pct`: 5일 고점 대비 pullback이 클수록 양성률 상승
- `single_up_day5_pct`: 최근 5일 내 큰 상승일이 있을수록 양성률 상승
- `fade_after_surge_score`: 급등 후 fade가 클수록 양성률 상승

train 기준 최고 10분위를 그대로 OOS에 적용했을 때 대표 lift는 다음 수준이었다.

| Universe | 대표 feature | OOS coverage | OOS lift |
|---|---|---:|---:|
| 로그 후처리 | `fade_after_surge_score` 상위 분위 | 14.71% | +13.30%p |
| Replay 후처리 | `ret_d1_pct` 하위 분위 | 13.64% | +12.87%p |
| Replay 후처리 | `single_up_day5_pct` 상위 분위 | 15.98% | +12.51%p |
| 통합 일별 | `ret_d1_pct` 하위 분위 | 11.44% | +10.39%p |
| 통합 일별 | `pullback_from_high5_pct` 상위 분위 | 11.16% | +9.74%p |

이는 “아무 예측 정보도 없다”는 결론을 부정한다. 그러나 정보는 소수의 단변량 tail에 약하게 존재하며, label entropy의 대부분은 설명하지 못한다.

### 4.3 예측 가능성의 상한에 대한 해석

정확한 Bayes error 상한은 현재 산출물만으로 계산할 수 없다. 다만 다음은 확인된다.

1. 단일 feature가 설명하는 label entropy는 OOS에서 최대 약 1.1~2.4%다.
2. 여러 feature는 서로 강하게 중복된다. 예를 들어 pullback, fade, single-up은 같은 변동성·반전 상태를 다른 형태로 표현한다.
3. train에서는 GA가 80% 이상의 정밀도를 만들 수 있지만 stress/OOS에서 크게 하락한다. 이는 안정적인 정보보다 표본 특이 조합을 포착한 비중이 크다는 증거다.
4. 단변량 tail은 OOS에서도 약 +8~13%p lift를 유지하지만 coverage는 대체로 7~16%다.

따라서 현재 12개 feature만으로 가능한 현실적 형태는 “광범위한 고정밀 hard filter”보다 “낮은 coverage의 약한 ranking signal”에 가깝다는 것이 **[추정]**이다.

## 5. Survivor가 얇은 원인 분해

### 5.1 (a) 게이트가 지나치게 빡빡한가?

주원인이 아니다.

Replay 민감도 결과:

- Train↔validation precision gap을 20%p에서 40%p까지 완화: survivor는 계속 AEVA 1개.
- OOS-only gate: 5개로 증가하지만 4개가 stress robustness 실패.
- 최소 통과표본을 절반 또는 3개로 완화: 3개로 증가하지만 원래 양쪽 robust 조건을 만족하는 것은 AEVA 1개뿐.

즉 gate를 완화하면 robust pattern이 늘기보다 regime-specific 또는 thin-sample 후보가 늘어난다.

### 5.2 (b) 5일 feature로 2일 +3% 예측 자체가 어려운가?

그렇다. 다만 신호가 완전히 0은 아니다.

- OOS 상관은 대체로 |0.10~0.14| 이하.
- 최고 단변량 MI가 label entropy의 1.1~2.4%.
- 강한 feature도 10분위 tail에서만 약 +8~13%p lift.
- 같은 변동성·pullback 정보를 표현하는 feature가 많아 12개가 12개의 독립 정보원이 아니다.

고정 +3% label은 종목 변동성, 가격 수준, 시장 regime에 따라 난이도가 달라진다. 같은 5일 pattern이라도 저변동 종목과 고변동 종목의 +3% 도달 가능성이 다르다. 현재 feature에는 장기 변동성 정규화, 시장 regime, event catalyst, 유동성 shock가 없다.

### 5.3 (c) Train↔stress↔OOS 분포 차이인가?

매우 중요하다.

| Universe | Train 양성률 | OOS 양성률 | 변화 |
|---|---:|---:|---:|
| 로그 후처리 | 38.91% | 49.20% | +10.29%p |
| Replay 후처리 | 37.48% | 48.16% | +10.68%p |
| 통합 일별 | 43.88% | 50.15% | +6.27%p |

Baseline 자체가 이동하므로 절대 precision과 train gap이 동시에 흔들린다.

더 직접적인 증거는 survivor 교체다.

- 로그 기반: AMSC, AVAV
- Replay 기반: AEVA
- 공통 survivor: 0

AMSC와 AVAV는 표본을 replay로 확대하자 stress/OOS precision이 붕괴했다. 통합 파일럿 150개 후보도 평균 train precision 81.75%에서 stress 43.83%, OOS 49.86%로 크게 하락했다.

따라서 표본 정의와 regime 변화에 대한 민감도가 공통 병목이다.

## 6. 통합 방식과 후처리 방식의 차이

방식 차이는 결과의 강도에는 영향을 준다.

### 후처리 방식

- 기존 entry signal만 대상으로 함.
- 개체당 최대 5개 활성 feature.
- Survivor 범위 OOS coverage 16~31%.
- 로그/replay에서 OOS precision 78~88%.
- 기존 진입 로직과 분리돼 shadow 적용·철회가 쉬움.

### 통합 방식

- 매 거래일 직접 label을 학습.
- 12개 feature 전부 strict AND.
- Survivor 범위 OOS coverage 4.96%.
- OOS precision 60%.
- 전체 OOS 평가 중 통과는 0.20%.
- 실제 거래 평균 순수익은 +0.145%로 작음.

통합 방식의 strict 12-way intersection이 coverage를 추가로 줄인 것은 분명하다. 하지만 후처리도 robust survivor가 1~2개뿐이라는 사실은 같다.

따라서 “통합 대신 후처리로 바꾸면 범용 survivor가 많이 생긴다”는 근거는 없다. 후처리가 현재 evidence에서는 더 높은 precision과 덜 극단적인 coverage를 보였지만, sample-definition 교체 시 survivor가 완전히 바뀌었다.

## 7. 공통 근본 원인

### 1순위: 낮고 중복된 예측 정보

5일 가격 path에는 단기 반등·변동성 continuation 정보가 약하게 있지만, 2일 +3% 여부를 안정적으로 결정할 정도로 충분하지 않다.

### 2순위: 고정 +3% target의 종목·regime 비정규화

종목별 실현 변동성과 시장 환경을 무시한 고정 퍼센트 label이 서로 다른 난이도의 사건을 하나로 묶는다.

### 3순위: entity별 분할로 인한 유효 표본 부족

전체 표본은 커도 개별 rulebook/ticker/split로 나누면 train·stress·OOS의 positive와 pass 표본이 급격히 얇아진다.

### 4순위: hard conjunction의 coverage 붕괴

후처리의 최대 5-feature AND와 통합의 12-feature strict AND 모두 작은 marginal signal을 교집합으로 만들어 신호를 대량 삭제한다. 통합 방식에서 가장 심하다.

### 5순위: 분포 이동과 표본 정의 민감도

양성률, feature-label 관계, survivor 정체가 train/stress/OOS 및 log/replay 사이에서 이동한다.

## 8. 결론

두 방식의 반복 실패는 동일한 공통 구조에서 발생한다.

- 약한 5일 path 정보
- 고정 +3% target의 변동성 비정규화
- entity별 얇은 표본
- hard filter에 의한 coverage 붕괴
- regime·universe 정의 변화에 대한 불안정성

게이트 조정만으로 해결된다는 증거는 없고, 통합/후처리 전환만으로도 해결되지 않는다.
