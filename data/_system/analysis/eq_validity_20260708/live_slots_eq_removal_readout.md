# 라이브 후보 슬롯 EQ 제거/강등 적용 결과

## 목적

실거래 라이브 후보 슬롯(`data/_system/ops/live_candidate_slots.py`)에서 검증 완료된 후보 선정·우선순위 로직만 사용하고, `EQ(entry_quality)` allow/block 판정은 후보 판단에서 완전히 배제했다.

앞선 검증 결과:

```text
EQ 최종 판정: EQ_FILTER_UNVERIFIED
근사 frozen 보조 판정: EQ_FILTER_HURTS_APPROX
```

## 0단계 read-only 확인

현재 코드에서 EQ는 다음 경로로 들어왔다.

```text
live_candidate_slots.py
→ evaluate_candidate(candidate, ctx)
→ ev.entry_quality
→ public_candidate_row()에서 entry_quality_allow/score/label/primary_reason으로 복사
```

확인 결과, EQ는 슬롯 후보 자격/정렬에는 쓰이지 않았다.

후보 자격 경로:

```text
1. gate_map에 존재해야 함
2. gate_keep=True 여야 함
   - IS bad-MAE/mfe-capture DROP 13개 제외
3. held_exclusions에 없어야 함
4. evaluate_candidate() 성공
5. ev.should_buy=True
   - evaluate_signal 기준 final_score >= threshold
```

정렬 경로:

```text
sort_candidate_pool(rows)
→ priority_group 오름차순
→ final_score 내림차순
→ ticker
→ candidate_id
```

`priority_group`은 다음 조건만 반영한다.

```text
SPY DOWN regime AND vol_group == HIGH_VOL → priority_group=1 후순위
그 외 → priority_group=0
```

즉 EQ allow/block은 원래도 gate/sort에 직접 관여하지 않았다. 다만 상태 파일/API/화면에 실제 allow/block처럼 복사되어 오해 가능성이 있었다.

## 수정 내역

수정 파일:

```text
data/_system/ops/live_candidate_slots.py
data/_system/live_slots_state.json
```

후보 선정·정렬 로직은 그대로 유지했다.

고정된 검증 후보 선정 로직:

```text
KEEP gate
+ evaluate_signal should_buy=True(final_score >= threshold)
+ final_score 높은 순
+ SPY DOWN이면 HIGH_VOL 후순위
```

EQ 처리 변경:

```text
변경 전:
entry_quality_allow = 실제 assess_shadow_entry_quality allow/block
entry_quality_score = 실제 EQ score
entry_quality_label = 실제 STRONG/HEALTHY/WEAK/FAILED
entry_quality_primary_reason = 실제 EQ reason

변경 후:
entry_quality_policy = EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
entry_quality_verdict = EQ_FILTER_UNVERIFIED
entry_quality_allow = null
entry_quality_score = null
entry_quality_label = EQ_UNVERIFIED_REFERENCE_ONLY
entry_quality_primary_reason = excluded_from_candidate_decision_after_eq_validity_20260708
```

코드 주석도 추가했다.

```text
EQ(entry_quality)는 EQ_FILTER_UNVERIFIED 판정으로 후보 자격·정렬에서 배제한다.
evaluate_candidate()가 entry_quality를 계산하더라도 여기서는 실제 allow/block 값을 복사하지 않는다.
라이브 슬롯 판단에 남기는 검증된 경로는 KEEP gate + should_buy + final_score priority + SPY DOWN/HIGH_VOL 후순위뿐이다.
```

## 제거 전후 슬롯 구성 확인

수정 전 슬롯:

| slot | ticker | candidate_id | final_score | EQ label | EQ allow |
|---:|---|---|---:|---|---|
| 1 | BMI | stage3:BMI:07d4ee0f7841 | 15.9444 | HEALTHY_FOLLOW_THROUGH | True |
| 2 | BMA | stage3:BMA:0c978464f9dd | 13.4703 | WEAK_FOLLOW_THROUGH | False |
| 3 | BTBT | stage3:BTBT:363898884d44 | 11.4244 | FAILED_FOLLOW_THROUGH | False |
| 4 | ADMA | stage3:ADMA:42437a3ee595 | 8.1339 | STRONG_FOLLOW_THROUGH | True |
| 5 | CE | stage3:CE:998b0b638c66 | 7.1955 | STRONG_FOLLOW_THROUGH | True |
| 6 | ALGT | stage2:ALGT:402f72d48c3c | 6.7068 | FAILED_FOLLOW_THROUGH | False |
| 7 | CAMT | stage3:CAMT:bd5f11c548d5 | 6.4889 | FAILED_FOLLOW_THROUGH | False |
| 8 | ALGT | stage3:ALGT:aec5dd5b1dc1 | 5.5973 | FAILED_FOLLOW_THROUGH | False |

수정 후 `refresh --force-evaluate` 슬롯:

| slot | ticker | candidate_id | final_score | EQ label | EQ allow | EQ policy |
|---:|---|---|---:|---|---|---|
| 1 | BMI | stage3:BMI:07d4ee0f7841 | 15.9707 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 2 | BMA | stage3:BMA:0c978464f9dd | 13.4703 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 3 | BTBT | stage3:BTBT:363898884d44 | 11.4328 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 4 | CE | stage3:CE:998b0b638c66 | 8.3632 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 5 | ADMA | stage3:ADMA:42437a3ee595 | 8.1339 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 6 | ALGT | stage2:ALGT:402f72d48c3c | 6.7068 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 7 | CAMT | stage3:CAMT:bd5f11c548d5 | 6.4889 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |
| 8 | ALGT | stage3:ALGT:aec5dd5b1dc1 | 5.5973 | EQ_UNVERIFIED_REFERENCE_ONLY | null | EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE |

구성 확인:

```text
후보 candidate_id 집합: 동일 8개
슬롯 filled: 8/8 유지
EQ allow/block 실제값: 제거됨(null/UNVERIFIED marker)
```

순서 확인:

```text
수정 후 순서는 final_score 내림차순 그대로다.
CE와 ADMA 순서가 수정 전 대비 바뀐 것은 EQ 때문이 아니라 refresh 시점의 live 평가 점수 변화 때문이다.
수정 후 CE final_score=8.3632, ADMA final_score=8.1339 이므로 final_score 정렬상 CE가 앞서는 것이 맞다.
```

## API 확인

`/api/real/candidate_slots` 확인 결과:

```text
candidate slots: 8
filled: 8
entry_quality_label: EQ_UNVERIFIED_REFERENCE_ONLY
entry_quality_allow: null
```

## 결론

```text
EQ는 원래 후보 pool 자격/정렬에는 직접 관여하지 않았다.
하지만 상태/API 출력에 실제 allow/block처럼 노출되어 오해 가능성이 있었다.
이번 수정으로 실제 allow/block 값 복사를 중단했고, EQ는 검증 안 된 참고 marker로 강등했다.
후보 판단에는 검증된 KEEP gate + should_buy + final_score priority + SPY DOWN/HIGH_VOL 후순위만 남았다.
```
