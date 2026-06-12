# CRWD·MPC 3단 실험 시퀀스 최종 보고 — LASR 재현

생성 시각: 2026-06-12 KST  
Run ID: `20260612_2005`  
목적: LASR에서 수행한 3단 실험을 CRWD·MPC에 동일 조건으로 재현해 방법론 재현성을 확인한다.

주의: 실험 2(역방향)와 실험 3(청산 GA)은 미래 구간이 포함된 진단용 실험이다. 실거래 OOS 성과로 해석하면 안 된다.

## Phase 0 — 자원·병렬·hash 확인

- 실행 방식: CRWD → MPC 순차 실행. 종목 병렬은 자원 경합을 피하기 위해 사용하지 않았다.
- 청산 GA 내부 워커: 종목당 3개. nproc 8 이하로 제한.
- 실행 전후 `vmstat 1 5`: swap-in/swap-out `si=0`, `so=0`.
- 실행 후 리소스: MemAvailable 약 30GiB, root disk 67G 여유, live runner PID `1102653` 유지.
- 실행 조건: `live_hard_stop_guard=True`, `t_plus_1_open`, `conservative_core`, `fold_end_mark_to_market`, `fitness_mode=swing`.
- manifest hash 재확인:
  - CRWD: `b00e0b2a...`
  - MPC: `6f39b3ba...`
- 진입 잠금 검산: CRWD/MPC GA top1~top3 모두 non-exit field diff 0개.

## 실험 1 — 22년산 정방향

| 종목 | 후보 | pass 2022 | pass 2023 | pass 2024 | pass 2025H2 | general3 | all4 | general pass 분포 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| LASR | 100 | 1 | - | - | - | 0 | 0 | 0:31, 1:65, 2:4, 3:0 |
| CRWD | 100 | 8 | 47 | 32 | 39 | 0 | 0 | 0:30, 1:53, 2:17, 3:0 |
| MPC | 100 | 90 | 68 | 37 | 92 | 23 | 20 | 0:1, 1:26, 2:50, 3:23 |

대표 청산값:

| 종목 | 대표 hash | all4 | general pass | max_holding | exit_strategy | breakeven | sell_omen | 비고 |
|---|---|---:|---:|---:|---|---|---|---|
| LASR | `59d10a89` | False | 2 | 29 | trailing | False | True | 최상위도 2022 exp 음수 |
| CRWD | `a768c64f` | False | 2 | 16 | trailing | True | True | 2022 exp 음수 |
| MPC | `268da5af` | True | 3 | 16 | fixed | True | False | all4 생존 |

판정: LASR·CRWD는 22년산 정방향 all4=0으로 기존 발견과 부합한다. 그러나 MPC는 22년산만으로 all4=20이 나와, “단일구간 학습은 다년 생존 못 함”은 종목 불문 명제가 아니다.

## 실험 2 — 25H2 역방향

| 종목 | 후보 | pass 2022 | pass 2023 | pass 2024 | pass 2025H2 | general3 | all4 | general pass 분포 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| LASR | 100 | 80 | - | - | - | 0 | 0 | 0:15, 1:77, 2:8, 3:0 |
| CRWD | 100 | 78 | 29 | 35 | 44 | 13 | 7 | 0:10, 1:51, 2:26, 3:13 |
| MPC | 100 | 76 | 44 | 57 | 91 | 31 | 27 | 0:11, 1:32, 2:26, 3:31 |

22년산 대비 역방향 비교:

| 종목 | 22년산 all4 | 25H2산 역방향 all4 | 차이 | 해석 |
|---|---:|---:|---:|---|
| LASR | 0 | 0 | 0 | LASR은 양방향 모두 다년 생존 실패 |
| CRWD | 0 | 7 | +7 | 25H2산 일부는 과거 구간까지 생존 |
| MPC | 20 | 27 | +7 | 양방향 모두 다년 생존 개체 다수 |

대표/분포 청산값:

| 종목 | 대표 hash | all4 | max_holding | exit_strategy | breakeven | sell_omen | all4 내 주요 분포 |
|---|---|---:|---:|---|---|---|---|
| LASR | `de9eb672` | False | 7 | fixed | True | True | all4 없음 |
| CRWD | `169f2e8e` | True | 19 | trailing | True | True | max_holding 19일 4/7, fixed 4/7 |
| MPC | `9608b133` | True | 27 | fixed | True | True | max_holding 27일 16/27, fixed 25/27 |

판정: CRWD와 MPC는 LASR과 다르게 역방향 all4 생존자가 있다. 특히 MPC는 정방향/역방향 모두 생존자가 많아, 종목 자체가 더 안정적인 규칙 공간을 가진 것으로 보인다.

## 실험 3 — 청산 GA, 라이브 매수 룰 고정

fitness: `avg_exp + 2.0*min_exp - 0.15*stdev_exp - 0.20*avg_abs_dd - 0.25*worst_abs_dd - 5.0*negative_period_count`

### CRWD baseline vs GA top1

| variant | composite | avg_exp | min_exp | worst_abs_dd | trades | hash |
|---|---:|---:|---:|---:|---:|---|
| baseline | -15.1466 | 3.9335 | -2.2765 | 26.6410 | 18 | `471438f3` |
| GA top1 | 10.2546 | 6.0316 | 4.8268 | 16.1625 | 19 | `de38cbfa` |

| period | baseline exp/DD/trades | GA top1 exp/DD/trades |
|---|---|---|
| 2022 | 1.5931 / 0.0000 / 1 | 8.0848 / 0.0000 / 1 |
| 2023 | 16.1381 / 0.0000 / 3 | 5.9974 / 0.0000 / 3 |
| 2024 | 0.2791 / -9.1493 / 7 | 4.8268 / -7.8638 / 8 |
| 2025H2 | -2.2765 / -26.6410 / 7 | 5.2174 / -16.1625 / 7 |

수렴 청산값:

| variant | max_holding | exit_strategy | breakeven | sell_omen | take_profit_atr | trailing_atr | stop_loss_atr |
|---|---:|---|---|---|---:|---:|---:|
| baseline | 28 | trailing | True | False | 2.775 | 2.684 | 3.405 |
| GA top1 | 14 | trailing | False | False | 1.500 | 1.822 | 2.061 |
| GA top2 | 14 | trailing | False | True | 1.514 | 1.822 | 2.061 |
| GA top3 | 14 | trailing | False | False | 2.945 | 1.837 | 2.061 |

### MPC baseline vs GA top1

| variant | composite | avg_exp | min_exp | worst_abs_dd | trades | hash |
|---|---:|---:|---:|---:|---:|---|
| baseline | 2.7140 | 5.7219 | 1.3920 | 14.2735 | 19 | `e1ced600` |
| GA top1 | 11.3892 | 6.4062 | 4.4165 | 9.5877 | 22 | `869f36d1` |

| period | baseline exp/DD/trades | GA top1 exp/DD/trades |
|---|---|---|
| 2022 | 3.1293 / -14.2735 / 5 | 5.2927 / -9.5877 / 6 |
| 2023 | 1.3920 / -9.7051 / 4 | 4.4165 / -7.0380 / 4 |
| 2024 | 11.1463 / 0.0000 / 2 | 9.4893 / 0.0000 / 2 |
| 2025H2 | 7.2199 / -9.1536 / 8 | 6.4265 / -6.6818 / 10 |

수렴 청산값:

| variant | max_holding | exit_strategy | breakeven | sell_omen | take_profit_atr | trailing_atr | stop_loss_atr |
|---|---:|---|---|---|---:|---:|---:|
| baseline | 30 | fixed | False | True | 4.051 | 2.125 | 2.961 |
| GA top1 | 30 | hybrid | False | True | 5.000 | 1.295 | 2.139 |
| GA top2 | 30 | hybrid | False | False | 5.000 | 1.364 | 2.139 |
| GA top3 | 30 | hybrid | False | False | 5.000 | 1.364 | 2.196 |

### LASR 비교 기준

| variant | composite | avg_exp | min_exp | worst_abs_dd | trades | max_holding | exit_strategy |
|---|---:|---:|---:|---:|---:|---:|---|
| LASR baseline | -20.7889 | 2.0635 | -4.6367 | 22.1990 | 22 | 30 | hybrid |
| LASR GA top1 | 18.0800 | 7.6605 | 6.3021 | 6.1987 | 20 | 16 | fixed |

## 3종목 종합 비교

| 종목 | 22년산 all4 | 25H2 역방향 all4 | 청산 GA composite 개선 | baseline min_exp → GA min_exp | worst_abs_dd 개선 | GA max_holding | GA exit_strategy |
|---|---:|---:|---:|---|---|---:|---|
| LASR | 0 | 0 | -20.7889 → 18.0800 | -4.6367 → 6.3021 | 22.1990 → 6.1987 | 16 | fixed |
| CRWD | 0 | 7 | -15.1466 → 10.2546 | -2.2765 → 4.8268 | 26.6410 → 16.1625 | 14 | trailing |
| MPC | 20 | 27 | 2.7140 → 11.3892 | 1.3920 → 4.4165 | 14.2735 → 9.5877 | 30 | hybrid |

## 결론

1. **청산 GA 개선은 3종목 모두에서 재현됐다.** LASR, CRWD, MPC 모두 baseline 대비 composite가 개선됐고, min_exp가 양수화 또는 개선됐으며, worst_abs_dd도 낮아졌다. 따라서 “매수 고정 + 매도 전용 GA”는 backlog 블록 1의 유력 방법론으로 승격할 근거가 생겼다.

2. **단일구간 학습의 다년 생존 실패는 종목 불문으로 재현되지 않았다.** LASR은 정방향/역방향 모두 all4=0, CRWD는 정방향 all4=0이지만 역방향 all4=7, MPC는 정방향 all4=20·역방향 all4=27이다. 즉 LASR의 실패는 종목 특성이 섞여 있으며, MPC는 단일구간 학습에서도 다년 생존 개체가 다수 나온다.

3. **청산폭은 종목별로 따로 학습해야 한다.** 수렴한 max_holding이 LASR 16일, CRWD 14일, MPC 30일로 갈렸다. exit_strategy도 LASR fixed, CRWD trailing, MPC hybrid로 다르다.

한 줄 결론: **LASR의 “청산 GA 개선”은 CRWD·MPC에서도 재현됐지만, “단일구간은 다년 생존 못 함”은 CRWD/MPC에서 완전 재현되지 않았고 특히 MPC는 반례에 가깝다.**

## 산출물

- `exp_crwd_multiyear_20260612_2005/`
- `exp_crwd_reverse_20260612_2005/`
- `exp_crwd_exitga_20260612_2005/`
- `exp_mpc_multiyear_20260612_2005/`
- `exp_mpc_reverse_20260612_2005/`
- `exp_mpc_exitga_20260612_2005/`
- 통합 실행: `exp_crwd_mpc_sequence_20260612_2005/`

## git status

보고서 작성 직전 상태: 실험 산출물 디렉터리만 untracked. 라이브 엔진·parameters.json·manifest·positions·trade_log·market_state 변경 없음.
