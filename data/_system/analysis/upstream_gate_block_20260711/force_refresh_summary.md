# Force refresh 검증 요약

## 사전 상태

- Alpaca open orders: 0
- candidate_pool: 18
- slots: 8
- waitlist: 10
- live_slots_state JSON parse: 정상
- 원본 artifact hash snapshot: 270개
- ledger/order/trade hash snapshot: 7개

## 설정

```yaml
live:
  integrated_gate_enforcement: SHADOW
  upstream_gate_enforcement: BLOCK
```

Daemon 재적재 후 확인:

```text
integrated_gate_enforcement=SHADOW
upstream_gate_enforcement=BLOCK
```

## 장외 cached 우회

실행:

```text
live_candidate_slots.py refresh --force-evaluate
```

State 결과:

```text
last_rebuild_reason=fresh_evaluation
decision_gate.reason=not_us_weekday
```

즉 장외 `outside_regular_hours_cached_pool` 분기를 우회하고 실제 report 재생성·signal 평가 경로를 탔다.

## 반복 결과

- force 1: 11개
- force 2: 10개
- force 3: 10개

Force 2와 3의 candidate ID 집합은 동일했다.

안정 pool 10개:

```text
ADMA CRS ALGT AEIS ARKW CBRL BTU BB BN ACMR
```

첫 회에만 CEF가 포함됐고 다음 평가에서 사라졌다.

## 예상 13개와 실제 10개 차이

과거 SHADOW의 13개는 기존 18개 candidate_pool에 정적 gate만 적용한 결과다.

Force refresh는 다음을 다시 수행한다.

```text
전체 elite report 재생성
→ KEEP gate
→ market/news/price 기반 evaluate_candidate 재평가
→ should_buy=true만 candidate_pool 작성
```

따라서 기존 13개 중 현재 신호가 threshold를 통과하지 못한 후보는 빠지고, 이전 pool에 없던 현재 신호 후보는 새로 들어온다.

실제 stable pool에서는:

- 제거 5개는 모두 소멸
- 차순위 candidate ID 등장 0
- BTU 신규 유입
- 일부 기존 예상 생존 후보는 fresh signal 미통과

즉 상류 BLOCK 동작은 정상이나 `18-5=13`은 fresh evaluation의 최종 pool count 보장이 아니다.

## 안정성

- 제거 5개 active section overlap: 0
- KEEP map missing: 0
- gate_keep=false: 0
- Alpaca open orders after validation: 0
- ledger/order/trade hash changes: 0
- original artifact hash changes: 0
