# Stage 2 ADPT entry-scope/EEC full-CPU notebook rerun

## 최종 판정

**STAGE2_NO_SURVIVORS**

노트북 `DESKTOP-TO74AR2`에서만 실행했고, VM fallback 없이 완료했다. 기존 Stage2 split 병렬 대신 **GA population fitness evaluation을 28 worker process로 병렬화**했다. 학습은 정상 완료됐지만, Stage2 rolling gate 첫 구간인 `stress_pre_2022h1`에서 300개 rulebook 전부 탈락했다. 따라서 이후 `train_3_eval → train_2_eval → train_1_eval → oos_2025h2`는 모두 reached=0으로 skip됐다.

| 항목 | 결과 |
|---|---:|
| 실행 host | `DESKTOP-TO74AR2` |
| VM fallback | 없음 |
| workers | 28 |
| 병렬 단위 | GA population fitness evaluation |
| generated rulebooks | 300 |
| unique rulebook hashes | 300 |
| final survivors | 0 |
| total elapsed | 1206.0 sec |
| 판정 | `STAGE2_NO_SURVIVORS` |

## 학습 진행

| train split | generations | early stop | final best fitness | elapsed sec |
|---|---:|---:|---:|---:|
| train_1 | 50 | false | -1.468590 | 358.1 |
| train_2 | 50 | false | -1.055356 | 309.0 |
| train_3 | 44 | true | 5.133392 | 243.2 |

`train_3`에서는 entry-scope/EEC fitness 기준으로 양수 fitness 개체까지 생성됐다. 하지만 Stage2 rolling gate는 stress를 첫 gate로 쓰므로, stress 통과 실패가 전체 생존 실패를 결정했다.

## Rolling gate 결과

| period | status | passed | count |
|---|---|---:|---:|
| stress_pre_2022h1 | evaluated | false | 300 |
| train_3_eval | skipped_after_early_cut | false | 300 |
| train_2_eval | skipped_after_early_cut | false | 300 |
| train_1_eval | skipped_after_early_cut | false | 300 |
| oos_2025h2 | skipped_after_early_cut | false | 300 |

첫 탈락 구간 분포:

| failed period | count |
|---|---:|
| stress_pre_2022h1 | 300 |

## Stress gate 병목

Stress evaluated rows: 300, passed: 0.

| metric | value |
|---|---:|
| stress expectancy max | 3.543905 |
| stress expectancy mean | -3.962779 |
| stress max drawdown median | -43.799103 |
| stress worst drawdown | -153.952524 |
| stress trade_count max | 31 |
| stress trade_count mean | 10.896667 |

Stress fail reason metric counts:

| metric | fail count |
|---|---:|
| stress_return_mdd_ratio | 300 |
| expectancy_pct | 296 |
| max_drawdown_pct | 275 |

Fail-reason combinations:

| combination | count |
|---|---:|
| expectancy_pct + max_drawdown_pct + stress_return_mdd_ratio | 272 |
| expectancy_pct + stress_return_mdd_ratio | 24 |
| max_drawdown_pct + stress_return_mdd_ratio | 3 |
| stress_return_mdd_ratio only | 1 |

가장 좋은 stress expectancy 개체도 ratio gate에서 탈락했다.

| rank by stress expectancy | trade_count | expectancy_pct | max_drawdown_pct | win_rate | fail reason summary |
|---:|---:|---:|---:|---:|---|
| 1 | 4 | 3.543905 | 0.000000 | 100.0 | `stress_return_mdd_ratio` unavailable |
| 2 | 10 | 2.088823 | -25.453355 | 80.0 | MDD < -20 and ratio 0.820647 <= 1 |
| 3 | 10 | 1.149790 | -26.466525 | 40.0 | MDD < -20 and ratio 0.434432 <= 1 |
| 4 | 9 | 1.065542 | -30.036977 | 66.67 | MDD < -20 and ratio 0.319269 <= 1 |

즉 이번 Stage2 방식의 ADPT 실패는 train/oos 문제가 아니라 **stress gate 방어력 부재**다. Stage3에서 OOS가 무너졌던 것과 별개로, Stage2는 stress를 첫 gate로 두기 때문에 여기서 전멸했다.

## 코드/실행 방식

Source change는 기존 Phase 2에서 적용한 `run_stage2.py::train_one_split.evaluate_fn()` 단일 지점 이식 그대로다.

이번 재실행용 runner:

`data/_system/analysis/stage2_adpt_eec_fullcpu_20260715/run_fullcpu.py`

이 runner는 Stage2 gate/rolling/evaluate_periods를 그대로 재사용하고, 학습 중 GA population fitness evaluation만 28-process pool로 병렬화했다. ADPT OHLCV는 노트북 staging 내부의 local `ADPT.pkl`만 읽었다. 외부 fetch/regenerate는 사용하지 않았다.

노트북 staging에서는 `C:\dask310\Scripts\python.exe` 의존성 부족 때문에 `loguru`, `dotenv`, `requests`, `yfinance` shim을 staging 내부에만 두었다. `requests`와 `yfinance`는 외부 접근 방지용 disabled shim이다. 원본 repo와 보호파일은 수정하지 않았다.

## 산출물

노트북 run output 전체는 아래 zip에 들어 있다.

`data/_system/analysis/stage2_adpt_eec_fullcpu_20260715/notebook_run_outputs.zip`

Zip 내부 주요 파일:

- `summary.json`
- `rulebooks_all.jsonl`
- `ga_history.csv`
- `period_metrics_all.csv`
- `early_cut_log.csv`
- `survivors.jsonl`
- `trades.jsonl`
- `rl_replay_trades.jsonl`
- `launch_command.json`
- `run.log`

## 보호파일 / daemon

보호파일 시작·종료 SHA 동일:

| file | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- pre-run backup commit: `d188f30`.
- fullcpu runner commit: `e781b2d`.
