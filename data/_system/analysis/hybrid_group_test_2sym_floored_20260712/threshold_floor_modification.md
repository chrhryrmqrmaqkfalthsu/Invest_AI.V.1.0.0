# 그룹 threshold 몰빵 제한 구현 기록

## 변경 범위

기존 strict-AND·rolling 청산·feature·label·fitness·GA search scale은 수정하지 않았다.

새로 추가한 연구 전용 파일:

```text
scripts/research/rolling_rediscovery/upstream_snapshot/
├── engine/learning/grouped_genetic_floored.py
└── scripts/research/
    ├── run_hybrid_group_test_2sym_floored.py
    └── run_hybrid_group_test_2sym_floored_entry.py
```

기준 grouped GA:

```text
engine/learning/grouped_genetic.py
threshold domain = 1..group_size
```

Floored grouped GA:

```text
G1 4개: 2..3
G2 4개: 2..3
G3 3개: 2..2
G4 3개: 2..2
```

## 코드상 제약

### 범위 계산

`grouped_genetic_floored.py` 28~35행:

```python
minimums = np.full(len(group_indexes), 2, dtype=int)
maximums = np.array([len(indexes) - 1 for indexes in group_indexes])
```

그룹 크기 4는 2~3, 크기 3은 2만 허용한다.

### Gene 검증

38~62행:

```python
if threshold < minimum:
    return False, "group_threshold_below_floor"
if threshold > maximum:
    return False, "group_threshold_above_cap"
```

따라서 1/N과 N/N은 모두 invalid gene이다.

### 초기 population

65~91행:

```python
rng.integers(minimum, maximum + 1)
baseline_thresholds = minimums
```

초기 개체와 baseline도 제약 범위 안에서만 생성된다.

### Mutation

94~128행:

```python
threshold = clip(threshold + step, minimum, maximum)
```

변이로도 1/N 또는 N/N에 도달할 수 없다.

### 기존 GA 골격 재사용

131~156행은 floored validation을 먼저 통과한 뒤 기존 grouped fitness를 그대로 호출한다.

159행 이후 학습부는 다음을 기존과 동일하게 유지한다.

- interval gene 구조
- 양방향 최소폭
- upper fallback
- precision 중심 fitness
- elite·tournament·crossover
- mutation rate·sigma
- population 100
- generation 50
- patience 15

## Runner 주입 경로

`run_hybrid_group_test_2sym_floored_entry.py` 49~57행은 기존 2종목 runner를 모듈로 로드한다.

60~80행:

```python
base_runner.OUT_DIR = floored_output_dir
base_runner.train_grouped_interval_ga = floored_train_function
base_runner.validate_grouped_gene = floored_validate_function
summary = base_runner.run()
```

따라서 기존 runner의 feature·label·gate·trade trace·rolling 청산을 그대로 재사용하면서 GA threshold domain만 교체했다.

## Smoke test

인공 4/4/3/3 그룹으로 다음을 검증했다.

```text
minimums = [2,2,2,2]
maximums = [3,3,2,2]
```

거부 사례:

```text
[1,2,2,2] → group_threshold_below_floor
[4,2,2,2] → group_threshold_above_cap
[2,2,3,2] → group_threshold_above_cap
```

Mutation 1,000회 반복에서도 범위 이탈 0건이었다.

## 실제 학습 threshold

| 종목 | split | G1 | G2 | G3 | G4 |
|---|---|---:|---:|---:|---:|
| AAP | train_1 | 3 | 3 | 2 | 2 |
| AAP | train_2 | 2 | 3 | 2 | 2 |
| AAP | train_3 | 2 | 2 | 2 | 2 |
| POWI | train_1 | 3 | 2 | 2 | 2 |
| POWI | train_2 | 2 | 3 | 2 | 2 |
| POWI | train_3 | 3 | 2 | 2 | 2 |

분포:

```text
G1: threshold 2 = 3개, threshold 3 = 3개
G2: threshold 2 = 3개, threshold 3 = 3개
G3: threshold 2 = 6개
G4: threshold 2 = 6개
```

- 1/N threshold: 0건
- 4/4 또는 3/3 threshold: 0건
- `group_threshold_check.csv` 72행 floor validation: 전부 TRUE
- `training_log.csv` 300세대 행 floor validation: 전부 TRUE

## 실행 무결성 메모

첫 시도에서 `runpy` namespace 교체가 base runner 함수 전역에 반영되지 않아 floored 학습이 실행되지 않았다. 이 시도는 결과로 사용하지 않았다.

- 직전 unfloored 산출물 중 변한 `summary.json` 한 파일을 사전 백업에서 복원
- 기존 manifest 재검증 전 항목 OK
- `importlib` 모듈 로더로 전환 후 새 경로·floored GA 주입을 smoke test
- 최종 결과는 `run_hybrid_group_test_2sym_floored_entry.py` 실행분만 사용
