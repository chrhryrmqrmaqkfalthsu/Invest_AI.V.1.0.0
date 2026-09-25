# 중복 integrated SHADOW hook 제거

## 결론

상류 `upstream_gate_enforcement=BLOCK`이 실제 v3·BOIL 차단을 담당하므로 `live_candidate_slots.py`의 downstream integrated SHADOW hook을 제거했다.

현재 설정:

```yaml
live:
  upstream_gate_enforcement: BLOCK
```

`live.integrated_gate_enforcement` 키는 dead config가 되므로 제거했다. audit-only로 남기지 않았다.

## 제거 전 중복 확인

최근 integrated hook batch 44개를 같은 시점의 상류 BLOCK decision catalog와 candidate_id로 join했다.

```text
integrated rows=44
upstream join=44
aggregate status match=44/44
integrated FAIL=0
integrated HOLD=0
integrated PASS=44
```

상류 BLOCK이 이미 FAIL/HOLD를 제거한 후라 downstream hook은 PASS 후보를 다시 PASS로 판정만 하고 있었다.

## 제거 내용

### `data/_system/ops/live_candidate_slots.py`

제거:

- `CandidateGateChecker` import
- `append_candidate_gate_log` import
- `integrated_gate_enforcement` import
- 후보별 integrated gate evaluate/logging
- `integrated_gate_*` blocked reason
- `last_refresh.integrated_gate_enforcement`
- `last_refresh.integrated_gate_shadow_summary`

상류 report, KEEP gate, signal evaluator, score, 주문 경로는 변경하지 않았다.

### `engine/live/candidate_gate.py`

상류에서 사용하지 않는 downstream 전용 항목 제거:

- `integrated_gate_enforcement()`
- downstream JSONL append 함수
- downstream shadow log path 및 `fcntl` 의존성

보존:

- `CandidateGateChecker`
- v3/BOIL 권위 source
- result schema
- `upstream_gate_enforcement()`

`CandidateGateChecker.evaluate()`는 enforcement 미지정 시 순수 판정용 `SHADOW`를 기본으로 사용한다.

### `config/policy.yaml`

```text
live.integrated_gate_enforcement
```

키를 제거했다.

## force refresh 결과

제거 전후 candidate ID 순서:

```text
ADMA CRS ALGT AEIS ARKW CBRL BTU BB BN ACMR
```

양쪽 모두 10개로 완전히 동일했다.

Identity canonical SHA-256:

```text
before=6b30d0b2d19dd2a86f7c86efd1bb9f1130b54c3585c1f461f13564fb23bd7000
after =6b30d0b2d19dd2a86f7c86efd1bb9f1130b54c3585c1f461f13564fb23bd7000
```

전체 row canonical hash는 force refresh가 `last_seen_at`, 현재 가격 등 변동 필드를 갱신하므로 달라졌다. 후보 식별자·순서 canonical hash는 동일하다.

## downstream

- `live_slots_state::candidate_pool`: 10개, 동일 ID
- Dashboard active slots: 동일 10개 + 빈 slot placeholder
- `S2AutoTrader.candidate_pool()`: 동일 10개
- `build_elite_shadow_report()`: 상류 BLOCK 57개 report 유지

## 테스트

```text
14 passed
```

검증 범위:

- v3·BOIL checker
- 상류 BLOCK/simple removal
- current pool이 모두 upstream PASS임
- integrated config/function 제거
- elite shadow state safety 및 mark-to-market 회귀

## 원본 artifact·진입 영향

- 원본 Stage2/Stage3 artifact 변경 없음
- 상류 BLOCK 유지
- 후보 ID 집합 변화 없음
- 주문 로직 변경 없음
- broker order 제출 없음

## 롤백

이 구현 커밋을 revert하면 downstream SHADOW audit hook과 config 키가 복구된다.
