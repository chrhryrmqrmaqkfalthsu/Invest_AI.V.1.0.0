# AAP 소규모 VM-only vs VM+Notebook Dask GA 재현성 대조

- 날짜: 2026-07-14
- 대상: AAP `train_3` entry-scope
- 설정: population 8, generations 3, seed `2026071401`
- 판정: **IDENTICAL**

## 실행 구성

- A: VM worker `tcp://127.0.0.1:46481`, fitness 평가 29회
- B: VM 16회 + Windows notebook 13회
- Parent만 candidate 생성·selection·crossover·mutation·RNG 소비
- Worker는 fitness 평가만 수행
- 결과는 candidate input index 순서로 병합
- Worker당 동시 평가 최대 1개

## Hash 대조

| 항목 | A | B |
|---|---|---|
| Best chromosome | `cb3593b135eb19d9388e5ba8548cc7d116c935b330116eb717e9abe4e33d769a` | 동일 |
| Best result | `965f4122b287eedab05520319ec48bf980b513aca35ad29cc7e068eb43f0591c` | 동일 |
| Fitness history | `15cddaac93eb8335ead7fd5f95317d687432c5d78a7389d95d796b0c01b900f6` | 동일 |
| Final population ordered | `01c0eab42ca47b27bb1975b589942bdc368774c36ece7a9327523eb2a6eda4c1` | 동일 |
| Final population multiset | `648f23bf5778c325124c2152eea073ebf1994090ff47ef54496b0e9fdbfd0924` | 동일 |
| Overall | `4c8029646164b0eb529a0acac9839f2202cb963b5f5ae03f08be899cf6500eb5` | 동일 |

## Best 결과

```text
fitness: 1.2996642847469069
float hex: 3ff4cb6cc6ec4670
chromosome: cb3593b135eb19d9388e5ba8548cc7d116c935b330116eb717e9abe4e33d769a
```

주요 entry interval:

```text
ma_trend:    -15.6876004526 ~ 1.2572416179
macd_hist:   -1.5669739578 ~ 0.7730961154
RSI:          26.5006223101 ~ 42.5357511908
BB position:   0.0562497899 ~ 0.6528878698
volume ratio:  0.8980575392 ~ 1.3756284340
joint support: 22
```

전체 canonical parameters는 다음 파일에 보존했다.

```text
data/_system/analysis/stage3_aap_dask_worker_mix_probe_20260714/best_parameters_full.json
```

## 세대 history

| Gen | Best | Best hex | Mean | Mean hex |
|---:|---:|---|---:|---|
| 1 | -0.13875506233064183 | `bfc1c2b9d36ea984` | -625000000.2787452 | `c1c2a05f2023adec` |
| 2 | 1.2996642847469069 | `3ff4cb6cc6ec4670` | -749999999.8548863 | `c1c65a0bbfed6cea` |
| 3 | 1.2996642847469069 | `3ff4cb6cc6ec4670` | -624999999.5843227 | `c1c2a05f1fcacb16` |

Best/mean은 decimal뿐 아니라 IEEE-754 bits까지 동일했다.

## 진단

```text
overall_sha_equal: true
best_fitness_bitwise_equal: true
best_parameters_equal: true
fitness_history_equal: true
final_population_ordered_equal: true
first_difference: null
classification: exact_match
```

Fitness 값, 부동소수점 말단, chromosome, 최종 population 순서에서 차이가 없었다.

## Worker 환경

| Worker | Python/OS | NumPy | pandas |
|---|---|---|---|
| VM | 3.10.12 / Linux | 2.2.6 | 2.3.3 |
| Notebook | 3.10.11 / Windows | 2.2.6 | 2.3.3 |

Python patch와 OS가 다르지만 이번 29개 대응 fitness 평가와 3세대 상태는 비트 단위로 같았다.

## 데이터

```text
market_history.csv SHA-256:
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
```

Context는 client에서 준비해 atomic `Client.scatter`로 전달했다. Worker local market file read와 auto-fetch는 없었다.

## Harness 수정

성공 실행 전 다음 두 전송 계층 문제를 수정했다.

1. Scheduler가 Rulebook/entry-domain 객체를 역직렬화할 때 `engine`을 찾지 못함
   - plain dict/list 전송 후 worker/client에서 객체 복원
2. Dask `scatter(dict)`가 key/value를 분해해 Windows worker가 VM의 localhost dependency를 기다림
   - dict를 atomic object로 scatter하고 candidate envelope도 worker별로 직접 배치

이는 GA 결과 차이가 아니라 성공 실행 전 harness 문제다.

## 산출물

```text
data/_system/analysis/stage3_aap_dask_worker_mix_probe_20260714/result.json
data/_system/analysis/stage3_aap_dask_worker_mix_probe_20260714/best_parameters_full.json
data/_system/analysis/stage3_aap_dask_worker_mix_probe_20260714/readout.md
scripts/research/stage23_rework_20260713/scripts/research/run_dask_worker_mix_ga_probe.py
scripts/research/stage23_rework_20260713/scripts/research/run_dask_worker_mix_ga_probe_launch.py
```

이번 결과는 실제 AAP entry fitness의 소규모 probe에서 완전 재현됐다는 증거다. 정식 population 100·40세대 전체까지 일반화하려면 별도 전체 hash 대조가 필요하다.
