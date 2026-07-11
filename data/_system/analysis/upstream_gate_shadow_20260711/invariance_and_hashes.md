# SHADOW 동작·해시 불변 확인

## report 후보 불변

변경 전 `engine/live/elite_shadow_report.py`를 Git HEAD에서 메모리 로드하고, 변경 후 builder와 동일 인자로 비교했다.

```text
stage2_limit=60
stage3_limit=80
include_trades=false
```

결과:

```text
before candidate_count=82
after candidate_count=82
candidate_id ordered list equal=true
before stage_counts={stage3:70, stage2:12}
after  stage_counts={stage3:70, stage2:12}
```

따라서 `upstream_gate_enforcement=SHADOW`에서 report 후보 목록·순서가 변하지 않았다.

## live state SHA-256

상류 report 생성 직전·직후:

```text
before=cd80170703e4915f90230469a113a7790127c4936dbf00d741e195334c5aad7d
after =cd80170703e4915f90230469a113a7790127c4936dbf00d741e195334c5aad7d
```

```text
state_equal=true
```

Candidate pool canonical SHA-256:

```text
before=16f58ce0e29e5f620ec58d4be0c20074d0f9226990f4fe4063747c354fc0b7d1
after =16f58ce0e29e5f620ec58d4be0c20074d0f9226990f4fe4063747c354fc0b7d1
```

```text
pool_equal=true
```

장외 daemon은 별도 주기에서 `updated_at`과 `REFRESH_SKIPPED` 이벤트를 저장하므로 장시간 전체 파일 비교 시 state hash가 달라질 수 있다. 위 값은 동일 report 호출을 사이에 둔 즉시 전후 비교다.

## 원본 artifact 불변

해시 대상:

- `exp_batch_stage123_2009_20260616_full/central_index.jsonl`
- 모든 `tickers/*/stage3/final_rulebooks.jsonl` 269개

총 270개 원본 artifact의 SHA-256 목록을 구현 검증 전후 비교했다.

```text
ARTIFACT_HASHES_UNCHANGED=true
changed_count=0
```

원본 Stage2/Stage3 artifact 수정·삭제·재생성은 없었다.

## 진입 동작

설정:

```yaml
live:
  upstream_gate_enforcement: SHADOW
  integrated_gate_enforcement: SHADOW
```

상류 helper는 SHADOW에서 baseline sorted/deduplicated 후보를 그대로 반환한다. 제거·승계 결과는 JSONL 시뮬레이션에만 기록한다.

따라서:

- 후보 제거 0
- report pool 변경 0
- live candidate pool 변경 0
- score 변경 0
- 주문 변경 0
- ledger 변경 0
