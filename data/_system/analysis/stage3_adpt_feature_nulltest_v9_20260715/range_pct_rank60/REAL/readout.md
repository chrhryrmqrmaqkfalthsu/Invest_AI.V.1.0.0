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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_adpt_feature_nulltest_v9\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_adpt_feature_nulltest_v9_20260715\range_pct_rank60\REAL' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"pid": 494330, "starttime_ticks": "36014393", "cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60"}' '--source-git-commit' 'e3bfb7c'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/11/259/30 |
| fold별 pass 수 | - | 106/87/88 |
| fold-best 거래수 | 20/15/13(±) | 13/13/17 |
| fold-best EEC | 2.30/2.53/3.70(±) | 5.121212/6.545455/5.142857 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 23.08%/16.67%/33.33% |
| fold-best fitness | ? | 1.2301207961686544/1.969858541208204/3.268616454023101 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 13 | 0 | 5.121212 | 1.0 | 23.08% | `2022-09-26~2022-09-28:3(23.1%) ; 2022-10-28~2022-10-28:1(7.7%) ; 2023-01-09~2023-01-09:1(7.7%) ; 2023-03-22~2023-03-27:3(23.1%) ; 2023-04-19~2023-04-27:2(15.4%) ; 2023-05-17~2023-05-22:3(23.1%)` | `{"1": 7, "2": 4, "3": 2}` | `{"1": 16, "2": 4, "3": 2}` |
| train_2 | 13 | 0 | 6.545455 | 1.0 | 16.67% | `2023-08-28~2023-08-29:2(16.7%) ; 2023-10-31~2023-11-01:2(16.7%) ; 2023-11-21~2023-11-28:2(16.7%) ; 2024-02-20~2024-02-22:2(16.7%) ; 2024-03-18~2024-03-18:1(8.3%) ; 2024-04-05~2024-04-05:1(8.3%) ; 2024-05-01~2024-05-02:2(16.7%)` | `{"1": 8, "2": 4}` | `{"1": 17, "2": 4}` |
| train_3 | 17 | 0 | 5.142857 | 1.0 | 33.33% | `2024-07-09~2024-07-12:2(16.7%) ; 2024-08-27~2024-08-27:1(8.3%) ; 2024-09-24~2024-09-24:1(8.3%) ; 2024-11-21~2024-11-21:1(8.3%) ; 2024-12-05~2024-12-20:4(33.3%) ; 2025-01-16~2025-01-27:2(16.7%) ; 2025-05-28~2025-05-28:1(8.3%)` | `{"1": 11, "2": 1}` | `{"1": 22, "2": 1}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 5.121212 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.121212 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.121212 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.121212 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 5.121212 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.259259 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.259259 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.259259 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.259259 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.538462 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.148936 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.148936 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.148936 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.148936 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.148936 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 106 | 99 (33.00%) | 0 (0.00%) | 3.313965 | 1.000000 | `{"12_19": 131, "20_80": 17, "8_11": 72, "lt_8": 80}` / `{"12_19": 95, "20_80": 5, "8_11": 6}` |
| train_2 | 300 | 87 | 138 (46.00%) | 0 (0.00%) | 3.796845 | 1.000000 | `{"12_19": 159, "20_80": 43, "8_11": 29, "lt_8": 69}` / `{"12_19": 67, "20_80": 9, "8_11": 11}` |
| train_3 | 300 | 88 | 20 (6.67%) | 0 (0.00%) | 2.354479 | 1.000000 | `{"12_19": 55, "20_80": 48, "8_11": 5, "gt_80": 1, "lt_8": 191}` / `{"12_19": 54, "20_80": 33, "8_11": 1}` |

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
