# AAP EEC 집중도 벌점 v5 재학습 readout

- source commit: `ca1154d`
- seed: `2026071401`
- host: `DESKTOP-TO74AR2`
- 실행: notebook host-local `28` process
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- 변경 변수: entry-scope fitness에 EEC concentration multiplier 추가
- 불변: 진입/청산·should_buy·strict interval·legacy scheduling·mutation·fixed-notional accounting
- EEC: target `4.0`, floor `0.7`, cluster gap `8` trading days
- 판정: **EEC_PENALTY_EFFECTIVE** — fold-best EEC가 모든 fold에서 상승했고 최대 클러스터 비중도 모두 하락했다.

## 재실행 명령

전체 argv와 실행 관련 환경변수는 `launch_command.json`에 기록했다.

```powershell
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_nulltest_v8\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_nulltest_v8\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_nulltest_v8\data\_system\analysis\stage3_feature_nulltest_v8_20260715\trend_chop20\SHUFFLED' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' 'ca1154d'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/11/219/70 |
| fold별 pass 수 | - | 75/80/86 |
| fold-best 거래수 | 20/15/13(±) | 23/17/18 |
| fold-best EEC | 2.30/2.53/3.70(±) | 5.400000/3.400000/6.400000 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 22.22%/47.06%/25.00% |
| fold-best fitness | ? | 0.8084074278980238/0.42694852076840634/1.5803279834201225 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 23 | 0 | 5.400000 | 1.0 | 22.22% | `2022-07-01~2022-07-11:4(22.2%) ; 2022-10-11~2022-10-14:3(16.7%) ; 2022-12-15~2022-12-15:1(5.6%) ; 2023-01-03~2023-01-05:3(16.7%) ; 2023-01-27~2023-01-31:3(16.7%) ; 2023-04-06~2023-04-14:4(22.2%)` | `{"1": 7, "2": 8, "3": 3}` | `{"1": 17, "2": 8, "3": 3}` |
| train_2 | 17 | 0 | 3.400000 | 1.0 | 47.06% | `2023-12-11~2023-12-13:2(11.8%) ; 2024-01-02~2024-01-19:8(47.1%) ; 2024-02-12~2024-02-14:3(17.6%) ; 2024-03-07~2024-03-08:2(11.8%) ; 2024-05-10~2024-05-16:2(11.8%)` | `{"1": 7, "2": 6, "3": 3, "4": 1}` | `{"1": 16, "2": 6, "3": 3, "4": 1}` |
| train_3 | 18 | 0 | 6.400000 | 1.0 | 25.00% | `2024-07-01~2024-07-08:2(12.5%) ; 2024-08-12~2024-08-13:2(12.5%) ; 2024-09-25~2024-09-25:1(6.2%) ; 2024-10-10~2024-10-11:2(12.5%) ; 2024-10-30~2024-11-04:3(18.8%) ; 2025-02-18~2025-02-18:1(6.2%) ; 2025-04-10~2025-04-10:1(6.2%) ; 2025-04-28~2025-05-08:4(25.0%)` | `{"1": 10, "2": 6}` | `{"1": 29, "2": 7}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 5.343434 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.343434 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.343434 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.938776 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.938776 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 3.400000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 3.400000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 3.400000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 3.400000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 3.400000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.043478 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.043478 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.043478 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.043478 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.043478 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 75 | 216 (72.00%) | 0 (0.00%) | 4.065373 | 1.000000 | `{"12_19": 29, "20_80": 266, "8_11": 1, "lt_8": 4}` / `{"12_19": 10, "20_80": 65}` |
| train_2 | 300 | 80 | 152 (50.67%) | 0 (0.00%) | 2.756341 | 1.000000 | `{"12_19": 170, "20_80": 122, "8_11": 4, "lt_8": 4}` / `{"12_19": 73, "20_80": 5, "8_11": 2}` |
| train_3 | 300 | 86 | 162 (54.00%) | 0 (0.00%) | 3.828606 | 1.000000 | `{"12_19": 62, "20_80": 222, "8_11": 12, "lt_8": 4}` / `{"12_19": 50, "20_80": 31, "8_11": 5}` |

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
- source git commit: `ca1154d`
