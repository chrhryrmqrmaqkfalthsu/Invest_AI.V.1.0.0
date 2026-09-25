# INVALID_LAG_MISSING

이 실행은 기술 feature D-5 lag가 적용되기 전에 수행되었으므로 최종 판정에 사용할 수 없다.

대상:

```text
AAP
POWI
```

확인된 상태:

```text
시장 context D-1 lag: 적용
strict interval 기술 feature D-5 lag: 미적용
실제 참조 시점: 신호일 D 행
```

따라서 이 디렉터리의 `qualify_result.json`, `summary.json`, 로그 및 기타 산출물은 성과·Survivor·CE·BOIL 판정에서 제외한다.

두 종목 실행은 qualify 단계에서 `qualified=false`로 종료됐으며, 무효 표식 작성 시점에 잔존 Stage 3 GA worker는 0개였다. daemon PID 494330은 종료하거나 변경하지 않았다.
