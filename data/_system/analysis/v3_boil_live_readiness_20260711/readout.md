# v3·BOIL 라이브 반영 실체 확인

## 최종 판정

`NEEDS_IMPLEMENTATION`

v3와 BOIL 모두 현재 운영 코드에 구현체가 없다. 활성화할 설정 키나 on/off 스위치도 없다.

현재 존재하는 것은:

- 설계 문서
- checker 인터페이스 제안
- dry-run 판정 파일
- 후보별 PASS/FAIL 산출물
- BOIL 차단 정당화 통계

까지다.

따라서 로드맵의 “v3·BOIL 라이브 반영”은 기존 구현을 켜는 작업이 아니라 신규 운영 구현 작업이다.

## 1. 운영 구현체 존재 여부

운영 검색 범위:

```text
engine/
scripts/
config/
data/_system/ops/
```

검색어:

```text
BOIL
check_boil
boil_block
v3_gate
check_v3
v3_overlap
high_vol_volume_blind
weightless_block
integrated_gate
```

결과:

```text
runtime implementation file: 0
runtime config/switch: 0
```

반면 분석 디렉터리에는 관련 설계·dry-run 파일이 다수 존재한다.

## 2. integrated_gate_architecture.json과 동일 대상인가

동일 대상이다.

`integrated_gate_architecture.json`:

```text
design_status=DESIGN_ONLY_NOT_IMPLEMENTED
```

v3 checker:

```text
name=one_sided_threshold_p99_reachability_weightless
enforcement=BLOCK
note=design-only v3 BLOCK; operational implementation false
```

BOIL checker:

```text
name=high_vol_volume_blind_near_zero_v3_exclusive
enforcement=BLOCK
note=BLOCK_JUSTIFIED design decision; operational implementation false
```

확정 섹션도 각각:

```text
confirmed_static_block_policy.operational_implementation=false
confirmed_boil_enforcement.operational_implementation=false
confirmed_parallel_gate_structure.operational_implementation=false
```

로 명시한다.

즉 앞서 확인한 `operational_implementation=false`와 이번 로드맵의 v3·BOIL은 같은 gate 설계다.

## 3. 활성화 방식

활성화 스위치는 없다.

확인되지 않은 항목:

- `v3_enabled`
- `boil_enabled`
- `integrated_gate_enabled`
- gate policy version runtime config
- shared CandidateGateChecker runtime registry

따라서 다음 형태의 단순 설정 전환은 현재 불가능하다.

```yaml
v3_enabled: true
boil_enabled: true
```

해당 키 자체와 이를 읽는 운영 코드가 없다.

## 4. 신규 구현 범위

### 공통 gate runtime

설계 문서가 제안한 다음 인터페이스를 실제 코드로 구현해야 한다.

```text
CandidateGateChecker
GateCheckResult
PASS / FAIL / HOLD / NOT_APPLICABLE / ERROR
BLOCK / MONITOR
policy_version
source_fingerprints
```

### v3 checker

필요 기능:

- candidate별 저장 임계와 weight 로드
- candidate의 training-window OHLCV와 indicator distribution 연결
- 단방향 GE/LE 임계의 p99/p01/max/min 판정
- weight=0도 검사 대상에 포함
- MA/MACD 이벤트형과 RSI band 제외
- FAIL reason과 evidence 저장
- training data 누락 시 fail-closed HOLD 처리

### BOIL checker

필요 기능:

- HIGH_VOL 분류
- Volume 없이 진입 가능한 구조 판정
- `abs(weight_volume_surge)<=0.05`
- v3 PASS 후보에만 적용
- v3와 BOIL 책임 중복 방지
- BOIL FAIL reason과 evidence 저장

### live pipeline 연결

설계 문서의 연결점:

1. `data/_system/ops/live_candidate_slots.py`
   - candidate report 생성 전 static gate catalog 갱신·조회
2. `engine/live/elite_shadow_report.py`
   - ticker dedup 이전 FAIL/HOLD 제거
3. `scripts/export_real_dashboard_buy_candidates.py`
   - policy version과 gate PASS 재확인
4. 후보 파일에 gate result·policy version 저장

### 상태·운영 안전성

필요 기능:

- source fingerprint와 incremental update
- unstable file write guard
- atomic gate catalog/live candidate outputs
- policy version 변경 시 full rebuild
- FAIL/HOLD 시 fallback 후보 승계
- shadow-only 단계와 rollback switch
- live slot daemon 재적재 절차

## 5. v3 검증 상태

v3의 확인된 검증은 구조 dry-run이다.

```text
Stage2 1,162
Stage3 15,909
총 17,071 후보
v3 FAIL 4,491
v2 대비 신규 포섭 524
```

검증 대상은 저장 단방향 임계가 training-window p99/p01 또는 max/min 밖인지다.

BOIL 원형도:

```text
Volume threshold=2.5
training p99=1.914656
training max=2.179716
2.5 > max → UNREACHABLE
```

로 포섭된다.

그러나 v3 BLOCK 자체가 독립 hold-out/OOS에서 수익성을 개선했다는 직접 검증 파일은 확인되지 않았다.

판정:

```text
v3 structural validation: 있음
v3 independent performance validation: 확인 불가
v3 prospective live validation: 없음
```

## 6. BOIL 검증 상태

BOIL 결정 문서와 PnL 0.40% vs 3.05%, CI는 같은 대상에 연결된다.

조건:

```text
HIGH_VOL
AND entry_possible_without_volume
AND abs(weight_volume_surge)<=0.05
AND v3 PASS
```

성과:

```text
BOIL형 후보 371
holdout trades 6,769
평균 PnL 0.4006%
승률 47.39%

non-BOIL HIGH_VOL 후보 2,135
holdout trades 36,059
평균 PnL 3.0484%
승률 53.64%
```

고유 entry-rule PnL 차이:

```text
-2.7062%p
95% CI [-3.4699, -1.9337]
```

추가 frozen OOS:

```text
prior live93 CI [-4.5214, -0.8894]
v3 survivor live93 CI [-5.9438, -1.7373]
```

실제 코드상 데이터:

- Stage2 `period_label=oos_2025h2`
- Stage3 `period_label=recent_1y`
- frozen live93 `split=OOS`

따라서 BOIL은 hold-out/OOS 기반 차단 정당화 근거가 존재한다.

다만:

- Stage3 recent_1y는 diagnostic validation으로 명시됨
- 조건 확정과 차단 결정이 holdout 결과를 본 사후 결정
- prospective live A/B는 없음

이므로 “완전한 실전 검증 완료”로 표현하면 과도하다.

## 7. 판정표

| 항목 | 결과 |
|---|---|
| v3 운영 구현체 | 없음 |
| BOIL 운영 구현체 | 없음 |
| 활성화 스위치 | 없음 |
| 설계 checker | 있음 |
| 후보별 dry-run | 있음 |
| v3 구조 검증 | 있음 |
| v3 독립 OOS 성과 검증 | 확인되지 않음 |
| BOIL hold-out/OOS 성과 근거 | 있음 |
| prospective live 검증 | 없음 |
| 최종 판정 | `NEEDS_IMPLEMENTATION` |

## 결론

로드맵의 “v3·BOIL 라이브 반영”은 활성화 작업이 아니다.

정확한 표현은:

```text
validated design/dry-run을 shared live static gate로 신규 구현하고,
shadow 단계 후 BLOCK enforcement를 전환하는 작업
```

이다.

현재 상태에서 설정만 켜는 방식으로 라이브 반영할 수 없다.

운영 코드·설정 변경: 0건

## 산출물

- `implementation_inventory.csv`
- `validation_evidence.md`
- `readout.md`
