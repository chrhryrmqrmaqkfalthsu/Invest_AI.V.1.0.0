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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_adpt_feature_nulltest_v9\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_adpt_feature_nulltest_v9\data\_system\analysis\stage3_adpt_feature_nulltest_v9_20260715\rs_peer_ret20\SHUFFLED' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"pid": 494330, "starttime_ticks": "36014393", "cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60"}' '--source-git-commit' 'e3bfb7c'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 3/20/251/26 |
| fold별 pass 수 | - | 114/85/101 |
| fold-best 거래수 | 20/15/13(±) | 12/23/17 |
| fold-best EEC | 2.30/2.53/3.70(±) | 4.545455/6.060606/8.757576 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 30.00%/30.00%/17.65% |
| fold-best fitness | ? | 0.9427983425310063/0.8475546448598581/3.7689816322651457 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 12 | 0 | 4.545455 | 1.0 | 30.00% | `2022-10-12~2022-10-14:2(20.0%) ; 2023-01-06~2023-01-09:2(20.0%) ; 2023-03-22~2023-03-23:2(20.0%) ; 2023-04-19~2023-04-19:1(10.0%) ; 2023-05-16~2023-05-22:3(30.0%)` | `{"1": 6, "2": 4}` | `{"1": 13, "2": 4}` |
| train_2 | 23 | 0 | 6.060606 | 1.0 | 30.00% | `2023-07-10~2023-07-19:3(15.0%) ; 2023-08-29~2023-08-30:2(10.0%) ; 2023-09-18~2023-09-22:3(15.0%) ; 2023-11-01~2023-11-01:1(5.0%) ; 2023-11-29~2023-11-29:1(5.0%) ; 2024-02-02~2024-02-21:6(30.0%) ; 2024-03-13~2024-03-13:1(5.0%) ; 2024-04-05~2024-04-05:1(5.0%) ; 2024-05-01~2024-05-02:2(10.0%)` | `{"1": 12, "2": 6, "3": 2}` | `{"1": 25, "2": 6, "3": 2}` |
| train_3 | 17 | 0 | 8.757576 | 1.0 | 17.65% | `2024-08-27~2024-08-27:1(5.9%) ; 2024-09-24~2024-09-25:2(11.8%) ; 2024-10-17~2024-10-17:1(5.9%) ; 2024-11-21~2024-11-22:2(11.8%) ; 2024-12-19~2024-12-19:1(5.9%) ; 2025-01-16~2025-01-17:2(11.8%) ; 2025-04-04~2025-04-04:1(5.9%) ; 2025-04-21~2025-04-22:2(11.8%) ; 2025-05-06~2025-05-07:2(11.8%) ; 2025-05-22~2025-05-29:3(17.6%)` | `{"1": 11, "2": 6}` | `{"1": 22, "2": 6}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 4.235294 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.235294 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.235294 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.235294 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.235294 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.080460 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.080460 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.080460 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.080460 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.080460 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.757576 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.757576 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.757576 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.757576 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.757576 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 114 | 65 (21.67%) | 0 (0.00%) | 3.518426 | 1.000000 | `{"12_19": 143, "20_80": 78, "8_11": 19, "lt_8": 60}` / `{"12_19": 80, "20_80": 24, "8_11": 10}` |
| train_2 | 300 | 85 | 199 (66.33%) | 0 (0.00%) | 4.318049 | 1.000000 | `{"12_19": 33, "20_80": 198, "8_11": 65, "lt_8": 4}` / `{"12_19": 6, "20_80": 77, "8_11": 2}` |
| train_3 | 300 | 101 | 23 (7.67%) | 0 (0.00%) | 3.703572 | 1.000000 | `{"12_19": 62, "20_80": 55, "8_11": 8, "gt_80": 3, "lt_8": 172}` / `{"12_19": 59, "20_80": 34, "8_11": 7, "gt_80": 1}` |

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
