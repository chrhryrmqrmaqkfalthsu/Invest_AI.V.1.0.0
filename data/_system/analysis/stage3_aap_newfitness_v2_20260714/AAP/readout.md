# AAP 새 fitness v2 — VM 6프로세스 / 노트북 최대 자원 독립 정식 실행

- 작업일: 2026-07-14
- 종목: AAP
- 규모: qualify population 100 / generations 40 × 3 fold
- Seed base: `2026071401`
- 시장 기준: 사용 가능한 root snapshot 마지막 거래일 `2026-07-10`
- source commit: `6338b91`
- VM: 독립 parent RNG + 로컬 6-process `fork`
- 노트북: 독립 parent RNG + 로컬 28-process `spawn`
- 장비 간 candidate-level 통신: 없음
- 최종 판정: **두 실행 모두 qualify 실패, all3=0 / all2=0**

## 1. 실행 전 gate

사용자가 시장 정보가 있는 날까지만 사용하도록 승인했으므로 wall-clock freshness 대신 root snapshot의 마지막 거래일을 고정 기준일로 삼았다.

```text
market available cutoff: 2026-07-10
market_history.csv SHA-256:
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv SHA-256:
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611

AAP OHLCV SHA-256:
6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717

sell_omen_scores.csv SHA-256:
d767a0ef73d6309a28f85e33292b80035252b9c5ca97f2c56684b89b3e43a923
```

두 장비에서 다음을 모두 통과했다.

- root 단일 source와 고정 SHA
- 필수 컬럼과 날짜 범위
- 외부 fetch·auto-regenerate 비활성
- `gene_scope='entry'`
- 실현손실 -1% 초과분 벌점
- 승 기준 `실현수익 > 0.5%`
- hard gate `trade_count >= 12 AND win_rate >= 60%`
- mutation hint는 fitness·gate가 아니라 interval width mutation에만 사용
- Stage 2 legacy 기본값 유지
- parent RNG / input-index merge 재현성 probe

노트북은 고정 source/data bundle을 한 번만 받은 뒤 standalone parent가 로컬 child 28개를 직접 관리했다. `.env`는 전송하지 않았고, snapshot-only 실행에서 네트워크 경로가 호출되면 즉시 실패하도록 `yfinance` fail-closed stub을 사용했다.

## 2. 실행 결과 두 개

| 실행 | 로컬 process | Start method | Wall time | 결과 |
|---|---:|---|---:|---|
| VM_6PROC | 6 | fork | 1,244.039초 | qualify 실패 |
| NOTEBOOK_MAX | 28 | spawn | 762.933초 | qualify 실패 |

노트북은 VM 대비:

```text
speedup: 1.6306×
wall time 감소: 38.67%
시간 차이: 481.106초
```

VM 첫 supervisor 시도는 GA 평가 전에 venv-only `PYTHONPATH`에서 `cloudpickle`을 찾지 못해 종료됐다. 출력 파일·평가 건수는 0이었고, 동일한 빈 출력 디렉터리와 동일 seed를 유지한 채 기존 user site-packages 경로를 추가해 정식 실행을 다시 시작했다. 위 wall time은 성공한 정식 실행만의 시간이다.

## 3. 최종 pass 분포

VM과 노트북의 집계는 동일하다.

```text
all3 = 0
all2 = 0
all1 = 191
all0 = 109
```

all1의 단일 통과 fold:

| 통과 fold | 후보 수 |
|---|---:|
| train_1 only | 52 |
| train_2 only | 78 |
| train_3 only | 61 |

따라서 모든 qualify pass 후보가 자기 fold 하나에서만 통과했다. 다른 fold로 이전되는 후보는 한 개도 없었다.

후속 단계:

```text
qualify all3: 0
entry survivor: 0
exit candidate: 0
validate survivor: 0
CE/BOIL candidate: 0
CE/BOIL zero: true
stop_reason: qualify_failed
```

## 4. 게이트 병목 분리

각 fold의 300개 cross-fold 후보를 다음 상호배타적 hard-gate 축으로 분해했다.

| Fold | 거래수 <12 | 거래수 충족·승률 <60% | 두 gate 통과 | Qualify pass |
|---|---:|---:|---:|---:|
| train_1 | 214 / 300 (71.33%) | 17 / 300 (5.67%) | 69 / 300 (23.00%) | 52 |
| train_2 | 212 / 300 (70.67%) | 5 / 300 (1.67%) | 83 / 300 (27.67%) | 78 |
| train_3 | 223 / 300 (74.33%) | 10 / 300 (3.33%) | 67 / 300 (22.33%) | 61 |

### 주 병목

```text
1위: 거래수 12 미달
2위: expectancy/member score 등 qualify 기준
3위: 거래수를 채운 뒤 승률 60% 미달
```

승 기준을 0.5%로 강화했지만, 승률 gate 자체가 주 병목은 아니었다. 거래수 기준을 채운 후보 중 승률 때문에 추가 탈락한 수는 train_1 17개, train_2 5개, train_3 10개뿐이다.

특히 예상과 달리 train_2가 hard gate에서 전멸하지 않았다. 83개가 거래수와 승률을 모두 통과했고 78개가 최종 train_2 qualify pass였다. 문제는 이들이 train_1·3으로 전혀 이전되지 않았다는 것이다.

## 5. 실현손실·MAE penalty

| Fold | 실현손실 감점 후보 | 감점 후보 평균 | MAE 감점 후보 | 감점 후보 평균 |
|---|---:|---:|---:|---:|
| train_1 | 206 (68.67%) | 8.899359 | 290 (96.67%) | 7.337504 |
| train_2 | 214 (71.33%) | 0.648503 | 292 (97.33%) | 0.558450 |
| train_3 | 298 (99.33%) | 1.581484 | 300 (100.00%) | 1.744734 |

해석:

- 실현손실 벌점은 실제로 강한 진화 압력으로 작동했다.
- train_3에서는 사실상 모든 후보가 실현손실 또는 MAE 감점을 받았다.
- train_1의 penalty 평균이 큰 이유는 다른 fold에서 생성된 후보 일부가 train_1에 적용될 때 큰 실현손실·MAE를 만든 cross-fold 꼬리 때문이다.
- penalty는 hard 제거가 아니라 주목표에서 독립 차감되며, hard gate 통과 여부와 별도다.

## 6. 거래수 분포와 하한 도피

| Fold | Min | Median | Max | 거래수 12~13 비율 |
|---|---:|---:|---:|---:|
| train_1 | 1 | 8 | 16 | 22.00% |
| train_2 | 1 | 5 | 16 | 24.00% |
| train_3 | 2 | 6 | 16 | 24.33% |

전체 후보에서도 12~13건 비중이 22~24%였고, fold-best 세 개는 모두 정확히 12건에 수렴했다. 새 하한을 10에서 12로 올리자 GA가 새 경계 12로 이동했다는 명확한 하한 도피 신호다.

## 7. Fold-best 거래와 새 fitness

| Fold | 거래 | 0.5% 초과 승 | 승률 | 주목표/일 | MAE 벌점 | 실현손실 벌점 | 최종 fitness |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 12 | 11 | 91.67% | 1.121410 | 0.140480 | 0.000000 | 0.980930 |
| train_2 | 12 | 11 | 91.67% | 1.631547 | 0.114022 | 0.055655 | 1.461870 |
| train_3 | 12 | 11 | 91.67% | 3.031212 | 0.129337 | 0.093479 | 2.808397 |

승/패 로그 검증:

```text
fold-best trade rows: 36
win threshold: pnl_pct > 0.5
win log mismatch: 0
```

두 장비의 진입일·체결일·청산일·가격·청산사유·보유일·실현수익·MAE·일수익·승패 결과는 36건 모두 정확히 같다.

청산 사유:

- train_1: `entry_interval_break` 12건
- train_2: `entry_interval_break` 12건
- train_3: `entry_interval_break` 11건, `entry_provisional_max_holding` 1건

## 8. Mutation bias와 interval width drift

Mutation hint는 세 fold 모두 `later`가 압도적이었다.

| Fold | Earlier hint | Later hint |
|---|---:|---:|
| train_1 | 325 | 3,775 |
| train_2 | 209 | 3,891 |
| train_3 | 57 | 4,043 |

평균 interval width 변화:

| Fold | MA trend | MACD hist | RSI | BB position | Volume ratio |
|---|---:|---:|---:|---:|---:|
| train_1 | -0.2175 | -0.4617 | +4.5719 | -0.1066 | +0.1283 |
| train_2 | +10.6658 | +0.4343 | -1.7055 | -0.2154 | +0.1405 |
| train_3 | -3.0950 | -0.4848 | +3.0209 | -0.0689 | -0.0911 |

train_2는 MA trend 폭을 크게 넓혔고, train_1·3은 MA/MACD/BB를 대체로 좁히면서 RSI 폭을 넓혔다. later hint는 fitness·승패·gate에 직접 들어가지 않고 mutation 방향에만 사용됐다.

## 9. 지난 A run과 비교

| 지표 | 지난 A | 새 fitness v2 | 변화 |
|---|---:|---:|---:|
| all3 | 0 | 0 | 0 |
| all2 | 44 | 0 | -44 |
| all1 | 164 | 191 | +27 |
| all0 | 92 | 109 | +17 |
| train_1 pass | 52 | 52 | 0 |
| train_2 pass | 65 | 78 | +13 |
| train_3 pass | 135 | 61 | -74 |

새 압력은 train_1의 통과 수를 바꾸지 않았고 train_2 단일-fold 적합 후보는 늘렸지만, train_3 통과 후보를 절반 이하로 줄였다. 가장 중요한 변화는 **all2 44개가 전부 사라진 것**이다.

즉 새 규칙은 손실 억제와 승 품질을 높인 대신 cross-fold 이전성을 더 약화시켰다. 현재 feature/strict-AND 표현에서 GA는 각 fold의 정확히 12개 고품질 거래로 더 강하게 특화됐다.

## 10. VM·노트북 재현성 판정

```text
PHENOTYPE_EXACT_HASH_ULP_DIFFERENCE
```

일치한 항목:

- 120개 세대의 best/mean fitness 수치
- 각 fold/rank의 population fitness
- cross-fold phenotype/metrics multiset
- pass vector multiset
- gate 병목 수치
- mutation summary 수치
- fold-best 36개 거래 결과
- all3/all2/all1/all0와 최종 결론

Byte-for-byte exact는 아니다.

```text
full rulebook float 차이 값 수: 574
최대 절대차: 1.4210854715202004e-14
```

Linux Python 3.10.12/fork와 Windows Python 3.10.11/spawn에서 일부 float의 마지막 비트가 달랐다. 예:

```text
weight_news_finance: ...6282 vs ...6280
RSI 경계: 약 1e-14 차이
```

이 때문에 train_2·3의 full-rulebook hash는 다르지만 신호·거래·fitness·gate 결과는 바뀌지 않았다.

Best hash:

| Fold | VM | Notebook |
|---|---|---|
| train_1 | `2b0b832b...0e67` | `2b0b832b...0e67` |
| train_2 | `ddcca16b...d54d` | `e0a4e36d...d0d6` |
| train_3 | `39a3ce72...ba15` | `72145009...07a8` |

따라서 이번 두 실행은 계산 의미와 투자 판정 수준에서는 동일하지만, full chromosome hash 수준의 exact match는 아니다.

## 11. 최종 해석

1. **주 병목은 승률이 아니라 거래수 12다.** 세 fold 모두 약 71~74%가 거래수에서 먼저 탈락했다.
2. **실현손실 벌점은 활성이다.** train_3 후보의 99.33%, train_1·2의 약 69~71%에 영향을 줬다.
3. **GA는 새 하한 12로 다시 수렴했다.** fold-best 세 개가 모두 정확히 12건이다.
4. **train_2 전멸 예상은 빗나갔다.** train_2 단독 pass는 78개로 이전 65개보다 늘었다.
5. **일반화는 더 나빠졌다.** all2가 44에서 0으로 줄고 모든 pass 후보가 한 fold에서만 생존했다.
6. 현재 규칙은 “적은 수의 매우 강한 거래”를 만드는 데 성공했지만, fold 공통 규칙을 만드는 데는 실패했다.

## 12. 산출물

```text
AAP/
├── VM_6PROC/
│   ├── readout.md
│   ├── SHA256SUMS.txt
│   ├── generation_best_fitness.jsonl
│   ├── ga_population_history.jsonl
│   ├── qualify_gate_bottleneck.json
│   ├── qualify_cross_fold_matrix.jsonl
│   ├── fold_best_trade_level.jsonl
│   └── official_final_summary.json
├── NOTEBOOK_MAX/
│   └── 동일 스키마
├── dual_comparison.json
├── readout.md
└── SHA256SUMS.txt
```

Per-run readout SHA:

```text
VM_6PROC/readout.md
b17afa29462c6fdbeb3fbefcce62107204da643e1523762a9581e4870951b3aa

NOTEBOOK_MAX/readout.md
9ff1051d67c2daaee4d2347d4441044fb3d1f62ae1d57e36c6679561062d5950
```

노트북 `SHA256SUMS.txt`는 Windows CRLF를 Linux LF로 정규화했다. 목록에 기록된 각 파일 hash는 변경되지 않았고 Linux에서 전부 재검증했다.

## 13. 보호 상태

시작·종료 SHA가 동일하다.

```text
.env
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

Daemon:

```text
PID: 494330
starttime_ticks: 36014393
상태: 실행 전후 동일
```

Source implementation commit:

```text
6338b91 AAP 새 fitness 정식 실행을 VM fork와 노트북 spawn 독립 병렬로 지원
```
