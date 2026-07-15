# AAP EEC 집중도 벌점 v5 재학습 readout

- source commit: `1b58d1f`
- seed: `2026071401`
- host: `DESKTOP-TO74AR2`
- 실행: notebook host-local `28` process
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- 변경 변수: entry-scope fitness에 EEC concentration multiplier 추가
- 불변: 진입/청산·should_buy·strict interval·legacy scheduling·mutation·fixed-notional accounting
- EEC: target `6.0`, floor `0.5`, cluster gap `8` trading days
- 판정: **EEC_PENALTY_PARTIAL** — EEC/클러스터 집중 완화가 일부 fold에서만 확인됐다.

## 재실행 명령

전체 argv와 실행 관련 환경변수는 `launch_command.json`에 기록했다.

```powershell
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_multiticker_v5\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_multiticker_v5_probe_20260715\LASR' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' '1b58d1f'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 2/149/114/35 |
| fold별 pass 수 | - | 165/91/162 |
| fold-best 거래수 | 20/15/13(±) | 20/20/20 |
| fold-best EEC | 2.30/2.53/3.70(±) | 5.882353/2.439024/5.555556 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 20.00%/45.00%/30.00% |
| fold-best fitness | ? | 1.7028195989725181/1.1220954622104982/3.8568156353012597 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 20 | 0 | 5.882353 | 1.0 | 20.00% | `2022-07-29~2022-08-02:3(15.0%) ; 2022-08-19~2022-08-23:3(15.0%) ; 2022-10-12~2022-10-26:4(20.0%) ; 2022-11-08~2022-11-22:4(20.0%) ; 2022-12-09~2022-12-09:1(5.0%) ; 2023-01-26~2023-02-13:4(20.0%) ; 2023-04-12~2023-04-12:1(5.0%)` | `{"1": 11, "2": 6, "3": 3}` | `{"1": 23, "2": 6, "3": 3}` |
| train_2 | 20 | 0 | 2.439024 | 1.0 | 45.00% | `2023-08-10~2023-08-10:1(5.0%) ; 2023-08-24~2023-08-24:1(5.0%) ; 2023-10-27~2023-11-08:9(45.0%) ; 2024-04-18~2024-05-01:9(45.0%)` | `{"1": 4, "2": 3, "3": 3, "4": 3, "5": 3, "6": 1, "7": 2, "8": 1}` | `{"1": 9, "2": 3, "3": 3, "4": 3, "5": 3, "6": 1, "7": 2, "8": 1}` |
| train_3 | 20 | 0 | 5.555556 | 1.0 | 30.00% | `2024-07-15~2024-07-19:2(10.0%) ; 2024-08-20~2024-08-23:3(15.0%) ; 2024-09-20~2024-09-20:1(5.0%) ; 2024-10-03~2024-10-10:4(20.0%) ; 2024-11-26~2024-11-26:1(5.0%) ; 2025-01-08~2025-01-14:2(10.0%) ; 2025-03-14~2025-03-14:1(5.0%) ; 2025-04-30~2025-05-07:6(30.0%)` | `{"1": 11, "2": 4, "3": 2, "4": 1, "5": 1, "6": 1}` | `{"1": 23, "2": 4, "3": 2, "4": 1, "5": 1, "6": 1}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 5.882353 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.882353 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.882353 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 2.439024 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 2.439024 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 2.439024 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 2.439024 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 2.439024 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 5.555556 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 5.555556 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 5.555556 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 5.555556 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 5.555556 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 165 | 115 (38.33%) | 0 (0.00%) | 3.609905 | 1.000000 | `{"12_19": 37, "20_80": 258, "8_11": 5}` / `{"12_19": 23, "20_80": 140, "8_11": 2}` |
| train_2 | 300 | 91 | 196 (65.33%) | 0 (0.00%) | 3.323272 | 1.000000 | `{"12_19": 109, "20_80": 110, "8_11": 72, "lt_8": 9}` / `{"12_19": 13, "20_80": 75, "8_11": 3}` |
| train_3 | 300 | 162 | 128 (42.67%) | 0 (0.00%) | 5.181741 | 1.000000 | `{"12_19": 45, "20_80": 237, "8_11": 11, "gt_80": 2, "lt_8": 5}` / `{"12_19": 31, "20_80": 122, "8_11": 9}` |

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
