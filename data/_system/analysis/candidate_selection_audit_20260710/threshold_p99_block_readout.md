# 도달불가 단방향 임계 BLOCK 최종 dry-run

- 정책 버전: `integrated-gate-v2-p99-reachability-block`
- 범위: Stage2 survivors 1,162 + Stage3 final rulebooks 15,909 = 17,071개
- 반영 상태: 통합 게이트 설계 파일에 STATIC BLOCK checker로 반영, 운영 구현은 하지 않음
- 원본·라이브·운영 코드·재학습·주문·삭제: 0건

## 1. 확정 판정식

- 공통 선행조건: 저장 core 가중치 `weight > 0`.
- 단방향 `>=`: 임계가 학습기간 raw p99를 초과하면 `NEAR_UNREACHABLE`, max를 초과하면 `UNREACHABLE`.
- 단방향 `<=`: 임계가 학습기간 raw p01보다 낮으면 `NEAR_UNREACHABLE`, min보다 낮으면 `UNREACHABLE`. 이는 부호를 뒤집은 activation-tail 분포에서 p99/max 초과와 동치다.
- 두 라벨 중 하나라도 있으면 룰 전체 `FAIL`.
- MA/MACD는 스칼라 임계가 없는 이벤트형이므로 제외한다. RSI는 양방향 밴드이므로 명시된 단방향 전용 정책에서 제외한다.

## 2. 17,071개 최종 PASS/FAIL

| 범위 | PASS | FAIL | 합계 |
|---|---:|---:|---:|
| ALL | 13,104 | 3,967 | 17,071 |
| stage2 | 894 | 268 | 1,162 |
| stage3 | 12,210 | 3,699 | 15,909 |

- FAIL 후보: **3,967개**
- PASS 후보: **13,104개**
- FAIL 고유 entry rule: **1,501개**

## 3. 지표별 결과

| 지표 | 정책 형태 | REACHABLE | NEAR | UNREACHABLE | 제외/비활성 | BLOCK 후보 |
|---|---|---:|---:|---:|---:|---:|
| ma | EVENT 제외 | 0 | 0 | 0 | 17,071 | 0 |
| macd | CROSS EVENT 제외 | 0 | 0 | 0 | 17,071 | 0 |
| rsi | BAND 제외 | 0 | 0 | 0 | 17,071 | 0 |
| bb | ONE_SIDED_LE | 16,094 | 0 | 0 | 977 | 0 |
| volume | ONE_SIDED_GE | 11,259 | 2,873 | 1,094 | 1,845 | 3,967 |

이번 원본에서 실제 BLOCK은 Volume 3,967개에 집중됐고 BB BLOCK은 0개였다.

## 4. BOIL·CE 7개 포섭

- BOIL 원형 `stage3:BOIL:9044dc2c67a3`: **미포섭**.
  - Volume 가중치가 0이므로 `INACTIVE_WEIGHT`; BB 임계는 `REACHABLE`이다.
- 기존 CE FAIL 7개: **1/7 포섭**.
- 이 BLOCK은 저장된 활성 단방향 임계의 학습분포 검증 여부를 검사한다. BOIL의 거래량 무시 구조와 CE의 동적 점수 집중 구조를 대신하는 조건이 아니다.

## 5. 기존 HIGH_VOL 3단계와의 관계

- HIGH_VOL_STAGE1_VOLUME_BLIND: 기존 3,036개 중 476개 포섭, 기존 전용 2,560개, 새 BLOCK 전용 3,491개 → **병렬 조건**.
- HIGH_VOL_STAGE2_STRICT_NEVER: 기존 84개 중 69개 포섭, 기존 전용 15개, 새 BLOCK 전용 3,898개 → **병렬 조건**.
- HIGH_VOL_STAGE2_RELAXED_NEVER_OR_RARE: 기존 801개 중 476개 포섭, 기존 전용 325개, 새 BLOCK 전용 3,491개 → **병렬 조건**.

새 BLOCK은 HIGH_VOL 여부와 무관하게 죽은 활성 단방향 임계를 찾지만, HIGH_VOL 게이트는 거래량을 진입 근거로 사용하지 않는 구조도 잡는다. 따라서 서로 완전 포섭하지 않는다.

## 6. 기존 통합 게이트와 결합한 잔여 후보

- 결합 정적 PASS: **2,226개**
- 결합 HOLD: **232개**
- 결합 FAIL: **14,613개**
- 기존 elite filter + denylist-before-dedup + stage cap 재선택: **88개**
  - Stage2 10개 / Stage3 78개

threshold BLOCK 단독 잔여 13,104개는 수십~수백 범위가 아니다. 다만 기존 정적 게이트·elite·ticker dedup까지 결합하면 88개로 실용 범위에 들어온다. Stage2는 10개로 기존 cap 60을 크게 밑돌아 단계 균형은 추가 감시가 필요하다.

## 7. 이벤트형 제외 누락 위험

- 활성 MA 이벤트 15,393개 중 학습기간 0회 발생: 0개.
- 활성 MACD 교차 15,578개 중 학습기간 0회 발생: 0개.
- 활성 RSI 밴드 16,243개 중 학습기간 0회 충족: 0개.
- 현재 17,071개에서는 제외형 조건의 0회 관측 개체가 없어 즉시 누락된 죽은 조건은 확인되지 않았다. 다만 미래 신규 룰에는 이벤트 빈도 전용 checker가 별도로 필요하며, p99 임계 checker로 잘못 대체하면 안 된다.

## 8. 최종 판단

- `one_sided_threshold_p99_reachability`는 독립 STATIC BLOCK으로 확정 반영했다.
- BOIL·CE를 전부 포섭하지 않으므로 기존 HIGH_VOL 구조 검사와 CE 동적 검사는 병렬 유지해야 한다.
- 운영 코드 반영은 금지 조건에 따라 수행하지 않았다. 현재 반영 범위는 설계 정책·dry-run 산출물뿐이다.

## 9. 산출물

- `threshold_p99_block_indicator_labels.csv.gz` — 85,355개 지표 행과 후보 최종 상태
- `threshold_p99_block_candidate_decisions.csv` — 17,071개 후보별 지표 라벨 및 PASS/FAIL
- `threshold_p99_block_fail_evidence.csv` — FAIL 근거 전체
- `threshold_p99_block_boil_ce_capture.csv` — BOIL·CE 포섭
- `threshold_p99_block_component_summary.csv` / `threshold_p99_block_stage_summary.csv` — 지표·stage 요약
- `threshold_p99_block_relationship_summary.csv` — HIGH_VOL 게이트 관계
- `threshold_p99_block_scenario_summary.csv` / `threshold_p99_block_combined_selected_candidates.csv` — 결합 잔여·재선택
- `threshold_p99_block_summary.json` — 기계 판독 요약
