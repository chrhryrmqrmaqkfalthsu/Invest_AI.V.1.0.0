# Fitness 일관성 λ 스윕 — AAP·POWI

## 먼저 볼 한 줄

```text
λ=0.2: 선택 OOS 정밀도 60.00%, Survivor 0/6
λ=0.3: 선택 OOS 정밀도 48.00%, Survivor 0/6
```

λ=0 기준선:

```text
선택 OOS 정밀도 58.06%
OOS gate 1/6
Survivor 0/6
```

## 최종 판정

# **LAMBDA_TRADEOFF_PERSISTS**

λ=0.2는 선택 OOS 정밀도를 60.00%까지 회복했지만 survivor가 없고 OOS gate도 기준선과 동일한 1/6이었다. λ=0.3은 선택 OOS 정밀도와 OOS gate가 모두 악화됐다.

따라서 요청한 확정 조건인 다음 조합은 어느 λ에서도 나오지 않았다.

```text
λ=0 수준의 선택 OOS 정밀도 유지
AND Survivor ≥1 또는 OOS gate 개선
```

일관성 페널티는 Stress 점수를 움직이지만 같은 개체에서 Stress와 독립 OOS를 동시에 통과시키지 못했다. 다음 사이클은 고정 +3% 타겟을 변동성 상대 타겟으로 재정의하는 방향이 타당하다.

## 실행 계약

- λ: 0.2, 0.3
- 종목: AAP, POWI
- split: train_1, train_2, train_3
- 후보: λ별 6개
- worker: λ별 정확히 6개
- population: 100
- generation 상한: 50
- patience: 15
- feature: 14개·4그룹
- threshold: G1/G2 2~3, G3/G4 2
- rolling 목표일 청산
- TP OFF
- Stress: fitness scorer only
- feature domain·G3 floor·fallback·gene 연산: TRAIN_ONLY

실제 조기종료:

```text
λ=0.2: AAP train_2·train_3이 40세대, 나머지 50세대
λ=0.3: AAP train_1이 45세대, 나머지 50세대
```

이는 기존 patience 15가 정상 작동한 결과다.

## λ 변경 무결성

기존 consistency fitness 파일은 수정하지 않았다.

```text
scripts/research/rolling_rediscovery/upstream_snapshot/
engine/learning/grouped_genetic_floored_consistency.py
SHA-256: 4c716806d69e8d3f3d1321113f0bfe9ed2b696328da08ae2783caa57b24bc8c6
```

Worker 시작 시 다음 상수만 런타임으로 교체했다.

```python
penalty_ga.CONSISTENCY_LAMBDA = 0.2
# 또는
penalty_ga.CONSISTENCY_LAMBDA = 0.3
```

수식은 λ=0.5 실험과 동일하다.

```text
gap = max(0, train_precision - stress_precision)
fitness penalty = 220 × λ × gap
```

산술 검증:

```text
train=0.80, stress=0.40, gap=0.40
λ=0.2 → 17.6점 감점
λ=0.3 → 26.4점 감점
```

Training log 전 행에서 실제 감점과 공식의 최대 오차는 각각 약 `4.0e-15`, `7.1e-15`였다.

## 비교표

지시서의 파일명은 `five_way_comparison.csv`지만 “직전 4개 + 신규 2개”는 산술상 6개이므로 6행을 모두 기록했다.

| 방식 | OOS Coverage | 선택 OOS 정밀도 | OOS 평균 6후보 | Stress 평균 — 커닝, 독립검증 아님 | OOS gate | Survivor | 평균수익 | 복리 | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Strict-AND 12개 | 4.96% | 60.00% | 55.68% | 40.99% | 2/6 | 2/6 | +0.1446% | +0.8181% | -12.81% |
| Hybrid 몰빵 허용 | 6.94% | 57.14% | 50.33% | 43.28% | 0/6 | 0/6 | +0.0203% | -1.3120% | -19.29% |
| Hybrid 몰빵 제한, λ=0 | 18.45% | 58.06% | **59.74%** | 43.26% | **1/6** | 0/6 | +2.0794% | +146.1539% | -17.45% |
| λ=0.5 | 7.34% | 48.65% | 56.01% | 69.70% | 1/6 | 0/6 | -1.4695% | -37.5994% | -31.45% |
| λ=0.3 | 14.88% | **48.00%** | 56.16% | 70.01% | **0/6** | **0/6** | -0.0929% | -14.6420% | -45.38% |
| λ=0.2 | 11.90% | **60.00%** | 57.18% | 54.49% | **1/6** | **0/6** | +0.8533% | +39.2791% | -34.01% |

판정은 Stress가 아니라 OOS와 survivor만 사용했다.

## λ=0.2 상세

### 결과

```text
선택 OOS 정밀도: 60.00%
6후보 OOS 평균: 57.18%
OOS gate: 1/6
Survivor: 0/6
Stress 평균 — 커닝: 54.49%
```

선택 OOS 정밀도는 λ=0 기준선보다 +1.94%p 높았다. 그러나 OOS 평균은 -2.56%p였고 OOS gate는 1/6로 개선되지 않았다.

### 게이트가 같은 개체에서 겹치지 않음

```text
AAP train_1
Stress gate: FAIL
OOS gate: PASS
OOS precision: 62.86%
```

```text
AAP train_3
Stress gate: PASS
OOS gate: FAIL
OOS precision: 63.64%
필요 OOS floor: 70.00%
```

즉 Stress를 통과한 개체와 OOS를 통과한 개체가 서로 달랐다.

POWI 세 후보는 모두 OOS gate를 통과하지 못했다.

### 진단 거래

```text
OOS signal: 60
거래: 49
평균수익: +0.8533%
순차 복리: +39.2791%
MDD: -34.01%
```

수익은 양수지만 survivor가 없고 λ=0의 MDD -17.45%보다 크게 악화됐다. 배포 가능한 성과가 아니다.

## λ=0.3 상세

```text
선택 OOS 정밀도: 48.00%
6후보 OOS 평균: 56.16%
OOS gate: 0/6
Survivor: 0/6
Stress 평균 — 커닝: 70.01%
```

Stress는 λ=0.5 수준까지 올라갔지만 OOS gate가 0으로 줄었다. Stress 적합과 독립 OOS 희생이 다시 나타났다.

진단 거래:

```text
OOS signal: 75
거래: 45
평균수익: -0.0929%
순차 복리: -14.6420%
MDD: -45.38%
```

## 왜 LAMBDA_FOUND가 아닌가

### λ=0.2

- 선택 OOS 정밀도는 유지: YES
- Survivor ≥1: NO
- OOS gate가 기준선 1/6보다 개선: NO

### λ=0.3

- 선택 OOS 정밀도 유지: NO
- Survivor ≥1: NO
- OOS gate 개선: NO

따라서 두 λ 모두 확정 조건을 충족하지 않는다.

## 결론

λ를 낮추면 λ=0.2에서 선택 OOS 정밀도를 복구할 수는 있었다. 하지만 consistency penalty가 Stress·OOS 이중 게이트를 같은 chromosome에서 정렬하지 못했고 survivor는 전 구간에서 0이었다.

```text
λ=0.0: Survivor 0
λ=0.2: Survivor 0
λ=0.3: Survivor 0
λ=0.5: Survivor 0
```

이 범위의 λ 튜닝만으로 일반화 문제를 해결하기 어렵다는 결론이다. 다음 사이클은 고정 +3% 라벨 대신 ATR·실현변동성 등에 연동된 상대 타겟으로 문제 정의를 바꾸는 것이 권고된다.
