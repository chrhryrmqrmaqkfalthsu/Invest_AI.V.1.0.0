# BOIL·v3 정적 게이트 SHADOW 모드 구현

## 최종 상태

- 공통 checker 구현 완료
- v3 checker 구현 완료
- BOIL checker 구현 완료
- live candidate slots 후보 평가 지점 hook 연결 완료
- 기본 enforcement: `SHADOW`
- 실제 후보 차단: 0건
- 현재 후보 18개 shadow 판정: 완료
- 기존 BOIL dry-run과 대조: BNTX 1개 FAIL 일치

## 변경 파일

### 공통 게이트

```text
engine/live/candidate_gate.py
```

구성:

- `GateCheckResult`
- `CandidateGateDecision`
- `CandidateGateChecker`
- `check_v3()`
- `check_boil()`
- `append_candidate_gate_log()`
- `integrated_gate_enforcement()`

결과 schema:

```text
PASS / FAIL / HOLD
reasons
evidence
policy_version
source
checked_at
enforcement
should_block
```

## v3 기준

정책 version:

```text
integrated-gate-v3-p99-reachability-block-weightless
```

권위 source:

```text
data/_system/analysis/candidate_selection_audit_20260710/
threshold_p99_weightless_block_candidate_decisions.csv
```

기존 확정 기준 그대로 사용한다.

```text
저장 단방향 threshold가 training p99/p01/max/min 밖이면 FAIL
```

Runtime checker는 frozen catalog의:

```text
final_p99_weightless_block_status
p99_weightless_reason_codes
p99_weightless_fail_components
reachability labels
```

를 읽는다.

Catalog row가 없으면 `HOLD`다.

## BOIL 기준

정책 version:

```text
high-vol-volume-blind-near-zero-v3-exclusive
```

확정 기준:

```text
HIGH_VOL
AND 거래량 무시 진입 가능
AND abs(weight_volume_surge) <= 0.05
AND 기존 BOIL-v3 exclusive 조건 충족
```

권위 source:

```text
integrated_gate_candidate_dryrun.csv::check_boil
```

보조 evidence:

```text
high_vol_volume_blind_risk_candidates.csv
```

현재 frozen 산출물에서 BNTX는:

```text
vol_group=HIGH_VOL
nonvolume_entry_possible_market_cap=true
weight_volume_surge=0.0
legacy_boil_check=FAIL
integrated check_boil=FAIL
```

이다.

최신 weightless-v3 catalog에서는 BNTX가 v3 FAIL이므로, BOIL 조건을 단순 재계산하면 기존 확정 BOIL dry-run과 불일치한다. 새 기준을 만들지 않는다는 제약에 따라 BOIL checker는 기존 integrated `check_boil`을 권위값으로 사용하고 최신 v3 상태를 evidence에 함께 기록한다.

## live pipeline hook

Hook 위치:

```text
data/_system/ops/live_candidate_slots.py::refresh_slots()
```

후보의 기존 KEEP gate와 held exclusion 확인 후, `evaluate_candidate()` 직전에 실행한다.

순서:

```text
CandidateGateChecker.evaluate()
→ JSONL append
→ SHADOW이면 후보 흐름 그대로 유지
→ BLOCK이고 FAIL/HOLD이면 evaluate_candidate 전에 차단
```

기본값은 SHADOW다.

## config

```yaml
live:
  integrated_gate_enforcement: SHADOW
```

지원 값:

```text
SHADOW
BLOCK
```

키 누락·설정 오류·알 수 없는 값은 안전하게 SHADOW로 fallback한다.

## 로그

Runtime 경로:

```text
data/_system/analysis/boil_v3_shadow/
candidate_gate_YYYYMMDD.jsonl
```

로그 필드:

- candidate ID
- ticker
- enforcement
- aggregate status
- should_block
- v3 status/reason/evidence/source
- BOIL status/reason/evidence/source
- policy version
- candidate snapshot
- live hook path

샘플:

```text
data/_system/analysis/boil_v3_shadow_impl_20260711/shadow_log_sample.jsonl
```

## 현재 후보 18개 검증

현재 `live_slots_state.json::candidate_pool` 18개를 SHADOW로 평가했다.

결과:

```text
v3 FAIL: BTBT, BMI, BCS, BNTX, CRK
BOIL FAIL: BNTX
aggregate PASS: 13
aggregate FAIL: 5
aggregate HOLD: 0
actual should_block: 0
```

BOIL frozen dry-run 대조:

```text
18/18 일치
BOIL FAIL=BNTX 1개
```

상세:

```text
dryrun_comparison.csv
```

## 동작 불변

검증 전후 `live_slots_state.json` SHA-256:

```text
91d878230ca342b2704b0dd920efde1dcd1cca9288cc4010093bc77595c21b3f
```

동일했다.

따라서 검증 과정에서:

- candidate pool 변화 0
- candidate score 변화 0
- slots/waitlist 변화 0
- 후보 통과 여부 변화 0
- 주문 변화 0
- ledger 변화 0

이다.

기본 SHADOW runtime에서도 FAIL/HOLD는 `should_block=false`다.

## 테스트

```text
PYTHONPATH=. venv/bin/python -m pytest -q tests/test_candidate_gate_shadow.py
```

결과:

```text
4 passed
```

검증 범위:

- SHADOW FAIL이 후보를 차단하지 않음
- BLOCK FAIL이 차단됨
- missing catalog는 HOLD
- 현재 후보 18개 전수 매핑
- BOIL FAIL BNTX 1개 일치
- config 기본값 SHADOW

## 롤백

구현 커밋을 revert하면 된다.

```bash
cd ~/kingmaker
git revert <track_A_commit>
git push
```

또는 config를 SHADOW로 유지하면 게이트는 실제 차단을 하지 않는다.

## 산출물

- `dryrun_comparison.csv`
- `behavior_invariance.md`
- `shadow_log_sample.jsonl`
- `readout.md`

운영 기본 동작 변경: 0건
