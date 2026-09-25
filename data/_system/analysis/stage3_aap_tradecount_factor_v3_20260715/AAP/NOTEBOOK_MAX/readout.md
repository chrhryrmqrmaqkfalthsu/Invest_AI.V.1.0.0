# AAP 거래수 factor 복원 재학습 검증 readout

- source commit: `e06b0add5149cf654223c105c16fe9994d7ae301`
- seed: `2026071401`
- host: `DESKTOP-TO74AR2`
- 실행: 독립 notebook parent + local `28` process
- inter-machine candidate communication: false
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- auto-fetch/regenerate: disabled
- 전체 소요시간: `877.9297504425049초`
- 판정: **FACTOR_RESTORE_INSUFFICIENT**

## 결론

거래수 factor는 코드와 실행 결과에서 정확히 활성화됐지만, 목표였던 fold-best의 `15~20건` 확산은 일어나지 않았다.

- 이전 v2 fold-best: `12 / 12 / 12`
- 이번 fold-best: `12 / 11 / 12`
- 실제 factor: `0.70 / 0.6125 / 0.70`
- strict-AND joint pass day: `25 / 29 / 32`

train_2가 11건으로 이동해 “정확히 12건”이라는 하드 경계 고정은 깨졌지만, 방향이 20건 쪽이 아니라 더 희소한 8~11 구간이었다. 따라서 연속 factor만으로는 넓은 support를 선택하게 만들기에 압력이 부족하다.

strict-AND raw joint pass day는 세 fold 모두 20일을 넘으므로 단순히 strict-AND 통과일이 12일뿐인 상황은 아니다. 다만 인접 신호일은 보유·청산·cooldown 중복으로 모두 실제 거래가 될 수 없으므로, joint pass day 수가 곧 최대 체결 거래수라는 뜻은 아니다.

all2 후보는 2개로 늘었지만 둘 다 `train_1=true / train_2=false / train_3=true`였다. train_2 일반화 병목은 해소되지 않았다.

## 정확한 실행 진입

`launch_command.json`의 `argv`와 환경을 재실행 원본으로 사용한다.

```powershell
$env:PYTHONPATH='C:\kingmaker_aap_tradecount_factor_v3_20260715\scripts\research\stage23_rework_20260713;C:\kingmaker\vendor;C:\kingmaker_aap_v2_swing_restore_20260715_r2\vendor'
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_aap_tradecount_factor_v3_20260715\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_aap_v2_20260714\data\_system\analysis\stage3_aap_newfitness_v2_20260714\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_aap_tradecount_factor_v3_20260715\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_proxy_for_notebook_independent_run", "starttime_ticks": "36014393", "state": "R"}' '--source-git-commit' 'e06b0add5149cf654223c105c16fe9994d7ae301'
```

실행 환경:

```text
PYTHONUTF8=1
PYTHONUNBUFFERED=1
OMP_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
MKL_NUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

후보 평가는 notebook 독립 parent가 생성한 로컬 28개 process에서만 수행됐다. Dask는 독립 parent를 시작하고 결과를 회수하는 제어 채널로만 사용됐으며 후보 계산·분배에는 사용되지 않았다.

## 이전 v2 직접 비교

| 지표 | 이전 v2 | 이번 factor 복원 |
|---|---:|---:|
| all3 / all2 / all1 / all0 | 0 / 0 / 191 / 109 | 0 / 2 / 192 / 106 |
| train_1 / train_2 / train_3 pass | 52 / 78 / 61 | 48 / 67 / 81 |
| fold-best 거래수 | 12 / 12 / 12 | 12 / 11 / 12 |
| fold-best fitness | 0.9809299162 / 1.4618697928 / 2.8083965875 | 0.6445069087 / 1.0460364481 / 2.0915756107 |
| 전체 소요시간(초) | 762.9326047897339 | 877.9297504425049 |

이번 fitness는 raw primary에 factor를 곱하므로 이전 거래수 중립 fitness와 절대값을 직접 우열 비교하면 안 된다. 비교 목적은 후보 선택과 거래수 분포 변화다.

## 최종 세대 best·mean fitness

| fold | best fitness | population mean fitness | best 거래수 | factor | raw primary | MAE 벌점 | 실현손실 벌점 |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 0.6445069087 | -219999999.69874397 | 12 | 0.7000 | 1.1214100249 | 0.1404801087 | 0.0000000000 |
| train_2 | 1.0460364481 | -339999999.5572147 | 11 | 0.6125 | 1.8426002549 | 0.0614493951 | 0.0211068129 |
| train_3 | 2.0915756107 | -169999999.07338226 | 12 | 0.7000 | 3.1040362759 | 0.0812497824 | 0.0000000000 |

mean fitness가 큰 음수인 이유는 실격 후보가 `-1e9`로 포함되기 때문이다.

## Fold-best 거래수·factor·strict-AND support

| fold | 거래수 | 실제 factor | 기대 factor | fitness | strict-AND joint pass day |
|---|---:|---:|---:|---:|---:|
| train_1 | 12 | 0.7000 | 0.7000 | 0.6445069087 | 25 |
| train_2 | 11 | 0.6125 | 0.6125 | 1.0460364481 | 29 |
| train_3 | 12 | 0.7000 | 0.7000 | 2.0915756107 | 32 |

activation probe에서 다음 anchor가 모두 통과했다.

```text
7 -> 0.0, 실격
8 -> 0.35
10 -> 0.525
12 -> 0.70
15 -> 0.8125
20 -> 1.00
81 -> 0.996
```

## Fold별 gate 병목·pass 거래수 분포

| fold | 후보 | 거래<8 | support 충족·승률<60 | 두 entry gate 통과 | 실현손실 감점 | MAE 감점 | qualify pass |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 300 | 30 (10.00%) | 118 (39.33%) | 152 (50.67%) | 204 (68.00%), 양수 평균 1.459697 | 258 (86.00%), 양수 평균 0.630259 | 48 |
| train_2 | 300 | 206 (68.67%) | 24 (8.00%) | 70 (23.33%) | 229 (76.33%), 양수 평균 0.503589 | 289 (96.33%), 양수 평균 0.568571 | 67 |
| train_3 | 300 | 170 (56.67%) | 37 (12.33%) | 93 (31.00%) | 290 (96.67%), 양수 평균 1.655062 | 300 (100.00%), 양수 평균 1.502085 | 81 |

### Pass 후보 거래수 histogram

```text
train_1: 8=1, 9=1, 11=5, 12=40, 13=1
train_2: 8=5, 10=12, 11=46, 12=3, 13=1
train_3: 8=3, 10=5, 11=4, 12=10, 13=27, 14=4, 15=12, 16=9, 17=5, 18=2
```

### Factor 구간별 전체 후보 / pass 후보

```text
train_1:
  전체: <8=30, 8~11=164, 12~19=106, 20+=0
  pass: 8~11=7, 12~19=41, 20+=0

train_2:
  전체: <8=206, 8~11=84, 12~19=10, 20+=0
  pass: 8~11=63, 12~19=4, 20+=0

train_3:
  전체: <8=170, 8~11=43, 12~19=87, 20+=0
  pass: 8~11=12, 12~19=69, 20+=0
```

어느 fold에서도 최종 cross-fold 후보에 20건 이상 거래가 없었다. 특히 train_2는 pass 67개 중 63개가 8~11건 구간이어서 가장 강한 희소성 병목이다.

## Cross-fold 일반화

```text
all3 = 0
all2 = 2
all1 = 192
all0 = 106
```

all2 후보 두 개의 pass vector는 동일하다.

```text
candidate 18e96b63...: train_1 PASS / train_2 FAIL / train_3 PASS
candidate 493e7be4...: train_1 PASS / train_2 FAIL / train_3 PASS
```

all2가 `0 -> 2`로 늘어난 것은 작은 개선이지만 train_2 병목은 그대로다. all3는 여전히 0이다.

## Trade-level 로그 검증

`fold_best_trade_level.jsonl`에는 총 35개 거래가 기록됐다.

```text
train_1: 12행
train_2: 11행
train_3: 12행
```

모든 행에 다음 필드가 존재한다.

- 진입 신호일·진입일·진입가격
- 청산일·청산가격·청산 사유
- 보유일
- 비용 차감 실현수익률
- MAE
- 일수익률
- `+0.5%` 승패
- 5-feature snapshot: `bb_position`, `ma_trend`, `macd_hist`, `rsi`, `volume_ratio`
- feature별 interval check
- fold-best 전체 fitness diagnostics

## 산출 로그

- `generation_best_fitness.jsonl`: 세대별 best/mean fitness, 거래수, 실제 factor
- `qualify_population_all.jsonl`: 최종 fold population 전체 diagnostics
- `qualify_cross_fold_matrix.jsonl`: 후보×fold pass, 거래수, factor, gate 실패
- `fold_best_trade_level.jsonl`: 상세 trade-level 로그와 joint-pass day
- `qualify_gate_bottleneck.json`: hard gate·penalty·factor-bin·pass histogram
- `trade_count_factor_comparison.json`: 이전 v2 직접 비교 원본
- `launch_command.json`: 재실행 가능한 argv·환경 원본

## 안전성

- manifest gate: `True`
- 보호 SHA 시작·종료 동일: `True`
- daemon PID/starttime 동일: `True`
- 병렬 재현성 probe: `True`
- factor activation probe: `True`
- profit concentration penalty absent probe: `True`
- legacy gene_scope defaults: `legacy`
- source git commit: `e06b0add5149cf654223c105c16fe9994d7ae301`

보호 파일 SHA:

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

Daemon:

```text
PID 494330
starttime_ticks 36014393
상태 유지
```

## 다음 단계 해석

이번 결과만 보면 concentration이나 cross-fold 항을 추가하기 전에, 거래수 압력 설계 자체를 한 번 더 분리 검증하는 편이 타당하다.

1. `8건` 하한이 train_2의 8~11건 희소 후보를 대량 허용했다.
2. factor가 raw primary에만 곱해지고 MAE·실현손실 벌점은 그대로 차감되므로, 높은 raw primary를 가진 11건 후보가 20건 방향 압력을 이길 수 있다.
3. strict-AND raw joint pass day는 충분하지만 실제 20건 이상 후보는 한 개도 없었다. 인접 신호·보유·cooldown 또는 interval 진화 구조가 실제 체결 support를 제한하는지 분해가 필요하다.

따라서 판정은 **FACTOR_RESTORE_INSUFFICIENT**다. 단순 factor 복원만으로는 12건 수렴과 희소 신호 선택을 해결하지 못했다.
