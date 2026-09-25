# 원본 룰풀 → 라이브 거름망 계보 역추적

- 기준 시각: 2026-07-11 00:22 KST 부근
- 기준 코드: 현재 `feat/intraday-reversal-ga`
- 생산 배치: `exp_batch_stage123_2009_20260616_full`
- 작업 모드: **read-only 식별**
- 삭제·이동·파이프라인 코드·원본 산출물 수정: **0건**

## 0. 결론

현재 실제 후보 daemon은 다음 체인을 사용한다.

```text
Stage2 rulebooks_all
  → Stage2 5단계 gate
  → survivors
  → central_index
  → Stage2 elite static filter

Stage3 entry_rulebooks
  → exit GA
  → final_rulebooks
  → Stage3 elite static filter

Stage2 + Stage3 후보
  → ticker당 1개 + stage cap(60/80)
  → candidate_denylist
  → 82개 elite report
  → 과거 93개 기준 MAE/MFE gate join
  → 실시간 should_buy
  → 17개 candidate_pool
  → 우선순위 정렬
  → 8 slots + 9 waitlist
  → full rulebook + should_buy 재검증
  → real_dashboard_buy_candidates
```

가장 큰 구조적 문제는 다음 다섯 가지다.

1. **현재 Stage3 라이브는 Stage3 validate/profile catalog를 우회한다.** 현재 Stage3 라이브 70개 중 profile catalog에 포함된 것은 9개뿐이고, 61개는 validate 기본 적격성 결과에 없다.
2. **candidate denylist가 ticker당 1개를 고른 뒤 적용된다.** 차단된 ticker에 차순위 후보가 있어도 대체되지 않는다.
3. **MAE/MFE live gate가 과거 93개 candidate ID에 고정돼 있다.** 현재 82개 중 9개는 gate list에 없어 `gate_missing`으로 자동 차단된다.
4. **`central_index.jsonl`은 Stage2 survivor마다 동일한 적격 행을 정확히 세 번 보유한다.** 1,162개 survivor가 3,486개 적격 행으로 중복 색인돼 있다.
5. **`real_dashboard_buy_candidates.json`은 현역 소비 파일이지만 현재 state보다 오래됐다.** 현재 pool은 17개인데 dashboard 파일은 과거 18개이며 BTE 후보 1개가 stale 상태다.

새 게이트의 최우선 삽입 후보는 **Stage2 survivors와 Stage3 final_rulebooks를 로드한 직후, ticker dedup·stage cap·denylist보다 앞**이다.

## 1. 실제 실행 시작점

현재 실행 중인 프로세스:

```text
uvicorn api_server_candidate_only:app --port 8001
data/_system/ops/live_candidate_slots.py daemon --interval 60
```

현재 `scripts/run_live.py` 프로세스는 없다. 따라서 본 분석에서 “현역 라이브”는 현재 실행 중인 후보 daemon과 대시보드 API 체인을 뜻한다.

활성 최종 파일:

| 파일 | 최종 수정 | 현재 내용 | 사용처 |
|---|---|---:|---|
| `data/_system/live_slots_state.json` | 2026-07-11 00:22 KST 부근 | pool 17, slots 8, waitlist 9 | daemon·API·export |
| `data/_system/real_dashboard_buy_candidates.json` | 2026-07-10 19:01 KST | 후보 18 | real dashboard·manual-buy API |
| `data/_system/candidate_denylist.json` | 2026-07-10 19:00 KST | active entry 11 | elite report |
| `data/_system/live_candidate_list_20260707.json` | 2026-07-08 05:38 KST | 과거 후보 93, KEEP 80, DROP 13 | live slot daemon |

## 2. 전체 거름망 체인 다이어그램

### 2.1 Stage2 원본에서 라이브까지

```text
rulebooks_all 599,100행
│
├─ hash 대표화
│  599,100 → 599,099
│  동일 hash는 train_fitness 최고 개체를 대표로 사용
│
├─ stress_pre_2022h1
│  조건: expectancy>=1, MDD>=-20,
│        expectancy×trades/abs(MDD)>1
│  599,099 → 52,087
│
├─ train_3_eval
│  조건: trades>=5, member_score>=10, expectancy>=1
│  52,087 → 28,668
│
├─ train_2_eval
│  동일 조건
│  28,668 → 11,365
│
├─ train_1_eval
│  동일 조건
│  11,365 → 5,429
│
├─ oos_2025h2
│  조건: trades>=5, member_score>=10,
│        expectancy>=1, MDD>=-15
│  5,429 → 1,162
│
└─ survivors.jsonl 1,162
   │
   ├─ central_index append/index
   │  1,162 unique → 3,486 eligible rows
   │  각 survivor가 정확히 3번 중복
   │
   ├─ Stage2 elite static filter
   │  OOS exp>=2.7
   │  fitness>=70
   │  trades>=15
   │  win>=70
   │  stress exp>=0.5
   │  worst DD>-18
   │  min period trades>=8
   │  + anti-pattern filter
   │  3,486 index rows → 78 rows
   │
   └─ elite_score 정렬 + ticker당 1개
      78 → 13 ticker 후보
```

### 2.2 Stage3 원본에서 라이브까지

```text
qualify GA 후보 147,899
│
├─ 3년 절대 gate
│  각 train_1/train_2/train_3:
│    trades>=5
│    member_score>=10
│    expectancy>=2
│  3개 연도 모두 통과
│  147,899 → 5,219
│
│  주의: 통과 개체 자체는 저장하지 않고 summary만 저장
│
├─ entry GA top100
│  pool 28,699
│
├─ entry expectancy cut
│  train_3 expectancy>=2
│  28,699 → 26,000
│
├─ entry diversity + top20
│  entry-date Jaccard>=0.7이면 중복 reject
│  ticker당 최대20
│  26,000 → entry_rulebooks 5,663
│
├─ exit GA
│  entry 하나당 exit gene 60개체, 25세대
│  composite fitness 상위3
│  완료된 5,303 entries → final_rulebooks 15,909
│
├─ validate basic eligibility
│  train_1/train_2/recent_1y 각각 expectancy>=1
│  15,909 → profile catalog 2,012
│             ineligible 13,897
│
└─ 현재 ACTIVE elite는 위 validate 결과를 사용하지 않음
   │
   ├─ final_rulebooks 직접 스캔
   ├─ exp>=2.7, fitness>=45, win>=70,
   │  trades>=8, DD>-18 + anti-pattern
   │  15,909 → 4,640
   ├─ elite_score 정렬 + ticker당 1개
   │  4,640 → 246 ticker
   └─ 상위80
      246 → 80
```

Stage3 합계는 단계별 파일 완성 범위가 다르다.

| 단계 | 결과 파일 수 |
|---|---:|
| qualify | 493 |
| entry | 287 |
| exit | 269 |
| validate | 269 |

따라서 Stage3의 전체 합계를 하나의 동일 cohort에서 순차 감소한 값으로 해석하면 안 된다. 각 숫자는 해당 결과 파일이 존재하는 canonical `stage3` 디렉터리들의 합계다.

### 2.3 합류 후 라이브 후보 체인

```text
Stage2 13 + Stage3 80 = 93
│
├─ candidate_denylist.json
│  위치: Stage2/3 merge 후,
│        ticker당 1개와 stage cap이 끝난 뒤
│  차단 11
│  93 → 82
│
├─ 정규장 gate
│  미국 평일 09:30<=ET<16:00
│  장외에는 새 평가 없이 cached pool 사용
│
├─ historical MAE/MFE gate join
│  입력 gate list는 과거 93개 candidate ID
│  현재 82 중:
│    gate_missing 9
│    DROP_BAD_MAE_CAPTURE 10
│  82 → 평가 대상 63
│
├─ evaluate_candidate / should_buy
│  현재 OHLCV + market + sector + VIX + news + event
│  score >= rulebook threshold
│  63 → BUY 17
│
├─ 우선순위
│  SPY DOWN + HIGH_VOL은 탈락이 아니라 후순위
│  final_score 내림차순
│
├─ 슬롯 cut
│  17 → slots 8 + waitlist 9
│
└─ dashboard export 재검증
   현재 elite report에서 candidate ID 재매칭
   full rulebook 키/필수 필드 검증
   evaluate_candidate와 should_buy 재실행
   → real_dashboard_buy_candidates.json
```

## 3. 거름망별 상세 표

전체 23개 gate의 코드·입력·출력·조건·통과 수는 `filter_gate_chain.csv`에 기록했다.

### Stage2

| 순서 | 거름망 | 입력 | 통과 | 탈락 | 핵심 기준 |
|---:|---|---:|---:|---:|---|
| 10 | hash 대표화 | 599,100 | 599,099 | 1 | 동일 hash 중 최고 train fitness |
| 20 | stress | 599,099 | 52,087 | 547,012 | exp, MDD, return/MDD ratio |
| 30 | train_3 | 52,087 | 28,668 | 23,419 | trades/member score/exp |
| 40 | train_2 | 28,668 | 11,365 | 17,303 | 동일 |
| 50 | train_1 | 11,365 | 5,429 | 5,936 | 동일 |
| 60 | OOS | 5,429 | 1,162 | 4,267 | trades/member score/exp/MDD |
| 200 | elite static | 3,486 index rows | 78 | 3,408 | 더 강한 OOS·승률·fitness·anti-pattern |

### Stage3

| 순서 | 거름망 | 입력 | 통과 | 탈락/비선택 | 핵심 기준 |
|---:|---|---:|---:|---:|---|
| 100 | qualify 3년 gate | 147,899 | 5,219 | 142,680 | 세 연도 모두 trades/member/exp |
| 110 | entry exp cut | 28,699 | 26,000 | 2,699 | train_3 exp>=2 |
| 120 | Jaccard + top20 | 26,000 | 5,663 | 10,988 명시 reject | overlap>=0.7, 최대20 |
| 130 | exit GA top3 | 5,303 entries | 15,909 outputs | 해당 없음 | entry별 top3 |
| 140 | validate eligibility | 15,909 | 2,012 | 13,897 | 세 OOS 각각 exp>=1 |
| 210 | active elite static | 15,909 | 4,640 | 11,269 | bull/stress metric + anti-pattern |

Entry 단계에는 계보 누락이 있다. 절대선 통과 26,000에서 선택 5,663과 overlap reject 10,988을 빼면 **9,349개**가 남는다. 코드는 top20에 도달하면 루프를 종료하므로, 이들은 `entry_rejected_overlap.json`에도 기록되지 않는다.

### 현역 후보·슬롯

| 순서 | 거름망 | 입력 | 통과 | 차단/대기 |
|---:|---|---:|---:|---:|
| 220 | ticker dedup + 60/80 cap | 4,718 | 93 | 4,625 |
| 230 | denylist | 93 | 82 | 11 |
| 310 | MAE/MFE join | 82 | 63 | missing 9 + drop 10 |
| 320 | should_buy | 63 | 17 | 46 |
| 330 | 8-slot | 17 | 8 | waitlist 9 |
| 340 | dashboard export 당시 재검증 | 18 | 18 | 0 |

Dashboard의 18은 현재 슬롯 17과 같은 시점의 입력이 아니다. 기존 export 시점에는 candidate pool이 18개였고 현재 state 갱신 후 BTE 후보가 빠졌다.

## 4. candidate_denylist 적용 위치

정확한 적용 위치:

```text
Stage2 static filter
  → ticker당 1개, 최대60
Stage3 static filter
  → ticker당 1개, 최대80
두 목록 merge
  → candidate_denylist 적용
  → bucket 정렬
```

매칭 정책:

- `candidate_id` exact match, 또는
- `rule_hash` match + ticker/stage 조건 충족
- inactive entry는 무시

문제점: denylist가 ticker dedup 이후라 차단 ticker의 2순위 후보를 다시 고르지 않는다. 실제로 현재 11개가 차단돼 93→82가 됐다.

새 게이트를 denylist보다 앞에 넣는 이유 중 하나도 이 fallback 문제를 해결하기 위해서다.

## 5. 거름망 산출물 현역/잔재 판정

| 산출물 | 크기 | 최근 수정 UTC | 판정 | 근거 |
|---|---:|---|---|---|
| Stage2 `period_metrics_all.csv` | 810.79 MiB | 2026-06-24 | `REGEN_OK` | rulebooks_all 재평가 가능, 라이브 미참조 |
| Stage2 `early_cut_log.csv` | 277.99 MiB | 2026-06-24 | `REGEN_OK` | gate trace 재생성 가능 |
| Stage2 `survivors.jsonl` | 6.80 MiB | 2026-06-24 | `ACTIVE_LIVE` | elite·central-control 직접 로드 |
| `central_index.jsonl` | 280.28 MiB | 2026-07-08 | `ACTIVE_LIVE` | Stage2 elite 직접 입력 |
| Stage3 `validation_results.jsonl` | 136.06 MiB | 2026-07-08 | `REGEN_OK` | final_rulebooks에서 재검증 가능 |
| Stage3 `stage3_profile_catalog.jsonl` | 18.20 MiB | 2026-07-08 | `REGEN_OK` | 현재 active elite는 미사용 |
| Stage3 `stage3_ineligible.jsonl` | 117.86 MiB | 2026-07-08 | `REGEN_OK` | validate 탈락 근거, 재생성 가능 |
| `stage3_live_pool.jsonl` | 2.10 MiB | 2026-06-26 | `STALE_OUTPUT` | 현재 process 미사용, 최신 catalog 미반영 |
| `stage3_live_pool_filtered.jsonl` | 1.69 MiB | 2026-06-26 | `STALE_OUTPUT` | code consumer 없음 |
| live-pool summary 2개 | 3.65 KiB | 2026-06-26 | `STALE_OUTPUT` | stale pool metadata |
| live-pool rejected sample 2개 | 109.16 KiB | 2026-06-26 | `STALE_OUTPUT` | stale pool 탈락 샘플 |
| `candidate_denylist.json` | 5.37 KiB | 2026-07-10 | `ACTIVE_LIVE` | elite report 직접 적용 |
| `live_candidate_list_20260707.json` | 29.01 KiB | 2026-07-07 | `ACTIVE_LIVE` | daemon 직접 사용, 단 내용은 stale-ID |
| `live_slots_state.json` | 156.27 KiB | 2026-07-10 | `ACTIVE_LIVE` | daemon·API·export 직접 사용 |
| `real_dashboard_buy_candidates.json` | 200.52 KiB | 2026-07-10 | `ACTIVE_LIVE` | real dashboard 직접 사용, snapshot stale |
| `live_slots_events.jsonl` | 1.18 MiB | 2026-07-10 | `REVIEW` | 과거 운영 이력 exact 재생성 불가 |
| MAE/MFE gate source CSV | 11.54 KiB | 2026-07-07 | `REGEN_OK` | gate list 재생성 입력, analysis 절대 보존 |

판정 합계:

| 판정 | 유형 수 | 합산 크기 |
|---|---:|---:|
| `ACTIVE_LIVE` | 6 | 약 287.46 MiB |
| `REGEN_OK` | 6 | 약 1.33 GiB |
| `STALE_OUTPUT` | 4 | 약 3.91 MiB |
| `REVIEW` | 1 | 약 1.18 MiB |

이 표는 ORIGIN 룰풀을 제외한 거름망 산출물만 대상으로 한다. 원본 룰풀 삭제 판정은 이전 `stage23_artifact_lineage.csv`에 있다.

## 6. 중복·충돌 상세

### 6.1 Stage3 validate와 active elite가 서로 다른 세계를 본다

- validate profile catalog: pure OOS 세 기간 각각 expectancy>=1
- active Stage3 elite: final_rulebooks의 bull/stress metrics에 exp>=2.7, fitness>=45, win>=70, trades>=8, DD>-18

현재 active Stage3 70개 중:

- profile catalog 포함: 9
- profile catalog 미포함: **61**

즉 validate에서 부적격으로 분류된 개체가 active live에는 들어갈 수 있다. 새 게이트 전에 가장 먼저 정리해야 할 충돌이다.

### 6.2 Stage2 central index 3중 중복

- `survivors.jsonl`: 1,162 unique source rows
- central index Stage2 eligible rows: 3,486
- 모든 survivor의 multiplicity: 정확히 3
- 중복 index rows: 2,324

현재 ticker dedup이 최종 결과 중복을 제거하지만, 필터 통계·스캔 비용·rank 해석을 왜곡한다.

### 6.3 Stage2 gate와 elite static filter 중복

Stage2 survivor는 이미 5개 기간을 통과했다. active elite는 같은 OOS·stress 성과를 다시 더 강하게 거른다.

특히 elite의 stress expectancy>=0.5는 기존 Stage2 stress>=1보다 느슨하므로 별도 거름망 역할을 하지 못한다. 반면 OOS exp>=2.7, fitness, win rate, trades 기준은 실질적인 추가 cut이다.

### 6.4 denylist 순서 문제

현재 순서:

```text
ticker top1 → denylist
```

권장 순서:

```text
candidate gate/deny → ticker top1
```

후자로 바꾸면 차단된 1순위 대신 같은 ticker의 2순위 후보를 검토할 수 있다.

### 6.5 stale 93-ID MAE/MFE gate

- gate source 후보: 93
- 현재 elite report: 82
- 현재 report 중 gate list 미존재: 9
- 과거 gate list에만 존재: 20

현재 미존재 9개는 성능이 나빠서가 아니라 단순히 ID가 없어서 `gate_missing`으로 차단된다.

### 6.6 should_buy 중복 평가

- live slots에서 `evaluate_candidate` 실행
- dashboard export에서 동일 후보를 다시 `evaluate_candidate`

시장·가격·뉴스 컨텍스트가 두 호출 사이에 변할 수 있으므로 후보 수가 달라질 수 있다. 재검증 자체는 안전 장치지만, 파일 간 시점 일관성 메타데이터가 필요하다.

### 6.7 비활성 Stage3 pool 두 버전

기본 pool:

- source 593
- first pass 427
- 최종 232

payoff-ratio pool:

- source 723
- first pass 244
- 최종 186

입력 source 시점과 크기가 달라 두 pool의 차이를 오직 payoff ratio 효과로 해석할 수 없다. 둘 다 현재 canonical profile catalog 2,012개를 반영하지 못한다.

## 7. 새 게이트 삽입 후보

### 1순위 — elite 원천 로드 직후

```text
Stage2 survivors 로드
Stage3 final_rulebooks 로드
→ [새 통합 게이트]
→ elite_score 정렬
→ ticker dedup/cap
→ denylist
→ 라이브 신호 평가
```

가장 적합한 이유:

- GA 재학습 없이 기존 원본에서 적용 가능.
- Stage2·Stage3에 같은 판정 schema를 적용할 수 있음.
- ticker dedup 전이라 차단 후보의 차순위 fallback 가능.
- stale 93-ID candidate join을 제거하고 현재 개체에 직접 판정 가능.
- Stage3 validate 우회를 명시적으로 해소할 수 있음.
- gate 결과를 candidate별 pass/fail/reason 파일로 남기기 쉬움.

구현 시 권장 위치:

- `engine/live/elite_shadow_report.py`
- `collect_stage2_elite`와 `collect_stage3_elite`에서 원본 row를 candidate로 만든 뒤
- ticker dedup loop 전에 공통 gate 함수 호출

### 2순위 — Stage3 final → validate 경계

```text
final_rulebooks
→ [새 pure-OOS gate]
→ 새 profile/gate catalog
→ active elite가 이 catalog만 소비
```

역사적 OOS 품질을 엄격히 고정하려면 가장 논리적인 위치다. 단, 현재 active elite가 catalog를 우회하므로 생성 코드만 추가해서는 효과가 없고 소비 경로도 함께 변경해야 한다.

### 3순위 — should_buy 이후, slot cut 이전

```text
실시간 should_buy
→ [시장·regime·유동성 gate]
→ top8 slot
```

현재 시점의 시장 상태를 반영하는 동적 gate에는 적합하다. 하지만 과거 OOS 품질이나 개체 구조 문제를 거르기에는 너무 늦다.

### 비권장 위치

- `real_dashboard_buy_candidates` 이후: 주문 직전이라 너무 늦고 후보 계보가 이미 소실됨.
- 현재 `stage3_live_pool` 위: 비활성·stale 경로라 현역 daemon에 효과 없음.
- denylist 이후: ticker fallback 불가능.
- Stage3 qualify 이후: qualify 개별 개체가 저장되지 않아 재게이트 불가능.

## 8. 산출물

- `filter_gate_chain.csv`
  - 23개 거름망의 순서, 코드, 입력·출력, 기준, 통과·탈락 수, 충돌.
- `filter_gate_outputs.csv`
  - 거름망 산출물 17개 유형의 경로, 크기, 수정일, live 사용, 현역/잔재 판정.
- `filter_gate_lineage_snapshot.json`
  - Stage2·3 집계, 현재 live 상태, 중복·충돌, 새 gate 후보.
- `run_filter_gate_lineage.py`
- `run_filter_gate_lineage_safe.py`
- `run_filter_gate_lineage_postprocess.py`
- `run_filter_gate_lineage_finalize.py`
- `filter_gate_lineage_readout.md`

이번 단계에서 삭제·이동·운영 파일 수정은 수행하지 않았다.
