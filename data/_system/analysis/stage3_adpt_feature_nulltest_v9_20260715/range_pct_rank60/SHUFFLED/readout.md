# AAP EEC 집중도 벌점 v5 재학습 readout

- source commit: `e3bfb7c`
- seed: `2026071401`
- host: `DESKTOP-TO74AR2`
- 실행: notebook host-local `28` process
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- 변경 변수: entry-scope fitness에 EEC concentration multiplier 추가
- 불변: 진입/청산·should_buy·strict interval·legacy scheduling·mutation·fixed-notional accounting
- EEC: target `6.0`, floor `0.5`, cluster gap `8` trading days
- 판정: **EEC_PENALTY_EFFECTIVE** — fold-best EEC가 모든 fold에서 상승했고 최대 클러스터 비중도 모두 하락했다.

## 재실행 명령

전체 argv와 실행 관련 환경변수는 `launch_command.json`에 기록했다.

```powershell
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_adpt_feature_nulltest_v9\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_adpt_feature_nulltest_v9_20260715\range_pct_rank60\SHUFFLED' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"pid": 494330, "starttime_ticks": "36014393", "cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60"}' '--source-git-commit' 'e3bfb7c'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/83/197/20 |
| fold별 pass 수 | - | 162/86/115 |
| fold-best 거래수 | 20/15/13(±) | 17/11/18 |
| fold-best EEC | 2.30/2.53/3.70(±) | 7.111111/5.260870/8.757576 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 25.00%/27.27%/17.65% |
| fold-best fitness | ? | 2.0605537174729243/0.933518292737211/3.6794837281626918 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 17 | 0 | 7.111111 | 1.0 | 25.00% | `2022-07-29~2022-08-01:2(12.5%) ; 2022-09-19~2022-09-19:1(6.2%) ; 2022-10-24~2022-10-28:2(12.5%) ; 2022-12-06~2022-12-06:1(6.2%) ; 2023-01-06~2023-01-09:2(12.5%) ; 2023-03-09~2023-03-23:4(25.0%) ; 2023-04-19~2023-04-19:1(6.2%) ; 2023-05-16~2023-05-17:2(12.5%) ; 2023-05-31~2023-05-31:1(6.2%)` | `{"1": 13, "2": 3}` | `{"1": 26, "2": 3}` |
| train_2 | 11 | 0 | 5.260870 | 1.0 | 27.27% | `2023-07-17~2023-07-18:2(18.2%) ; 2023-08-29~2023-08-29:1(9.1%) ; 2023-11-21~2023-11-21:1(9.1%) ; 2024-01-17~2024-01-18:2(18.2%) ; 2024-02-05~2024-02-13:2(18.2%) ; 2024-03-18~2024-04-05:3(27.3%)` | `{"1": 9, "2": 2}` | `{"1": 18, "2": 2}` |
| train_3 | 18 | 0 | 8.757576 | 1.0 | 17.65% | `2024-08-27~2024-08-27:1(5.9%) ; 2024-09-24~2024-09-25:2(11.8%) ; 2024-10-17~2024-10-18:2(11.8%) ; 2024-11-21~2024-11-22:2(11.8%) ; 2024-12-19~2024-12-19:1(5.9%) ; 2025-01-16~2025-01-17:2(11.8%) ; 2025-04-04~2025-04-04:1(5.9%) ; 2025-04-21~2025-04-22:2(11.8%) ; 2025-05-07~2025-05-07:1(5.9%) ; 2025-05-22~2025-05-29:3(17.6%)` | `{"1": 11, "2": 6}` | `{"1": 22, "2": 6}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 6.422222 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.260870 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.260870 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.260870 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.260870 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.142857 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.000000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.000000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.000000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.000000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.000000 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 162 | 121 (40.33%) | 0 (0.00%) | 4.173795 | 1.000000 | `{"12_19": 115, "20_80": 92, "8_11": 88, "lt_8": 5}` / `{"12_19": 71, "20_80": 42, "8_11": 49}` |
| train_2 | 300 | 86 | 201 (67.00%) | 0 (0.00%) | 4.705018 | 1.000000 | `{"12_19": 108, "20_80": 115, "8_11": 73, "lt_8": 4}` / `{"12_19": 70, "20_80": 4, "8_11": 12}` |
| train_3 | 300 | 115 | 182 (60.67%) | 0 (0.00%) | 5.148660 | 1.000000 | `{"12_19": 116, "20_80": 175, "8_11": 5, "gt_80": 2, "lt_8": 2}` / `{"12_19": 66, "20_80": 47, "8_11": 2}` |

## Trade-level 로그

`fold_best_trade_level.jsonl`에는 진입/청산일·가격, 청산 사유, 보유일, 실현손익, MAE, +0.5% 승/패, entry-time 동시 포지션 수에 더해 다음 EEC 필드를 기록한다.

- `entry_fitness_effective_event_count`
- `entry_fitness_eec_multiplier`
- `entry_fitness_eec_cluster_index`
- `entry_fitness_eec_cluster_trade_share`
- `entry_fitness_eec_cluster_share_squared`
- `entry_fitness_eec_trade_share`

## 안전성

- manifest gate: True
- 보호 SHA 불변: True
- daemon 불변: True
- 병렬 재현성 probe: True
- EEC activation patch: `entry_scope_eec_penalty_v5_20260715`
- source git commit: `e3bfb7c`
