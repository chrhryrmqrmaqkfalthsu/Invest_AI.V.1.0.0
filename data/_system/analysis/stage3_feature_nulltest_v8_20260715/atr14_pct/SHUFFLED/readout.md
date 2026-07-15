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
& 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_nulltest_v8\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' 'C:\kingmaker_nulltest_v8\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_nulltest_v8\data\_system\analysis\stage3_feature_nulltest_v8_20260715\atr14_pct\SHUFFLED' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_snapshot_for_notebook_staging_run", "starttime_ticks": "36014393", "state": "Sl"}' '--source-git-commit' 'ca1154d'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 1/26/229/44 |
| fold별 pass 수 | - | 79/111/94 |
| fold-best 거래수 | 20/15/13(±) | 27/18/14 |
| fold-best EEC | 2.30/2.53/3.70(±) | 4.840000/4.567568/7.117647 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 27.27%/30.77%/18.18% |
| fold-best fitness | ? | 0.8059024017289121/1.364763873918462/1.7106577620593564 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 27 | 0 | 4.840000 | 1.0 | 27.27% | `2022-07-01~2022-07-11:2(18.2%) ; 2022-10-11~2022-10-14:3(27.3%) ; 2022-12-29~2022-12-30:2(18.2%) ; 2023-01-26~2023-01-31:2(18.2%) ; 2023-04-06~2023-04-12:2(18.2%)` | `{"1": 8, "2": 3}` | `{"1": 16, "2": 4}` |
| train_2 | 18 | 0 | 4.567568 | 1.0 | 30.77% | `2023-11-29~2023-12-07:3(23.1%) ; 2024-02-22~2024-03-01:3(23.1%) ; 2024-04-15~2024-04-24:4(30.8%) ; 2024-05-08~2024-05-08:1(7.7%) ; 2024-05-28~2024-05-28:1(7.7%) ; 2024-06-10~2024-06-10:1(7.7%)` | `{"1": 10, "2": 2, "3": 1}` | `{"1": 21, "2": 2, "3": 1}` |
| train_3 | 14 | 0 | 7.117647 | 1.0 | 18.18% | `2024-07-08~2024-07-08:1(9.1%) ; 2024-07-26~2024-07-29:2(18.2%) ; 2024-08-12~2024-08-12:1(9.1%) ; 2024-10-10~2024-10-11:2(18.2%) ; 2024-10-31~2024-11-04:2(18.2%) ; 2025-04-10~2025-04-10:1(9.1%) ; 2025-05-13~2025-05-13:1(9.1%) ; 2025-05-29~2025-05-29:1(9.1%)` | `{"1": 8, "2": 3}` | `{"1": 18, "2": 3}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 5.400000 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.878049 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.737705 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.737705 | 1.000000 | 100 | 100 | 0 |
| train_1 | `None` | 4.737705 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 4.500000 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.533333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.533333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.533333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.533333 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 6.533333 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 79 | 59 (19.67%) | 0 (0.00%) | 2.500338 | 1.000000 | `{"12_19": 45, "20_80": 79, "8_11": 24, "lt_8": 152}` / `{"12_19": 30, "20_80": 47, "8_11": 2}` |
| train_2 | 300 | 111 | 93 (31.00%) | 0 (0.00%) | 3.073097 | 1.000000 | `{"12_19": 138, "20_80": 67, "8_11": 6, "lt_8": 89}` / `{"12_19": 74, "20_80": 36, "8_11": 1}` |
| train_3 | 300 | 94 | 193 (64.33%) | 0 (0.00%) | 4.202980 | 1.000000 | `{"12_19": 102, "20_80": 127, "8_11": 65, "lt_8": 6}` / `{"12_19": 83, "20_80": 10, "8_11": 1}` |

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
