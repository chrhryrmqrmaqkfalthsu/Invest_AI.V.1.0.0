# 2차 DELETE_OK 1,082행 삭제 실행 결과

- 기준일: 2026-07-10
- 삭제 기준: `operational_unused_second_pass.csv`의 `safety_verdict=DELETE_OK`
- 스냅샷: 생성하지 않음
- PARTIAL·KEEP 삭제: 0건

## 1. 삭제 전 대조

삭제 직전 대상 전체를 두 개의 manifest로 고정했다.

- 행 단위: `second_pass_delete_targets.csv`
- 실제 파일 단위: `second_pass_delete_expanded_manifest.csv`

| 항목 | CSV | 실제 파일시스템 | 결과 |
|---|---:|---:|---|
| DELETE_OK 행 | 1,082 | 1,082 | 일치 |
| 대표 파일 | 3,507 | 3,507 | 일치 |
| 용량 | 1,795,116,467 bytes | 1,795,116,467 bytes | 일치 |

유형별 구성:

| 유형 | 행 | 파일 | 바이트 |
|---|---:|---:|---:|
| 종료된 실험 데이터 | 674 | 3,099 | 1,591,116,141 |
| 회전·종료 로그 | 352 | 352 | 203,999,936 |
| stale lock/PID | 56 | 56 | 390 |

전체 목록을 채팅으로 한 번에 출력하면 도구 응답 한도를 넘기므로, 삭제 전에 1,082행 전체를 `second_pass_delete_targets.csv`에 기록하고 실제 3,507파일 전체를 `second_pass_delete_expanded_manifest.csv`에 기록했다.

## 2. 보호·교집합 검사

각 DELETE_OK 행과 그 하위 파일을 다음 대상과 비교했다.

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
- 살아있는 파이프라인 148개
- Stage3 동적 본문
- `data/_system/ml_sell_omen`
- `tests/`
- `backup/`
- 현재 열린 파일 핸들
- 활성 로그 `data/logs/kingmaker.log`, `error.log`, `trades.log`
- CSV에서 PARTIAL 또는 KEEP으로 분류된 경로

검사 결과:

| 항목 | 결과 |
|---|---:|
| 보호 대상 교집합 | 0 |
| 살아있는 경로 교집합 | 0 |
| 열린 파일 교집합 | 0 |
| PARTIAL·KEEP 경로 중첩 | 0 |
| 건너뛴 행 | 0 |

`second_pass_delete_skipped.csv`는 헤더만 있으며 데이터 행은 없다.

## 3. 실제 삭제

| 결과 | 값 |
|---|---:|
| 삭제 행 | 1,082 |
| 삭제 파일 | 3,507 |
| 삭제 바이트 | 1,795,116,467 |
| 삭제 용량 | 약 1.672 GiB |
| 남은 DELETE_OK 파일 | 0 |
| Git 추적 삭제 | 121파일 |
| untracked/ignored 삭제 | 3,386파일 |

Git 추적 파일은 커밋의 삭제 diff로 남고, untracked/ignored 파일은 `second_pass_delete_expanded_manifest.csv`의 `git_tracked` 및 `deletion_status=DELETED`로 기록된다.

## 4. 삭제 후 스모크 체크

### 살아있는 파이프라인

- 기준 파일: `live_dependency_tree.csv`
- 예상: 148개
- 존재: 148개
- 누락: 0개

### Python 진입점

다음 11개 import가 성공했다.

1. `scripts.research.run_stage2`
2. `scripts.research.run_stage3_aggressive`
3. `scripts.research.build_stage3_live_pool`
4. `engine.live.elite_shadow_report`
5. `scripts.export_real_dashboard_buy_candidates`
6. `scripts.run_live`
7. `api_server_candidate_only`
8. `api_server_aftermarket`
9. `scripts.build_sentiment_history`
10. `scripts.ensure_caddy_dashboard_route`
11. `data/_system/ops/live_candidate_slots.py`

### Stage3 동적 본문

다음 파일의 존재와 wrapper `main` 노출을 확인했다.

```text
scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
```

결과: PASS.

### Shell guard

- `scripts/dashboard_guard.sh`: `bash -n` PASS
- `scripts/live_candidate_slots_guard.sh`: `bash -n` PASS

### ml_sell_omen

`data/_system/ml_sell_omen`이 존재하고 다음 세 파일의 참조가 유지되는지 확인했다.

- `engine/pipeline/context.py`
- `engine/central/signal_collector.py`
- `engine/central/stage2_survivor_loader.py`

결과: 3/3 PASS.

### 활성 로그

다음 로그가 유지됐다.

- `data/logs/kingmaker.log`
- `data/logs/error.log`
- `data/logs/trades.log`

PARTIAL 또는 KEEP으로 분류된 파일 경로의 삭제 후 누락은 0건이다.

## 5. 산출물

- `second_pass_delete_targets.csv`: 삭제 대상 1,082행
- `second_pass_delete_expanded_manifest.csv`: 실제 삭제 파일 3,507개와 크기·Git 추적 여부
- `second_pass_delete_skipped.csv`: 건너뛴 항목, 현재 0건
- `second_pass_delete_result.json`: 실행 집계
- `second_pass_delete_smoke_check.csv`: 삭제 후 검증 결과
- `execute_second_pass_delete.py`: 보호 assertion과 삭제 실행기
- `second_pass_delete_readout.md`: 본 문서

## 6. 최종 판정

`operational_unused_second_pass.csv`의 DELETE_OK 1,082행만 삭제했다. PARTIAL·KEEP, 절대 보존 대상, 살아있는 148개, Stage3 동적 본문, ml_sell_omen, tests, backup 및 활성 로그는 모두 유지됐다.
