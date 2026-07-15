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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_multiticker_v5\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_multiticker_v5\data\_system\analysis\stage3_multiticker_v5_probe_20260715\BTBT' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' '1b58d1f'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/9/270/21 |
| fold별 pass 수 | - | 102/95/91 |
| fold-best 거래수 | 20/15/13(±) | 17/18/12 |
| fold-best EEC | 2.30/2.53/3.70(±) | 4.587302/5.785714/6.545455 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 29.41%/22.22%/25.00% |
| fold-best fitness | ? | 4.0941360475820785/2.6398988271094677/1.3796719730071554 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 17 | 0 | 4.587302 | 1.0 | 29.41% | `2022-07-06~2022-07-18:5(29.4%) ; 2022-08-22~2022-08-23:2(11.8%) ; 2022-11-02~2022-11-11:3(17.6%) ; 2023-01-10~2023-01-12:3(17.6%) ; 2023-03-29~2023-04-11:4(23.5%)` | `{"1": 8, "2": 6, "3": 2, "4": 1}` | `{"1": 19, "2": 6, "3": 2, "4": 1}` |
| train_2 | 18 | 0 | 5.785714 | 1.0 | 22.22% | `2023-09-07~2023-09-11:3(16.7%) ; 2023-09-25~2023-09-27:3(16.7%) ; 2023-10-11~2023-10-19:4(22.2%) ; 2024-02-07~2024-02-12:4(22.2%) ; 2024-03-11~2024-03-11:1(5.6%) ; 2024-03-26~2024-03-26:1(5.6%) ; 2024-04-17~2024-04-18:2(11.1%)` | `{"1": 8, "2": 6, "3": 3, "4": 1}` | `{"1": 14, "2": 7, "3": 3, "4": 2}` |
| train_3 | 12 | 0 | 6.545455 | 1.0 | 25.00% | `2024-08-07~2024-08-07:1(8.3%) ; 2024-08-20~2024-08-20:1(8.3%) ; 2024-09-18~2024-09-23:2(16.7%) ; 2024-10-14~2024-10-14:1(8.3%) ; 2024-12-12~2024-12-23:2(16.7%) ; 2025-01-10~2025-01-14:3(25.0%) ; 2025-02-10~2025-02-10:1(8.3%) ; 2025-04-30~2025-04-30:1(8.3%)` | `{"1": 10, "2": 1, "3": 1}` | `{"1": 21, "2": 1, "3": 1}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 4.587302 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.587302 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.587302 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.785714 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.785714 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.785714 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.785714 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.785714 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.545455 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.545455 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.545455 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.545455 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.545455 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 102 | 174 (58.00%) | 0 (0.00%) | 4.676751 | 1.000000 | `{"12_19": 225, "20_80": 61, "8_11": 12, "lt_8": 2}` / `{"12_19": 77, "20_80": 21, "8_11": 4}` |
| train_2 | 300 | 95 | 204 (68.00%) | 0 (0.00%) | 4.269606 | 1.000000 | `{"12_19": 148, "20_80": 141, "8_11": 8, "gt_80": 2, "lt_8": 1}` / `{"12_19": 61, "20_80": 29, "8_11": 5}` |
| train_3 | 300 | 91 | 202 (67.33%) | 0 (0.00%) | 3.881156 | 1.000000 | `{"12_19": 101, "20_80": 188, "8_11": 6, "gt_80": 4, "lt_8": 1}` / `{"12_19": 77, "20_80": 9, "8_11": 5}` |

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
