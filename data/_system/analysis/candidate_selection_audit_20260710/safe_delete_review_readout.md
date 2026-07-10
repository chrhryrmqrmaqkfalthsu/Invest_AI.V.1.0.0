# SAFE_TO_DELETE 실행 및 REVIEW 상위 개별 조사

- 기준일: 2026-07-10
- 1단계: SAFE_TO_DELETE 실제 삭제 완료
- 2단계: REVIEW 상위 조사만 수행, 삭제 0건
- 원본 코드·설정·룰북·frozen·audit 수정/삭제: 없음

## 0. 결과 요약

### 1단계 삭제

`orphan_file_candidates.csv`의 `risk=SAFE_TO_DELETE` 행만 사용했다.

| 항목 | 결과 |
|---|---:|
| CSV SAFE 행 | 45 |
| 실제 대표 파일 | 343 |
| 삭제 용량 | 5,150,150 bytes / 4.91 MiB |
| REVIEW/KEEP 삭제 | 0 |
| 살아있는 148개와 교집합 | 0 |
| 절대 보존 대상과 교집합 | 0 |
| 삭제 후 남은 SAFE 경로 | 0 |

삭제 대상은 실행 직전 실제 파일시스템과 다시 대조했다. 각 디렉터리의 현재 하위 파일 수와 바이트가 CSV 기록과 모두 일치했다.

삭제된 유형:

- Python/pytest 재생성 캐시 디렉터리 23행, 321파일
- 미참조 API·대시보드 명시적 백업 사본 20파일
- 종료된 PID의 atomic temp 2파일

전체 45행은 `safe_delete_execution.csv`에 기록했다.

### 2단계 조사 판정

| 경로 | 도달 | 크기 | 마지막 수정 | 판정 |
|---|---|---:|---|---|
| `data/_system/pipeline` | X | 78.804 MiB | 2026-06-05 21:01:46 KST | **DELETE_OK** |
| `data/_system/condition_db_sell_omen_clean` | X | 123.875 MiB | 2026-06-09 07:35:39 KST | **DELETE_OK** |
| `data/_system/condition_db_sell_omen_lr8d85` | X | 60.098 MiB | 2026-06-10 00:01:55 KST | **DELETE_OK** |
| `backup` | X | 72.422 MiB | 2026-07-10 03:27:06 KST | **PARTIAL** |
| `data/_system/logs` | X | 58.703 MiB | 2026-06-24 11:17:54 KST | **DELETE_OK** |

2단계 대상은 이번 작업에서 삭제하지 않았다.

## 1. 삭제 전 검증

실행 전 다음 조건을 모두 assertion으로 강제했다.

1. SAFE 행 수가 정확히 45개인지 확인.
2. 행이 대표하는 파일 수가 정확히 343개인지 확인.
3. 총 바이트가 정확히 5,150,150인지 확인.
4. 허용 유형이 아래 세 가지뿐인지 확인.
   - `REGENERABLE_CACHE_DIRECTORY`
   - `EXPLICIT_BACKUP_COPY`
   - `STALE_ATOMIC_TEMP`
5. CSV의 REVIEW/KEEP 행과 경로 중복이 없는지 확인.
6. 각 SAFE 디렉터리를 실제 파일 단위로 확장해 중복이 없는지 확인.
7. 확장된 모든 파일이 살아있는 148개 파일에 포함되지 않는지 확인.
8. 다음 절대 보존 대상을 파일 단위로 차단.
   - rulebooks_all
   - entry_rulebooks
   - final_rulebooks
   - survivors
   - profile_catalog
   - validation_results
   - exit_trades
   - frozen OOS
   - `data/_system/analysis`
   - `candidate_denylist.json`
   - `.env`, `.env.backup`
   - Stage3 동적 본문

`stage2_survivor_loader.pyc`처럼 파일명에 `survivor`가 포함된 bytecode는 원본 룰풀이 아니라 재생성 캐시다. 첫 검증에서 이를 과잉 차단해 삭제 전에 중단됐고, 원본 JSONL과 bytecode를 구분하도록 보호 조건을 좁힌 후 다시 전체 검증했다. 첫 시도에서는 삭제가 한 건도 발생하지 않았다.

## 2. 실제 삭제 결과

삭제 실행 결과:

```text
DELETE_VALIDATION_OK
safe_rows=45
expanded_files=343
bytes=5150150
live_overlap=0
protected_overlap=0

DELETION_COMPLETE
deleted_rows=45
deleted_files=343
deleted_bytes=5150150
remaining_targets=0
```

Git 확인 결과 SAFE 대상 343개는 모두 ignore 또는 untracked 상태였고 Git 추적 파일은 0개였다. 따라서 Git은 개별 삭제 diff를 만들 수 없다. 대신 다음을 커밋해 삭제 행위와 검증을 추적 가능하게 했다.

- 45행 삭제 manifest
- 삭제 실행 스크립트
- smoke-check 결과
- REVIEW 조사표와 readout

## 3. 삭제 후 스모크 체크

bytecode가 다시 생성되지 않도록 `PYTHONDONTWRITEBYTECODE=1` 상태에서 확인했다.

### 3.1 살아있는 파일 재검증

- `live_dependency_tree.csv`: 148개
- 삭제 후 존재: 148개
- 누락: 0개

### 3.2 Python 진입점 import

다음 11개 진입점·핵심 모듈 import 성공:

- `scripts.research.run_stage2`
- `scripts.research.run_stage3_aggressive`
- `scripts.research.build_stage3_live_pool`
- `engine.live.elite_shadow_report`
- `scripts.export_real_dashboard_buy_candidates`
- `scripts.run_live`
- `api_server_candidate_only`
- `api_server_aftermarket`
- `scripts.build_sentiment_history`
- `scripts.ensure_caddy_dashboard_route`
- `data/_system/ops/live_candidate_slots.py`

### 3.3 Stage3 동적 본문

다음 핵심 파일이 존재하고 wrapper가 `main`을 노출하는지 확인했다.

```text
scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
```

결과: PASS.

### 3.4 Shell 진입점

- `scripts/dashboard_guard.sh`: `bash -n` PASS
- `scripts/live_candidate_slots_guard.sh`: `bash -n` PASS

### 3.5 삭제 경로 재확인

45개 SAFE 경로 중 다시 존재하는 경로: 0개.

세부 결과는 `safe_delete_smoke_check.csv`에 기록했다.

## 4. REVIEW 조사 1 — data/_system/pipeline

### 판정: DELETE_OK

| 항목 | 값 |
|---|---:|
| 파일 수 | 4,155 |
| 크기 | 78.804 MiB |
| 최초 수정 | 2026-06-04 04:57:15 KST |
| 최종 수정 | 2026-06-05 21:01:46 KST |
| 살아있는 참조 | 0 |
| 미도달 구 스크립트 참조 | 12 |

이 경로는 살아있는 소스인 `engine/pipeline`과 다른 **데이터 산출물 디렉터리**다.

구조:

```text
data/_system/pipeline/v1/
├── runs/
├── analysis/
└── promotions/
```

참조하는 파일은 다음 구 파이프라인·분석 스크립트뿐이며 모두 살아있는 148개 집합 밖이다.

- `engine/pipeline/orchestrator.py`
- `scripts/pipeline/run_full_training_candidates.py`
- `scripts/pipeline/run_screening_small.py`
- `scripts/pipeline/run_rolling_batch_small.py`
- `scripts/pipeline/run_batch.py`
- `scripts/pipeline/run_rolling_nvda.py`
- 구 분석·promotion·replay 스크립트

현재 Stage2/Stage3는 `exp_batch_stage123_2009_20260616_full`을 사용하며 `data/_system/pipeline/v1`을 읽지 않는다. 내부에서 절대 보존 파일명과 frozen 파일도 발견되지 않았다.

운영·현재 학습 기준으로 전체 디렉터리 삭제 가능하다. 실제 삭제는 다음 승인까지 보류했다.

## 5. REVIEW 조사 2 — condition DB 두 개

### 5.1 data/_system/condition_db_sell_omen_clean

판정: **DELETE_OK**

| 항목 | 값 |
|---|---:|
| CSV | 183개 |
| 크기 | 123.875 MiB |
| 최종 수정 | 2026-06-09 07:35:39 KST |
| 살아있는 참조 | 0 |
| 미도달 참조 | 2 |

참조 2건:

- `scripts/ml_sell_omen/build_condition_db_expanded.py`
  - 기본 출력 디렉터리
- `scripts/research/lr8d_postrun_analysis.py`
  - 과거 LR8D coverage 분석 입력

두 스크립트 모두 현재 살아있는 148개 집합에 포함되지 않는다. Stage2·Stage3·라이브 실행부에서 `condition_db` 문자열 참조는 0건이다.

### 5.2 data/_system/condition_db_sell_omen_lr8d85

판정: **DELETE_OK**

| 항목 | 값 |
|---|---:|
| CSV | 85개 |
| 크기 | 60.098 MiB |
| 최종 수정 | 2026-06-10 00:01:55 KST |
| 살아있는 참조 | 0 |
| 현재 소스 직접 참조 | 0 |

과거 로그에서 85개 종목의 POC 학습 입력으로 사용된 것이 확인됐다.

```text
data_dir: data/_system/condition_db_sell_omen_lr8d85
raw_rows=137392
raw_tickers=85
```

이 입력으로 생성된 다음 점수 파일은 현재 살아있는 코드에서 참조한다.

```text
data/_system/ml_sell_omen/sell_omen_scores_lr8d85.csv
```

하지만 살아있는 코드가 참조하는 것은 점수 파일이며 원본 condition DB 두 디렉터리는 아니다. DB 삭제가 현재 점수 조회·Stage2/3·실행 경로에 영향을 주지 않는다.

두 디렉터리는 builder로 다시 생성 가능한 중간 데이터다. 다만 재생성 시 외부 가격 데이터 수정으로 byte-identical 보장은 없으므로 연구 재현을 중요시한다면 별도 압축 보관 정책을 선택할 수 있다. 현재 운영 관점 판정은 DELETE_OK다.

## 6. REVIEW 조사 3 — backup

### 판정: PARTIAL

| 항목 | 값 |
|---|---:|
| 파일 | 4,684개 |
| 크기 | 72.422 MiB |
| 최종 수정 | 2026-07-10 03:27:06 KST |
| 살아있는 직접 참조 | 0 |
| 현재 소스 직접 참조 | 0 |

주요 구성:

- tar.gz 롤백 스냅샷 23개
- `pre_refactor_20260603_155613`: 2,255파일, 10.221 MiB
- `screening_halt_20260603_175058`: 2,378파일, 10.021 MiB
- 대시보드·뉴스 변경 전 소규모 파일 스냅샷

현재 파이프라인은 `backup` 하위 파일을 직접 읽거나 import하지 않는다. 내부에서 이번 절대 보존 목록에 해당하는 파일명도 발견되지 않았다.

그러나 이 디렉터리의 목적 자체가 장애 복구와 rollback이다. 전체를 한 번에 DELETE_OK로 판정하면 최근 위험 변경 전 복구점을 잃는다.

권장 후속 분할:

1. 최근 안전·주문·후보생성 변경 전 tar.gz는 KEEP 후보.
2. 오래된 `pre_refactor`, `screening_halt` 전체 스냅샷은 별도 내용 중복 검사 후 DELETE_OK 후보.
3. 동일 변경 계열의 연속 tar.gz는 최신 1개 또는 중요 checkpoint만 유지.
4. 보존 기간을 정한 후 하위 단위로 삭제.

따라서 디렉터리 전체 판정은 PARTIAL이다.

## 7. REVIEW 조사 4 — data/_system/logs

### 판정: DELETE_OK

| 항목 | 값 |
|---|---:|
| 파일 | 8개 |
| 크기 | 58.703 MiB |
| 최종 수정 | 2026-06-24 11:17:54 KST |
| 살아있는 참조 | 0 |
| 열린 파일 핸들 | 0 |
| PID 파일 | 3개, 전부 stale |

구성:

| 파일 | 크기 | 마지막 수정 KST |
|---|---:|---|
| `run_live_stage1_alpaca.log` | 58.36 MiB | 2026-06-24 11:17:54 |
| `run_live_stage1.log` | 337.44 KiB | 2026-06-10 00:01:59 |
| `rebuild_lr8d85_conddb.log` | 6.53 KiB | 2026-06-10 00:01:55 |
| `poc_train_lr8d85.log` | 3.49 KiB | 2026-06-10 00:04:06 |
| `kis.log` | 2.16 KiB | 2026-05-26 22:39:27 |
| PID 3개 | 각 7 bytes | stale |

현재 열린 로그는 다음 별도 경로다.

- `logs/api_server_candidate_only_8001_guard*.log`
- `logs/live_candidate_slots_daemon_guard*.log`
- `data/logs/kingmaker.log`
- `data/logs/error.log`
- `data/logs/trades.log`

따라서 `data/_system/logs`는 현재 로그 경로가 아니며, frozen 로그도 아니다. 전체 삭제 가능하다. 실제 삭제는 다음 승인까지 보류했다.

## 8. 다음 삭제 지시에서의 권장 범위

승인 시 바로 삭제 가능한 합계:

| 경로 | 크기 |
|---|---:|
| `data/_system/pipeline` | 78.804 MiB |
| `data/_system/condition_db_sell_omen_clean` | 123.875 MiB |
| `data/_system/condition_db_sell_omen_lr8d85` | 60.098 MiB |
| `data/_system/logs` | 58.703 MiB |
| 합계 | **321.480 MiB** |

`backup` 72.422 MiB는 하위 분할 승인 전 전체 삭제 금지.

## 9. 산출물

- `safe_delete_execution.csv`: 실제 삭제된 45행
- `safe_delete_smoke_check.csv`: 삭제 후 검증
- `review_top_investigation.csv`: REVIEW 상위 판정표
- `safe_delete_review_readout.md`: 본 문서
- `execute_safe_deletion.py`: 보호 assertion 포함 실행 스크립트

2단계 REVIEW 대상 삭제는 0건이다.
