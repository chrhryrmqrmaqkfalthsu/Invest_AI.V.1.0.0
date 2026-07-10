# 93개 후보 3대 증상 개별 스캔 readout

범위: 코드·데이터·설정·주문 변경 없음. `build_elite_shadow_report(stage2_limit=60, stage3_limit=80)` 상위 93개 전체를 대상으로 세 증상을 독립적으로 스캔했다. 현재 ratio와 core technical component는 read-only 평가로 산출했고, 과거 exit 품질은 각 후보의 동일 `rule_hash`를 trade history에 매칭해 계산했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/live93_three_symptom_scan_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/live93_three_symptom_scan.csv`

CSV에는 93개 전부에 대해 `ratio`, `top2_share_pct`, `bb_rsi_only_pass`, `exit_avg_pnl_pct`, `exit_win_rate_pct`, `stop_timeout_pct`, `exit_avg_mfe_pct`, 세 증상별 boolean, `symptom_count`, `symptom_labels`를 모두 넣었다.

## 0. 기준

이번 임계값은 관찰 분포 기반 참고치다. 확정 제외 기준이 아니며, 세 증상을 합쳐 단일 제외 규칙으로 확정하지 않는다.

| 증상 | cross-count 기준 | 별도 구간 |
|---|---|---|
| 증상 1, 턱걸이 ratio | `ratio < 1.25` | `<1.05`, `<1.10`, `<1.15`, `<1.25` |
| 증상 2, 소수 지표 집중 | core technical `top2_share_pct >= 90%` | `>=80%`, `>=90%`, `>=95%` |
| 증상 2 별도 | `BB+RSI 두 개로만 통과` | should_buy=True이고 core positive가 BB·RSI뿐 |
| 증상 3, exit 부진 | `avg PnL < 0` OR `win < 45%` OR `stop+timeout > 80%` | 각 조건별 명단 분리 |

core technical component는 `MA/MACD/RSI/BB/volume`만 사용했다. 뉴스·이벤트는 이 집중도 계산에서 제외했다.

## 1. 전체 집계

| 항목 | 개체 수 |
|---|---:|
| 전체 후보 | 93 |
| live26 포함 | 26 |
| ratio 평가 가능 | 93 |
| top2 평가 가능 | 83 |
| exit history OK | 93 |
| NO_HISTORY | 0 |
| 증상 1 hit, ratio < 1.25 | 69 |
| 증상 2 hit, top2 >= 90% | 82 |
| 증상 3 hit, exit 부진 any | 22 |

## 2. 증상 1 — 턱걸이 ratio

| 구간 | 개체 수 |
|---|---:|
| ratio < 1.05 | 61 |
| ratio < 1.10 | 61 |
| ratio < 1.15 | 63 |
| ratio < 1.25 | 69 |

live26 중 `ratio < 1.25`에 걸린 개체:

```text
CDE, BOIL, ANET, FIX, BKSY, CIEN, ARKW, CRS, CEF, CE, BB
```

전체 `ratio < 1.25` 명단은 CSV의 `ratio_lt_1_25=True`로 필터링하면 된다. 주요 저ratio 후보 예시는 다음과 같다.

```text
CSIQ -6.778, CRMD -4.827, AX -3.649, APA -3.381, BTU -2.922,
APH -1.570, AAOI -1.367, CLF -1.292, AGI -0.950, BNTX -0.939,
BIDU -0.781, ARKG -0.665, BGC -0.589, CC -0.121, CRK -0.085,
CACI -0.015, AEVA 0.000, AMSC 0.000, BILL 0.000, AES 0.000,
CIGI 0.000, STM 0.000, CAT(stage2) 0.026, CENX 0.109, BBIO 0.112,
BKSY 0.246, CE 1.009, BOIL 1.241
```

주의: 93개에는 현재 should_buy가 아닌 후보도 포함되어 있어 ratio가 0 이하로 나오는 개체가 많다. 이 증상은 “현재 매수 후보 26개를 자르는 규칙”이 아니라 93개 전체의 현재 통과 여유/비통과 상태를 넓게 보는 스캔이다.

## 3. 증상 2 — 소수 지표 집중

| 구간 | 개체 수 |
|---|---:|
| top2 >= 80% | 83 |
| top2 >= 90% | 82 |
| top2 >= 95% | 80 |
| BB+RSI 두 개로만 통과 | 7 |

`top2 >= 90%`가 82개로 매우 많다. 즉 현재 core technical 신호는 대부분 1~2개 지표에 집중되어 있다. 이 자체만으로는 제외 기준으로 쓰기 어렵고, 다른 증상 또는 OOS 검증과 결합해 해석해야 한다.

BB+RSI 두 개로만 통과한 후보:

```text
#2 CDE* ratio 1.204
#5 BOIL* ratio 1.241
#51 AEIS* ratio 1.492
#55 AAP* ratio 1.530
#61 CE* ratio 1.009
#32 BTE ratio 1.015
#49 CWK ratio 1.238
```

`*`는 현재 live26 포함이다.

## 4. 증상 3 — 과거 exit 실적 부진

| 조건 | 개체 수 |
|---|---:|
| avg PnL < 0 | 8 |
| win rate < 45% | 12 |
| stop+timeout > 80% | 11 |
| NO_HISTORY | 0 |

### avg PnL < 0

```text
AEVA avg -4.45
CGC avg -1.32
CE* avg -1.02
BILL avg -0.96
CVNA avg -0.83
CBRL* avg -0.55
CRMD avg -0.32
ALGT(stage3)* avg -0.29
```

### win rate < 45%

```text
ALGT(stage3)* 24.0%
CAR 34.9%
AMBA 36.5%
CBRL* 36.8%
CRMD 38.3%
AEVA 39.6%
AAP* 39.7%
ARKG 41.3%
CE* 41.8%
BKSY* 42.5%
CENX 43.8%
APH 44.3%
```

### stop+timeout > 80%

```text
CRMD 91.7%
BWXT* 89.1%
CE* 88.6%
CW 84.6%
HCC 84.3%
BKSY* 83.8%
BILL 83.6%
BCS* 82.8%
ALGT(stage3)* 82.7%
CDE* 82.5%
CBRL* 82.4%
```

## 5. 교차 확인 — 증상 개수별 명단

| 증상 개수 | 개체 수 |
|---:|---:|
| 3개 | 10 |
| 2개 | 58 |
| 1개 | 25 |
| 0개 | 0 |

### 3개 증상 hit

```text
CDE*, BKSY*, CE*, CENX, CRMD, CAR, AMBA, APH, ARKG, HCC
```

상세 label:

```text
CDE*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
BKSY*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
CE*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
CENX: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
CRMD: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
CAR: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
AMBA: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
APH: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
ARKG: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
HCC: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
```

### 2개 증상 hit, live26 중심

```text
BOIL*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
ANET*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
FIX*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
CIEN*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
ALGT(stage3)*: TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
ADPT*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
AAP*: TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
BWXT*: TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
BB*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
CBRL*: TOP2_CONCENTRATION_GE_90 | WEAK_EXIT_HISTORY
CEF*: LOW_RATIO_LT_1_25 | TOP2_CONCENTRATION_GE_90
```

나머지 2개 증상 후보 전체는 CSV에서 `symptom_count=2`로 필터링한다.

### 1개 증상 hit, live26 중심

```text
CAPR*: TOP2_CONCENTRATION_GE_90
ALGT(stage2)*: TOP2_CONCENTRATION_GE_90
CMC*: TOP2_CONCENTRATION_GE_90
ARKW*: LOW_RATIO_LT_1_25
CRS*: LOW_RATIO_LT_1_25
BMA*: TOP2_CONCENTRATION_GE_90
BCS*: WEAK_EXIT_HISTORY
BMI*: TOP2_CONCENTRATION_GE_90
AEIS*: TOP2_CONCENTRATION_GE_90
ADMA*: TOP2_CONCENTRATION_GE_90
BTBT*: TOP2_CONCENTRATION_GE_90
ACMR*: TOP2_CONCENTRATION_GE_90
```

나머지 1개 증상 후보 전체는 CSV에서 `symptom_count=1`로 필터링한다.

## 6. CE·BOIL sanity check

| ticker | rank93 | live26 | ratio | top2 | BB+RSI only pass | avg PnL | win | stop+timeout | symptom_count | labels |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| CE | 61 | True | 1.009 | 100.0% | True | -1.02% | 41.8% | 88.6% | 3 | LOW_RATIO_LT_1_25, TOP2_CONCENTRATION_GE_90, WEAK_EXIT_HISTORY |
| BOIL | 5 | True | 1.241 | 100.0% | True | +1.15% | 67.3% | 32.7% | 2 | LOW_RATIO_LT_1_25, TOP2_CONCENTRATION_GE_90 |

CE는 세 증상 모두에 걸린다. BOIL은 이번 read-only 재평가 기준 ratio가 1.241로 `ratio < 1.25`에 들어오고, BB+RSI 몰빵에도 걸린다. 다만 exit 부진 조건(`avg PnL < 0`, `win < 45%`, `stop+timeout > 80%`)에는 걸리지 않는다. BOIL의 tail-risk는 `avg MAE -8.45%`, `worst PnL -26.75%`로 별도 해석해야 하며, 이번 증상 3의 세 조건에는 포함하지 않았다.

## 7. 결론

- 이 스캔은 제외 규칙 확정이 아니라 증상별 명단 작성이다.
- `top2 >= 90%`가 82개로 너무 넓게 걸리므로 단독 제외 규칙으로 부적합하다.
- 세 증상 모두 걸리는 live26 후보는 `CDE`, `BKSY`, `CE`다.
- BOIL은 2개 증상(`ratio<1.25`, `BB+RSI 집중`)에 걸리고, exit 부진 조건에는 걸리지 않는다.
- 전체 명단과 필터링 가능한 boolean은 CSV가 기준 산출물이다.
