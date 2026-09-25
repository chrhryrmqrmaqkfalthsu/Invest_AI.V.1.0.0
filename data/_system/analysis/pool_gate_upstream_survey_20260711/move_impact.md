# 상류 이동 영향

## 현재 18개에 v3·최종 BOIL만 적용

Event OFF는 별도 축이며 이 표에는 포함하지 않는다.

현재 v3 FAIL:

```text
BTBT
BMI
BCS
BNTX
CRK
```

현재 최종 BOIL-exclusive FAIL:

```text
없음
```

따라서 v3·최종 BOIL pool 필터만 적용하면 현재 candidate_pool 18개 중 5개 룰북이 제거 대상이다.

남는 candidate ID는 13개다.

단, 권장 삽입점인 ticker dedup 이전에 필터를 넣으면 같은 ticker의 차순위 PASS 룰북이 존재할 경우 해당 ticker가 다른 candidate ID로 다시 들어올 수 있다. 따라서 실제 재생성 후 pool 크기는 단순히 18-5=13으로 고정되지 않을 수 있다.

## 재생성 필요 여부

원본 룰북 artifact 재학습·재생성은 필요 없다.

필요한 것은 candidate report와 live state 재생성이다.

```text
다음 정규장 refresh
또는 명시적 force-evaluate refresh
```

가 실행되면 상류 필터가 적용된 candidate report에서 `candidate_pool`이 다시 작성된다.

장외에는 `live_candidate_slots.py`가 cached pool을 재사용하므로 코드만 배포해도 기존 `live_slots_state.json::candidate_pool`은 즉시 바뀌지 않는다.

기존 state에 직접 소급 필터를 적용하는 것도 기술적으로 가능하지만 이는 파생 state를 수동 변형하는 방식이며, 권장 방식은 다음 정상 refresh에서 재생성하는 것이다.

## shadow hook 필요성

필터가 `build_elite_shadow_report()` 내부에 들어가면 이 report를 사용하는 다음 경로는 동일하게 정리된다.

- live candidate slots
- elite shadow trader/simulator
- dashboard candidate exporter
- S2 auto의 full candidate lookup
- signal history/pullback report 계열

따라서 live slots의 차단 목적 hook은 기능적으로 중복된다.

다만 전환 직후에는:

```text
상류 filter 결과
vs 현재 live hook 결과
```

를 shadow 대조하기 위해 hook을 잠시 유지할 수 있다. 전수 일치와 다른 report 소비 경로 커버리지가 확인되면 live hook을 제거하거나 audit-only로 축소할 수 있다.

## 비가역성

권장 A/B/C/D 방식은 원본 survivor/final_rulebook artifact를 삭제하지 않으므로 비가역이 아니다.

- config/code rollback 후 다음 refresh로 복구 가능
- 기존 candidate ID와 룰북 artifact 보존
- 재학습 불필요

반면 artifact 생성 단계 F에서 제거하면 기존 batch에 소급하려면 artifact 재생성 또는 원본 복원이 필요해 롤백이 어렵다.

## 라이브 hook과 롤백 비교

| 방식 | 롤백 난이도 | 복구 시점 | 원본 artifact 영향 |
|---|---|---|---|
| 현재 live hook | 가장 쉬움 | config 변경 + daemon refresh | 없음 |
| report 상류 필터 | 쉬움 | 코드/config rollback + 다음 refresh | 없음 |
| denylist materialization | 중간 | denylist rollback + 다음 refresh | 없음 |
| artifact 생성 필터 | 어려움 | artifact 복구/재생성 | 있음 |
