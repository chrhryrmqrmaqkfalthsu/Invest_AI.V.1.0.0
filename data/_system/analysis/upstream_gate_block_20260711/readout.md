# 상류 게이트 BLOCK 전환 + 강제 pool 재생성 검증

## 최종 상태

```yaml
live:
  integrated_gate_enforcement: SHADOW
  upstream_gate_enforcement: BLOCK
```

상류 BLOCK은 활성화됐고 downstream hook은 SHADOW로 유지됐다.

정책 커밋:

```text
a26e9ce
상류 v3·BOIL 게이트를 BLOCK으로 전환하고 downstream hook은 SHADOW로 유지
```

## 1. 사전 안전 확인

전환 전:

```text
Alpaca open orders=0
candidate_pool=18
slots=8
waitlist=10
live_slots_state JSON parse=정상
```

현재 pool snapshot은 `pool_before_after.csv`에 기록했다.

## 2. Daemon 재적재

기존 daemon을 정상 종료하고 새 PID로 재기동했다.

```text
new PID=474823
integrated_gate_enforcement=SHADOW
upstream_gate_enforcement=BLOCK
```

## 3. 장외 cached 우회

실행:

```text
data/_system/ops/live_candidate_slots.py refresh --force-evaluate
```

State 근거:

```text
last_rebuild_reason=fresh_evaluation
decision_gate.reason=not_us_weekday
```

따라서 장외 cached pool 경로가 아니라 실제:

```text
build_elite_shadow_report()
→ upstream BLOCK
→ KEEP gate
→ evaluate_candidate()
→ candidate_pool 재작성
```

경로를 탔다.

## 4. 예상 13개와 실제 결과

SHADOW의 `18 - 5 = 13`은 기존 candidate_pool에 정적 gate만 적용한 결과였다.

Force refresh는 전체 report와 live signal을 다시 계산하므로 최종 candidate_pool은 13개로 고정되지 않았다.

실행 결과:

```text
force 1=11개
force 2=10개
force 3=10개
```

Force 2와 Force 3의 candidate ID 집합은 동일했다.

안정된 실제 pool 10개:

```text
ADMA
CRS
ALGT
AEIS
ARKW
CBRL
BTU
BB
BN
ACMR
```

따라서 요청의 “실제 pool=13개” 조건은 성립하지 않았다.

원인:

- 기존 18개 중 정적 gate PASS여도 fresh signal이 threshold 미달이면 제외됨
- 이전 pool에 없던 현재 신호 후보 BTU가 신규 유입됨
- 첫 force에서만 CEF가 통과했고 다음 두 회에서는 미통과

이 차이는 상류 filter의 승계·재충원 때문이 아니다.

## 5. 제거 5개 검증

제거 대상:

```text
BTBT
BMI
BCS
BNTX
CRK
```

현재 활성 section:

```text
candidate_pool overlap=0
slots overlap=0
waitlist overlap=0
current_slots overlap=0
```

차순위 replacement candidate ID 등장:

```text
0건
```

상류 report에도 제거 ticker overlap은 0건이다.

단, `first_seen_signals`에는 과거 audit history로 5개 ticker가 남아 있다. 이는 활성 후보 재등장이 아니라 이력 보존이다.

## 6. SHADOW-BLOCK 대조

정적 gate 관점에서는 SHADOW 시뮬레이션과 실제 BLOCK의 제거 대상 5개가 일치했다.

그러나 최종 candidate_pool 전체는 fresh signal 재평가가 추가되므로 13개 시뮬레이션과 완전 동일하지 않았다.

정확한 판정:

```text
static gate removal match=5/5
final pool count match=false (expected 13, actual stable 10)
```

## 7. Downstream 연결

### Dashboard API

```text
_real_candidate_slots_payload count=10
removed overlap=0
```

### Dashboard exporter

Dry-run:

```text
source_section=candidate_pool
exported_count=10
validation_ok=true
canonical output replacement=false
removed overlap=0
```

### S2 auto trader

```text
candidate_pool count=10
removed overlap=0
KEEP map missing=0
KEEP=false=0
```

### Elite shadow/report consumers

```text
build_elite_shadow_report count=57
removed overlap=0
```

다음 경로가 이 report를 직접 사용한다.

- elite shadow trader
- elite strategy simulator
- elite signal history
- elite pullback forecast/replay
- S2 execution-time full candidate lookup
- dashboard exporter full candidate verification

### Signal history

`elite_signal_history._find_candidate()`는 `build_elite_shadow_report()` 결과에서 후보를 찾는다.

현재 report에서 제거 5개가 없으므로 새 signal-history 조회 대상이 되지 않는다.

## 8. KEEP gate

현재 실제 pool 10개 모두:

```text
gate map present=true
gate_keep=true
```

따라서 `gate_missing`은 0건이다.

차순위 승계를 제거했기 때문에 replacement ID coverage 문제도 없다.

## 9. 안정성

### 반복 재생성

```text
force 2 candidate IDs == force 3 candidate IDs
```

Pool row 전체 hash는 timestamp/price 같은 갱신 필드 때문에 달랐지만 candidate ID 집합은 안정적으로 10개였다.

### 주문

```text
전환 전 open orders=0
검증 후 open orders=0
```

### Ledger/order/trade files

사전·사후 7개 hash 비교:

```text
changed_count=0
```

### 원본 artifact

다음 270개 hash 비교:

```text
central_index.jsonl 1개
stage3 final_rulebooks.jsonl 269개
changed_count=0
```

원본 artifact는 불변이다.

## 10. 롤백

Config 한 줄:

```yaml
live:
  upstream_gate_enforcement: SHADOW
```

으로 복구한 뒤 daemon을 재시작하고 필요하면:

```text
live_candidate_slots.py refresh --force-evaluate
```

로 pool을 다시 생성한다.

커밋 revert:

```bash
cd ~/kingmaker
git revert a26e9ce
git push
```

## 결론

상류 BLOCK과 downstream 연결은 정상 작동한다.

- 권위 v3·BOIL FAIL 제거 정상
- 제거 5개 active downstream 소멸
- 차순위 승계 0
- KEEP coverage 문제 0
- 주문·ledger·artifact 불변

다만 fresh evaluation 결과는 예상 13개가 아니라 안정 10개였다. 따라서 “BLOCK 동작 검증”은 통과했지만 “최종 pool이 반드시 13개”라는 사전 예상은 폐기해야 한다.

## 산출물

- `pool_before_after.csv`
- `downstream_validation.csv`
- `force_refresh_summary.md`
- `readout.md`
