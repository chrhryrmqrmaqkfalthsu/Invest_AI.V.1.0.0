# AAP 정식 규모 Dask GA — VM 단독 vs 혼합 워커 bit-identical·성능 대조

- 작업일: 2026-07-14
- 대상: AAP `train_3` entry-scope GA
- Population: 100
- Generations: 40
- Seed: `2026071401`
- 각 실행 fitness 평가: 3,300회
- 최종 판정: **IDENTICAL / exact_match**
- 성능 판정: **혼합 B가 64.75% 느림**

## 1. 최종 실행 구성

### 실행 A — VM 단독

- Dask worker: `tcp://127.0.0.1:46481`
- worker nthreads: 8
- worker memory limit: 33,651,986,432 bytes
- 외부 loopback fitness service: 1개
- persistent spawn 평가 프로세스: 8개
- 평가 배정: VM 3,300회, 노트북 0회

### 실행 B — VM + 노트북

- VM: 8개 평가 프로세스
- Windows notebook: 28개 평가 프로세스
- notebook Dask worker: `tcp://127.0.0.1:60941`
- notebook worker reported address: `tcp://localhost:60941`
- notebook nthreads: 28
- notebook memory limit: 34,070,192,128 bytes
- 평가 배정:
  - VM: 742회, 22.48%
  - Notebook: 2,558회, 77.52%

두 실행 모두 후보 생성·tournament selection·crossover·mutation은 client/parent에서만 수행했다. Worker에는 fitness 평가만 전달했고 결과는 completion 순서와 무관하게 candidate input index 순서로 병합했다.

## 2. 최종 hash 판정

| 항목 | 실행 A | 실행 B | 결과 |
|---|---|---|---|
| Best chromosome | `27261017d6c00f92b9cd15b5564461b946fd62478dfeb685a9f094053586fe3a` | 동일 | PASS |
| Best result | `43822dd47ee547e08509f93d09abc1fbe89e351c7dc8a3e7c294743df33885a3` | 동일 | PASS |
| Fitness history | `acb9c696c673974a3788d22147e1f5a1a47e1facf8ec244bd4d7c7d9af7f6e80` | 동일 | PASS |
| Final population ordered | `8c4158218778f4cfec6992d2517e0439d7e474d9a02580c2a91f9bccfa68587e` | 동일 | PASS |
| Final population multiset | `48b01ef5880455461571fb684accb23fbafa47d348cdd19cd5b2f4d2a203133f` | 동일 | PASS |
| Overall | `44b197c9b234d6ecdd2d6c19442339713e6b95b889b1c9aced62f5dadce368e1` | 동일 | PASS |

추가 판정:

```text
best_fitness_bitwise_equal: true
best_parameters_equal: true
fitness_history_equal: true
final_population_ordered_equal: true
final_population_multiset_equal: true
first_difference: null
classification: exact_match
```

## 3. Best 결과

```text
Best fitness: 3.0184388997091576
IEEE-754 hex: 400825c34b39ccea
Best chromosome/parameter SHA:
27261017d6c00f92b9cd15b5564461b946fd62478dfeb685a9f094053586fe3a
```

Best fitness는 decimal 표시값뿐 아니라 IEEE-754 64비트 표현까지 동일하다.

## 4. Wall-clock 성능

| 항목 | 실행 A | 실행 B |
|---|---:|---:|
| GA wall-clock | 426.761421초 | 703.074500초 |
| Pool warm-up, 별도 | 3.186530초 | 23.751328초 |
| 평가 횟수 | 3,300 | 3,300 |
| 평균 evaluation time | 0.874893초 | 1.125669초 |
| p50 | 0.867606초 | 1.168605초 |
| p95 | 0.946433초 | 1.653807초 |
| 최대 | 1.312726초 | 2.433581초 |

성능 차이:

```text
B - A: 276.313079초
B / A: 1.647465배
A / B speedup ratio: 0.606993
wall-clock reduction: -64.746499%
classification: SLOWER
```

즉 노트북을 추가했지만 B는 A보다 **64.75% 더 오래 걸렸다.**

## 5. 노트북 자원 활용과 느려진 이유

실제 생성·사용된 child process:

- A VM: 8개 PID
- B VM: 8개 PID
- B notebook: 28개 PID

따라서 요청한 28개 notebook process 생성과 평가 분담은 수행됐다. Notebook은 2,558회, 전체의 77.52%를 평가했다.

그러나 service telemetry의 peak concurrent request는:

- A VM: 8
- B VM: 2
- B notebook: 14

Task 시간 합계 / wall-clock으로 계산한 유효 평균 동시성:

```text
A: 6.7996
B: 5.3329
```

B의 유효 평균 동시성이 A보다 약 21.57% 낮았다. Notebook 쪽 개별 평가 지연도 증가했고, 모든 세대에서 candidate index 순서를 보존하기 위한 batch/wave barrier가 가장 느린 task를 기다렸다. 하나의 Windows Dask worker 연결을 통해 28개 process service에 요청을 중계하는 구조의 직렬화·전송·barrier 비용이 코어 증가 이득을 초과했다.

따라서 현재 구성에서 notebook worker는 결과 정확성에는 문제가 없지만 **성능 가속용으로는 부적합**하다.

## 6. Retry·worker loss·memory pressure

| 점검 | 실행 A | 실행 B |
|---|---:|---:|
| Task re-execution | 0 | 0 |
| Service evaluation failure | 0 | 0 |
| Worker loss | 0 | 0 |
| Scheduler ERROR/WARNING 이벤트 | 0 | 0 |
| Memory spill to memory tier | 0 | 0 |
| Memory spill to disk | 0 | 0 |

네트워크 지연으로 인한 task 재시도, worker 이탈, memory spill 또는 재계산은 발생하지 않았다. 따라서 성능 저하는 장애·재시도 때문이 아니라 정상 평가 경로 자체의 overhead다.

## 7. 최초 정식 실행이 DIVERGENT였던 이유

RNG 격리 수정 전 정식 실행에서는 다음처럼 갈렸다.

```text
A overall:
b1fdac9938726f46e99c689d7412bac3e7bab447c8071ef518b4cd03eeb50da5

B overall:
eab097cb0dcf0005002653b2d55abea7aea7c167b0cbf4d3956f250bd97c1211

first difference:
offspring-1, candidate index 0, chromosome hash
```

초기 population 100개를 별도 대조한 결과:

- chromosome 동일
- fitness IEEE-754 bits 동일
- entry fitness diagnostics 동일
- entry-exit mutation hint 동일
- candidate runtime state 동일

따라서 worker 계산이나 부동소수점 차이가 원인이 아니었다.

Parent RNG state를 직접 hash한 결과:

```text
평가 전 Python random:
efdbf912d66a82d8d671866c07274114fffd711c0e38bd6059c79b38983ec7f3

A 평가 후:
035efe0e974484b30c760b77e7cd9fd4d977f82d35d0112e4cef5eb7830c3801

B 평가 후:
d1ed5ca86e628798904cdab07cb475b9143ab54156b2afd570ffebce1ab8cae1
```

NumPy RNG state는 A/B 모두 변하지 않았다.

원인은 `Client.submit(..., pure=False)`를 포함한 Dask 제출 과정이 client 프로세스의 전역 Python `random` state를 소비한 것이다. A와 B는 worker 수와 wave 수가 달라 서로 다른 횟수만큼 전역 RNG를 소비했고, 그 다음 offspring selection/crossover/mutation부터 chromosome이 갈렸다.

## 8. RNG 격리 수정

각 A/B GA 실행마다 다음 전용 RNG 객체를 생성했다.

```text
random.Random(2026071401)
```

GA 실행 동안 다음 모듈의 `random` 참조를 이 전용 객체로 교체했다.

```text
engine.learning.genetic.random
engine.learning.genetic_parallel.random
```

범위:

- random Rulebook 생성
- tournament selection
- crossover
- mutation
- mutation direction sampling

Dask background thread는 이 전용 객체를 알 수 없으므로 전역 `random` 소비가 GA stream에 영향을 줄 수 없다. 평가 batch 전후의 전역 Python·NumPy state도 추가로 복원했다.

수정 후:

- pop 8 / gen 3: IDENTICAL
- pop 100 / gen 40: IDENTICAL

## 9. 28코어 활용 구현

기존 Dask worker 프로세스는 daemon이라 내부에서 `multiprocessing` child를 만들 수 없었다.

```text
AssertionError: daemonic processes are not allowed to have children
```

또한 단일 28-thread worker 직접 평가는 Python GIL 때문에 사실상 약 1코어만 사용했다.

최종 방식:

1. 기존 Dask worker는 scheduler 통신과 task relay만 담당
2. Worker가 localhost에 독립 non-daemon fitness service subprocess 실행
3. Service가 `spawn` 기반 persistent process pool 소유
4. VM service 8개 process
5. Notebook service 28개 process
6. Dask task thread가 loopback TCP로 service에 평가 요청
7. Market/context payload는 worker로 scatter 후 service configure 시 한 번 전달

Dynamic Windows Dask worker 28개를 직접 scheduler에 붙이는 방식도 시험했지만, 각 worker가 광고한 동적 localhost port가 VM에서 reverse-forward되지 않아 폐기했다. 생성된 임시 worker와 process tree는 모두 종료했다.

## 10. 데이터·환경

```text
market_history.csv SHA-256:
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market rows: 1759
AAP dataframe rows: 1526
auto-fetch: false
worker local market file read: false
```

환경:

| Worker | Python | OS | NumPy | pandas |
|---|---|---|---|---|
| VM | 3.10.12 | Linux | 2.2.6 | 2.3.3 |
| Notebook | 3.10.11 | Windows | 2.2.6 | 2.3.3 |

Python patch와 OS가 다르지만 최종 3,300회×2 GA 상태는 bit-identical했다.

## 11. 산출물

```text
data/_system/analysis/stage3_aap_dask_official_vm_vs_mix_20260714/final_result.json
data/_system/analysis/stage3_aap_dask_official_vm_vs_mix_20260714/divergence_diagnosis.json
data/_system/analysis/stage3_aap_dask_official_vm_vs_mix_20260714/readout.md
```

핵심 코드:

```text
engine/learning/entry_fitness_threadsafe.py
engine/learning/dask_fitness_service.py
engine/learning/dask_process_fitness.py
scripts/research/run_dask_worker_mix_ga_official.py
scripts/research/run_dask_worker_mix_ga_official_rng_guard.py
scripts/research/diagnose_official_initial_state_divergence.py
scripts/research/diagnose_official_parent_rng_state.py
```

## 12. 최종 결론

### 재현성

**IDENTICAL / exact_match**

VM 단독과 VM+노트북 혼합의 best chromosome, best result, 전 세대 fitness history, final population ordered/multiset, overall SHA가 완전히 일치했다.

### 성능

**혼합 B가 64.75% 느림**

Notebook은 평가의 77.52%를 담당하고 28개 process를 실제 사용했지만, 단일 Dask relay 연결과 batch barrier 비용 때문에 유효 평균 동시성이 오히려 낮아졌다. 현재 환경에서는 notebook을 결과 검증용으로는 신뢰할 수 있으나 정식 GA 가속에는 사용하지 않는 것이 수익성·안정성 측면에서 우선이다.
