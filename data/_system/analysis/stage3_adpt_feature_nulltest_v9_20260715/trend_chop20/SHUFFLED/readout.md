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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_adpt_feature_nulltest_v9\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_adpt_feature_nulltest_v9_20260715\trend_chop20\SHUFFLED' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"pid": 494330, "starttime_ticks": "36014393", "cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60"}' '--source-git-commit' 'e3bfb7c'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/15/255/30 |
| fold별 pass 수 | - | 102/85/98 |
| fold-best 거래수 | 20/15/13(±) | 19/12/14 |
| fold-best EEC | 2.30/2.53/3.70(±) | 4.500000/4.172414/6.125000 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 27.78%/36.36%/21.43% |
| fold-best fitness | ? | 1.3399270832630292/1.1391993239581295/3.4392870973320497 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 19 | 0 | 4.500000 | 1.0 | 27.78% | `2022-10-24~2022-11-09:5(27.8%) ; 2022-12-29~2023-01-11:3(16.7%) ; 2023-03-10~2023-03-14:2(11.1%) ; 2023-03-28~2023-04-17:5(27.8%) ; 2023-05-31~2023-06-02:3(16.7%)` | `{"1": 11, "2": 6, "3": 1}` | `{"1": 23, "2": 7, "3": 1}` |
| train_2 | 12 | 0 | 4.172414 | 1.0 | 36.36% | `2023-08-30~2023-08-30:1(9.1%) ; 2023-10-31~2023-10-31:1(9.1%) ; 2023-11-21~2023-11-21:1(9.1%) ; 2024-02-13~2024-02-22:4(36.4%) ; 2024-03-18~2024-04-05:3(27.3%) ; 2024-05-01~2024-05-01:1(9.1%)` | `{"1": 9, "2": 2}` | `{"1": 19, "2": 2}` |
| train_3 | 14 | 0 | 6.125000 | 1.0 | 21.43% | `2024-07-09~2024-07-12:2(14.3%) ; 2024-08-27~2024-08-27:1(7.1%) ; 2024-09-24~2024-09-25:2(14.3%) ; 2024-11-21~2024-11-21:1(7.1%) ; 2024-12-05~2024-12-19:3(21.4%) ; 2025-01-16~2025-01-27:2(14.3%) ; 2025-05-22~2025-05-27:3(21.4%)` | `{"1": 11, "2": 2, "3": 1}` | `{"1": 22, "2": 2, "3": 1}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.569620 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.125000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.125000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.125000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.125000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.125000 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 102 | 95 (31.67%) | 0 (0.00%) | 3.041486 | 1.000000 | `{"12_19": 81, "20_80": 61, "8_11": 60, "gt_80": 1, "lt_8": 97}` / `{"12_19": 60, "20_80": 37, "8_11": 5}` |
| train_2 | 300 | 85 | 138 (46.00%) | 0 (0.00%) | 3.350031 | 1.000000 | `{"12_19": 90, "20_80": 115, "8_11": 24, "gt_80": 2, "lt_8": 69}` / `{"12_19": 69, "20_80": 14, "8_11": 2}` |
| train_3 | 300 | 98 | 106 (35.33%) | 0 (0.00%) | 3.282943 | 1.000000 | `{"12_19": 138, "20_80": 53, "8_11": 11, "gt_80": 2, "lt_8": 96}` / `{"12_19": 64, "20_80": 34}` |

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
