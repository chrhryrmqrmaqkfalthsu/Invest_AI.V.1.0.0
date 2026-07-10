# 4개 개체 후보 생성 단계 제외 deny-list 구현 readout

범위: CE, CDE, BKSY, BOIL 4개 개체를 원본 룰 풀을 보존한 채 후보 생성 단계에서 제외했다. 원본 `final_rulebooks.jsonl`, `survivors.jsonl`, `exit_trades.jsonl`는 삭제·수정하지 않았다. 실주문 경로는 호출하지 않았고, `.env` 또는 `direct_orders_enabled` 관련 설정은 건드리지 않았다.

산출물:

- `data/_system/candidate_denylist.json`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_denylist_implementation_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_denylist_verification.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/candidate_denylist_live_pool_after.csv`
- 갱신된 정규 후보 파일: `data/_system/real_dashboard_buy_candidates.json`

## 1. 제외 대상 확정

이전 스캔 산출물 `live93_three_symptom_scan.csv`와 `build_elite_shadow_report` 원본 후보에서 full rule_hash를 확정했다.

| ticker | candidate_id | rule_hash |
|---|---|---|
| CE | `stage3:CE:998b0b638c66` | `998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78` |
| CDE | `stage3:CDE:ceb9fe0512dc` | `ceb9fe0512dc780e9aed7e8625bd67d935f1376a5a1bc01b97e146ce6142d000` |
| BKSY | `stage3:BKSY:f1bcc8efea02` | `f1bcc8efea02d63490f0c6ee1dbe7507416a85e2197551cf16a96db9054d9afc` |
| BOIL | `stage3:BOIL:9044dc2c67a3` | `9044dc2c67a3d3bc0c5d93cece5f266c162c4f0ecf3ed5bc8ded1edbafb4bd67` |

rule_hash 중복 확인 결과, 현재 report 후보군 안에서는 각 hash가 1개 후보에만 매칭됐다. 그래도 구현은 hash 단독 차단이 아니라 `candidate_id exact OR ticker/stage constrained rule_hash` 정책으로 만들어 의도치 않은 타 종목 제외를 막았다.

## 2. 진입 지점 특정

정확한 흐름:

```text
engine.live.elite_shadow_report.build_elite_shadow_report()
  -> data/_system/ops/live_candidate_slots.py refresh_slots()
      -> gate_keep + held_exclusion + evaluate_candidate + should_buy
      -> live_slots_state.candidate_pool
  -> scripts/export_real_dashboard_buy_candidates.py
      -> build_elite_shadow_report()로 full candidate 재조회
      -> full_rulebook validation + should_buy 재검증
      -> real_dashboard_buy_candidates.json
```

파일화된 93 리스트나 현재 `live_slots_state`만 손대면 다음 refresh/export 때 다시 올라올 수 있다. 재생성해도 안 올라오게 하려면 `build_elite_shadow_report()` 내부에서 stage2/stage3 후보가 합쳐진 직후 필터링해야 한다.

구현 위치:

```text
engine/live/elite_shadow_report.py
build_elite_shadow_report()
  stage2, skip2 = collect_stage2_elite(...)
  stage3, skip3 = collect_stage3_elite(...)
  candidates = stage2 + stage3
  candidates, denylist_summary = apply_candidate_denylist(candidates)
```

이 위치에서 제외하면 `refresh_slots()`와 export가 둘 다 동일하게 차단된다. 원본 `final_rulebooks/survivors`는 읽기만 한다.

## 3. deny-list 설계

파일:

```text
data/_system/candidate_denylist.json
```

정책:

```text
candidate_id exact OR ticker/stage constrained rule_hash match
```

이유:

- `candidate_id`는 가장 안전한 개체 단위 키다.
- `rule_hash`도 저장하되, 같은 hash를 쓰는 무관한 종목이 생기는 경우를 막기 위해 ticker/stage 조건을 함께 만족해야 match한다.
- entry의 `active=false`로 비활성화 가능하다.
- 차단된 후보는 report의 `candidate_denylist.blocked`와 `summary.denylist_blocked`에 남고, logger에도 `candidate deny-list blocked ...` 형식으로 기록된다.

## 4. 구현 diff 요약

수정 파일:

```text
engine/live/elite_shadow_report.py
```

추가된 주요 함수:

```text
load_candidate_denylist()
_denylist_entry_matches()
apply_candidate_denylist()
```

변경된 report payload:

```text
candidate_denylist: denylist_summary
summary.denylist_blocked_count
summary.denylist_blocked
filters.candidate_denylist
```

기존 `gate_keep`, `should_buy`, safety guard, 청산 배관, export의 full-rulebook 검증 로직은 변경하지 않았다.

## 5. 93 후보 생성 단계 검증

명령:

```text
venv/bin/python -m py_compile engine/live/elite_shadow_report.py
venv/bin/python - <<'PY'
from engine.live.elite_shadow_report import build_elite_shadow_report
r = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
PY
```

결과:

| 항목 | 값 |
|---|---:|
| deny-list 적용 전 report 후보 | 93 |
| deny-list 적용 후 report 후보 | 89 |
| deny-list blocked_count | 4 |
| denied candidate present in report | 0 |

차단된 후보:

```text
stage3:BKSY:f1bcc8efea02
stage3:CDE:ceb9fe0512dc
stage3:BOIL:9044dc2c67a3
stage3:CE:998b0b638c66
```

## 6. live 후보 재생성 검증

명령:

```text
venv/bin/python data/_system/ops/live_candidate_slots.py refresh --force-evaluate --max-candidates 93
```

결과:

| 항목 | 값 |
|---|---:|
| report candidate_count | 89 |
| evaluated | 68 |
| buy_signal_count | 18 |
| eligible_pool_count | 18 |
| denied candidate present in candidate_pool | 0 |

재생성 후 live candidate_pool:

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

중요: 요청서에는 “26개 → 22개”를 기대값으로 적었지만, 실제 재생성 시점의 시장/뉴스 컨텍스트가 갱신되어 should_buy 결과가 바뀌었다. 같은 현재 컨텍스트에서 deny-list를 임시 비활성화한 read-only 대조 baseline은 20개였고, deny-list 적용 후 18개였다.

| 비교 | 후보 수 | 포함된 deny 대상 |
|---|---:|---|
| same-context deny 미적용 baseline | 20 | CDE, BOIL |
| same-context deny 적용 | 18 | 없음 |

즉 후보 생성 단계에서는 4개가 모두 제거됐고, 현재 should_buy 상태에서는 그중 CDE와 BOIL만 실제 live pool에 들어왔을 상태였다. CE와 BKSY는 93 후보에는 있었지만 현재 context에서는 should_buy가 아니었다.

## 7. 정규 후보 파일 export 검증

명령:

```text
PYTHONPATH=. venv/bin/python scripts/export_real_dashboard_buy_candidates.py --source-section candidate_pool --limit 26 --write --summary-path /tmp/km_denylist_export_summary.json
```

결과:

| 항목 | 값 |
|---|---:|
| report_candidate_count | 89 |
| exported_count | 18 |
| skipped_count | 0 |
| denied candidate present in canonical export | 0 |
| selected_rulebook/rulebook missing | 0 |

정규 후보 파일 `data/_system/real_dashboard_buy_candidates.json`은 18개 후보로 갱신됐고, 모든 후보에 `selected_rulebook`과 `rulebook`이 정상 포함됐다.

## 8. 원본 룰 풀 보존 확인

다음 파일들의 sha256과 mtime을 refresh/export 전후 비교했다.

```text
exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
exp_batch_stage123_2009_20260616_full/tickers/CDE/stage3/final_rulebooks.jsonl
exp_batch_stage123_2009_20260616_full/tickers/BKSY/stage3/final_rulebooks.jsonl
exp_batch_stage123_2009_20260616_full/tickers/BOIL/stage3/final_rulebooks.jsonl
exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/exit_trades.jsonl
exp_batch_stage123_2009_20260616_full/tickers/CDE/stage3/exit_trades.jsonl
exp_batch_stage123_2009_20260616_full/tickers/BKSY/stage3/exit_trades.jsonl
exp_batch_stage123_2009_20260616_full/tickers/BOIL/stage3/exit_trades.jsonl
```

결과: 전부 `sha_same=True`, `mtime_same=True`.

## 9. 안전 확인

- 원본 룰 풀 삭제·수정 없음.
- 재학습 없음.
- 실주문 없음.
- `.env` 및 direct order 설정 변경 없음.
- 이미 열린 실계좌 포지션은 이 deny-list로 자동 청산되지 않는다. deny-list는 신규 후보 진입만 막는다.

## 10. 최종 판정

구현 목적은 충족했다.

```text
CE, CDE, BKSY, BOIL은 build_elite_shadow_report 후보 생성 단계에서 차단된다.
따라서 재생성 시에도 live 93 후보군, live candidate_pool, real_dashboard_buy_candidates export로 진입하지 않는다.
원본 final_rulebooks/survivors/exit_trades는 보존된다.
```
