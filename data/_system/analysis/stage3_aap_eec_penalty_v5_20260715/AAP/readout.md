# AAP EEC 집중도 벌점 v5 재학습 readout

- source commit: `50997c2`
- seed: `2026071401`
- host: `invest-bot`
- 실행: notebook host-local `28` process
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- 변경 변수: entry-scope fitness에 EEC concentration multiplier 추가
- 불변: 진입/청산·should_buy·strict interval·legacy scheduling·mutation·fixed-notional accounting
- EEC: target `6.0`, floor `0.5`, cluster gap `8` trading days
- 판정: **EEC_PENALTY_EFFECTIVE** — fold-best EEC가 모든 fold에서 상승했고 최대 클러스터 비중도 모두 하락했다.

## 재실행 명령

전체 argv와 실행 관련 환경변수는 `launch_command.json`에 기록했다.

```powershell
& '/home/g3000kkw/kingmaker/scripts/research/stage23_rework_20260713/../../../venv/bin/python' '/home/g3000kkw/kingmaker/scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aap_tradecount_factor_v3_host.py' '--baseline-dir' '../../../data/_system/analysis/stage3_aap_tradecount_factor_v3_20260715/AAP/NOTEBOOK_MAX' '--out-dir' '../../../data/_system/analysis/stage3_aap_eec_penalty_v5_20260715/AAP' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env":"da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce","data/_system/market_history.csv":"35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38","data/_system/market_history_v2.csv":"b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline":"/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60","pid":494330,"snapshot_source":"VM_proxy_for_notebook_independent_run","starttime_ticks":"36014393","state":"R"}' '--source-git-commit' '50997c2'
```

## v4 대비 비교표

| Metric | v4 | This run |
|---|---:|---:|
| all3/all2/all1/all0 | 0/2/192/106 | 0/4/245/51 |
| fold별 pass 수 | - | 90/80/83 |
| fold-best 거래수 | 20/15/13(±) | 21/21/13 |
| fold-best EEC | 2.30/2.53/3.70(±) | 4.955056/6.211268/8.047619 |
| fold-best 최대 클러스터 비중 | 60%/60%/37% | 23.81%/19.05%/23.08% |
| fold-best fitness | ? | 0.8734105702136226/1.5040265454276753/1.4454281972759289 |

## Fold-best EEC·클러스터

| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 21 | 21 | 4.955056 | 0.8258426966292136 | 23.81% | `2022-07-01~2022-07-11:4(19.0%) ; 2022-10-11~2022-10-14:4(19.0%) ; 2022-12-29~2023-01-04:4(19.0%) ; 2023-01-26~2023-01-31:4(19.0%) ; 2023-04-06~2023-04-14:5(23.8%)` | `{"1": 6, "2": 6, "3": 5, "4": 4}` | `{"1": 12, "2": 6, "3": 5, "4": 5}` |
| train_2 | 21 | 21 | 6.211268 | 1.0 | 19.05% | `2023-09-15~2023-09-15:1(4.8%) ; 2023-11-28~2023-12-05:4(19.0%) ; 2024-01-17~2024-01-24:4(19.0%) ; 2024-02-22~2024-03-01:3(14.3%) ; 2024-04-15~2024-04-17:3(14.3%) ; 2024-04-30~2024-05-09:4(19.0%) ; 2024-06-07~2024-06-10:2(9.5%)` | `{"1": 11, "2": 7, "3": 3}` | `{"1": 23, "2": 7, "3": 3}` |
| train_3 | 13 | 13 | 8.047619 | 1.0 | 23.08% | `2024-07-10~2024-07-10:1(7.7%) ; 2024-08-12~2024-08-12:1(7.7%) ; 2024-09-25~2024-09-25:1(7.7%) ; 2024-10-10~2024-10-11:2(15.4%) ; 2024-10-30~2024-10-30:1(7.7%) ; 2024-11-15~2024-11-15:1(7.7%) ; 2025-02-18~2025-02-18:1(7.7%) ; 2025-04-10~2025-04-10:1(7.7%) ; 2025-04-28~2025-05-08:3(23.1%) ; 2025-05-29~2025-05-29:1(7.7%)` | `{"1": 11, "2": 2}` | `{"1": 24, "2": 2}` |

## 몰빵 개체 vs 분산 개체 순위 변화

`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.

| fold | candidate | EEC | multiplier | rank before | rank after | delta |
|---|---|---:|---:|---:|---:|---:|
| train_1 | `None` | 4.955056 | 0.825843 | 100 | 100 | 0 |
| train_1 | `None` | 4.955056 | 0.825843 | 100 | 100 | 0 |
| train_1 | `None` | 4.955056 | 0.825843 | 100 | 100 | 0 |
| train_1 | `None` | 4.955056 | 0.825843 | 100 | 100 | 0 |
| train_1 | `None` | 4.955056 | 0.825843 | 100 | 100 | 0 |
| train_2 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_2 | `None` | 6.211268 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.047619 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.047619 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.047619 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.047619 | 1.000000 | 100 | 100 | 0 |
| train_3 | `None` | 8.047619 | 1.000000 | 100 | 100 | 0 |

## Gate·factor 병목

| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---:|---|
| train_1 | 300 | 90 | 165 (55.00%) | 288 (96.00%) | 4.143499 | 0.729342 | `{"12_19": 101, "20_80": 143, "8_11": 12, "gt_80": 3, "lt_8": 41}` / `{"12_19": 13, "20_80": 75, "8_11": 2}` |
| train_2 | 300 | 80 | 208 (69.33%) | 240 (80.00%) | 4.405590 | 0.747963 | `{"12_19": 119, "20_80": 105, "8_11": 65, "gt_80": 3, "lt_8": 8}` / `{"12_19": 13, "20_80": 66, "8_11": 1}` |
| train_3 | 300 | 83 | 201 (67.00%) | 221 (73.67%) | 4.401168 | 0.667444 | `{"12_19": 89, "20_80": 120, "8_11": 82, "gt_80": 2, "lt_8": 7}` / `{"12_19": 73, "20_80": 5, "8_11": 5}` |

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
- source git commit: `50997c2`
