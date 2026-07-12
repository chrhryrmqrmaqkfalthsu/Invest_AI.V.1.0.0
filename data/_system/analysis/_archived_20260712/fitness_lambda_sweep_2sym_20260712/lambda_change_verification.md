# λ 상수 변경 검증

## 기준 fitness 구현

```text
scripts/research/rolling_rediscovery/upstream_snapshot/engine/learning/grouped_genetic_floored_consistency.py
SHA-256: 4c716806d69e8d3f3d1321113f0bfe9ed2b696328da08ae2783caa57b24bc8c6
```

이 파일은 λ=0.5 실험 이후 수정하지 않는다.

기존 수식:

```python
gap = max(0.0, train_precision - stress_precision)
adjusted_precision = train_precision - CONSISTENCY_LAMBDA * gap
penalty_points = 220.0 * CONSISTENCY_LAMBDA * gap
fitness = original_fitness - penalty_points
```

## 이번 스윕 방식

새 runner의 worker 시작 시 다음 상수만 런타임으로 교체한다.

```python
penalty_ga.CONSISTENCY_LAMBDA = 0.2
```

또는:

```python
penalty_ga.CONSISTENCY_LAMBDA = 0.3
```

Fitness 함수 본문, gap 정의, precision weight 220, 기존 composite fitness의 다른 항은 변경하지 않는다.

## 동일 유지 항목

- Stress는 현재 chromosome 정밀도 채점에만 사용
- feature domain: TRAIN_ONLY
- G3 percentile floor: TRAIN_ONLY
- upper fallback: TRAIN_ONLY
- interval·threshold gene 생성: TRAIN_ONLY
- crossover·mutation: 기존과 동일
- threshold: G1/G2 2~3, G3/G4 2
- population/generation/patience: 100/50/15
- 14개 feature·4그룹
- rolling 목표일·TP OFF

## 출력 분리

```text
lambda_0.2/
lambda_0.3/
```

각 worker는 별도 프로세스에서 상수를 설정하므로 두 λ의 module global 상태가 섞이지 않는다. 각 λ 실행은 최대 6개 worker를 사용하고 완료 후 다음 λ의 새 process pool을 생성한다.

## 비교표 행 수

지시서에는 “5-way”라고 되어 있지만 “직전까지 4개 + 이번 2개”는 산술상 6개 방식이다. 파일명은 요청대로 `five_way_comparison.csv`를 사용하고 실제 표에는 다음 6행을 모두 포함한다.

1. Strict-AND
2. Hybrid 몰빵 허용
3. Hybrid 몰빵 제한 λ=0
4. λ=0.5
5. λ=0.3
6. λ=0.2
