# Kingmaker 미사용·고아 파일 식별 감사

- 기준 시각: 2026-07-10
- 모드: **read-only 식별**
- 삭제·이동·원본 코드/설정/룰북 수정: **없음**
- 판정 원칙: 확실한 재생성 캐시·명시적 구버전 사본·죽은 PID 임시파일만 `SAFE_TO_DELETE`; 불명확한 항목은 전부 `REVIEW`

## 0. 결론

프로젝트 전체를 현재 Stage2 → Stage3 → 라이브 후보 생성 → 라이브 실행·대시보드·정기 작업 진입점에서 역추적했다.

정적 import, 동적 파일 로드, 구체적 경로 리터럴, Python 패키지 `__init__.py`, crontab/systemd/실행 프로세스를 합쳐 살아있는 의존성 집합을 만들었다.

| 위험도 | 목록 행 | 행이 대표하는 파일 수 | 대표 크기 |
|---|---:|---:|---:|
| `SAFE_TO_DELETE` | **45** | 343 | **4.91 MiB** |
| `REVIEW` | **1,571** | 13,402 | **2.09 GiB** |
| `KEEP` | **406** | 513,527 | **120.90 GiB** |

대용량 트리는 디렉터리 한 행으로 집계하고, 소스·루트 상태 파일·로그·비주력 실험 산출물은 파일 단위로 기록했다. 따라서 상위 디렉터리 행과 그 안의 개별 캐시 행이 일부 중첩될 수 있으며, 세 위험도 크기를 프로젝트 총량으로 단순 합산하면 안 된다.

**실제 삭제는 수행하지 않았다.**

## 1. 확정한 파이프라인 진입점

### 1.1 Stage2·Stage3 학습

```text
scripts/research/run_stage23_batch.py
├── scripts/research/run_stage2.py
└── scripts/research/run_stage3_aggressive.py
    └── scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
```

Stage3의 현재 파일은 단순 wrapper이고, 실제 본문은 확장자가 `.py`로 끝나지 않는 백업 파일을 `SourceFileLoader`로 동적 로드한다. 이 파일을 일반적인 `.py` 정적 스캔만으로는 고아로 오판할 수 있다.

이번 감사에서는 동적 본문을 Python 소스로 별도 파싱해 다음 핵심 모듈까지 살아있는 경로로 포함했다.

- `engine/pipeline/exit_gene.py`
- `engine/pipeline/stage3_gate.py`
- `engine/pipeline/context.py`
- `engine/pipeline/topn_survivor.py`
- `engine/learning/execution_mode_backtest.py`
- `engine/learning/genetic.py`
- `engine/learning/fitness_cache.py`
- `engine/strategies/rulebook.py`

따라서 이름에 `.bak`이 들어가더라도 Stage3가 실제 로드하는 본문은 `KEEP`이다.

### 1.2 라이브 후보 생성

확정 진입점:

- `scripts/research/build_stage3_live_pool.py`
- `engine/live/elite_shadow_report.py`
- `data/_system/ops/live_candidate_slots.py`
- `scripts/export_real_dashboard_buy_candidates.py`

주요 경로:

```text
원본 Stage2/Stage3 batch
→ elite_shadow_report
→ live_candidate_slots
→ evaluate_candidate / market context / regular-hours gate
→ live_slots_state 및 대시보드 후보
```

`candidate_denylist.json`, 원본 룰북, 후보 소스 행, Stage3 profile catalog는 이 경로의 입력 또는 절대 보존 대상이므로 모두 `KEEP`이다.

### 1.3 실전 실행부

systemd의 `kingmaker.service`에서 확인한 실제 진입점:

```text
scripts/run_live.py --mode real
```

핵심 의존 경로:

```text
run_live.py
├── broker.factory
├── central_control
├── Runner
├── SafetyLayer
├── Scheduler / MarketClock
├── scheduled_open_buy_queue
├── live universe
├── Telegram notifier / locked bot
└── learned/demo rulebook
```

현재 서비스 unit은 disabled 상태지만, unit의 `ExecStart`는 위 진입점을 명시한다. 외부 운영에서 재기동될 수 있으므로 실행부 전체는 살아있는 경로로 보존했다.

### 1.4 대시보드·정기 작업

현재 실행 중인 대시보드:

```text
api_server_candidate_only.py
→ api_server_aftermarket.py
→ api_server.py 및 live/dashboard patch 모듈
```

crontab에서 확인한 정기 진입점:

- `scripts/build_sentiment_history.py`
- `scripts/dashboard_guard.sh`
- `scripts/live_candidate_slots_guard.sh`

실행 중 프로세스에서도 다음을 확인했다.

- `uvicorn api_server_candidate_only:app --port 8001`
- `data/_system/ops/live_candidate_slots.py daemon --interval 60`

## 2. 의존성 추적 결과

| 항목 | 결과 |
|---|---:|
| 검사한 소스·설정 파일 | 395 |
| 살아있는 의존 파일 | **148** |
| 내부 Python import edge | 1,000 |
| 구체적 파일·디렉터리 리터럴 참조 | 82 |
| Python 파싱 실패 | **0** |
| 최종 CSV 행 | 2,022 |

살아있는 148개 파일의 최상위 분포:

| 루트 | 파일 수 |
|---|---:|
| `engine` | 129 |
| `scripts` | 12 |
| API 서버 파일 | 3 |
| `data/_system/ops` | 1 |
| 대시보드 HTML | 3 |

정적 import 외에 다음을 추가 보정했다.

1. Python이 자동 실행하는 상위 패키지 `__init__.py`.
2. Stage3 wrapper가 동적으로 로드하는 비표준 확장 본문.
3. crontab, systemd, 현재 프로세스에서 직접 실행되는 파일.
4. 활성 코드가 구체적인 파일 경로로 참조하는 HTML·JSON·데이터 입력.
5. 대규모 데이터 루트는 광역 문자열 `"."`, `"data"`만으로 전체를 KEEP하지 않고, 구체적 하위 경로 참조가 있을 때만 보존 근거로 사용.

전체 살아있는 파일 목록과 직접 importer는 `live_dependency_tree.csv`에 수록했다.

## 3. SAFE_TO_DELETE

`SAFE_TO_DELETE`는 총 45행, 343파일, 약 4.91 MiB다.

### 3.1 재생성 캐시

- `__pycache__` 계열 22개 디렉터리
- `.pytest_cache` 1개 디렉터리
- 합계 23행, 321파일, 약 3.61 MiB

Python bytecode와 pytest 캐시는 원본 소스에서 다시 생성할 수 있다.

### 3.2 명시적 구버전 UI/API 사본

20개 파일, 약 1.13 MiB:

- `api_server.py.bak*` 6개
- `dashboard_home.html.bak*` 14개

현재 활성 API·대시보드 경로에서 참조되지 않고, canonical 파일이 별도로 존재하며, 파일명이 명시적으로 이전 수정 전 사본임을 나타낸다.

주의: Stage3의 `run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001`은 이름만 백업일 뿐 실제 실행 본문이므로 이 집단에서 제외하고 `KEEP` 처리했다.

### 3.3 죽은 PID의 atomic temp

2개 파일, 약 171 KiB:

- `data/_system/.real_dashboard_buy_candidates.json.tmp.223994.20260709T161230Z`
- `data/_system/.real_dashboard_buy_candidates.json.tmp.224108.20260709T161407Z`

해당 PID는 현재 존재하지 않고, canonical `real_dashboard_buy_candidates.json`이 별도로 존재한다.

전체 SAFE 경로·수정 시각·크기·판단 근거는 `orphan_file_candidates.csv`에 수록했다.

## 4. REVIEW

도달하지 않는다는 사실만으로 삭제를 확정할 수 없는 항목은 모두 REVIEW다.

| 유형 | 행 | 대표 파일 수 | 크기 |
|---|---:|---:|---:|
| 일회성·비주력 실험 산출물 | 630 | 630 | 1,503.00 MiB |
| 미참조 시스템 데이터 디렉터리 | 15 | 7,119 | 351.97 MiB |
| 로그 | 310 | 310 | 191.32 MiB |
| 백업 아카이브 디렉터리 | 2 | 4,729 | 72.93 MiB |
| 일반 미도달 파일 | 206 | 206 | 13.57 MiB |
| 운영 상태 백업 | 19 | 19 | 2.33 MiB |
| 동일 내용 중복 후보 | 74 | 74 | 1.52 MiB |
| 일회성 연구 스크립트 | 59 | 59 | 0.80 MiB |
| 죽은 것으로 보이는 수동 스크립트 | 52 | 52 | 0.61 MiB |
| 미도달 테스트 | 87 | 87 | 0.53 MiB |
| 미도달 engine 모듈 | 23 | 23 | 0.38 MiB |
| 문서·메모 | 38 | 38 | 0.23 MiB |
| lock·PID 파일 | 56 | 56 | 390 B |

### 4.1 큰 REVIEW 대상

가장 큰 디렉터리 후보:

| 경로 | 크기 | 파일 수 | 사유 |
|---|---:|---:|---|
| `data/_system/condition_db_sell_omen_clean` | 123.88 MiB | 183 | 현재 진입점에서 도달하지 않지만 ML/연구 재사용 가능 |
| `data/_system/pipeline` | 78.80 MiB | 4,155 | 구 파이프라인 산출물로 보이나 동적 접근 여부 확인 필요 |
| `backup` | 72.42 MiB | 4,684 | 복구 가치 불명확 |
| `data/_system/condition_db_sell_omen_lr8d85` | 60.10 MiB | 85 | 이전 sell-omen 데이터 가능성 |
| `data/_system/logs` | 58.70 MiB | 8 | 과거 운영 로그·PID 포함 |

비주력 실험 중에는 `predictors_all.jsonl` 계열이 파일당 약 42~49 MiB로 가장 크다. 실험 재현 필요 여부가 확인되지 않아 모두 REVIEW다.

### 4.2 미도달 코드

현재 진입점에서 도달하지 않는 코드 예:

- 구 파이프라인: `engine/pipeline/batch.py`, `full_training.py`, `orchestrator.py`, `rolling_validation.py`, `screening.py`
- 구/별도 라이브 기능: `engine/live/s2_auto_trader.py`, `elite_pullback_replay.py`, `elite_shadow_mark_to_market.py`, `real_focus_news_refresh.py`
- 포트폴리오 probe 계열
- `scripts/pipeline/*`의 이전 실행·분석 스크립트
- 다수의 `scripts/research/*` 일회성 실험 스크립트
- `scripts/run_bot.py`, `scripts/run_live_lr8d16_legacy.py`, `scripts/run_s2_auto_live.py`

이 파일들은 import 도달성이 없더라도 수동 실행, 장애 복구, 비교 실험에 사용될 수 있으므로 자동 삭제 대상으로 올리지 않았다.

### 4.3 운영 상태 백업

`data/_system` 아래의 다음 종류는 명시적 backup 이름이어도 REVIEW로 내렸다.

- positions
- pending orders
- scheduled open queue
- market history/state
- ticker universe
- manual intent
- 뉴스 캐시 migration 전 사본

canonical 파일이 존재하더라도 사고 복구에 필요할 수 있어 사람 확인 없이 삭제하면 안 된다.

### 4.4 중복 파일

SHA-256이 같은 소형 파일 74행을 `EXACT_DUPLICATE_REVIEW`로 표시했다. 예:

- `dashboard.html` / `dashboard_live.html`
- 반복된 실험 README
- 동일한 stage completion/summary 파일
- 빈 패키지 `__init__.py`

내용이 같다는 사실만으로 경로 의미가 같지는 않으므로 SAFE로 승격하지 않았다.

## 5. KEEP 및 절대 보존 검증

다음 보존 규칙을 강제로 적용했다.

### 5.1 원본·파생 룰풀

파일명에 다음이 포함되면 모든 실험 트리에서 KEEP 처리했다.

- `rulebook`
- `survivor`
- `profile_catalog`
- `validation_results.jsonl`
- `candidate_universe.json`
- `central_index.jsonl`

명시적으로 식별된 룰풀 계열 KEEP 파일은 175개다.

검증 결과:

- `rulebooks_all`: 전부 KEEP
- `entry_rulebooks`: 전부 KEEP
- `final_rulebooks`: 전부 KEEP
- `survivors.jsonl`: 전부 KEEP
- `profile_catalog`: 전부 KEEP
- `exit_trades`: 전부 KEEP
- `candidate_denylist.json`: KEEP

### 5.2 frozen·audit

- `data/_system/analysis` 전체: KEEP
- `oos_reproduce_frozen` 및 frozen 이름 패턴: KEEP
- 현재 감사 산출물과 과거 audit 결과: KEEP

### 5.3 실제 파이프라인

- Stage2/Stage3 진입점 및 재귀 의존성: KEEP
- Stage3 동적 본문: KEEP
- 라이브 후보 생성 경로: KEEP
- 실전 실행·broker·safety·scheduler·central-control 경로: KEEP
- 대시보드·guard·crontab 경로: KEEP
- 활성 코드가 참조하는 `data/symbols`, 뉴스·sentiment 캐시, central, research 입력: KEEP

### 5.4 민감 파일

- `.env`
- `.env.backup`

두 파일 모두 민감 구성으로 분류해 KEEP 처리했으며 내용을 읽거나 수정하지 않았다.

## 6. 판정 한계

1. 정적 분석은 reflection, `eval`, 외부 플러그인, 사람이 직접 실행하는 스크립트를 완전히 증명할 수 없다.
2. crontab·systemd·현재 프로세스는 확인했지만 저장소 밖의 수동 운영 문서나 외부 서버 작업은 알 수 없다.
3. 대용량 데이터 트리는 디렉터리 단위 집계다. REVIEW 디렉터리 안의 일부 파일만 실제 고아일 수 있다.
4. 로그와 실험 산출물은 현재 실행에 사용되지 않아도 재현·감사·장애 분석 가치가 있을 수 있다.
5. `SAFE_TO_DELETE`는 기술적 삭제 후보 판정일 뿐이며, 이번 작업에서 삭제 권한을 행사하지 않았다.

## 7. 산출물

- `orphan_file_candidates.csv`  
  경로, 유형, 위험도, 크기, 파일 수, 최근 수정일, 도달 여부, 직접 참조 흔적, Git 마지막 변경, 판단 근거.
- `live_dependency_tree.csv`  
  살아있는 148개 파일과 직접 importer·리터럴 리소스.
- `orphan_file_audit_summary.json`  
  집계와 실행 환경 증거.
- `run_orphan_file_audit.py`
- `run_orphan_file_audit_safe.py`
- `orphan_file_audit_readout.md`

**삭제·이동 작업은 0건이다.**
