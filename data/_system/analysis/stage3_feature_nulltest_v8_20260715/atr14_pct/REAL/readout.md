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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_nulltest_v8\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_nulltest_v8\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_nulltest_v8\data\_system\analysis\stage3_feature_nulltest_v8_20260715\atr14_pct\REAL' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' 'ca1154d'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/2/257/41 |
| fold별 pass 수 | - | 86/83/92 |
| fold-best 거래수 | 20/15/13(±) | 31/19/19 |
| fold-best EEC | 2.30/2.53/3.70(±) | 5.113636/3.657143/4.500000 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 26.67%/37.50%/33.33% |
| fold-best fitness | ? | 0.8488445099027621/1.3806312821523188/1.7431708958359289 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 31 | 0 | 5.113636 | 1.0 | 26.67% | `2022-07-01~2022-07-18:6(20.0%) ; 2022-10-11~2022-10-21:8(26.7%) ; 2022-12-19~2022-12-30:5(16.7%) ; 2023-01-25~2023-01-31:5(16.7%) ; 2023-04-06~2023-04-14:5(16.7%) ; 2023-05-04~2023-05-04:1(3.3%)` | `{"1": 9, "2": 10, "3": 5, "4": 4, "5": 2}` | `{"1": 20, "2": 14, "3": 5, "4": 5, "5": 2}` |
| train_2 | 19 | 0 | 3.657143 | 1.0 | 37.50% | `2024-01-12~2024-01-24:6(37.5%) ; 2024-02-14~2024-02-23:3(18.8%) ; 2024-04-15~2024-04-17:3(18.8%) ; 2024-04-30~2024-05-10:4(25.0%)` | `{"1": 7, "2": 5, "3": 2, "4": 1, "5": 1}` | `{"1": 14, "2": 5, "3": 2, "4": 1, "5": 1}` |
| train_3 | 19 | 0 | 4.500000 | 1.0 | 33.33% | `2024-07-10~2024-07-10:1(8.3%) ; 2024-08-12~2024-08-13:2(16.7%) ; 2024-10-10~2024-10-16:3(25.0%) ; 2024-10-30~2024-11-08:4(33.3%) ; 2025-04-10~2025-04-10:1(8.3%) ; 2025-05-29~2025-05-29:1(8.3%)` | `{"1": 8, "2": 4}` | `{"1": 16, "2": 5}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 5.368715 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.368715 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.368715 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.368715 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.368715 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.367347 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.367347 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.367347 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.367347 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 7.367347 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 86 | 118 (39.33%) | 0 (0.00%) | 3.741955 | 1.000000 | `{"12_19": 88, "20_80": 110, "8_11": 14, "lt_8": 88}` / `{"12_19": 2, "20_80": 84}` |
| train_2 | 300 | 83 | 204 (68.00%) | 0 (0.00%) | 3.707694 | 1.000000 | `{"12_19": 173, "20_80": 70, "8_11": 48, "lt_8": 9}` / `{"12_19": 49, "20_80": 33, "8_11": 1}` |
| train_3 | 300 | 92 | 204 (68.00%) | 0 (0.00%) | 4.199969 | 1.000000 | `{"12_19": 93, "20_80": 136, "8_11": 70, "gt_80": 1}` / `{"12_19": 70, "20_80": 20, "8_11": 2}` |

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
