# DELETE_OK 4경로 스냅샷·삭제 및 운영 불필요 파일 2차 선별

- 기준일: 2026-07-10
- 1단계: 스냅샷 생성 후 원본 4경로 삭제 완료
- 2단계: 남은 REVIEW 재분류만 수행, 추가 삭제 0건
- 절대 보존 항목 및 `backup` 기존 내용 삭제·수정: 없음

## 0. 결과 요약

### 1단계

| 항목 | 결과 |
|---|---:|
| 스냅샷 | 4개 tar.gz |
| 원본 파일 | 4,431개 |
| 원본 용량 | 337,096,609 bytes / 321.480 MiB |
| 압축 용량 | 67,404,031 bytes / 64.278 MiB |
| tar 파일 수 대조 | 4/4 PASS |
| tar payload 바이트 대조 | 4/4 PASS |
| SHA-256 재검증 | 4/4 PASS |
| 삭제된 원본 경로 | 4개 |
| 살아있는 의존성 | 148/148 존재 |
| Python 진입점 import | 11/11 PASS |
| 추가 REVIEW 삭제 | 0건 |

스냅샷 위치:

```text
backup/pre_cleanup_20260710/
```

### 2단계

기존 REVIEW 1,571행 중 1단계에서 삭제된 네 디렉터리 행을 제외한 **1,567행**을 다시 분류했다.

| 판정 | 행 | 대표 파일 수 | 대표 용량 |
|---|---:|---:|---:|
| `DELETE_OK` | **1,082** | 3,507 | 1,795,116,467 bytes / 약 1.672 GiB |
| `PARTIAL` | **379** | 379 | 17,338,572 bytes / 약 16.54 MiB |
| `KEEP` | **106** | 5,088 | 160,997,981 bytes / 약 153.54 MiB |

이번 단계에서는 `DELETE_OK`로 재분류된 항목도 실제 삭제하지 않았다.

## 1. 스냅샷 및 삭제 내역

| 삭제 원본 | 파일 | 원본 용량 | 스냅샷 | 압축 용량 | SHA-256 |
|---|---:|---:|---|---:|---|
| `data/_system/pipeline` | 4,155 | 82,632,425 | `backup/pre_cleanup_20260710/data__system__pipeline.tar.gz` | 11,765,436 | `2cb035081030c21bf6d1ff9144805a00f2b917dbff04fc543b219f358b367eec` |
| `data/_system/condition_db_sell_omen_clean` | 183 | 129,892,573 | `backup/pre_cleanup_20260710/data__system__condition_db_sell_omen_clean.tar.gz` | 34,009,969 | `4b09b8d80c0a0163e08f3cfd6c3d35978bb4edfa8edae7f05cd2f32a61b97906` |
| `data/_system/condition_db_sell_omen_lr8d85` | 85 | 63,017,369 | `backup/pre_cleanup_20260710/data__system__condition_db_sell_omen_lr8d85.tar.gz` | 17,549,331 | `cb81e2feec0e017d7f18e92c873fc29f6e6d48a965cde1225608bce4d2b62cf9` |
| `data/_system/logs` | 8 | 61,554,242 | `backup/pre_cleanup_20260710/data__system__logs.tar.gz` | 4,079,295 | `c9193f85ba4b33eaf102f3daa37c81d93f5ece00e0a9bcdc7c683d8fe965ea71` |

스냅샷 생성 순서:

1. 각 원본 경로의 전체 파일 상대경로와 파일 크기 manifest를 메모리에서 생성.
2. 절대 보존 파일명, frozen 패턴, audit 경로 포함 여부 검사.
3. 경로별 독립 tar.gz 생성.
4. tar를 다시 열어 파일 member 상대경로·파일 수·payload bytes를 원본 manifest와 완전 일치 비교.
5. 절대경로 또는 `..` member가 없는지 검사.
6. 네 스냅샷이 모두 PASS한 뒤에만 원본 네 경로 삭제.
7. 삭제 후 tar 목록·payload bytes·SHA-256 재검증.

원본 네 경로는 현재 모두 존재하지 않는다. 기존 `backup` 내용은 건드리지 않고 `pre_cleanup_20260710`만 추가했다.

## 2. 삭제 후 스모크 체크

### 살아있는 파일

- 기준 집합: `live_dependency_tree.csv`
- 예상: 148개
- 존재: **148개**
- 누락: **0개**

### Python 진입점

`PYTHONDONTWRITEBYTECODE=1` 상태에서 다음 11개를 import했다.

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

결과: **11/11 PASS**.

Stage3가 동적으로 읽는 핵심 본문도 존재하며 wrapper의 `main` 노출을 확인했다.

```text
scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
```

Shell 진입점:

- `scripts/dashboard_guard.sh`: PASS
- `scripts/live_candidate_slots_guard.sh`: PASS

## 3. 2차 선별 기준

운영 도달성은 다음을 합쳐 판정했다.

1. Stage2 → Stage3 → 라이브 후보 → 실행의 148개 재귀 import 집합.
2. Stage3 비표준 확장 동적 본문.
3. 활성 코드의 구체적 파일·디렉터리 리터럴.
4. 현재 열린 파일 핸들.
5. 현재 실행 프로세스에 포함된 실험 경로.
6. 실험의 `summary.json`, `batch_summary.json`, `run_status.json` 완료 이벤트.
7. 파일 마지막 수정일과 이름의 smoke/audit/verify/rotation 표식.

애매한 스크립트·문서·시스템 데이터는 `PARTIAL`, 테스트·백업·활성 경로는 `KEEP`으로 유지했다.

## 4. DELETE_OK 재분류

| 유형 | 행 | 대표 파일 | 용량 |
|---|---:|---:|---:|
| 종료된 실험 데이터 | 674 | 3,099 | 1,591,116,141 bytes |
| 회전·종료 로그 | 352 | 352 | 203,999,936 bytes |
| stale lock/PID | 56 | 56 | 390 bytes |
| 합계 | **1,082** | **3,507** | **1,795,116,467 bytes** |

### 4.1 종료된 실험 데이터

다음 조건을 모두 만족한 비주력 실험 산출물만 DELETE_OK로 내렸다.

- 현재 생산 배치 `exp_batch_stage123_2009_20260616_full`이 아님.
- 현재 프로세스가 해당 실험 경로를 사용하지 않음.
- `summary.json` 또는 `batch_summary.json` 존재, 또는 smoke/audit/verify 등 명시적 일회성 이름.
- CE 두 배치는 `run_status.json`에서 `stage2_done`, `stage3_done`, `compare_done`과 return code 0 확인.
- 원본 룰풀·survivor·profile catalog·validation·exit_trades는 이전 감사에서 이미 KEEP으로 분리되어 본 목록에 없음.

큰 후보는 2026-07-04~05의 range predictor 실험 `predictors_all.jsonl` 계열이다. 개별 파일이 약 42~48.5 MiB이며 운영 경로에서 참조되지 않는다.

### 4.2 회전·종료 로그

DELETE_OK 조건:

- 열린 파일 핸들 없음.
- 활성 import/리터럴 참조 없음.
- 타임스탬프, legacy, rollback, smoke, shard, launcher, wrapper 등 종료 표식이 있음.
- 또는 `.log.zip` 압축 회전 로그.
- 현재 생산 Stage123 재개 로그는 제외.

대표 후보:

- `logs/elite_strategy_sim_20260701_133052_entry_quality_reset.log`: 25.28 MiB
- `logs/run_live_lr8d16_legacy_20260702_134511_chart_exit_tp_sl.log`: 21.60 MiB
- `data/logs/run_live_central_off_rollback.log`: 19.51 MiB
- 과거 `data/logs/kingmaker.*.log.zip`: 회전 로그로 DELETE_OK

### 4.3 stale lock/PID

56개 모두 다음을 만족했다.

- 실행 PID 없음.
- 열린 파일 핸들 없음.
- 활성 경로 참조 없음.
- 1일 이상 경과.

## 5. KEEP 재분류

| 유형 | 행 | 대표 파일 | 용량 |
|---|---:|---:|---:|
| 백업 스냅샷 | 5 | 4,979 | 146,562,649 bytes |
| 활성 런타임 의존성 | 14 | 22 | 13,874,695 bytes |
| 테스트 코드 | 87 | 87 | 560,637 bytes |
| 합계 | **106** | **5,088** | **160,997,981 bytes** |

### 5.1 백업

다음은 자동 삭제 후보에서 제외했다.

- `backup` 전체
- `data/backups`
- `data/_system/backups`
- `data/_system/code_backups`
- `data/_system/ga_dump_backup_before_trainonly_20260601_190125`

이번에 추가된 `backup/pre_cleanup_20260710`도 보존된다.

### 5.2 테스트

`tests/` 아래 87행은 운영 import 비도달과 관계없이 별도 테스트 감사 대상으로 KEEP했다.

### 5.3 활성 의존성

초기 REVIEW였지만 구체 경로 참조 또는 열린 핸들이 확인되어 KEEP으로 올린 항목:

- `data/_system/ml_sell_omen`
  - `engine/pipeline/context.py`
  - `engine/central/signal_collector.py`
  - `engine/central/stage2_survivor_loader.py`
- `data/_system/calendars`
  - `engine/live/us_market_calendar.py`
- 현재 열린 `data/logs/kingmaker.log`, `error.log`, `trades.log`
- 동적·보조 실행 경로로 도달하는 일부 live/rehearsal 모듈

현재 프로젝트에서 열린 로그는 총 7개다. REVIEW에 있던 위 3개는 KEEP으로 승격했고, guard 로그 4개는 기존 감사에서 이미 KEEP이었다.

## 6. PARTIAL 재분류

| 유형 | 행 | 용량 |
|---|---:|---:|
| 미참조 시스템 데이터 | 86 | 7,692,051 bytes |
| 운영 상태 백업 | 19 | 2,445,496 bytes |
| 완료 여부가 불명확한 실험 데이터 | 59 | 2,068,264 bytes |
| 미참조 일반 로그 | 64 | 2,041,490 bytes |
| 일회성·구버전 코드 | 120 | 1,645,470 bytes |
| 기타 미분류 | 10 | 494,018 bytes |
| 현재 생산 배치 이력 로그 | 1 | 430,384 bytes |
| 청산 스냅샷 | 1 | 350,921 bytes |
| 미참조 문서·노트 | 17 | 168,892 bytes |
| 내용 중복 | 2 | 1,586 bytes |
| 합계 | **379** | **17,338,572 bytes** |

대표 PARTIAL:

- `data/_system/ticker_sentiment_update.log.jsonl`: 동적 사용 가능성 확인 필요.
- `data/_system/ticker_universe.json` 및 `.bak`: 이전 universe인지 현재 수동 경로인지 확인 필요.
- `logs/live_candidate_slots_daemon.log`: 현재 열린 guard 로그와 이름이 달라 회전 잔여 가능성이 높지만 단정 보류.
- `exp_batch_stage123_2009_20260616_full.resume_20260706_103328.log`: 현재 생산 배치 재개 이력이라 보존 판단 필요.
- `data/_system/liquidation_snapshots`: 청산 검증 재현 가치 가능.
- 120개 미도달 Python 스크립트·engine 모듈: 수동 실행 가능성 때문에 자동 DELETE_OK 금지.
- 문서·노트 17개: 설계·장애 이력 가치가 불명확해 자동 삭제 금지.

## 7. 보호 규칙 검증

다음은 삭제·DELETE_OK 승격 대상에서 제외했다.

- rulebooks_all
- entry_rulebooks
- final_rulebooks
- survivors
- profile_catalog
- validation_results
- exit_trades
- frozen OOS
- `data/_system/analysis` 전체
- `candidate_denylist.json`
- `.env`, `.env.backup`
- 살아있는 파이프라인 148개
- Stage3 동적 본문
- `backup` 전체
- `tests/`
- 활성 로그

2차 선별 CSV는 조사 결과이며 삭제 명령으로 사용하지 않았다.

## 8. 산출물

- `delete_ok_snapshot_execution.csv`
- `delete_ok_snapshot_smoke_check.csv`
- `snapshot_delete_ok_paths.py`
- `operational_unused_second_pass.csv`
- `operational_unused_second_pass_summary.json`
- `run_second_pass_operational_review.py`
- `run_second_pass_operational_review_v2.py`
- `pre_cleanup_snapshot_and_second_pass_readout.md`

2단계 추가 삭제: **0건**.
