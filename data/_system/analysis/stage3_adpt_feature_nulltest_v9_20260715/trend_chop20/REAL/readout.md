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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_adpt_feature_nulltest_v9\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_adpt_feature_nulltest_v9_20260715\trend_chop20\REAL' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"pid": 494330, "starttime_ticks": "36014393", "cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60"}' '--source-git-commit' 'e3bfb7c'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 1/13/275/11 |
| fold별 pass 수 | - | 107/94/103 |
| fold-best 거래수 | 20/15/13(±) | 14/20/18 |
| fold-best EEC | 2.30/2.53/3.70(±) | 3.903226/6.118644/8.333333 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 36.36%/26.32%/20.00% |
| fold-best fitness | ? | 0.9865843471751788/1.3882506983969594/3.4868785038649244 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 14 | 0 | 3.903226 | 1.0 | 36.36% | `2022-09-22~2022-09-28:3(27.3%) ; 2022-10-14~2022-10-14:1(9.1%) ; 2023-01-06~2023-01-09:2(18.2%) ; 2023-03-23~2023-03-23:1(9.1%) ; 2023-05-05~2023-05-22:4(36.4%)` | `{"1": 6, "2": 4, "3": 1}` | `{"1": 14, "2": 5, "3": 3}` |
| train_2 | 20 | 0 | 6.118644 | 1.0 | 26.32% | `2023-07-17~2023-07-18:2(10.5%) ; 2023-08-28~2023-08-30:2(10.5%) ; 2023-10-04~2023-10-04:1(5.3%) ; 2023-10-31~2023-10-31:1(5.3%) ; 2023-11-21~2023-11-28:2(10.5%) ; 2024-02-13~2024-02-22:5(26.3%) ; 2024-03-15~2024-04-05:4(21.1%) ; 2024-05-01~2024-05-02:2(10.5%)` | `{"1": 12, "2": 5, "3": 1, "4": 1}` | `{"1": 26, "2": 5, "3": 1, "4": 1}` |
| train_3 | 18 | 0 | 8.333333 | 1.0 | 20.00% | `2024-07-12~2024-07-12:1(6.7%) ; 2024-08-12~2024-08-12:1(6.7%) ; 2024-08-27~2024-08-28:2(13.3%) ; 2024-09-24~2024-09-24:1(6.7%) ; 2024-10-16~2024-10-21:3(20.0%) ; 2024-11-21~2024-11-21:1(6.7%) ; 2024-12-19~2024-12-19:1(6.7%) ; 2025-01-16~2025-01-17:2(13.3%) ; 2025-04-04~2025-04-04:1(6.7%) ; 2025-05-22~2025-05-23:2(13.3%)` | `{"1": 10, "2": 4, "3": 1}` | `{"1": 21, "2": 4, "3": 1}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 4.666667 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.666667 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.666667 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.666667 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.666667 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.882353 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.882353 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.553846 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.553846 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 5.553846 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.526316 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.526316 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.526316 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.256410 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 9.256410 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 107 | 111 (37.00%) | 0 (0.00%) | 2.951243 | 1.000000 | `{"12_19": 147, "20_80": 64, "8_11": 52, "lt_8": 37}` / `{"12_19": 73, "20_80": 23, "8_11": 11}` |
| train_2 | 300 | 94 | 142 (47.33%) | 0 (0.00%) | 4.012568 | 1.000000 | `{"12_19": 80, "20_80": 143, "8_11": 19, "lt_8": 58}` / `{"12_19": 51, "20_80": 43}` |
| train_3 | 300 | 103 | 9 (3.00%) | 0 (0.00%) | 3.043761 | 1.000000 | `{"12_19": 61, "20_80": 49, "8_11": 4, "lt_8": 186}` / `{"12_19": 60, "20_80": 41, "8_11": 2}` |

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
