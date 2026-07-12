# Phase 1b — D-1 market lookup 실제값 확인

## 판정

```text
PHASE1_LOOKUP_GATE: PASS
```

AAP·POWI 모두 동일한 복구 시장 이력과 `sector_tech`를 사용했다. 5개 기준일에서 `lookup_market_at_lagged(..., lag_days=1)` 반환값이 기본 중립값으로 고정되지 않았고 날짜별로 변동했다.

## 실제 lookup 값

두 종목의 시장 context 값은 같은 날짜에 동일하므로 아래 표는 AAP·POWI 공통이다.

| trade date | cutoff D-1 | 실제 선택 시장일 | score | sector_tech | VIX | 기본값 50/50/18 | 미래 누출 없음 |
|---|---|---|---:|---:|---:|---|---|
| 2021-06-15 | 2021-06-14 | 2021-06-14 | 88.471159 | 100.000000 | 16.390000 | NO | PASS |
| 2022-12-15 | 2022-12-14 | 2022-12-14 | 66.313984 | 71.487466 | 21.139999 | NO | PASS |
| 2023-12-15 | 2023-12-14 | 2023-12-14 | 87.935222 | 100.000000 | 12.480000 | NO | PASS |
| 2024-12-16 | 2024-12-15 | 2024-12-13 | 82.111657 | 84.500510 | 13.810000 | NO | PASS |
| 2025-12-15 | 2025-12-14 | 2025-12-12 | 70.099785 | 70.389704 | 15.740000 | NO | PASS |

주말 cutoff에서는 직전 거래일로 forward-fill됐다.

```text
2024-12-16 trade → cutoff 2024-12-15 → selected 2024-12-13
2025-12-15 trade → cutoff 2025-12-14 → selected 2025-12-12
```

선택 시장일은 모든 표본에서 cutoff 이하이고 trade date보다 이전이다.

## 변동성 확인

```text
score 고유값: 5/5
sector_tech 고유값: 4/5
VIX 고유값: 5/5
정확한 기본 triplet(50, 50, 18): 0/5
```

AAP와 POWI 모두:

```text
market_history 정상 로드: PASS
sector 매핑 유효: PASS
실제값 변동: PASS
D-1 cutoff: PASS
```

## Phase 1 최종 게이트

```text
PHASE1_PASS = TRUE
PHASE2_ALLOWED = TRUE
```
