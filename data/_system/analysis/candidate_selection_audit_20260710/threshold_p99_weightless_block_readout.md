# 가중치 무관 단방향 임계 p99 BLOCK v3 dry-run

- 정책 버전: `integrated-gate-v3-p99-reachability-block-weightless`
- 범위: Stage2 1,162 + Stage3 15,909 = 17,071개
- 설계 정책 반영: 완료 / 운영 구현: `false`
- 원본·라이브·운영 코드·재학습·주문·삭제: 0건

## 1. 판정식

- 저장 단방향 임계가 있으면 가중치와 무관하게 검사한다.
- `weight <= 0`은 `inactive_weight=True`로 기록하되 제외하지 않는다.
- `>=`: 임계 > p99는 NEAR_UNREACHABLE, 임계 > max는 UNREACHABLE.
- `<=`: 임계 < p01은 NEAR_UNREACHABLE, 임계 < min은 UNREACHABLE.
- MA/MACD 이벤트형과 RSI 밴드형은 제외한다.

## 2. v2 대비 추가 포섭

- v2 FAIL: **3,967개**
- v3 FAIL: **4,491개**
- 신규 포섭: **524개** — Stage2 32, Stage3 492
- 신규 포섭 중 inactive_weight: **524개**
- 신규 포섭 중 active_weight: **0개**
- 신규 고유 entry rule: **196개**

신규 524개는 모두 `weight=0`인 Volume 임계 도달불가 개체로, 검사대상 확장과 정확히 일치한다.

## 3. 최종 PASS/FAIL

| 범위 | PASS | FAIL | 합계 |
|---|---:|---:|---:|
| ALL | 12,580 | 4,491 | 17,071 |
| stage2 | 862 | 300 | 1,162 |
| stage3 | 11,718 | 4,191 | 15,909 |

- FAIL 고유 entry rule: **1,697개**
- active_weight FAIL: **3,967개**
- inactive_weight FAIL: **524개**
- 실제 차단 원인은 Volume 4,491개이며 BB는 0개다.

## 4. BOIL·CE 포섭

### BOIL 원형

`stage3:BOIL:9044dc2c67a3`

- Volume weight: `0.0`
- inactive_weight: `True`
- 저장 임계: `2.5`
- 학습기간 p99: `1.914656`
- 학습기간 max: `2.179716`
- 판정: `2.5 > 2.179716` → **UNREACHABLE / FAIL**
- reason: `INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_MAX`
- v2 대비 신규 포섭: `True`

BOIL 원형은 가중치 0 때문에 v2에서 제외됐지만, v3에서는 저장 임계 자체를 검사하므로 정상 포섭됐다.

### CE FAIL 7개

- 포섭: **3/7** — BOIL, BTE, CWK
- 미포섭: ANET, BB, CDE, CE

나머지 4개는 저장 단방향 임계가 p99 밖이 아니므로 CE 동적 검사가 계속 필요하다.

## 5. 기존 HIGH_VOL·BOIL형 게이트 관계

- HIGH_VOL_STAGE1_VOLUME_BLIND: 기준 3,036, 겹침 530, 기준 전용 2,506, v3 전용 3,961 → `PARALLEL_NOT_SUPERSET`
- HIGH_VOL_STAGE1_VOLUME_WEIGHT_NEAR_ZERO: 기준 425, 겹침 54, 기준 전용 371, v3 전용 4,437 → `PARALLEL_NOT_SUPERSET`
- HIGH_VOL_STAGE1_VOLUME_WEIGHT_EXACT_ZERO: 기준 410, 겹침 54, 기준 전용 356, v3 전용 4,437 → `PARALLEL_NOT_SUPERSET`
- HIGH_VOL_WEIGHT_ZERO_LIVE93: 기준 8, 겹침 3, 기준 전용 5, v3 전용 4,488 → `PARALLEL_NOT_SUPERSET`
- HIGH_VOL_STAGE2_STRICT_NEVER: 기준 84, 겹침 84, 기준 전용 0, v3 전용 4,407 → `NEW_BLOCK_SUPERSET`
- HIGH_VOL_STAGE2_RELAXED_NEVER_OR_RARE: 기준 801, 겹침 530, 기준 전용 271, v3 전용 3,961 → `PARALLEL_NOT_SUPERSET`

엄격 NEVER_FIRED 84개는 84/84 완전 포섭한다. 그러나 HIGH_VOL weight-zero 구조 중 임계가 p99 이내인 개체는 차단하지 않으므로 BOIL형 게이트 전체와는 여전히 병렬이다.

## 6. 최종 후보

- 결합 정적 PASS/HOLD/FAIL: **2,126 / 220 / 14,725**
- elite + denylist-before-dedup + fallback + stage cap: **85개**
  - Stage2 10 / Stage3 75
- v2 88개 대비: **-3개**
  - v2 후보 중 탈락 5 / fallback 신규 2

최종 85개로 실용 범위는 유지된다. Stage2 10개는 그대로이고 Stage3가 78→75개로 감소해 Stage3 중심 편중은 계속된다.

## 7. 결론

- v3는 v2의 완전 상위집합이며 가중치 0 도달불가 524개를 추가 포섭한다.
- BOIL 원형은 정상적으로 FAIL이지만, HIGH_VOL·BOIL형 전체와 CE 동적 검사는 병렬 유지가 필요하다.
- 정책은 설계 파일에만 반영했으며 운영 구현은 false다.

## 8. 산출물

- `threshold_p99_weightless_block_indicator_labels.csv.gz` — 85,355개 지표 라벨·weight·inactive_weight
- `threshold_p99_weightless_block_candidate_decisions.csv` — 17,071개 후보별 PASS/FAIL
- `threshold_p99_weightless_block_fail_evidence.csv` — v3 FAIL 전체 근거
- `threshold_p99_weightless_block_new_capture_evidence.csv` — 신규 524개 근거
- `threshold_p99_weightless_block_boil_ce_capture.csv` — BOIL·CE 포섭
- `threshold_p99_weightless_block_relationship_summary.csv` — 3단계 관계
- `threshold_p99_weightless_block_scenario_summary.csv` / `threshold_p99_weightless_block_combined_selected_candidates.csv` — 잔여·최종 후보
- `threshold_p99_weightless_block_summary.json` — 기계 판독 요약
