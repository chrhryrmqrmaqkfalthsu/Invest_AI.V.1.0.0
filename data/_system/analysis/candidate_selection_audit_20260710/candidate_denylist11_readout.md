# 3증상 매칭 7개 추가 deny-list 등록 readout

범위: 기존 deny-list 4개(CE, CDE, BKSY, BOIL)를 유지하고, `live93_three_symptom_scan.csv`에서 3개 증상이 모두 걸린 7개(CENX, CRMD, CAR, AMBA, APH, ARKG, HCC)를 추가 등록했다. 이번 단계는 코드 변경 없이 `data/_system/candidate_denylist.json` 갱신만으로 처리했다. 원본 룰 풀은 보존했고, 실주문·재학습·direct order 설정 변경은 하지 않았다.

산출물:

- `data/_system/candidate_denylist.json`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_denylist11_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_denylist11_verification.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_denylist11_live_pool_after.csv`
- 갱신된 정규 후보 파일: `data/_system/real_dashboard_buy_candidates.json`

## 1. 추가 7개 대상 확정

`live93_three_symptom_scan.csv`와 deny-list를 일시 비활성화한 `build_elite_shadow_report` 원본 후보에서 full rule_hash를 확정했다.

| ticker | stage | candidate_id | rule_hash | scan labels |
|---|---|---|---|---|
| CENX | stage3 | `stage3:CENX:53d7b0caa6a3` | `53d7b0caa6a321410f8fdbec061b327aa226b52517f6e1f52e7036e0692d70f2` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |
| CRMD | stage3 | `stage3:CRMD:1a48a9a1b768` | `1a48a9a1b768496205b1a73550702d1c1800c43b9f7b1f19b3fc1b0123bb904c` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |
| CAR | stage3 | `stage3:CAR:ce44ff6acbb5` | `ce44ff6acbb5b04e863cb58225fefd3ae22547aacc513fe3c91df6085c4f2ad6` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |
| AMBA | stage3 | `stage3:AMBA:5e057e3cfc2d` | `5e057e3cfc2df98fbb87ebe52f32c9aafde092b1f0cbcfc81836cdbf4520a41e` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |
| APH | stage3 | `stage3:APH:c7885deba35c` | `c7885deba35cae43987baff9a497a3ecf1064d3396679bf09cac2b78ee691ff8` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |
| ARKG | stage3 | `stage3:ARKG:50b05b8de94f` | `50b05b8de94fb41aa249a5dfba005c5bd54a4c3524896d1729f852013aa4b4ac` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |
| HCC | stage2 | `stage2:HCC:2154d17783f3` | `2154d17783f315e2f23f440d5a501739f59e19f60e6f8227671e689ceed22264` | LOW_RATIO + TOP2_GE_90 + WEAK_EXIT |

모든 신규 entry의 `reason_label`은 의도적으로 `THREE_SYMPTOM_MATCH`로 통일했다. 이 7개는 CE처럼 개별 실전·과적합 검증을 깊게 거친 것이 아니라 3증상 라벨 일괄 제외이므로, 나중에 재검토할 때 이 라벨로 쉽게 분리할 수 있다.

## 2. deny-list 상태

파일:

```text
data/_system/candidate_denylist.json
```

상태:

```text
version: 2
entries: 11
match_policy: candidate_id exact OR ticker/stage constrained rule_hash match
```

기존 4개는 유지했다.

```text
CE, CDE, BKSY, BOIL
```

신규 7개는 `THREE_SYMPTOM_MATCH`로 추가했다.

```text
CENX, CRMD, CAR, AMBA, APH, ARKG, HCC
```

`apply_candidate_denylist()` 코드는 이전 구현 그대로 사용했다. 코드 수정은 필요 없었다.

## 3. 후보 생성 단계 검증

명령:

```text
venv/bin/python - <<'PY'
from engine.live.elite_shadow_report import build_elite_shadow_report
r = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
PY
```

결과:

| 항목 | 값 |
|---|---:|
| 원래 상위 후보 기준 | 93 |
| deny-list 적용 후 report 후보 | 82 |
| blocked_count | 11 |
| denied_present in report | 0 |

blocked 후보:

```text
stage2:HCC:2154d17783f3
stage3:CENX:53d7b0caa6a3
stage3:CRMD:1a48a9a1b768
stage3:CAR:ce44ff6acbb5
stage3:BKSY:f1bcc8efea02
stage3:CDE:ceb9fe0512dc
stage3:AMBA:5e057e3cfc2d
stage3:APH:c7885deba35c
stage3:BOIL:9044dc2c67a3
stage3:CE:998b0b638c66
stage3:ARKG:50b05b8de94f
```

## 4. live candidate_pool 재생성 검증

명령:

```text
venv/bin/python data/_system/ops/live_candidate_slots.py refresh --force-evaluate --max-candidates 93
```

결과:

| 항목 | 값 |
|---|---:|
| report candidate_count | 82 |
| evaluated | 63 |
| buy_signal_count | 18 |
| eligible_pool_count | 18 |
| denied_present in live candidate_pool | 0 |

재생성 후 live candidate_pool 18개:

```text
stage3:BMA:0c978464f9dd
stage3:ADMA:42437a3ee595
stage3:BTBT:363898884d44
stage3:BMI:07d4ee0f7841
stage3:BCS:5e7da5a74b01
stage3:ALGT:aec5dd5b1dc1
stage2:CMC:4f6ee2739add
stage3:BN:d264957fe5f6
stage3:BGC:d8c39420992c
stage3:ACMR:44c1e02681c4
stage3:CRS:8695c9ce3320
stage3:BTE:4ba9af200f79
stage3:BB:f1bdfe7f8ad9
stage3:BWXT:f195725cb792
stage3:ANET:fe220620802b
stage3:ARKW:296c057b4ef7
stage3:CBRL:677767a0b6a9
stage3:AEIS:6e26f08a7c6d
```

주의: ARKG는 deny-list에 추가되어 제외됐고, ARKW는 별도 ticker라 남아 있다.

## 5. 정규 후보 파일 export 검증

명령:

```text
PYTHONPATH=. venv/bin/python scripts/export_real_dashboard_buy_candidates.py --source-section candidate_pool --limit 26 --write --summary-path /tmp/km_denylist11_export_summary.json
```

결과:

| 항목 | 값 |
|---|---:|
| report_candidate_count | 82 |
| exported_count | 18 |
| skipped_count | 0 |
| denied_present in real_dashboard_buy_candidates.json | 0 |
| selected_rulebook/rulebook missing | 0 |

정규 후보 파일 `data/_system/real_dashboard_buy_candidates.json`은 18개 후보로 갱신됐고, 남은 후보는 모두 `selected_rulebook`과 `rulebook`을 포함해 정상 export됐다.

## 6. 원본 룰 풀 보존 확인

다음 22개 원본 artifact를 refresh/export 전후 sha256 및 mtime으로 대조했다.

- stage3 10개 ticker의 `final_rulebooks.jsonl` + `exit_trades.jsonl`
  - CE, CDE, BKSY, BOIL, CENX, CRMD, CAR, AMBA, APH, ARKG
- stage2 HCC의 `survivors.jsonl` + `trades.jsonl`

결과:

```text
all sha_same=True
all mtime_same=True
FAIL []
```

즉 원본 룰 풀과 trade history는 수정·삭제되지 않았다.

## 7. 안전 확인

- 원본 룰 풀 삭제·수정 없음.
- 재학습 없음.
- 실주문 없음.
- `.env` 및 direct order 설정 변경 없음.
- 기존 `apply_candidate_denylist()` 로직 유지, 코드 수정 없음.
- deny-list는 신규 후보 진입만 막는다. 이미 열린 실계좌 포지션을 자동 청산하지 않는다.

## 8. 최종 판정

```text
deny-list 총 11개 확정.
기존 4개 + 신규 THREE_SYMPTOM_MATCH 7개는 build_elite_shadow_report 후보 생성 단계에서 차단된다.
따라서 재생성 시 report / live candidate_pool / real_dashboard_buy_candidates export 어디에도 진입하지 않는다.
원본 final_rulebooks / survivors / exit_trades는 보존된다.
```
