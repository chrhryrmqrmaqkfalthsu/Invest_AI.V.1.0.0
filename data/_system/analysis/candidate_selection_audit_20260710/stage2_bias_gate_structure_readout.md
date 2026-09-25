# Stage2 편중 원인 진단 + 게이트 병렬 구조 확정

- 기준일: 2026-07-11 KST
- 분석 범위: 기존 dry-run 산출물과 원본 룰 read-only
- 원본·라이브·운영 코드·재학습·주문·삭제: 0건
- 운영 구현: `false`
- 최종 판정: **MIXED**

## 1. 결론

최종 후보 `Stage2 10 / Stage3 75`의 직접 원인은 stage cap이 아니다. cap 직전부터 이미 Stage2는 10개, Stage3는 75개뿐이어서 두 cap `60/80`은 모두 비활성이다.

Stage2의 주 병목은 **elite filter**다.

```text
Stage2: elite 직전 796 → elite 통과 22 → denylist 21 → ticker dedup 10
Stage3: elite 직전 1,330 → elite 통과 381 → denylist 380 → ticker dedup 75
```

판정은 다음과 같다.

> **MIXED = 기존 elite 지표상 Stage2 품질분포가 낮은 영향 + Stage2 전용 elite 임계의 과잉 차단이 함께 존재.**

공통 OOS expectancy `2.7` 기준에서 Stage2가 대량 탈락하므로 1차 원인은 `QUALITY_DRIVEN`이다. 그러나 Stage2 전용 `oos_trade_count>=15`, `oos_fitness>=70`, `min_trade_count>=8`을 Stage3 숫자 기준으로 완화하면 양호한 holdout 후보가 상당수 회복되므로 `GATE_OVER_FILTER`도 확인된다.

표본수 임계 `35/24`, v3 도달불가 BLOCK, denylist, stage cap은 Stage2 편중의 주 원인이 아니다.

## 2. 단계별 순차 탈락

현재 최종 85개를 만든 근거 안전 정책을 그대로 재생했다. 승률과 BOIL형은 현재 설계상 MONITOR이므로 hit 수는 기록하지만 이 표에서 후보를 제거하지 않는다.

| 단계 | Stage2 잔존 | Stage2 순탈락 | Stage2 탈락률 | Stage3 잔존 | Stage3 순탈락 | Stage3 탈락률 |
|---|---:|---:|---:|---:|---:|---:|
| 원본 | 1,162 | — | — | 15,909 | — | — |
| 완성도 | 1,162 | 0 | 0.00% | 2,012 | 13,897 | 87.35% |
| 표본수 HOLD 제외 | 1,059 | 103 | 8.86% | 1,824 | 188 | 9.34% |
| 평균 PnL | 1,059 | 0 | 0.00% | 1,824 | 0 | 0.00% |
| 승률 MONITOR | 1,059 | 0 | 0.00% | 1,824 | 0 | 0.00% |
| v3 p99 BLOCK | 796 | 263 | 24.83% | 1,330 | 494 | 27.08% |
| BOIL형 MONITOR | 796 | 0 | 0.00% | 1,330 | 0 | 0.00% |
| elite filter | **22** | **774** | **97.24%** | **381** | **949** | **71.35%** |
| denylist | 21 | 1 | 4.55% | 380 | 1 | 0.26% |
| ticker dedup | **10** | 11 | 52.38% | **75** | 305 | 80.26% |
| stage cap | **10** | 0 | 0.00% | **75** | 0 | 0.00% |

조건 hit 참고:

- 승률 하위 MONITOR: Stage2 106/1,059, Stage3 164/1,824
- v3 이후 BOIL exact-zero MONITOR: Stage2 2/796, Stage3 9/1,330

Stage3는 완성도에서 훨씬 많이 탈락한다. Stage2만 유독 높은 탈락률은 elite filter에서 나타난다.

## 3. stage cap 이전 상태

- Stage2 cap: 60
- Stage2 cap 직전 고유 ticker: **10**
- Stage3 cap: 80
- Stage3 cap 직전 고유 ticker: **75**

두 단계 모두 cap이 후보를 한 개도 제거하지 않았다. 따라서 `10/75`는 cap 배정 문제가 아니라 cap 이전 유효 pool 크기 문제다.

Stage2는 elite 직후 후보가 22개, denylist 뒤 21개이고 이들이 10개 ticker에 집중돼 있다. ticker dedup이 21→10으로 줄이지만 Stage3의 dedup 탈락률은 80.26%로 Stage2의 52.38%보다 더 높다. dedup 자체가 Stage2 편향 게이트는 아니다. Stage2는 dedup 전에 이미 pool이 작다.

## 4. 표본수 게이트 비대칭 영향

현재 기준:

| 단계 | 임계 | 완성 개체 | 임계 미달 | 비율 |
|---|---:|---:|---:|---:|
| Stage2 | `<35` | 1,162 | 103 | 8.86% |
| Stage3 | `<24` | 2,012 | 188 | 9.34% |

숫자 임계는 Stage2가 높지만 각 단계의 development 거래수 P10에서 도출돼 실제 제거 비율은 거의 같다. Stage3가 오히려 0.48%p 더 높다.

반사실 결과:

| 시나리오 | Stage2 최종 | Stage3 최종 |
|---|---:|---:|
| 표본 게이트 없음 | **10** | 77 |
| 공통 24건 | **10** | 75 |
| 공통 35건 | **10** | 67 |
| 현재 35/24건 | **10** | 75 |

Stage2 임계를 35→24로 낮추거나 표본 게이트를 제거해도 최종 Stage2는 10개로 변하지 않는다. 저표본 Stage2 후보는 후속 v3·elite 기준을 통과하지 못하므로 현재 편중에 대한 한계효과가 0이다.

표본 부족군이 실제 저품질인 것도 아니다.

| 그룹 | 후보 | holdout 거래 | 후보동일가중 평균 PnL | 거래가중 PnL | 승률 |
|---|---:|---:|---:|---:|---:|
| Stage2 `<35` | 103 | 1,023 | 3.2177% | 2.8639% | 67.13% |
| Stage2 `>=35` | 1,059 | 13,706 | 2.3102% | 2.2817% | 68.87% |
| Stage3 `<24` | 188 | 1,566 | 4.9479% | 4.8353% | 66.24% |
| Stage3 `>=24` | 1,824 | 25,653 | 3.5584% | 3.4668% | 64.62% |

따라서 표본 게이트는 수익성 차단선이 아니라 신뢰도 HOLD로만 유지하는 현재 구조가 맞다. **Stage2 편중 원인으로는 기각한다.**

## 5. v3와 BOIL형의 Stage2 편향 여부

v3 순차 탈락률:

- Stage2: 263/1,059 = 24.83%
- Stage3: 494/1,824 = 27.08%

Stage2가 더 낮다. v3는 Stage2 편향 원인이 아니다.

BOIL exact-zero 조건은 v3 뒤 pool에서 Stage2 2개, Stage3 9개에 hit한다. 현재 설계에서 enforcement를 변경하지 않았고 MONITOR 병렬 조건으로만 기록했다. 이 조건을 민감도상 BLOCK으로 가정해도 Stage2 최종은 10개로 변하지 않고 Stage3만 75→74로 줄어든다.

## 6. elite filter: 품질 문제와 과잉 차단의 동시 확인

### 6.1 QUALITY_DRIVEN 근거

elite 직전 pool:

- Stage2: 796
- Stage3: 1,330

동일 숫자 기준인 `OOS expectancy >=2.7`의 최초 탈락:

- Stage2: **580/796, 72.86%**
- Stage3: **35/1,330, 2.63%**

중앙값:

| 지표 | Stage2 | Stage3 |
|---|---:|---:|
| OOS expectancy | **1.9909%** | 6.1837% |
| OOS fitness | 44.1123 | 102.6899 |
| OOS win rate | 68.75% | 76.92% |
| OOS trade count | 12 | 13 |

기존 elite metric 정의 안에서는 Stage2 분포가 공통 expectancy 2.7과 win 70 아래에 더 많이 위치한다. Stage2 elite 통과군의 holdout 평균 PnL도 3.8255%로 elite 탈락군 2.2349%보다 높아, elite가 전혀 무의미한 필터라고 볼 수는 없다.

단, Stage2 OOS 지표와 Stage3 bull/profile 지표의 산출 계보가 완전히 동일하지 않으므로 절대 수준 비교는 기존 선택 로직 안에서만 해석해야 한다.

### 6.2 GATE_OVER_FILTER 근거

Stage2 796개에 Stage3의 숫자 elite 기준을 적용한 반사실:

```text
fitness 70 → 45
OOS trade count 15 → 8
Stage2 전용 min_trade_count 8 제거
나머지 공통 기준과 anti-pattern 유지
```

결과:

| 시나리오 | 후보 | denylist 후 고유 ticker | holdout 평균 PnL | 거래가중 PnL | 승률 |
|---|---:|---:|---:|---:|---:|
| 현재 Stage2 elite | 22 | 10 | 3.8255% | 3.8429% | 82.20% |
| Stage3 숫자 기준 적용 | 59 | 32 | 4.1375% | 4.0851% | 82.04% |
| 신규 회복분 | **37** | **25** | **4.3229%** | **4.3036%** | 81.95% |

신규 회복 37개의 현재 최초 탈락 사유:

- `oos_trade_count >=15` 대 `>=8`: 25개
- `oos_fitness >=70` 대 `>=45`: 8개
- Stage2 전용 `min_trade_count >=8`: 4개

회복군의 holdout PnL이 현재 Stage2 elite 통과군보다 높다. 따라서 이 세 임계는 단순 품질 보호를 넘어 **Stage2를 과잉 차단하는 근거가 확인됐다.**

다만 Stage3 숫자 기준을 적용해도 Stage2 고유 ticker는 32개로 cap 60에 못 미친다. 즉 과잉 차단을 제거해도 Stage2와 Stage3의 전체 격차가 완전히 사라지지는 않는다.

## 7. 최종 원인 판정

### 판정: MIXED

**주 원인 — QUALITY_DRIVEN**

- 공통 OOS expectancy 2.7에서 Stage2 580개가 탈락한다.
- Stage2 elite 직전 expectancy 중앙값 1.99는 기준 아래다.
- elite 통과군은 Stage2 elite 탈락군보다 holdout PnL이 높다.

**부 원인 — GATE_OVER_FILTER**

- Stage2 전용 거래수 15, fitness 70, 기간별 최소 거래수 8이 37개·25 ticker를 추가 제거한다.
- 해당 회복군의 holdout 평균 PnL은 현재 Stage2 elite 통과군보다 높다.
- 현재 Stage2 elite 기준은 Stage3 기준보다 엄격하면서 이 차이를 정당화하는 holdout 근거가 부족하다.

**원인 아님**

- 표본수 35/24: 제거율이 유사하고 Stage2 최종 한계효과 0
- v3: Stage2 탈락률이 Stage3보다 낮음
- denylist: 각 단계 1개
- ticker dedup: Stage3 탈락률이 더 높음
- stage cap: 양쪽 모두 비활성

## 8. 세 게이트 병렬 구조 CONFIRMED

다음 책임 분리를 설계 사실로 고정했다.

| 게이트 | phase | 담당 결함 |
|---|---|---|
| v3 도달불가 | STATIC | **검증표본 없는 도달불가 임계** |
| BOIL형 | STATIC | **위험구간 거래량 무시 구조** |
| CE | DYNAMIC, `evaluate_candidate` | **진입 시점 점수여유·소수지표 집중** |

### v3와 BOIL형

- HIGH_VOL volume-blind exact-zero: 410개
- v3 포섭: 54개
- BOIL형 전용: **356개**

356개는 거래량 weight가 0이지만 저장 임계는 p99 이내여서 v3가 잡지 못한다. 따라서 v3가 BOIL형을 대체하지 못한다.

### v3와 CE

CE FAIL 7개 중 v3 포섭은 3개다. 다음 4개는 동적 검사 전용이다.

```text
stage3:ANET:fe220620802b
stage3:BB:f1bdfe7f8ad9
stage3:CDE:ceb9fe0512dc
stage3:CE:998b0b638c66
```

CE는 현재 `final_score`, `signal_threshold`, realized component share가 필요한 `evaluate_candidate` 단계 검사다. 정적 v3로 대체할 수 없다.

### 확정 구조

```text
STATIC v3 reachability
OR STATIC BOIL volume-blind structure
OR DYNAMIC CE margin/concentration at evaluate_candidate
```

세 검사는 병렬로 유지한다. 이번 작업에서는 enforcement를 새로 승격하거나 운영 코드에 연결하지 않았다.

확정 설계 기록:

`integrated_gate_parallel_structure_confirmed.json`

## 9. 산출물

- `stage2_stage3_gate_attrition_funnel.csv`
- `stage2_sample_gate_asymmetry.csv`
- `stage2_elite_gate_bias_diagnosis.csv`
- `stage2_bias_gate_structure_summary.json`
- `integrated_gate_parallel_structure_confirmed.json`
- `stage2_bias_gate_structure_readout.md`
- `run_stage2_bias_gate_structure_diagnosis.py`

운영 구현 상태는 계속 `false`다.
