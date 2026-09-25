# AAP EEC 집중도 벌점 v5 재학습 readout

- source commit: `1b58d1f`
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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_multiticker_v5\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_multiticker_v5_probe_20260715\ADPT' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' '1b58d1f'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 1/24/248/27 |
| fold별 pass 수 | - | 117/76/106 |
| fold-best 거래수 | 20/15/13(±) | 16/19/22 |
| fold-best EEC | 2.30/2.53/3.70(±) | 5.333333/6.563636/6.451613 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 25.00%/21.05%/25.00% |
| fold-best fitness | ? | 1.2753044838375789/1.557111360383653/2.7638802873261747 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 16 | 0 | 5.333333 | 1.0 | 25.00% | `2022-09-22~2022-09-28:3(18.8%) ; 2022-12-30~2023-01-09:3(18.8%) ; 2023-03-06~2023-03-06:1(6.2%) ; 2023-03-22~2023-03-30:3(18.8%) ; 2023-04-19~2023-04-27:2(12.5%) ; 2023-05-17~2023-05-22:4(25.0%)` | `{"1": 9, "2": 4, "3": 2, "4": 1}` | `{"1": 20, "2": 5, "3": 2, "4": 1}` |
| train_2 | 19 | 0 | 6.563636 | 1.0 | 21.05% | `2023-07-10~2023-07-20:3(15.8%) ; 2023-08-30~2023-08-30:1(5.3%) ; 2023-09-20~2023-09-22:2(10.5%) ; 2023-11-21~2023-11-28:2(10.5%) ; 2023-12-18~2023-12-18:1(5.3%) ; 2024-01-31~2024-02-16:4(21.1%) ; 2024-03-15~2024-04-05:4(21.1%) ; 2024-05-01~2024-05-02:2(10.5%)` | `{"1": 15, "2": 4}` | `{"1": 30, "2": 5}` |
| train_3 | 22 | 0 | 6.451613 | 1.0 | 25.00% | `2024-07-02~2024-07-12:5(25.0%) ; 2024-09-23~2024-09-25:3(15.0%) ; 2024-10-18~2024-10-21:2(10.0%) ; 2024-11-06~2024-11-06:1(5.0%) ; 2024-12-04~2024-12-04:1(5.0%) ; 2025-01-02~2025-01-02:1(5.0%) ; 2025-01-27~2025-01-27:1(5.0%) ; 2025-04-22~2025-04-23:2(10.0%) ; 2025-05-23~2025-06-02:4(20.0%)` | `{"1": 11, "2": 7, "3": 2}` | `{"1": 23, "2": 7, "3": 2}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.333333 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.914286 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.451613 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.451613 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.451613 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.451613 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 117 | 170 (56.67%) | 0 (0.00%) | 3.261024 | 1.000000 | `{"12_19": 166, "20_80": 120, "8_11": 12, "lt_8": 2}` / `{"12_19": 81, "20_80": 33, "8_11": 3}` |
| train_2 | 300 | 76 | 140 (46.67%) | 0 (0.00%) | 3.512967 | 1.000000 | `{"12_19": 73, "20_80": 130, "8_11": 15, "lt_8": 82}` / `{"12_19": 55, "20_80": 21}` |
| train_3 | 300 | 106 | 18 (6.00%) | 0 (0.00%) | 3.817170 | 1.000000 | `{"12_19": 37, "20_80": 89, "8_11": 5, "lt_8": 169}` / `{"12_19": 25, "20_80": 77, "8_11": 4}` |

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
- source git commit: `1b58d1f`
