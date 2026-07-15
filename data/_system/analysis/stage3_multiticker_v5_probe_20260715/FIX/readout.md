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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_multiticker_v5\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_multiticker_v5_probe_20260715\FIX' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' '1b58d1f'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/51/219/30 |
| fold별 pass 수 | - | 91/82/148 |
| fold-best 거래수 | 20/15/13(±) | 19/19/20 |
| fold-best EEC | 2.30/2.53/3.70(±) | 6.563636/6.563636/8.695652 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 21.05%/21.05%/20.00% |
| fold-best fitness | ? | 1.747167279191996/1.743152981603458/2.180971432020005 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 19 | 0 | 6.563636 | 1.0 | 21.05% | `2022-07-19~2022-07-25:2(10.5%) ; 2022-09-29~2022-09-30:2(10.5%) ; 2022-10-14~2022-10-27:3(15.8%) ; 2023-01-06~2023-01-11:3(15.8%) ; 2023-01-27~2023-02-15:3(15.8%) ; 2023-04-26~2023-04-27:2(10.5%) ; 2023-05-31~2023-06-08:4(21.1%)` | `{"1": 13, "2": 5, "3": 1}` | `{"1": 32, "2": 5, "3": 1}` |
| train_2 | 19 | 0 | 6.563636 | 1.0 | 21.05% | `2023-07-07~2023-07-12:3(15.8%) ; 2023-08-17~2023-08-17:1(5.3%) ; 2023-12-04~2023-12-07:4(21.1%) ; 2023-12-20~2023-12-20:1(5.3%) ; 2024-01-08~2024-01-09:2(10.5%) ; 2024-02-21~2024-02-29:4(21.1%) ; 2024-03-18~2024-03-19:2(10.5%) ; 2024-05-21~2024-05-22:2(10.5%)` | `{"1": 9, "2": 7, "3": 2, "4": 1}` | `{"1": 19, "2": 7, "3": 2, "4": 1}` |
| train_3 | 20 | 0 | 8.695652 | 1.0 | 20.00% | `2024-07-01~2024-07-01:1(5.0%) ; 2024-07-18~2024-07-18:1(5.0%) ; 2024-08-02~2024-08-05:2(10.0%) ; 2024-08-19~2024-08-19:1(5.0%) ; 2024-09-18~2024-09-19:2(10.0%) ; 2024-10-30~2024-10-31:2(10.0%) ; 2024-11-21~2024-11-21:1(5.0%) ; 2025-01-13~2025-01-16:4(20.0%) ; 2025-02-14~2025-02-18:2(10.0%) ; 2025-04-29~2025-04-29:1(5.0%) ; 2025-06-23~2025-06-26:3(15.0%)` | `{"1": 11, "2": 6, "3": 2, "4": 1}` | `{"1": 22, "2": 6, "3": 2, "4": 1}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.563636 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.695652 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.695652 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.695652 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.695652 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.695652 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 91 | 198 (66.00%) | 0 (0.00%) | 5.651483 | 1.000000 | `{"12_19": 63, "20_80": 231, "8_11": 1, "gt_80": 5}` / `{"12_19": 53, "20_80": 38}` |
| train_2 | 300 | 82 | 105 (35.00%) | 0 (0.00%) | 5.668105 | 1.000000 | `{"12_19": 117, "20_80": 167, "8_11": 8, "gt_80": 3, "lt_8": 5}` / `{"12_19": 56, "20_80": 25, "8_11": 1}` |
| train_3 | 300 | 148 | 96 (32.00%) | 0 (0.00%) | 4.991788 | 1.000000 | `{"12_19": 72, "20_80": 105, "8_11": 85, "lt_8": 38}` / `{"12_19": 26, "20_80": 81, "8_11": 41}` |

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
