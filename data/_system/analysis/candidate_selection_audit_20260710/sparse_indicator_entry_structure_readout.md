# 소수 지표 진입 허용 구조 — 원본 17,071개 정적 판정

- 기준일: 2026-07-11 KST
- 입력: Stage2 survivors 1,162개 + Stage3 final_rulebooks 15,909개
- 총 원본: **17,071개**
- 작업: 저장된 룰의 5개 core weight와 threshold에 대한 조합론 계산
- 원본 수정·라이브 후보 변경·재학습·주문·삭제: **0건**

## 1. 판정 정의

실제 매수 score의 core 항은 다음 5개다.

```text
MA 정배열
MACD cross
RSI zone
BB 근접
volume surge
```

각 지표는 해당 시점에 활성화되면 저장 weight, 비활성화되면 0을 기여한다.

```text
core_raw_score = Σ(active indicator weight)
final_score = core_raw_score × market_adjustment
진입 = final_score >= signal_threshold
```

각 룰에 대해 공집합을 제외한 31개 부분집합을 실제로 순회했다. threshold를 넘기는 부분집합 중 활성 지표 수가 가장 적은 값을 `진입 최소 지표 수`로 정했다.

가중치 내림차순 누적 방식과 31개 전수 열거 결과도 비교했다.

- 일치: **17,071/17,071**
- 불일치: 0
- 음수 core weight: 0
- 필수 정적 필드 누락: 0

따라서 UNJUDGED는 없다.

`IMPOSSIBLE_CORE_ONLY`는 미판정이 아니라 해당 가정에서 core 5개를 모두 켜도 threshold를 넘지 못한다는 확정 결과다.

## 2. 동적 항 처리

### 중립 시나리오

```text
market_adjustment = 1.0
news/topic/event/crash bonus = 0
```

저장된 core weight 자체만 보는 기준이다.

### 보수적 시장배수 상한 시나리오 — 주 판정

코드에서 시장배수는 다음 범위를 가진다.

```text
strength = clamp(market_adjustment_strength, 0, 1)
market_adjustment <= 1 + strength
```

따라서 시장보정 사용 룰에는 `1+strength`, 미사용 룰에는 1.0을 적용했다. 이는 실제 특정 시점에 해당 상한이 실현된다는 뜻이 아니라, 소수 지표 진입 위험을 과소평가하지 않기 위한 코드상 가능 상한이다.

### 비-core 가산점

다음 항은 core-only 구조 정의에서 0으로 고정했다.

- 전체 뉴스 감성
- topic news
- event response
- crash-buy bonus

17,071개 모두 룰 구조상 하나 이상의 양의 비-core 가산점 가능성이 있다. 따라서:

```text
N<=2로 잡힌 룰: core만으로도 소수 진입 가능
N>2 또는 IMPOSSIBLE: 비-core 가산점까지 포함해 안전하다는 뜻은 아님
```

본 판정은 “잡히면 확실한 core-only 구조 위험”이며, 미포착 룰의 완전한 안전 증명은 아니다.

## 3. 전수 결과

### 중립 배수

| 최소 지표 수 | 개체 수 |
|---|---:|
| 1 | **3,496** |
| 2 | 11,040 |
| 3 | 1,916 |
| 4 | 278 |
| 5 | 38 |
| core-only 불가능 | 303 |

- N=1: 3,496개, **20.48%**
- N<=2: 14,536개, **85.15%**

### 보수적 시장배수 상한 — 주 판정

| 최소 지표 수 | 개체 수 |
|---|---:|
| 1 | **6,794** |
| 2 | 8,600 |
| 3 | 1,203 |
| 4 | 220 |
| 5 | 22 |
| core-only 불가능 | 232 |

- N=1: 6,794개, **39.80%**
- N<=2: 15,394개, **90.18%**
- UNJUDGED: **0**

단계별 보수적 결과:

| 단계 | 전체 | N=1 | N<=2 | core-only 불가능 |
|---|---:|---:|---:|---:|
| Stage2 | 1,162 | 347 | 955 | 25 |
| Stage3 | 15,909 | 6,447 | 14,439 | 207 |

완성도 검증 개체 3,174개에서도 N=1은 34.94%, N<=2는 88.41%다.

## 4. 단일 지표 진입 구성

보수적 시장배수에서 단독으로 threshold를 넘기는 지표:

| 단독 지표 | 개체 수 |
|---|---:|
| BB | 2,046 |
| MA | 1,366 |
| RSI | 1,238 |
| volume | 1,179 |
| MACD | 965 |

N=2의 대표 조합은 `RSI+BB`, `RSI+volume`, `MA+BB`, `BB+volume`, `MACD+BB` 순이다.

전체 목록:

- `sparse_indicator_entry_n1_market_cap.csv`
- `sparse_indicator_entry_n2_market_cap.csv`
- `sparse_indicator_entry_n1_neutral.csv`
- `sparse_indicator_entry_n2_neutral.csv`

N=2 목록에는 N=1도 포함한다.

## 5. BOIL parity

기존 BOIL형:

```text
HIGH_VOL
AND abs(weight_volume_surge)<=0.05
```

기존 BOIL 개체: 444개

| 시나리오 | N=1 포섭 | N<=2 포섭 | N<=2 포섭률 |
|---|---:|---:|---:|
| 중립 | 112 | 437 | 98.42% |
| 시장배수 상한 | 254 | **440** | **99.10%** |

즉 N<=2 구조는 거래량 없이 다른 소수 지표만으로 진입 가능한 BOIL형을 거의 전부 포섭한다.

놓친 4개:

```text
stage2:GSAT:7d070e5b4580  — 비-volume 지표 3개 필요
stage3:CRM:569bf32c251f  — 비-volume 지표 4개 필요
stage3:CRM:7ed298ed06cd  — 비-volume 지표 4개 필요
stage3:CRM:df2e3113ef23  — 비-volume 지표 4개 필요
```

이들은 volume weight가 0이지만 threshold가 높아 “2개 이하 지표 진입” 구조에는 해당하지 않는다. 따라서 기존 BOIL 조건과 N<=2 조건은 완전한 동치가 아니다.

- GSAT holdout 평균 PnL: +4.61%, 승률 90.0%
- CRM 3개 holdout 평균 PnL: -2.04%, 승률 26.67%

상세 목록:

- `sparse_indicator_entry_boil_parity.csv`
- `sparse_indicator_entry_boil_n2_missed.csv`

## 6. CE parity

기존 CE형 FAIL 7개:

| 시나리오 | N=1 포섭 | N<=2 포섭 |
|---|---:|---:|
| 중립 | 0/7 | 6/7 |
| 시장배수 상한 | 1/7 | **7/7** |

보수적 N<=2는 기존 CE형 7개를 전부 포섭한다.

- BOIL 후보는 시장배수 상한에서 BB 하나만으로 진입 가능하다.
- BTE는 중립에서는 3개가 필요하지만 시장배수 상한에서는 RSI+BB 두 개로 진입 가능하다.
- 나머지 CE형은 중립에서도 2개 조합으로 진입 가능하다.

상세 목록: `sparse_indicator_entry_ce_parity.csv`

## 7. 성과 대조 — 참고 전용

완성도 검증 개체만 대상으로 holdout 성과를 비교했다. 차단 여부에는 사용하지 않았다.

### 보수적 시장배수

| 구조 | 개체 | holdout 평균 PnL | holdout 승률 | 거래가중 PnL |
|---|---:|---:|---:|---:|
| N=1 | 1,109 | 3.2680% | 66.81% | 3.2193% |
| N<=2 | 2,806 | **3.3121%** | 66.29% | **3.2038%** |
| N>2 또는 불가능 | 368 | 2.4588% | 65.63% | 2.3691% |

### 중립 배수

| 구조 | 개체 | holdout 평균 PnL | holdout 승률 | 거래가중 PnL |
|---|---:|---:|---:|---:|
| N=1 | 471 | 2.7529% | 67.00% | 2.7476% |
| N<=2 | 2,576 | **3.3440%** | 66.06% | **3.2322%** |
| N>2 또는 불가능 | 598 | 2.6496% | 66.91% | 2.5526% |

소수 지표 진입 구조군이 과거 성과에서 일관되게 나쁘지는 않았다. 따라서 이 조건은 성과 예측 필터가 아니라, 사용자가 정의한 “소수 지표만 켜져도 진입 가능한 원인 구조”를 직접 판정하는 규칙이다.

상세 성과표: `sparse_indicator_entry_performance_summary.csv`

## 8. 해석

1. 원본 17,071개를 정적으로 전부 판정했고 UNJUDGED는 없다.
2. 보수적 N<=2는 CE 7개 전부와 BOIL 444개 중 440개를 포섭한다.
3. 그러나 보수적 N<=2는 원본 전체의 90.18%다. 실전 BLOCK으로 사용하면 매우 광범위한 차단 규칙이 된다.
4. N=1은 더 좁지만 여전히 39.80%다.
5. N<=2 미포착은 core-only 안전을 뜻하지 않는다. 뉴스·이벤트·crash bonus가 실제 최소 core 수를 더 낮출 수 있다.
6. 지표 조합이 논리적으로 가능하다는 것만 계산했으며, 해당 지표들이 실제 같은 봉에서 동시에 켜질 확률은 계산하지 않았다.
7. 차단 여부는 구조 원인으로 결정한다는 원칙에 따라 성과는 참고로만 기록했다.

## 9. 산출물

- `sparse_indicator_entry_structure_full.csv` — 17,071개 전수 계산
- `sparse_indicator_entry_n1_market_cap.csv`
- `sparse_indicator_entry_n2_market_cap.csv`
- `sparse_indicator_entry_n1_neutral.csv`
- `sparse_indicator_entry_n2_neutral.csv`
- `sparse_indicator_entry_boil_parity.csv`
- `sparse_indicator_entry_boil_n2_missed.csv`
- `sparse_indicator_entry_ce_parity.csv`
- `sparse_indicator_entry_parity_summary.csv`
- `sparse_indicator_entry_performance_summary.csv`
- `sparse_indicator_entry_scenario_summary.csv`
- `sparse_indicator_entry_summary.json`
- `run_sparse_indicator_structure_analysis.py`
- `finalize_sparse_indicator_structure.py`
- `sparse_indicator_entry_structure_readout.md`

원본과 라이브 후보 파일은 불변이다.
