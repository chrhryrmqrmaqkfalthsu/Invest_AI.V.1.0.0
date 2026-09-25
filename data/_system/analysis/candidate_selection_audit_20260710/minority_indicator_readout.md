# 소수지표 구조 rule 성과 분포 분석

- 최종 판정: **NO_SIGNAL**
- 분석 대상: 저장 rulebook의 MA·MACD·RSI·BB·Volume 가중치 구조
- 진입 순간 realized component 분석이 아님
- Stage2·Stage3 완전 분리
- 운영·라이브·원본 코드·설정·설계 변경: 0건

## 1. 결론

소수지표 저장 가중치 구조가 성과 열위라는 증거는 확인되지 않았다.

- IS discovery 평균 PnL FDR 통과 cut: 0개
- Frozen 평균 PnL 명목 CI 통과 cut: 0개
- Frozen 평균 PnL FDR 통과 cut: 0개
- 기존 history·v3·BOIL 통과 순증군 robust cut: 0개
- 평균 PnL·승률·5% tail·하위 10% 평균·worst MAE 전체에서 IS→frozen→순증군 동시 통과: 0개

따라서 구조 기준 정적 BLOCK 후보는 없다. CE형 실패 검증은 진입 시점 component logging과 동적 검증 경로로 남는다.

## 2. 구조 기준

각 rule의 저장 core 가중치에서 다음을 계산했다.

- exact active count: 가중치 > 0인 지표 수
- material active count: 가중치 > 0.05인 지표 수
- Top2 집중도: 상위 두 가중치 합 / 전체 양수 가중치 합

검증한 8개 cut:

- 활성 exact 2개 이하
- 활성 material 2개 이하
- Top2 80% 이상
- Top2 90% 이상
- 활성 exact 3개 이하 + Top2 80% 이상
- 활성 exact 3개 이하 + Top2 90% 이상
- 활성 material 2개 이하 + Top2 80% 이상
- 활성 material 2개 이하 + Top2 90% 이상

## 3. 데이터와 검증 규율

- Stage2 canonical 거래: 72,690건 / rule 1,162개
- Stage3 canonical 거래: 975,118건 / rule 15,909개
- 전체 canonical 거래: 1,047,808건
- Frozen OOS: Stage2 13개, Stage3 80개 rule
- 기존 게이트 통과 frozen 순증군: Stage2 9개, Stage3 9개 rule
- 최소 거래 수: rule당 8건
- 효과량: rule별 성과 평균의 `소수지표군 - 기타군`
- CI: ticker-cluster bootstrap 5,000회
- 검정: ticker-cluster robust 단측 검정
- 다중검정: stage·scope·metric별 8개 cut BH FDR

Canonical 거래 수는 크지만 frozen 외부검증은 총 93개 rule이다. 특히 exact 2개 이하 구조는 frozen에 거의 없어 일부 cut은 검정 자체가 불가능했다.

## 4. ANET·BB 정의 타당성

| 후보 | MA | MACD | RSI | BB | Volume | 활성 exact/material | Top2 | 소수 cut |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ANET | 1.3148 | 2.0000 | 1.7102 | 1.3451 | 0.2530 | 5/5 | 56.02% | 0/8 |
| BB | 1.5405 | 1.5987 | 1.7504 | 0.3610 | 1.1616 | 5/5 | 52.23% | 0/8 |

ANET·BB는 진입 시점 point snapshot에서 RSI+MA만 발화했지만, 저장 rule 구조에서는 5개 core 가중치가 모두 양수다. 두 rule은 소수지표 구조가 아니며 어떤 cut에도 걸리지 않는다.

- ANET frozen 평균 PnL 1.7764%, 승률 61.41%
- BB frozen 평균 PnL 8.6038%, 승률 64.57%

즉 저장 구조 기준 gate는 ANET·BB의 상반된 실제 방향을 설명하거나 차단하지 못한다.

## 5. IS discovery 평균 PnL

음수 차이는 소수지표군의 열위를 뜻한다.

### Stage2

| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |
|---|---:|---:|---:|---:|---:|---:|---:|
| ACTIVE_EXACT_LE2 | 7 | 1155 | 2.6054 | 2.6132 | -0.0078 | [-0.0964, 0.1012] | 0.7564 |
| ACTIVE_MATERIAL_LE2 | 8 | 1154 | 2.6388 | 2.6130 | 0.0258 | [-0.0799, 0.2847] | 0.7564 |
| TOP2_GE80 | 109 | 1053 | 2.6283 | 2.6116 | 0.0166 | [-0.1341, 0.1711] | 0.7564 |
| TOP2_GE90 | 26 | 1136 | 2.6493 | 2.6124 | 0.0369 | [-0.2256, 0.2827] | 0.7564 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE80 | 38 | 1124 | 2.6963 | 2.6104 | 0.0859 | [-0.1360, 0.3377] | 0.7781 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE90 | 19 | 1143 | 2.6558 | 2.6125 | 0.0433 | [-0.2829, 0.3983] | 0.7564 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE80 | 8 | 1154 | 2.6388 | 2.6130 | 0.0258 | [-0.0755, 0.2845] | 0.7564 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE90 | 8 | 1154 | 2.6388 | 2.6130 | 0.0258 | [-0.0729, 0.2867] | 0.7564 |

### Stage3

| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |
|---|---:|---:|---:|---:|---:|---:|---:|
| ACTIVE_EXACT_LE2 | 42 | 15854 | 2.2005 | 1.8833 | 0.3172 | [-0.7318, 1.5803] | 0.8216 |
| ACTIVE_MATERIAL_LE2 | 69 | 15827 | 2.0481 | 1.8835 | 0.1647 | [-1.0453, 1.2515] | 0.8216 |
| TOP2_GE80 | 1001 | 14895 | 1.8911 | 1.8837 | 0.0074 | [-0.3420, 0.3460] | 0.8216 |
| TOP2_GE90 | 192 | 15704 | 2.0759 | 1.8818 | 0.1941 | [-0.5096, 0.9411] | 0.8216 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE80 | 317 | 15579 | 1.9748 | 1.8823 | 0.0925 | [-0.3400, 0.5619] | 0.8216 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE90 | 129 | 15767 | 2.3037 | 1.8807 | 0.4229 | [-0.2590, 1.1548] | 0.8780 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE80 | 69 | 15827 | 2.0481 | 1.8835 | 0.1647 | [-1.0121, 1.2014] | 0.8216 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE90 | 69 | 15827 | 2.0481 | 1.8835 | 0.1647 | [-1.0338, 1.2526] | 0.8216 |

IS discovery에서는 16개 stage×cut 조합 모두 평균 PnL 열위가 FDR를 통과하지 못했다. Stage3에서는 모든 관측 가능한 cut의 점추정이 오히려 양수였다.

## 6. Frozen OOS 평균 PnL

### Stage2

| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |
|---|---:|---:|---:|---:|---:|---:|---:|
| ACTIVE_EXACT_LE2 | 0 | 13 | NA | 2.3407 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2 | 0 | 13 | NA | 2.3407 | NA | [NA, NA] | 1.0000 |
| TOP2_GE80 | 1 | 12 | 2.3093 | 2.3433 | -0.0340 | [-0.8455, 0.8211] | 0.9409 |
| TOP2_GE90 | 1 | 12 | 2.3093 | 2.3433 | -0.0340 | [-0.8478, 0.7759] | 0.9409 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE80 | 1 | 12 | 2.3093 | 2.3433 | -0.0340 | [-0.8577, 0.7864] | 0.9409 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE90 | 1 | 12 | 2.3093 | 2.3433 | -0.0340 | [-0.8532, 0.7747] | 0.9409 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE80 | 0 | 13 | NA | 2.3407 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE90 | 0 | 13 | NA | 2.3407 | NA | [NA, NA] | 1.0000 |

Stage2에서 관측 가능한 소수군은 Top2 계열의 단 1개 rule이다. PnL 차이 -0.0340%p, CI는 약 -0.85~+0.82%p로 0을 넓게 포함한다.

### Stage3

| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |
|---|---:|---:|---:|---:|---:|---:|---:|
| ACTIVE_EXACT_LE2 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |
| TOP2_GE80 | 4 | 76 | 5.9300 | 3.0811 | 2.8489 | [-0.6719, 7.4310] | 1.0000 |
| TOP2_GE90 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE80 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE90 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE80 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE90 | 0 | 80 | NA | 3.2235 | NA | [NA, NA] | 1.0000 |

Stage3에서 검정 가능한 유일한 Top2>=80군 4개 rule은 평균 PnL가 기타군보다 +2.8489%p 높았다. CI가 0을 포함해 우위도 확정할 수 없지만, 적어도 열위 가설과 같은 방향은 아니다.

## 7. 기존 게이트 통과 순증군

### Stage2

| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |
|---|---:|---:|---:|---:|---:|---:|---:|
| ACTIVE_EXACT_LE2 | 0 | 9 | NA | 2.2582 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2 | 0 | 9 | NA | 2.2582 | NA | [NA, NA] | 1.0000 |
| TOP2_GE80 | 1 | 8 | 2.3093 | 2.2519 | 0.0574 | [-0.9552, 1.1411] | 1.0000 |
| TOP2_GE90 | 1 | 8 | 2.3093 | 2.2519 | 0.0574 | [-0.9443, 1.1519] | 1.0000 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE80 | 1 | 8 | 2.3093 | 2.2519 | 0.0574 | [-0.9374, 1.1264] | 1.0000 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE90 | 1 | 8 | 2.3093 | 2.2519 | 0.0574 | [-1.0031, 1.1288] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE80 | 0 | 9 | NA | 2.2582 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE90 | 0 | 9 | NA | 2.2582 | NA | [NA, NA] | 1.0000 |

### Stage3

| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |
|---|---:|---:|---:|---:|---:|---:|---:|
| ACTIVE_EXACT_LE2 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |
| TOP2_GE80 | 1 | 8 | 6.9809 | 4.0317 | 2.9491 | [0.9129, 4.8977] | 1.0000 |
| TOP2_GE90 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE80 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |
| ACTIVE_EXACT_LE3_AND_TOP2_GE90 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE80 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |
| ACTIVE_MATERIAL_LE2_AND_TOP2_GE90 | 0 | 9 | NA | 4.3594 | NA | [NA, NA] | 1.0000 |

순증군에서도 열위는 유지되지 않았다. Stage2 Top2 계열은 +0.0574%p, Stage3 TOP2>=80은 +2.9491%p로 모두 소수지표군 우위 방향이다.

## 8. 평균 외 성과지표

평균 PnL 외에 승률, PnL 5% 분위, 하위 10% 평균 PnL, worst MAE도 같은 family로 검증했다.

- IS→frozen→순증군을 모두 통과한 cut×metric: 0개
- Frozen Stage2 승률에서 4개 중복 cut이 FDR를 통과했으나 모두 같은 소수 rule 1개에 의존
- 해당 승률 신호는 IS discovery에서 선택되지 않았고 순증군 robust 기준도 통과하지 못함
- Tail과 worst MAE는 frozen FDR 통과 cut 0개

따라서 평균 PnL만 놓친 tail-risk 구조 신호도 발견되지 않았다.

## 9. 커브피팅 점검

Stage2 내부 holdout에서는 8개 cut 모두 평균 PnL 열위가 유의해 보였다. 그러나:

- IS discovery에서는 8개 모두 FDR 실패
- Frozen 평균 PnL에서는 차이가 사라짐
- 기존 게이트 통과 순증군에서는 방향이 양수로 반전

따라서 내부 holdout만 보고 경계를 채택하면 전형적인 구간 선택·커브피팅이 된다.

Frozen Stage2 승률 신호도 소수군 1개 rule에 의존하므로 게이트 근거로 사용할 수 없다.

## 10. 최종 판정

**NO_SIGNAL**

저장 rule 가중치의 소수지표 구조는 OOS 성과 열위를 구분하지 못했다. 구조 기준 정적 게이트 후보는 없다.

ANET·BB는 저장 구조상 소수지표 rule도 아니다. 두 후보에서 관찰된 CE형은 rule 구조가 아니라 실제 진입 순간 일부 지표만 발화한 동적 현상이다.

따라서 현재 근거에서는 진입 시점 component logging과 동적 CE 검증이 유일한 경로다.

## 11. 산출물

- `minority_indicator_rule_performance.csv.gz`
- `minority_indicator_group_performance.csv`
- `minority_indicator_cut_results.csv`
- `minority_indicator_incremental_results.csv`
- `minority_indicator_anet_bb.csv`
- `minority_indicator_curve_fit_notes.csv`
- `minority_indicator_summary.json`
