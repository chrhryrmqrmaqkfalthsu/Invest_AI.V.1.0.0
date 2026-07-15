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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_adpt_feature_nulltest_v9\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_adpt_feature_nulltest_v9_20260715\rs_xbi_ret20\SHUFFLED' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"pid": 494330, "starttime_ticks": "36014393", "cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60"}' '--source-git-commit' 'e3bfb7c'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/29/257/14 |
| fold별 pass 수 | - | 121/88/106 |
| fold-best 거래수 | 20/15/13(±) | 13/16/19 |
| fold-best EEC | 2.30/2.53/3.70(±) | 4.333333/8.000000/6.720930 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 30.77%/18.75%/17.65% |
| fold-best fitness | ? | 1.3572190080882964/0.649326778294682/3.096317558231644 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 13 | 0 | 4.333333 | 1.0 | 30.77% | `2022-10-24~2022-10-24:1(7.7%) ; 2023-01-06~2023-01-11:4(30.8%) ; 2023-03-10~2023-03-28:4(30.8%) ; 2023-04-19~2023-04-27:2(15.4%) ; 2023-05-17~2023-05-17:1(7.7%) ; 2023-05-31~2023-05-31:1(7.7%)` | `{"1": 9, "2": 2, "3": 1, "4": 1}` | `{"1": 19, "2": 2, "3": 1, "4": 1}` |
| train_2 | 16 | 0 | 8.000000 | 1.0 | 18.75% | `2023-07-18~2023-07-19:2(12.5%) ; 2023-08-11~2023-08-11:1(6.2%) ; 2023-08-30~2023-08-30:1(6.2%) ; 2023-09-18~2023-09-22:3(18.8%) ; 2023-10-31~2023-10-31:1(6.2%) ; 2023-11-21~2023-11-21:1(6.2%) ; 2023-12-19~2023-12-19:1(6.2%) ; 2024-01-18~2024-01-19:2(12.5%) ; 2024-02-13~2024-02-21:3(18.8%) ; 2024-04-05~2024-04-05:1(6.2%)` | `{"1": 12, "2": 4}` | `{"1": 24, "2": 4}` |
| train_3 | 19 | 0 | 6.720930 | 1.0 | 17.65% | `2024-07-03~2024-07-12:3(17.6%) ; 2024-09-09~2024-09-09:1(5.9%) ; 2024-09-23~2024-09-25:3(17.6%) ; 2024-10-17~2024-10-21:3(17.6%) ; 2024-12-31~2025-01-02:2(11.8%) ; 2025-01-27~2025-01-27:1(5.9%) ; 2025-03-10~2025-03-10:1(5.9%) ; 2025-05-29~2025-06-02:3(17.6%)` | `{"1": 9, "2": 5, "3": 3}` | `{"1": 18, "2": 5, "3": 3}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 4.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.333333 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 3.789474 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 3.789474 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 8.000000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 8.000000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 8.000000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 8.000000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 8.000000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.333333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.333333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.333333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.333333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.333333 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 121 | 128 (42.67%) | 0 (0.00%) | 3.198487 | 1.000000 | `{"12_19": 156, "20_80": 128, "8_11": 10, "lt_8": 6}` / `{"12_19": 46, "20_80": 75}` |
| train_2 | 300 | 88 | 113 (37.67%) | 0 (0.00%) | 4.489688 | 1.000000 | `{"12_19": 97, "20_80": 98, "8_11": 18, "lt_8": 87}` / `{"12_19": 74, "20_80": 12, "8_11": 2}` |
| train_3 | 300 | 106 | 102 (34.00%) | 0 (0.00%) | 3.442910 | 1.000000 | `{"12_19": 114, "20_80": 90, "8_11": 8, "lt_8": 88}` / `{"12_19": 48, "20_80": 58}` |

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
