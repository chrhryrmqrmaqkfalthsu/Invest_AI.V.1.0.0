# BLOCK 전환 판정

## 판정

`NEEDS_MORE`

① BNTX 불일치의 원인과 권위 소스는 기존 자료만으로 해소됐다.

- 최신 v3 권위: `threshold_p99_weightless_block_candidate_decisions.csv`
- 최종 BOIL 권위: `boil_block_exclusive_targets.csv` 및 `boil_block_enforcement_decision.json`
- 초기 integrated `check_boil`은 v3 확정 전 산출물이라 최종 BOIL 권위가 아님

따라서 `BLOCKED_BY_①`은 아니다.

그러나 현재 SHADOW 구현은 BOIL 판정에 초기 `integrated_gate_candidate_dryrun.csv::check_boil`을 권위값으로 사용한다. 이는 최종 BOIL 정책의 `v3 PASS only / v3 overlap excluded`와 일치하지 않는다.

현재 18개에서는 BNTX가 어차피 v3 FAIL이라 최종 차단 수는 같지만, 차단 사유가 잘못되고 전체 universe에서는 초기 BOIL FAIL과 최종 371개 exclusive target의 범위가 다를 수 있다.

따라서 BLOCK 전환 전 필요한 사항:

1. BOIL runtime source를 최종 `boil_block_exclusive_targets.csv`로 정렬
2. BNTX를 `v3 FAIL / BOIL PASS 또는 NOT_APPLICABLE`로 기록
3. 18개 및 전체 frozen target에 대해 shadow 재대조
4. 후보 수·score·주문 불변 재확인

이 정렬 후에는 현재 18개 기준 BLOCK 결과가 명확하다.

```text
Event OFF 탈락: 7
v3 추가 탈락: BCS, CRK 2
최종 BOIL 추가 탈락: 0
최종 생존: 9
```

현재 단계에서는 설정을 BLOCK으로 바꾸지 않는 것이 맞다.
