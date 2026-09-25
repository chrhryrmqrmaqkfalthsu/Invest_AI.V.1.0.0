# Stage2·Stage3 산출물 계보 분석

- 기준: 현재 `feat/intraday-reversal-ga` 코드와 `exp_batch_stage123_2009_20260616_full`
- 작업 모드: 식별·집계만 수행
- 삭제·이동·원본 산출물 수정: **0건**
- 목적: 새 게이트를 적용할 때 GA를 다시 돌려야 하는 원본과, 원본에서 재생성 가능한 필터 결과를 분리

## 0. 핵심 결론

### 반드시 보존해야 할 GA 원본

| 단계 | 산출물 | 개체 수 | 크기 | 판정 |
|---|---|---:|---:|---|
| Stage2 GA | `rulebooks_all.jsonl` | **599,100** | 1.96 GiB | `KEEP_ORIGIN` |
| Stage3 entry GA | `entry_rulebooks.jsonl` | **6,003** | 22.73 MiB | `KEEP_ORIGIN` |
| Stage3 exit GA | `final_rulebooks.jsonl` | **16,929** | 87.08 MiB | `KEEP_ORIGIN` |

`final_rulebooks.jsonl`은 이름과 달리 단순 최종 필터 결과가 아니다. `entry_rulebooks.jsonl`의 진입 유전자를 고정한 뒤 청산 유전자 14개를 별도 GA로 학습해 직접 생성한 **exit-GA 원본**이다. 삭제하면 `entry_rulebooks`에서 exit GA를 다시 돌려야 한다.

### 원본에서 다시 만들 수 있는 대표 DERIVED

| 단계 | 산출물 | 개체 수 | 크기 | 판정 |
|---|---|---:|---:|---|
| Stage2 게이트 | `survivors.jsonl` | 1,162 | 6.80 MiB | `KEEP_ORIGIN` — 라이브 직접 참조 |
| Stage3 검증 | `validation_results.jsonl` | 16,929 | 136.06 MiB | `SAFE_TO_REGEN_DELETE` |
| Stage3 적격 카탈로그 | `stage3_profile_catalog.jsonl` | 2,124 | 18.20 MiB | `SAFE_TO_REGEN_DELETE` |
| Stage3 부적격 목록 | `stage3_ineligible.jsonl` | 14,805 | 117.86 MiB | `SAFE_TO_REGEN_DELETE` |

`survivors.jsonl`은 Stage2 GA 원본이 아니라 `rulebooks_all`을 게이트로 거른 DERIVED다. 그러나 현재 Stage2 후보 생성과 central-control이 실제 룰북을 이 파일에서 다시 찾기 때문에 삭제하면 즉시 깨진다. 따라서 계보는 DERIVED지만 삭제 판정은 `KEEP_ORIGIN`이다.

### Stage3 qualify의 계보 공백

Stage3 qualify는 자체 GA를 수행하지만 개별 qualify 룰북을 저장하지 않는다. 코드가 다음과 같이 요약만 남긴다.

```text
qualification rulebooks are intentionally discarded; only summary counts are persisted
```

남는 것은 `qualify_result.json`의 후보 수·통과 수·해시 샘플뿐이다. 따라서 새 게이트를 **qualify 개체 전체에 처음부터** 적용하려면 기존 산출물만으로 복구할 수 없고 Stage3 qualify GA를 다시 실행해야 한다.

## 1. 전체 흐름도

```text
티커 + OHLCV/시장·섹터·뉴스 컨텍스트
│
├─ Stage2: run_stage2.py
│  ├─ train_1 / train_2 / train_3 각각 GA
│  │  ├─ rulebooks_all.jsonl        [ORIGIN: Stage2 GA 개체]
│  │  └─ ga_history.csv             [ORIGIN: GA 이력]
│  ├─ rulebook_hash 중복 통합·대표 선택
│  ├─ 5개 평가 구간 순차 백테스트 + Stage2 gate
│  │  ├─ period_metrics_all.csv     [DERIVED]
│  │  ├─ early_cut_log.csv          [DERIVED]
│  │  ├─ survivors.jsonl            [DERIVED, 현재 라이브 직접 참조]
│  │  ├─ trades.jsonl               [DERIVED]
│  │  └─ rl_replay_trades.jsonl     [DERIVED]
│  └─ run_stage23_batch.py
│     └─ central_index.jsonl에 Stage2 survivor 위치·지표 색인 [DERIVED, 라이브 직접 참조]
│
├─ Stage3 qualify: run_stage3_aggressive.py::run_qualify
│  ├─ 3개 train split에서 별도 qualify GA
│  ├─ 개별 GA 룰북은 저장하지 않고 폐기
│  └─ qualify_result.json           [ORIGIN 요약, 개체 재게이트 불가능]
│
├─ Stage3 entry: run_entry_ga
│  ├─ train_3에서 entry GA
│  ├─ expectancy 절대선 + 진입일 Jaccard 중복 제거
│  ├─ entry_rulebooks.jsonl         [ORIGIN: entry GA 선택 개체]
│  ├─ entry_rejected_overlap.json   [ORIGIN: 선택 전 풀의 거절 흔적]
│  └─ entry_result.json             [ORIGIN 보조 메타데이터]
│
├─ Stage3 exit: run_exit_ga
│  ├─ entry_rulebooks를 입력
│  ├─ 각 entry별 청산 유전자 14개 GA
│  ├─ final_rulebooks.jsonl         [ORIGIN: exit GA 개체]
│  └─ exit_result.json              [ORIGIN 보조 메타데이터]
│
├─ Stage3 validate: run_validate
│  ├─ final_rulebooks를 순수 OOS 3구간 + stress 구간 재백테스트
│  ├─ validation_results.jsonl      [DERIVED: 전체 16,929]
│  ├─ stage3_profile_catalog.jsonl  [DERIVED: 적격 2,124]
│  ├─ stage3_ineligible.jsonl       [DERIVED: 부적격 14,805]
│  ├─ exit_trades.jsonl             [DERIVED, 절대 보존]
│  ├─ rl_replay_trades.jsonl        [DERIVED]
│  └─ validate_result.json          [DERIVED 요약]
│
└─ 라이브 후보·실행
   ├─ 활성 elite 후보 daemon
   │  ├─ Stage2: central_index → survivors.jsonl
   │  ├─ Stage3: final_rulebooks.jsonl 직접 스캔
   │  ├─ candidate_denylist 적용
   │  └─ live_slots_state.json → real_dashboard_buy_candidates.json
   │
   └─ run_live.py central-control 경로
      ├─ Stage2: central_index + survivors
      └─ Stage3 mix ON일 때만 stage3_live_pool.jsonl
         └─ stage3_profile_catalog에서 build_stage3_live_pool.py로 생성
```

## 2. Stage2 계보

### 2.1 ORIGIN

#### `rulebooks_all.jsonl`

- 경로: `exp_batch_stage123_2009_20260616_full/tickers/*/stage2*/rulebooks_all.jsonl`
- 파일: 1,997개
- 레코드: 599,100개
- 크기: 1.96 GiB
- 최근 수정: 2026-06-24 UTC
- 생성 코드: `scripts/research/run_stage2.py::run_training`
- 재생성: **Stage2 GA 재학습 필요**
- 판정: `KEEP_ORIGIN`

세 train split의 GA 결과를 그대로 합쳐 쓴다. 새 Stage2 게이트를 적용할 때 가장 중요한 원본 개체 풀이다.

#### `ga_history.csv`, `config.json`

- `ga_history.csv`: 1,997파일, 43.51 MiB
- `config.json`: retry·미완료 attempt까지 2,045파일, 13.45 MiB
- seed, 세대 수, early stop, 데이터 기간, 코드 commit과 GA 설정을 보존한다.
- exact 재현을 위해 `KEEP_ORIGIN`.

### 2.2 DERIVED

#### `survivors.jsonl`

- `rulebooks_all`의 hash별 대표를 5개 기간에 재평가하고 Stage2 gate를 순차 적용한 결과.
- GA 재학습 없이 재생성 가능.
- 현재 별도 “rulebooks_all만 읽어 재게이트” CLI는 없다. `build_representatives()`와 `evaluate_periods()`를 호출하는 전용 wrapper가 필요하다.
- 현재 라이브는 `central_index.jsonl`의 `source_file/source_row_index`를 따라 `survivors.jsonl`에서 전체 룰북을 로드한다.
- 삭제 시 Stage2 elite 후보와 central-control loader가 즉시 실패한다.
- 판정: `KEEP_ORIGIN`.

#### 재생성 가능 Stage2 중간물

| 산출물 | 크기 | 라이브 직접 참조 | 판정 |
|---|---:|---|---|
| `period_metrics_all.csv` | 810.79 MiB | X | `SAFE_TO_REGEN_DELETE` |
| `early_cut_log.csv` | 277.99 MiB | X | `SAFE_TO_REGEN_DELETE` |
| `rl_replay_trades.jsonl` | 33.97 GiB | X | `SAFE_TO_REGEN_DELETE` |
| `trades.jsonl` | 33.88 GiB | shadow report의 `include_trades=True`에서 사용 | `REVIEW` |

`trades.jsonl`을 지워도 현재 후보 점수 계산은 유지되지만 API의 과거 거래 요약이 비게 된다. 운영 영향 범위가 후보 실행이 아니라 대시보드·검증이므로 자동 SAFE가 아니라 REVIEW로 남겼다.

## 3. Stage3 계보

### 3.1 qualify

`qualify_result.json`은 511개 attempt에 존재하고 최근 수정은 2026-07-08 UTC다.

- qualify GA 자체의 최종 개체는 저장되지 않는다.
- `unique_candidate_count`, `all3_pass_count`, train split별 통계만 저장한다.
- 새 qualify gate로 기존 개체를 다시 거르는 것은 불가능.
- 재생성 명령 예:

```bash
venv/bin/python scripts/research/run_stage3_aggressive.py \
  --ticker TICKER --stage qualify --out-dir NEW_DIR
```

이는 필터 재실행이 아니라 GA 재학습이다.

### 3.2 entry 원본

`entry_rulebooks.jsonl`:

- 파일: 304개
- 개체: 6,003개
- 크기: 22.73 MiB
- 최근 수정: 2026-07-08 UTC
- 생성: `run_entry_ga()`
- 재생성: entry GA 필요
- 판정: `KEEP_ORIGIN`

`entry_rejected_overlap.json`은 선택된 entry만으로 재구성할 수 없는 거절 후보 흔적이다. 새 게이트가 entry 풀 수준에서 작동할 가능성이 있으므로 함께 보존해야 한다.

### 3.3 exit 원본

`final_rulebooks.jsonl`:

- 파일: 286개
- 개체: 16,929개
- 크기: 87.08 MiB
- 최근 수정: 2026-07-08 UTC
- 입력: `entry_rulebooks.jsonl`
- 생성: entry별 exit-gene GA
- 라이브: Stage3 elite 후보 생성과 `evaluate_candidate()`가 직접 읽음
- 판정: `KEEP_ORIGIN`

삭제 시 현재 Stage3 후보 70개가 다음 report rebuild부터 사라지거나 룰북 로드 실패가 발생한다.

### 3.4 validate 파생물

`validation_results`, `profile_catalog`, `ineligible`은 16,929개의 `final_rulebooks`를 동일 기간에 다시 백테스트하고 적격성·프로파일만 붙인 결과다.

```text
validation_results 16,929
= profile_catalog 2,124
+ ineligible 14,805
```

세 파일은 GA 재학습 없이 재생성 가능하다.

재생성 코드:

```text
scripts/research/run_stage3_aggressive.py::run_validate
```

주의: 현재 `run_validate`는 JSONL을 `append_jsonl`로 쓴다. 기존 Stage3 디렉터리에서 그대로 재실행하면 중복 append 위험이 있다. 안전한 복구는 다음 방식이다.

1. 새 임시 디렉터리를 생성한다.
2. 보존된 `final_rulebooks.jsonl`을 입력으로 둔다.
3. `--stage validate`를 새 디렉터리에서 실행한다.
4. 새 게이트 결과를 별도 경로로 검증한 후 교체한다.

원본 Stage3 디렉터리에 in-place append 재생성하면 안 된다.

### 3.5 trade 계열

- `exit_trades.jsonl`: 834.68 MiB, DERIVED지만 절대 보존 규칙 때문에 `KEEP_ORIGIN`.
- `rl_replay_trades.jsonl`: 2.76 GiB, 라이브 직접 참조 없음, `SAFE_TO_REGEN_DELETE`.

## 4. 라이브 의존성

### 4.1 현재 활성 후보 daemon

현재 실행 코드:

```text
data/_system/ops/live_candidate_slots.py daemon --interval 60
```

후보 원천:

```text
build_elite_shadow_report(stage2_limit=60, stage3_limit=80)
```

읽는 계보:

| 후보 단계 | 직접 읽는 파일 | 삭제 영향 |
|---|---|---|
| Stage2 | `central_index.jsonl` | Stage2 후보 색인 소실 |
| Stage2 | 각 티커 `survivors.jsonl` | 전체 룰북 로드 실패 |
| Stage3 | 각 티커 `final_rulebooks.jsonl` | Stage3 후보 생성·평가 실패 |
| 공통 | `candidate_denylist.json` | 차단 정책 제거 |
| 거래 표시만 | Stage2 `trades.jsonl`, Stage3 `exit_trades.jsonl` | 거래 요약·대시보드 손상 |

### 4.2 현재 수치 검증

지시서에 적힌 “후보 89개, 열린 6개 포지션”은 현재 파일에서는 확인되지 않았다.

분석 시점의 실제 상태:

| 소스 | 현재 값 |
|---|---:|
| `build_elite_shadow_report` | **82개** — Stage2 12, Stage3 70 |
| denylist 차단 | 11개 |
| 과거 `live93_*` 감사 | **93개**, denylist 확대 전 역사적 스냅샷 |
| `live_slots_state.candidate_pool` | 17개 |
| 슬롯 | 8개 |
| 대기열 | 9개 |
| `real_dashboard_buy_candidates` | 18개 |
| `elite_shadow_state.open_positions` | 16개, 마지막 갱신 2026-07-07 UTC |
| `positions.json` | 3개 — CAKE, MPLX, WPM |

현재 82개는 과거 93개에서 denylist 11개를 차단한 수와 정확히 일치한다. 현재 프로세스에는 API 서버와 live slot daemon은 있지만 `scripts/run_live.py` 프로세스는 확인되지 않았다. 따라서 “89/6”은 이전 시점의 상태이거나 다른 ledger를 가리킨 것으로 보이며, 현재 삭제 안전 판정은 실제 82/17/8/16/3 상태와 코드 직접 참조를 기준으로 했다.

### 4.3 central-control 실행 경로

`run_live.py`의 Stage3 mix는 기본값이 `off`이고 systemd 명령도 다음뿐이다.

```text
scripts/run_live.py --mode real
```

Stage3 mix를 명시적으로 켜면 `data/_system/central/stage3_live_pool/stage3_live_pool.jsonl`을 직접 읽는다. 이 파일은 profile catalog에서 재생성할 수 있지만 지원되는 실행 모드의 직접 입력이므로 자동 SAFE가 아닌 `REVIEW`다.

## 5. 삭제 안전 판정 요약

전체 29개 유형:

| 판정 | 유형 수 |
|---|---:|
| `KEEP_ORIGIN` | 17 |
| `SAFE_TO_REGEN_DELETE` | 7 |
| `REVIEW` | 5 |

### SAFE_TO_REGEN_DELETE

| 산출물 | 합계 크기 | 복구 원본 |
|---|---:|---|
| Stage2 `period_metrics_all.csv` | 810.79 MiB | `rulebooks_all` |
| Stage2 `early_cut_log.csv` | 277.99 MiB | `rulebooks_all` |
| Stage2 `rl_replay_trades.jsonl` | 33.97 GiB | `rulebooks_all` |
| Stage3 `validation_results.jsonl` | 136.06 MiB | `final_rulebooks` |
| Stage3 `stage3_profile_catalog.jsonl` | 18.20 MiB | `final_rulebooks` |
| Stage3 `stage3_ineligible.jsonl` | 117.86 MiB | `final_rulebooks` |
| Stage3 `rl_replay_trades.jsonl` | 2.76 GiB | `final_rulebooks` |

이번 단계에서는 어떤 파일도 삭제하지 않았다.

### REVIEW

- Stage2 `trades.jsonl`: 후보 실행에는 불필요하지만 shadow report 거래 표시에 직접 사용.
- Stage2 `summary.json`: 대부분 파생이나 runtime·cache 정보 exact 복구 불가.
- Stage3 `validate_result.json`: 파생이지만 batch full-output validator가 필수 파일로 검사.
- Stage3 `last_run_summary.json`: 파생이지만 batch validator가 사용.
- `stage3_live_pool.jsonl`: 파생이지만 Stage3 mix ON 실행의 직접 입력.

## 6. 새 게이트 적용 시 권장 출발점

게이트 위치에 따라 출발점이 달라진다.

| 새 게이트 위치 | 사용해야 할 원본 | GA 재학습 |
|---|---|---|
| Stage2 평가·survivor gate | `rulebooks_all.jsonl` | X |
| Stage3 qualify 개체 gate | 보존된 개별 개체 없음 | **O — qualify GA 재실행** |
| Stage3 entry 선택 후 gate | `entry_rulebooks.jsonl` | gate만이면 X; 이후 exit를 다시 만들면 exit GA 필요 |
| Stage3 final rulebook gate | `final_rulebooks.jsonl` | X |
| Stage3 OOS 적격성/profile gate | `final_rulebooks.jsonl` | X |
| 라이브 elite threshold/denylist gate | `survivors` + `final_rulebooks` | X |

가장 안전한 새 게이트 실험 순서:

1. Stage2는 `rulebooks_all`을 원본으로 별도 re-gate 디렉터리에 결과를 쓴다.
2. Stage3는 `final_rulebooks`를 원본으로 새 validate/gate 결과를 별도 디렉터리에 쓴다.
3. 기존 `survivors`, `final_rulebooks`, `central_index`, live state는 교체하지 않는다.
4. 새 결과의 후보 수·OOS·라이브 영향 검증 후 별도 승인으로 교체한다.
5. qualify 수준을 바꾸려는 경우에만 Stage3 qualify GA 재학습 계획을 별도로 세운다.

## 7. 산출물

- `stage23_artifact_lineage.csv`
  - 경로 패턴, ORIGIN/DERIVED, 재학습 필요 여부, 재생성 코드, 파일 수, 크기, 레코드 수, 최근 수정일, 라이브 참조, 안전 판정.
- `stage23_live_dependency_snapshot.json`
  - 현재 후보·슬롯·포지션 수와 denylist 적용 후 report 상태.
- `stage23_artifact_lineage_readout.md`
- `run_stage23_artifact_lineage.py`
- `run_stage23_artifact_lineage_safe.py`

삭제·이동은 0건이다.
