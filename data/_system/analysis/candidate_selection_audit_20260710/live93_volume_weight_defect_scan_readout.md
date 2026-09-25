# 93개 후보 volume weight 결함 스캔 readout

범위: 코드·데이터·설정·주문 변경 없음. `build_elite_shadow_report(stage2_limit=60, stage3_limit=80)` 상위 93개 후보를 대상으로, volume 관련 rulebook 파라미터와 OOS vol_group, BB 의존도, 동일 rule_hash exit history를 읽어 스캔했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/live93_volume_weight_defect_scan_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/live93_volume_weight_defect_scan.csv`

## 0. 대상과 기준

- 대상 후보: 93개
- 현재 live 26 포함 개체: 26개
- OOS vol_group 기준 HIGH_VOL: 29개
- 변동성 분류 근거: `data/_system/analysis/vol_perstock_mae_mfe_20260707/per_candidate_summary.csv`의 OOS `vol_group`, `avg_std20_ann`, `avg_atr14_pct`, `n`.
- volume_surge_ratio 유의미 기준: `>= 1.5`. 분포상 룰북 값은 1.2~2.5 범위이며, 1.5 이상은 실질적인 거래량 급증 확인 요구로 보았다.
- weight_volume_surge≈0 기준: `abs(weight_volume_surge) <= 0.05`.
- BB 주 근거 기준: `core technical BB share >= 45%` 또는 live 26에 포함된 경우 `현재 active component BB share >= 50%`.

주의: BB share는 전체 이벤트/뉴스 잠재 가중치까지 포함하면 BOIL이 6.0%로 희석되어 sanity check를 통과하지 못한다. 이번 결함은 “기술 진입 조건에서 volume 확인 없이 BB/RSI로 들어가는 구조”이므로 MA/MACD/RSI/BB/volume의 core technical weights 기준을 별도로 사용했다. BOIL은 core 기준 49.82%, 현재 live active component 기준 79.49%다.

## 1. BOIL sanity check

| 항목 | 값 |
|---|---:|
| rank93 | 5 |
| live26 포함 | True |
| vol_group | HIGH_VOL |
| vol_basis | `OOS avg_std20_ann=0.975; avg_atr14_pct=8.281; n=135` |
| volume_surge_ratio | 2.500 |
| weight_volume_surge | 0.000 |
| weight_bb_near_lower | 2.000 |
| core technical BB share | 49.82% |
| live current BB share | 79.49% |
| pattern_match | True |
| exit avg PnL | 1.155% |
| exit avg MAE | -8.453% |
| worst PnL | -26.747% |

BOIL sanity check: **PASS**. BOIL은 HIGH_VOL이고, `volume_surge_ratio=2.5`인데 `weight_volume_surge=0.0`이며, 현재 active 진입 점수는 BB가 79.49%를 차지한다.

## 2. 패턴 매칭 결과

| rank93 | ticker | candidate_id | live26 | vol_group | volume_surge_ratio | weight_volume_surge | BB share core % | live BB share % | exit avg PnL % | avg MAE % | worst PnL % |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 5 | BOIL | `stage3:BOIL:9044dc2c67a3` | True | HIGH_VOL | 2.500 | 0.000 | 49.82 | 79.49 | 1.155 | -8.453 | -26.747 |

패턴 매칭 개체 수: **1개**

## 3. 전체 집계

| 조건 | 개체 수 |
|---|---:|
| 전체 후보 | 93 |
| live26 포함 | 26 |
| HIGH_VOL | 29 |
| HIGH_VOL + volume_surge_ratio>=1.5 | 20 |
| HIGH_VOL + volume_surge_ratio>=1.5 + weight_volume_surge<=0.05 | 5 |
| 최종 패턴 매칭 | 1 |

## 4. 해석

이 결함 패턴은 93개 전체에서 BOIL 1개만 명확히 매칭됐다. 따라서 현재 증거만으로는 “후보군 전반에 퍼진 구조적 결함”이라기보다 **BOIL 단일 케이스**에 가깝다.

BOIL의 exit history는 평균 PnL이 +1.15%, 승률 67.3%로 전체 평균 품질은 나쁘지 않지만, avg MAE -8.45%, worst PnL -26.75%로 tail-risk가 크다. 즉 이 결함은 “항상 손실 나는 룰”보다는 “HIGH_VOL/레버리지성 상품에서 volume confirmation 없이 BB 하단 반등에 기대는 tail-risk 구조”로 해석하는 편이 맞다.

## 5. 최종 판정

판정: **SINGLE_CASE_DENYLIST**

근거:

- BOIL sanity check는 통과했다.
- 그러나 동일 조건을 만족하는 후보는 93개 중 BOIL 1개뿐이다.
- 따라서 현재는 규칙화된 OOS gate보다 BOIL 개체별 deny-list/수동 제외 검토가 더 타당하다.
- 만약 이 조건을 규칙화하려면 별도 OOS 검증 세션에서 `HIGH_VOL & volume_surge_ratio>=1.5 & weight_volume_surge<=0.05 & BB-active-share high` gate를 검증해야 하며, 이번 세션에서는 라이브 적용하지 않는다.
