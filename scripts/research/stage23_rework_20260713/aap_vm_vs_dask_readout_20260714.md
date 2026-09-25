# AAP VM 6워커 vs Dask 노트북 재현성 대조 readout

- 대상: AAP 단일 종목
- seed: `2026071401`
- 규모: qualify population 100 / generations 40, 이후 entry 100/50·exit 14-field·validate
- 코드: N_min=10 AND 승률 60% 결합 gate 반영
- 판정: **`DIVERGENT`**
- 최초 갈림: **STEP 0 환경 preflight, generation 0 이전**

## 1. 결론

A의 VM 로컬 6프로세스 실행은 정상 완료됐다. B의 Dask 노트북 실행은 두 worker 모두 NumPy·pandas·프로젝트 `engine` 모듈이 없고, Windows worker의 scatter 주소도 VM에서 접근되지 않아 fail-closed됐다.

따라서 요청된 hash 네 항목은 A에는 존재하지만 B에는 생성되지 않았다. B hash가 없으므로 `IDENTICAL` 판정은 불가능하며, 환경 차이로 인한 **`DIVERGENT`**로 판정한다.

이 판정은 “값이 조금 달랐다”는 의미가 아니라 **노트북 실행 환경이 계산 전제조건을 만족하지 않아 generation 0에도 진입하지 못했다**는 의미다.

## 2. STEP 0 — worker 및 환경

확인된 주소:

- `tcp://127.0.0.1:35761` — 8 threads
- `tcp://127.0.0.1:60941` — 28 threads

주소 2개, advertised thread 총 36개였다.

| 환경 | Python | OS | NumPy | pandas | Dask/distributed | engine |
|---|---|---|---|---|---|---|
| VM A | 3.10.12 | Linux x86_64 | 2.2.6 | 2.2.3 | client bridge 2026.7.0 | 있음 |
| worker 35761 | 3.10.12 | Linux | 없음 | 없음 | 2026.7.0 | 없음 |
| worker 60941 | 3.10.11 | Windows | 없음 | 없음 | 2026.7.0 | 없음 |

사전 DIVERGENT 신호:

- VM Python 3.10.12 vs notebook Python 3.10.11
- Linux vs Windows
- 두 worker 모두 NumPy/pandas 없음
- 두 worker 모두 프로젝트 `engine` import 불가

## 3. 데이터 scatter 검증

root 고정값:

```text
market_history.csv
SHA-256: 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
bytes: 276656
```

- worker 35761: scheduler 경유 scatter 성공, worker 계산 SHA 완전 일치
- worker 60941: advertised 주소 연결 30초 timeout으로 scatter 실패
- 두 worker broadcast scatter: 180초 timeout
- worker local file read: 없음
- auto-fetch/재생성: 없음

Windows worker는 scheduler side-channel로 환경 결과를 돌려줄 수는 있었지만 실제 데이터 scatter/gather 경로는 사용할 수 없었다.

## 4. A — VM 로컬 6워커 ground truth

### 실행 상태

- 병렬 축: GA population fitness evaluation
- RNG: 부모 프로세스에서만 소비
- 병합: candidate input index 순서
- qualify 3개 fold: 각 40/40세대 완료
- 전체 final population: 300개 보존
- cross-fold matrix: 900행
- 실행시간: 1253.69초
- manifest snapshot gate: 통과
- 보호 SHA: 시작·종료 동일
- daemon PID 494330: 동일 starttime 유지

### Qualify 결과

| 분포 | 후보 수 |
|---|---:|
| all3 | 0 |
| all2 | 44 |
| all1 | 164 |
| all0 | 92 |

fold별 pass:

- train_1: 52 / 300
- train_2: 65 / 300
- train_3: 135 / 300

all2 44개는 전부 `train_1 + train_3` 통과, `train_2` 실패 조합이다. `all3=0`이므로 Entry/Exit/Validate는 실행되지 않았다.

## 5. N_min=10 gate 효과

기존 1건·100% best 도피는 사라졌다. 세 fold best가 모두 정확히 10건을 거래했다.

### 실패 원인 분리

| fold | 거래수 <10 | 승률 <60 | 둘 다 실패 | 거래수만 실패 | 승률만 실패 | 결합 gate 통과 |
|---|---:|---:|---:|---:|---:|---:|
| train_1 | 48 | 202 | 19 | 29 | 183 | 69 |
| train_2 | 145 | 165 | 85 | 60 | 80 | 75 |
| train_3 | 115 | 87 | 62 | 53 | 25 | 160 |

`qualify_gate_bottleneck.json`의 `win_rate_gate_disqualified`는 하위 호환 alias라 거래수+승률 결합 gate 전체를 의미한다. 위 표는 matrix의 `trade_count`와 `win_rate_pct`를 다시 분해한 값이다.

### 새 도피 신호 — 10~11건 경계 집중

결합 gate 통과 개체 중 거래수 10~11건 비율:

- train_1: 61 / 69 = 88.41%
- train_2: 61 / 75 = 81.33%
- train_3: 121 / 160 = 75.63%

최종 qualify pass 개체 중 거래수 10~11건 비율:

- train_1: 52 / 52 = 100.00%
- train_2: 59 / 65 = 90.77%
- train_3: 103 / 135 = 76.30%

따라서 N_min=10은 극소수 거래 도피를 차단했지만 GA가 새 하한인 10~11건에 강하게 몰렸다. 결합 gate는 효과가 있었으나 **경계 최적화라는 새 도피 신호가 확인됐다.**

## 6. Fold-best trade-level

| fold | hash | 거래 | 승률 | 평균 하루당 수익 | worst MAE | MAE -2% 이탈 | 청산 |
|---|---|---:|---:|---:|---:|---:|---|
| train_1 | `55f85e98...f72ce60` | 10 | 100% | 1.284088% | -1.949435% | 0 | interval-break 10/10 |
| train_2 | `38ddb0eb...ebbb09` | 10 | 80% | 1.800939% | -2.635806% | 1 | interval-break 10/10 |
| train_3 | `3a76dbfa...3c680` | 10 | 100% | 3.473461% | -1.109243% | 0 | interval-break 10/10 |

세 best 모두 최소거래수 10건에 정확히 붙었고 청산은 30/30 전부 interval-break였다.

## 7. Mutation 편향

later 방향 개별 width mutation 정합률:

- train_1: 87.15%
- train_2: 85.11%
- train_3: 87.77%

mutation 편향은 계속 작동했다. earlier/later 정보는 fitness·승패·gate·qualify pass에 사용되지 않았다.

## 8. A canonical hash

canonicalization:

```text
json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)
UTF-8 → SHA-256
```

| fold | best chromosome hash | 40세대 best/mean history SHA | final population hash·fitness·rank 순서 SHA |
|---|---|---|---|
| train_1 | `55f85e98e54ffd113908f3dd76ae8576692e9813e66ab647f8b7debd5f72ce60` | `c85cf639e8890066b08d445beaa9b8334353b6106104b59208d8d69e4276ac22` | `0ae621fd28b60bb2702fdede34e6561cdb77e2582faee0ec65efaedc827cb620` |
| train_2 | `38ddb0eb36688984cefe97a6de47382a928983b671e5b6eba2dca88099ebbb09` | `68776072ea3a6756aada877060713d24d55df0da66ced83fbce5f05df0da992c` | `2434ead0b4baade3fa751c6942b71fc31bdbcc3edc2c1a4743a3ade752e7bb7e` |
| train_3 | `3a76dbfadabbe609eb51b59156ac97f9f81503ab8f7c12d120ee7c974a03c680` | `5d2871e56e44c603e7e44d77355db2d714fbbb5a50c969e99bb1b6881f4a9593` | `51599325e7962ac3f3905604c8be92d8234de00c8bfdc22edd2f7786e7c064a3` |

qualify 분포 SHA:

```text
849649e3493acbe2ce50ffe3928cd88aff9f127f0674844057e516ccb113fb81
```

A composite SHA:

```text
9af73c7c26b0868c016c1816cc0dcb7a68506c7b0c50dd183860c4c314b0a42f
```

## 9. 요청 항목별 A/B 대조

| 항목 | A | B | 판정 |
|---|---|---|---|
| fold별 best chromosome hash | 있음 | 없음 | DIVERGENT — B preflight 실패 |
| 전 세대 best/mean history | 있음 | 없음 | DIVERGENT — B preflight 실패 |
| final population hash·fitness·순서 | 있음 | 없음 | DIVERGENT — B preflight 실패 |
| all3/all2/all1/all0 | 0/44/164/92 | 없음 | DIVERGENT — B preflight 실패 |

최초 갈림 시점은 특정 GA 세대가 아니라 **generation 0 이전 환경·scatter gate**다.

## 10. Fallback

로컬 fallback 경로는 존재한다.

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aap_newfitness_launch.py
```

그러나 fallback을 실행하면 “노트북 실행 아님”이며 A를 중복 수행할 뿐이다. 이번 B에는 발동하지 않았고 B 결과를 노트북 실행으로 오인하지 않도록 `is_notebook_execution=false`로 기록했다.

## 11. 최종 판정

**`DIVERGENT`**

노트북 Dask worker는 현재 신뢰할 수 없다. 환경을 VM과 완전히 동일하게 맞추고, Windows worker의 routable worker address/scatter 경로를 복구한 뒤에만 다시 hash 대조할 수 있다.
