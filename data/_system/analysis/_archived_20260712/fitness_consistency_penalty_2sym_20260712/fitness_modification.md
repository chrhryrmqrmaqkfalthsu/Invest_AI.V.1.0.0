# Fitness 일관성 페널티 변경 기록

## 변경 목표

검증 질문:

```text
train↔stress 정밀도 차이를 fitness에 넣으면 stress gate 통과 개체가 나오는가?
```

기준 구현은 직전 floored hybrid다.

```text
engine/learning/grouped_genetic_floored.py
```

이번 전용 구현:

```text
engine/learning/grouped_genetic_floored_consistency.py
```

기존 파일은 수정하지 않았다.

## 유일한 모델 선택 변화

`grouped_genetic_floored_consistency.py` 52~106행의 `_evaluate_grouped_consistency()`만 새로운 점수 의미를 가진다.

```python
stress_mask = apply_same_gene_to_stress(x_stress)
stress_precision = mean(y_stress[stress_mask]) if any_pass else 0.0

gap = max(0.0, train_precision - stress_precision)
adjusted_precision = train_precision - 0.5 * gap
```

LAMBDA:

```text
CONSISTENCY_LAMBDA = 0.5
```

기존 grouped fitness는 train precision 항에 220배를 적용한다.

```python
original_precision_term = train_precision * 220
```

따라서 동일 scale에서 다음을 적용했다.

```python
penalty_points = 220 * 0.5 * gap
fitness = original_fitness - penalty_points
```

이는 기존 fitness의 **train precision 항만** 다음과 같이 바꾸는 것과 대수적으로 같다.

```python
precision_term = (train_precision - 0.5 * gap) * 220
```

## 그대로 유지한 fitness 요소

다음은 직전 floored GA와 동일하다.

- train 최소 통과 표본 `max(20, train_rows*2%)`
- train precision threshold 미달 페널티
- train lift
- train recall
- train coverage
- 통과 표본 로그 항
- interval 평균폭 페널티
- group threshold complexity 항
- upper-bound fallback
- bilateral 최소폭

Stress 통과 표본에 새로운 최소표본 gate나 보너스를 추가하지 않았다. 요청된 정밀도 gap만 적용했다.

## Stress 사용 범위

Stress는 gene의 입력 데이터가 아니다.

```text
Feature domain min/max: TRAIN_ONLY
G3 percentile floor: TRAIN_ONLY
Positive-success upper fallback: TRAIN_ONLY
Train minimum sample: TRAIN_ONLY
Interval/threshold initialization: stress 미사용
Crossover/mutation: stress 미사용
```

Stress가 쓰이는 위치:

```text
현재 train gene을 stress에 그대로 적용
→ stress precision 계산
→ train_precision - stress_precision 양의 gap만 fitness에서 감점
```

따라서 stress는 out-of-train **일관성 채점자**로만 사용한다. 다만 stress 점수가 chromosome 선택에 영향을 주므로 이 실험 이후 stress는 더 이상 완전히 untouched validation set이 아니라 model-selection scorer라는 점을 결과 해석에 명시한다. OOS는 계속 최종 검증 전용이다.

## 변경하지 않은 구조

- 14개 지표
- G1/G2/G3/G4 구성
- 그룹 내 interval 통과 count
- 그룹 간 AND
- G1/G2 threshold 2~3
- G3/G4 threshold 2 고정
- population 100
- generation 50
- patience 15
- train_1/2/3
- rolling 목표일 청산
- early take profit OFF

## 산술 smoke test

인공 데이터:

```text
train precision = 0.80
stress precision = 0.40
gap = 0.40
lambda = 0.50
```

기대값:

```text
adjusted precision = 0.60
fitness penalty = 220 * 0.5 * 0.4 = 44.0점
```

검증 결과:

```text
기존 score: 196.1976807753
변경 score: 152.1976807753
차이: 44.0
```

산술 검증 통과.

## 병렬 실행

AAP·POWI × train_1/2/3 = 6개 후보를 `ProcessPoolExecutor(max_workers=6)`로 실행한다.

각 worker는 독립 train domain과 stress scorer를 사용하며, 파일 출력은 parent process가 병합 후 수행한다.
