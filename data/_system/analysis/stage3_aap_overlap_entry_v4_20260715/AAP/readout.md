# AAP 동시진입 v4 정식 재학습 readout

- source commit: `faed59a43761076b9a1544d5f48c0bcf2d867ec8`
- seed: `2026071401`
- host: `DESKTOP-TO74AR2`
- 실행: 독립 notebook parent + local `28` process
- qualify: population 100 / generations 40 × train_1·train_2·train_3
- 변경 변수: entry-scope 단일 포지션 인덱스 점프 제거
- 불변: strict-AND·exit·fitness·gate·mutation·legacy scheduling
- 자본 회계: 거래별 독립 fixed-notional; 총노출/cash ledger 없음
- 판정: **OVERLAP_ENTRY_EFFECT_CONFIRMED** — 세 fold 모두 fold-best 거래수가 v3보다 증가했고 보유·cooldown 흡수가 0으로 확인됐다.

## 재실행 명령

전체 argv와 실행 관련 환경변수는 `launch_command.json`에 기록했다.

```powershell
$env:PYTHONPATH='C:\kingmaker_aap_overlap_entry_v4_20260715_r3\scripts\research\stage23_rework_20260713;C:\kingmaker\vendor;C:\kingmaker_aap_v2_swing_restore_20260715_r2\vendor'; $env:PATH='C:\kingmaker\vendor;C:\kingmaker_aap_v2_swing_restore_20260715_r2\vendor;C:\WINDOWS\system32;C:\WINDOWS;C:\WINDOWS\System32\Wbem;C:\WINDOWS\System32\WindowsPowerShell\v1.0\;C:\WINDOWS\System32\OpenSSH\;C:\Users\1000k\AppData\Local\Programs\Python\Python310\Scripts\;C:\Users\1000k\AppData\Local\Programs\Python\Python310\;C:\Users\1000k\AppData\Local\Programs\Python\Python312\Scripts\;C:\Users\1000k\AppData\Local\Programs\Python\Python312\;C:\Users\1000k\AppData\Local\Programs\Python\Launcher\;C:\Users\1000k\AppData\Local\Microsoft\WindowsApps;C:\Users\1000k\AppData\Local\GitHubDesktop\bin;C:\Users\1000k\AppData\Local\Programs\Ollama;C:\Users\1000k\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin'; $env:PYTHONUTF8='1'; $env:PYTHONUNBUFFERED='1'; $env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:NUMEXPR_NUM_THREADS='1'; $env:KINGMAKER_MARKET_CUTOFF_DATE='2026-07-10'; & 'C:\dask310\Scripts\python.exe' 'C:\kingmaker_aap_overlap_entry_v4_20260715_r3\scripts\research\stage23_rework_20260713\scripts\research\run_stage3_aap_overlap_entry_v4_cutoff_host.py' '--baseline-dir' 'C:\kingmaker_aap_tradecount_factor_v3_20260715\data\_system\analysis\stage3_aap_tradecount_factor_v3_20260715\AAP\NOTEBOOK_MAX' '--out-dir' 'C:\kingmaker_aap_overlap_entry_v4_20260715_r3\data\_system\analysis\stage3_aap_overlap_entry_v4_20260715\AAP' '--seed-base' '2026071401' '--workers' '28' '--host-role' 'notebook' '--market-cutoff-date' '2026-07-10' '--protected-snapshot-json' '{".env": "da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce", "data/_system/market_history.csv": "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38", "data/_system/market_history_v2.csv": "b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611"}' '--daemon-snapshot-json' '{"cmdline": "/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60", "pid": 494330, "snapshot_source": "VM_proxy_for_notebook_independent_run", "starttime_ticks": "36014393", "state": "R"}' '--source-git-commit' 'faed59a43761076b9a1544d5f48c0bcf2d867ec8'
```

## v3 단일포지션 직접 비교

| 지표 | v3 단일포지션 | 이번 동시진입 |
|---|---:|---:|
| all3 / all2 / all1 / all0 | 0 / 2 / 192 / 106 | 61 / 27 / 173 / 39 |
| train_1 / train_2 / train_3 pass | 48 / 67 / 81 | 101 / 154 / 155 |
| fold-best 거래수 | 12 / 11 / 12 | 20 / 15 / 13 |
| fold-best 최대 동시 포지션 | 1 / 1 / 1 | 8 / 7 / 2 |
| fold-best fitness | 0.6445069087263888 / 1.0460364481256468 / 2.091575610734492 | 1.1724113242558079 / 1.6321081616005721 / 1.5883405825352652 |
| effective event count | 4.084967 / 4.062802 / 3.792593 | 2.298851 / 2.528090 / 4.368932 |

## Fold-best strict-AND·체결·동시 보유

| fold | joint pass day | 실제 거래 | executed joint day | held/cooldown 흡수 | 기타 미체결 joint day | 최대 동시 포지션 | entry-time 분포 | active-day 분포 | effective event count |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|
| train_1 | 20 | 20 | 20 | 0 | 0 | 8 | `{"1": 4, "2": 4, "3": 3, "4": 1, "5": 1, "6": 1, "7": 5, "8": 1}` | `{"1": 9, "2": 4, "3": 3, "4": 1, "5": 1, "6": 1, "7": 5, "8": 1}` | 2.298851 |
| train_2 | 15 | 15 | 15 | 0 | 0 | 7 | `{"1": 6, "2": 3, "3": 1, "4": 1, "5": 1, "6": 2, "7": 1}` | `{"1": 13, "2": 3, "3": 1, "4": 1, "5": 1, "6": 2, "7": 1}` | 2.528090 |
| train_3 | 30 | 13 | 13 | 0 | 17 | 2 | `{"1": 8, "2": 5}` | `{"1": 21, "2": 5}` | 4.368932 |

## Fold-best 거래수 분포

### 40세대 qualify generation-best 거래수

| fold | generation 수 | min | median | max | histogram |
|---|---:|---:|---:|---:|---|
| train_1 | 40 | 15 | 20.0 | 26 | `{"15": 2, "16": 1, "18": 2, "20": 32, "21": 1, "22": 1, "26": 1}` |
| train_2 | 40 | 10 | 15.0 | 38 | `{"10": 1, "12": 4, "14": 2, "15": 18, "16": 3, "18": 2, "19": 2, "20": 6, "22": 1, "38": 1}` |
| train_3 | 40 | 11 | 13.0 | 28 | `{"11": 4, "12": 6, "13": 23, "14": 1, "17": 2, "21": 3, "28": 1}` |

### 최종 population·pass 후보 거래수와 gate 병목

| fold | 후보 | 거래수 gate 탈락 | win_rate<60 탈락 | 실현손실 벌점 | MAE 벌점 | 전체 거래수 histogram | pass 거래수 histogram | factor bins 전체/pass |
|---|---:|---:|---:|---:|---:|---|---|---|
| train_1 | 300 | 5 (1.67%) | 180 (60.00%) | 214 (71.33%), 평균 0.797626 | 229 (76.33%), 평균 0.689441 | `{"0": 2, "1": 1, "10": 2, "11": 3, "12": 4, "13": 2, "14": 5, "15": 18, "16": 38, "17": 4, "18": 57, "19": 18, "20": 59, "21": 20, "22": 12, "23": 14, "24": 6, "25": 7, "26": 5, "27": 3, "28": 1, "29": 4, "3": 1, "31": 1, "32": 1, "35": 1, "40": 1, "42": 1, "44": 1, "46": 1, "5": 1, "52": 1, "53": 1, "55": 1, "57": 1, "58": 2}` | `{"14": 1, "15": 1, "16": 4, "17": 1, "18": 4, "19": 9, "20": 52, "21": 11, "22": 4, "23": 3, "24": 4, "25": 3, "26": 2, "28": 1, "29": 1}` | `{"12_19": 146, "20_80": 144, "8_11": 5, "lt_8": 5}` / `{"12_19": 20, "20_80": 81}` |
| train_2 | 300 | 14 (4.67%) | 124 (41.33%) | 215 (71.67%), 평균 1.056940 | 232 (77.33%), 평균 1.336321 | `{"1": 1, "10": 2, "11": 6, "12": 4, "13": 8, "14": 6, "15": 55, "16": 19, "17": 13, "18": 10, "19": 48, "2": 1, "20": 11, "21": 19, "22": 4, "23": 1, "24": 4, "25": 2, "26": 2, "27": 3, "29": 3, "31": 2, "32": 1, "33": 1, "35": 1, "36": 2, "38": 1, "39": 1, "4": 3, "40": 2, "44": 1, "48": 1, "5": 4, "51": 1, "52": 1, "54": 1, "55": 1, "56": 1, "6": 1, "62": 1, "7": 4, "8": 5, "9": 42}` | `{"10": 1, "12": 1, "13": 3, "14": 5, "15": 52, "16": 13, "17": 6, "18": 5, "19": 44, "20": 8, "21": 13, "22": 1, "24": 1, "27": 1}` | `{"12_19": 163, "20_80": 68, "8_11": 55, "lt_8": 14}` / `{"12_19": 129, "20_80": 24, "8_11": 1}` |
| train_3 | 300 | 4 (1.33%) | 122 (40.67%) | 246 (82.00%), 평균 1.132350 | 298 (99.33%), 평균 1.139588 | `{"10": 4, "11": 5, "12": 5, "13": 41, "14": 4, "15": 5, "16": 3, "17": 5, "18": 2, "19": 2, "20": 5, "23": 1, "24": 2, "25": 1, "26": 1, "27": 2, "30": 4, "31": 4, "32": 1, "33": 1, "34": 6, "36": 3, "38": 3, "39": 3, "40": 2, "41": 5, "42": 8, "43": 4, "44": 2, "45": 9, "46": 3, "47": 3, "48": 4, "49": 43, "5": 1, "50": 12, "51": 28, "52": 16, "53": 6, "54": 6, "55": 5, "56": 3, "57": 2, "58": 3, "59": 3, "6": 2, "60": 2, "62": 1, "68": 2, "7": 1, "70": 1, "71": 1, "77": 1, "79": 1, "8": 2, "83": 1, "9": 4}` | `{"10": 4, "11": 4, "12": 5, "13": 40, "14": 4, "15": 5, "16": 2, "17": 4, "18": 2, "19": 2, "20": 3, "27": 1, "31": 2, "33": 1, "34": 2, "36": 3, "38": 1, "40": 1, "41": 2, "42": 8, "43": 2, "45": 8, "46": 1, "47": 1, "48": 2, "49": 34, "50": 2, "54": 1, "55": 1, "60": 1, "62": 1, "8": 2, "9": 3}` | `{"12_19": 67, "20_80": 213, "8_11": 15, "gt_80": 1, "lt_8": 4}` / `{"12_19": 64, "20_80": 78, "8_11": 13}` |

## 독립 사건 수 해석

| fold | v3 effective event count | v4 effective event count | 변화 |
|---|---:|---:|---:|
| train_1 | 4.084967 | 2.298851 | -1.786117 |
| train_2 | 4.062802 | 2.528090 | -1.534712 |
| train_3 | 3.792593 | 4.368932 | +0.576339 |

동시진입은 세 fold의 거래수를 모두 늘렸지만 독립 사건 다양성을 일관되게 늘리지는 않았다. train_1·train_2는 같은 신호 군집 내부의 pass day가 여러 거래로 살아나면서 effective event count가 감소했고, train_3만 증가했다.

따라서 이번 변경의 확인된 효과는 **보유·cooldown 흡수 제거와 거래 support 증가**다. 독립 시장 사건 수 증가 효과는 fold별로 혼재되어 있다.

train_3의 joint pass 30일 중 실제 거래는 13건이고 held/cooldown 흡수는 0이다. 나머지 17일은 단일 포지션 제약이 아닌 기존 entry guard에서 미체결된 날짜이며, 이번 산출물은 guard별 세부 사유를 별도로 분류하지 않았으므로 원인은 미확정으로 남긴다.

## Trade-level 로그

`fold_best_trade_level.jsonl`에는 기존 진입/청산일·가격, 청산사유, 보유일, 실현수익, MAE, 일수익, +0.5% 승패, 5-feature snapshot에 더해 다음 필드를 기록한다.

- `entry_time_concurrent_positions`
- `overlapping_position_at_entry`
- `fold_max_concurrent_positions`
- `fold_entry_time_concurrency_distribution`
- `fold_daily_concurrency_distribution`
- `fold_joint_pass_vs_execution`
- `fold_effective_event_count`

동일 날짜 open에서 청산되는 기존 포지션은 해당 날짜 신규 진입 시점의 활성 포지션에서 제외했다.

## 안전성

- manifest gate: True
- 보호 SHA 불변: True
- daemon 불변: True
- 병렬 재현성 probe: True
- fitness activation probe: True
- source git commit: `faed59a43761076b9a1544d5f48c0bcf2d867ec8`

## Post-entry checkpoint resume

- qualify/entry local workers: 28
- exit/validate local workers: 6
- fixed exit seed: `seed_base + 1000 + entry_index`
- merge order: candidate input index order
- initial source commit: `faed59a43761076b9a1544d5f48c0bcf2d867ec8`
- resume source commit: `004bf73`
- market cutoff propagated to spawn children: `2026-07-10`
- removed partial sequential outputs: `["_parallel_exit_workers/"]`

## VM 완료 검증

- VM 회수 경로: `data/_system/analysis/stage3_aap_overlap_entry_v4_20260715/AAP/`
- 원격 SHA 검증: 33개 파일 전부 PASS
- trade-level 검증: 48건, 필수 필드 누락 0, 5-feature 누락 0
- 보유 중 신규 진입 거래: 30건
- 계산 source commit: `faed59a43761076b9a1544d5f48c0bcf2d867ec8`
- post-entry resume source commit: `004bf73`
- 보고서 repair source HEAD: `44ec218cb9ef7cbb5c3dd6c8cf471fe15b771b96`
- branch: `feat/intraday-reversal-ga`
- 산출물 커밋 직전 working tree: source clean, output만 신규 회수

보호 파일 시작·종료 SHA는 동일하다.

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

Daemon 종료 상태:

```text
PID: 494330
starttime_ticks: 36014393
started: Sat Jul 11 20:16:00 2026
command: /home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60
```

최종 산출물 commit SHA는 이 readout을 포함하는 커밋 생성 후 repository history와 최종 전달 메시지에 기록한다.
