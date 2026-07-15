# Stage 2 ADPT entry-scope/EEC ingest — Phase 2

## 판정

**EXECUTION_INCOMPLETE_NOTEBOOK_ENV_LIMIT**

코드 이식과 정적 검증은 완료됐다. 그러나 ADPT Stage 2 학습·롤링 검증은 완료하지 못했다. 노트북 `DESKTOP-TO74AR2`는 접근 가능했지만, `C:\dask310\Scripts\python.exe` 환경에 `loguru`, `dotenv`, `requests`, `yfinance`가 없어 staging shim이 필요했고, Windows spawn 방식의 Stage2 parallel child가 cache-only context를 안정적으로 상속하지 못했다. VM fallback은 하지 않았다.

최종 sequential 노트북 실행은 ADPT.pkl source를 보존한 상태에서 `train_1`까지 진입했으나, 단일 프로세스 Stage2 기본 GA가 장시간 `train_1`에서 종료되지 않아 백그라운드 작업을 남기지 않기 위해 중단했다.

| 항목 | 결과 |
|---|---|
| 코드 이식 | 완료 |
| 수정 범위 | `run_stage2.py::train_one_split.evaluate_fn()` 단일 지점 |
| py_compile | PASS |
| marker OFF 동치성 | PASS |
| marker 누수 smoke | PASS |
| Stage2 gate/rolling/survivor 파일 | 불변 |
| ADPT Stage2 학습 | 미완료 |
| rolling validation | 미실행 |
| survivors | 미확인 |
| fallback to VM | 없음 |

따라서 `STAGE2_YIELDS_SURVIVORS / STAGE2_NO_SURVIVORS / STAGE2_PARTIAL` 중 하나로 판정할 수 없다. 현재 산출의 의미는 **이식 코드와 정적 안전성은 확인됐지만, 노트북 실행 환경 제약 때문에 ADPT 학습 결과는 미확정**이다.

## 코드 변경

수정 파일:

`data/_system/analysis/stage2_adpt_eec_ingest_20260715/run_stage2_adpt_eec_launch.py`는 실행용 helper이고, 실제 소스 수정은 아래 한 곳이다.

`/scripts/research/stage23_rework_20260713/scripts/research/run_stage2.py`

변경 지점: `train_one_split.evaluate_fn()` 내부.

변경 내용:

- `engine.learning.execution_mode_backtest`의 entry-scope marker를 `evaluate_fn()` 안에서만 set.
- EEC target/floor를 `6.0 / 0.5`로 rulebook 임시 attribute에 set.
- `run_backtest_execution_mode()` 실행 후 `finally` 성격의 context manager에서 marker/target/floor를 원상복구.
- `evaluate_periods()`, `stage2_gate.py`, `PERIODS_TEMPLATE`, survivors output, `should_buy`, evaluator, exit simulator는 수정하지 않음.

| file | pre SHA | post SHA |
|---|---|---|
| `scripts/research/stage23_rework_20260713/scripts/research/run_stage2.py` | `9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c` | `a5e7b7547caaf3b359282ad5b044bcbee988577aca4e3770bbda43540766aa33` |

## 정적 검증

| 검증 | 결과 |
|---|---|
| `py_compile run_stage2.py` | PASS |
| marker OFF 동치성 | PASS. marker on/off 후 다시 off 평가가 기존 fitness/trade_count와 동일 |
| marker ON 진단 | `scope=entry`, `eec_target=6.0`, `eec_floor=0.5` 확인 |
| train_one_split smoke | PASS. output rulebook에 marker/target/floor attr 누수 없음 |
| mutation helper AST SHA | `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d` |
| `stage2_gate.py` | SHA 불변 |
| `rolling_validation.py` | SHA 불변 |
| `topn_survivor.py` | SHA 불변 |
| `full_training.py` | SHA 불변 |
| `run_stage23_batch.py` | SHA 불변 |

Smoke 주요 값:

| 항목 | 값 |
|---|---:|
| marker OFF fitness | `-136.93121020529992` |
| marker OFF 재평가 fitness | `-136.93121020529992` |
| marker ON entry-scope fitness | `-1000000000.0` |
| marker ON scope | `entry` |
| output rulebook marker leak | false |

## ADPT 데이터

| 항목 | 값 |
|---|---|
| ADPT OHLCV | `data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/ADPT.pkl` |
| SHA | `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503` |
| rows | 1527 |
| coverage | 2020-05-18 ~ 2026-06-15 |
| feature set | 기존 5-feature only. trend_chop20 미포함 |

## 노트북 실행 시도

| attempt | host | mode | result | reason |
|---|---|---|---|---|
| par2 | DESKTOP-TO74AR2 | Stage2 parallel 3 split | FAILED | Windows spawn child의 ADPT.pkl root 계산 오류 |
| par3 | DESKTOP-TO74AR2 | Stage2 parallel 3 split | FAILED/HUNG | staging context shim 후 child가 active GA worker로 안정 진입하지 못함 |
| finalseq | DESKTOP-TO74AR2 | sequential 1 process | ABORTED_INCOMPLETE | `train_1` GA가 장시간 종료되지 않아 중단 |

회수 로그: `notebook_attempt_logs.zip`.

## 산출물 상태

| file | 상태 |
|---|---|
| `stage2_survivors.jsonl` | empty. 학습 미완료로 survivor 없음/미확인 |
| `stage2_period_results.jsonl` | empty. rolling validation 미실행 |
| `launch_command.json` | notebook attempts와 실패 사유 기록 |
| `notebook_attempt_logs.zip` | 노트북 시도 로그 회수 |

## 보호파일 / daemon

보호파일 시작·종료 SHA 동일:

| file | SHA |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- 산출 전 백업 commit: `7114943`.
