# STALE_OUTPUT 잔재 삭제 결과

- 기준일: 2026-07-11
- 삭제 기준: `filter_gate_outputs.csv`의 `verdict=STALE_OUTPUT`
- 삭제·변경 대상: 비활성 Stage3 live-pool 잔재 4종, 실제 파일 6개
- 스냅샷: 생성하지 않음
- ACTIVE_LIVE·REGEN_OK·ORIGIN·분석 산출물 삭제: 0건

## 1. 삭제 전 대조

`filter_gate_outputs.csv`에서 STALE_OUTPUT으로 분류된 4행을 실제 파일로 확장했다.

| 산출물 유형 | 파일 수 | 바이트 |
|---|---:|---:|
| Stage3 live pool | 1 | 2,206,962 |
| Stage3 live pool filtered | 1 | 1,775,994 |
| Stage3 live-pool rejected samples | 2 | 111,776 |
| Stage3 live-pool summaries | 2 | 3,733 |
| **합계** | **6** | **4,098,465** |

CSV의 행 수·파일 수·용량과 실제 파일시스템이 정확히 일치했다.

삭제 대상 전체:

| 경로 | 크기 |
|---|---:|
| `data/_system/central/stage3_live_pool/stage3_live_pool.jsonl` | 2,206,962 bytes |
| `data/_system/central/stage3_live_pool/stage3_live_pool_filtered.jsonl` | 1,775,994 bytes |
| `data/_system/central/stage3_live_pool/rejected_sample.jsonl` | 50,027 bytes |
| `data/_system/central/stage3_live_pool/rejected_sample_filtered.jsonl` | 61,749 bytes |
| `data/_system/central/stage3_live_pool/summary.json` | 1,800 bytes |
| `data/_system/central/stage3_live_pool/summary_filtered.json` | 1,933 bytes |

## 2. 보호·교집합 검사

삭제 직전 각 파일을 다음 집합과 비교했다.

- 모든 ORIGIN 룰풀
  - `rulebooks_all`
  - `entry_rulebooks`
  - `final_rulebooks`
  - `survivors`
- ACTIVE_LIVE 6종
  - `survivors`
  - `central_index`
  - `candidate_denylist.json`
  - `live_candidate_list_20260707.json`
  - `live_slots_state.json`
  - `real_dashboard_buy_candidates.json`
- REGEN_OK 6종
- `data/_system/analysis` 전체
- `.env`, `.env.backup`
- 살아있는 파이프라인 148개
- 현재 열린 파일 핸들
- 현재 실행 프로세스

결과:

| 검사 | 결과 |
|---|---:|
| ACTIVE_LIVE·REGEN_OK 중첩 | 0 |
| ORIGIN 이름·경로 중첩 | 0 |
| 살아있는 148개 파일 중첩 | 0 |
| 분석 경로 중첩 | 0 |
| 열린 파일 핸들 | 0 |
| `scripts/run_live.py` 실행 프로세스 | 0 |
| `--central-stage3-mix on` 프로세스 | 0 |
| 건너뛴 대상 | 0 |

현재 실행 중인 프로세스는 다음 두 개였다.

```text
uvicorn api_server_candidate_only:app --port 8001
data/_system/ops/live_candidate_slots.py daemon --interval 60
```

현재 daemon/API 직접 코드 체인에서 여섯 대상 파일명 참조는 0건이었다.

## 3. 실제 삭제

| 항목 | 결과 |
|---|---:|
| 삭제 유형 행 | 4 |
| 삭제 파일 | 6 |
| 삭제 바이트 | 4,098,465 |
| 남은 대상 파일 | 0 |
| Git 추적 삭제 | 0 |
| ignored/untracked 삭제 | 6 |

여섯 파일은 모두 Git 추적 대상이 아니었다. 따라서 Git 삭제 diff는 생기지 않으며, 실제 삭제 내역은 `stale_output_delete_manifest.csv`의 `deletion_status=DELETED`로 기록했다.

## 4. 삭제 후 스모크 체크

### 살아있는 파이프라인

- 기준: `live_dependency_tree.csv`
- 예상: 148개
- 존재: 148개
- 누락: 0개

### Python 진입점

다음 11개 진입점·핵심 모듈 import가 성공했다.

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

- `scripts/dashboard_guard.sh`: PASS
- `scripts/live_candidate_slots_guard.sh`: PASS

### 현재 라이브 참조

현재 활성 daemon/API 직접 코드에서 삭제 대상 파일명 참조: 0건.

현재 열린 대상 파일 핸들: 0건.

현재 `run_live.py` 또는 Stage3 mix 프로세스: 0건.

## 5. 휴면 지원 경로 주의

현재 실행 중인 라이브는 대상 파일을 읽지 않지만, 저장소에는 미래 Stage3 mix를 위한 휴면 경로가 남아 있다.

- `engine/live/central_control.py`
  - 기본 경로 `data/_system/central/stage3_live_pool/stage3_live_pool.jsonl` 선언
- `scripts/research/build_stage3_live_pool.py`
  - `stage3_live_pool.jsonl`과 `rejected_sample.jsonl` 재생성 코드

따라서 나중에 `run_live.py --central-stage3-mix on`을 사용하려면 먼저 `build_stage3_live_pool.py`로 pool을 재생성해야 한다. 현재 기본 실행과 현재 daemon/API에는 영향이 없다.

## 6. 산출물

- `stale_output_delete_targets.csv`
  - STALE_OUTPUT 4행과 실제 파일 수·크기 대조
- `stale_output_delete_manifest.csv`
  - 실제 삭제 6파일, 크기, Git 추적 여부, 삭제 상태
- `stale_output_delete_skipped.csv`
  - 건너뛴 항목 0건
- `stale_output_delete_result.json`
  - preflight와 실행 집계
- `stale_output_delete_smoke_check.csv`
  - 삭제 후 스모크 체크 결과
- `execute_stale_output_delete.py`
  - 보호 검사와 삭제 실행기
- `stale_output_delete_readout.md`
  - 본 문서

## 7. 최종 판정

`filter_gate_outputs.csv`에서 STALE_OUTPUT으로 분류된 4종만 삭제했다. ACTIVE_LIVE·REGEN_OK·ORIGIN·분석 결과·환경 파일은 모두 보존됐다. 현재 라이브 후보 daemon과 API는 삭제된 파일을 읽지 않으며, 148개 의존성과 11개 진입점 import는 정상이다.
