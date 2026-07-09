# 라이브 26개 개체별 과적합 진단 스캔 readout

범위: 코드·데이터·설정·주문 변경 없음. 현재 라이브 export 26개(`data/_system/real_dashboard_buy_candidates.json`)를 기준으로, 현재 점수 구성요소·동일 rule_hash exit 기록·IS/OOS 라벨별 성과를 읽어 개체별 과적합 의심도를 산출했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/live26_overfit_scan_readout.md`
- `data/_system/analysis/candidate_selection_audit_20260710/live26_overfit_scan.csv`

## 0. 입력과 라벨 매핑

입력:

- live candidates: `data/_system/real_dashboard_buy_candidates.json` 26개
- rule_hash별 direct exit history: 각 candidate의 `trade_file`에서 동일 `rulebook_hash` 매칭
  - stage3: `final_rulebook_hash == candidate.rulebook_hash`
  - stage2: `rulebook_hash == candidate.rulebook_hash` 또는 `member_hash == candidate.rulebook_hash`

IS/OOS 매핑:

| stage | IS | OOS | STRESS |
| --- | --- | --- | --- |
| stage2 | `period_kind=train` 또는 `train_*_eval` | `period_kind=oos` 또는 `oos_*` | `period_kind=stress` |
| stage3 | `train_1`, `train_2` | `recent_1y` | `stress_pre_2022h1` |

주의: 이 IS/OOS 라벨 매핑은 파일 구조 기반의 진단용 매핑이다. stage3의 `recent_1y`는 실제 캘린더 OOS라기보다 최종 rulebook 산출물의 최근 구간 라벨이므로, 본 판정은 “검증된 라이브 차단 기준”이 아니라 과적합 의심 스캔이다.

## 1. 판별 기준

이번 스캔에서 관찰된 분포를 기준으로 임시 점수를 만들었다. 아직 OOS 검증된 라이브 gate가 아니다.

### A. 지표 집중도 점수, 0~3

| 조건 | 점수 |
| --- | ---: |
| 양수 기여 지표 수 ≤ 2 | +1 |
| top1 지표 비중 ≥ 70% | +1 |
| top2 지표 비중 ≥ 90% | +1 |

보조 flag:

```text
FEW_INDICATORS = concentration_score >= 2
```

CE처럼 양수 기여 지표가 3개라도 세 번째가 매우 작아 top2 비중이 96%인 경우는 “집중도가 높다”고 별도 해석한다.

### B. IS/OOS 괴리 점수, 0~4

| 조건 | 점수 |
| --- | ---: |
| IS avg PnL - OOS avg PnL ≥ 3%p | +1 |
| IS avg PnL - OOS avg PnL ≥ 6%p | +1 추가 |
| IS win rate - OOS win rate ≥ 15%p | +1 |
| IS avg PnL > 0이고 OOS avg PnL < 0 | +1 |

보조 flag:

```text
IS_OOS_GAP = gap_score >= 2
```

### C. exit 품질 점수, 0~7

| 조건 | 점수 |
| --- | ---: |
| direct avg PnL < 0 | +2 |
| direct win rate < 50% | +1 |
| stop_loss + time_out 비율 ≥ 70% | +1 |
| avg MFE / abs(avg MAE) < 1.0 | +1 |
| avg MAE ≤ -8% | +1 |
| min PnL ≤ -20% | +1 |

보조 flag:

```text
BAD_EXIT_QUALITY = exit_quality_score >= 3
TAIL_MAE = avg MAE <= -8%
```

### 종합 등급

| 등급 | 기준 |
| --- | --- |
| `OVERFIT_SUSPECT` | total score ≥ 6, 또는 exit_quality_score ≥ 4이고 concentration/gap 중 하나가 의미 있게 존재 |
| `BORDERLINE` | total score ≥ 3 |
| `HEALTHY` | total score < 3 |

## 2. 전체 요약

| 항목 | 값 |
| --- | ---: |
| 대상 live candidates | 26 |
| OVERFIT_SUSPECT | 7 |
| BORDERLINE | 9 |
| HEALTHY | 10 |
| concentration_score ≥ 2 | 18 |
| gap_score ≥ 2 | 4 |
| exit_quality_score ≥ 3 | 5 |
| 소수지표 + 큰 IS/OOS 괴리 + 나쁜 exit 품질 동시 충족 | 0, 엄격 기준 |
| top2≥90% + 큰 IS/OOS 괴리 + 나쁜 exit 품질 동시 충족 | CE 1개 |

엄격 기준에서 “소수 지표 + 큰 IS/OOS 괴리 + 나쁜 exit 품질”을 모두 만족하는 후보는 0개다. 다만 CE는 `positive_indicator_count=3`이라 concentration_score는 1점이지만, 실제로는 `RSI+BB`가 현재 점수의 96.0%를 차지하고 `events=0.106`만 얹힌 구조라 완화 기준에서는 동일 패턴의 대표 사례다.

## 3. 과적합 의심 순위

| rank | grade | score | ticker | rule_hash | pos n | top2 % | direct avg | win % | avg MAE | min PnL | stop+timeout % | IS avg | OOS avg | gap | flags |
| ---: | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | OVERFIT_SUSPECT | 8 | CE | 998b0b638c66 | 3 | 96.0 | -1.02 | 41.8 | -5.57 | -14.9 | 88.6 | -0.12 | -5.12 | +5.00 | IS_OOS_GAP, BAD_EXIT_QUALITY |
| 2 | OVERFIT_SUSPECT | 7 | BKSY | f1bcc8efea02 | 2 | 100.0 | +0.95 | 42.5 | -8.58 | -21.7 | 83.8 | -0.15 | +4.40 | -4.55 | FEW_INDICATORS, BAD_EXIT_QUALITY, TAIL_MAE |
| 3 | OVERFIT_SUSPECT | 7 | ADMA | 42437a3ee595 | 2 | 100.0 | +1.68 | 57.6 | -6.27 | -29.4 | 77.2 | +3.12 | -2.20 | +5.32 | FEW_INDICATORS, IS_OOS_GAP |
| 4 | OVERFIT_SUSPECT | 7 | BTBT | 363898884d44 | 2 | 100.0 | +1.19 | 58.0 | -8.85 | -28.5 | 29.5 | +2.78 | -0.20 | +2.98 | FEW_INDICATORS, IS_OOS_GAP, TAIL_MAE |
| 5 | OVERFIT_SUSPECT | 7 | BMI | 07d4ee0f7841 | 2 | 100.0 | +1.03 | 60.0 | -4.99 | -19.8 | 67.5 | +2.85 | -4.84 | +7.69 | FEW_INDICATORS, IS_OOS_GAP |
| 6 | OVERFIT_SUSPECT | 6 | CBRL | 677767a0b6a9 | 2 | 100.0 | -0.55 | 36.8 | -5.59 | -11.1 | 82.4 | -0.75 | +0.09 | -0.84 | FEW_INDICATORS, BAD_EXIT_QUALITY |
| 7 | OVERFIT_SUSPECT | 6 | ALGT stage3 | aec5dd5b1dc1 | 2 | 100.0 | -0.29 | 24.0 | -5.68 | -11.0 | 82.7 | -0.31 | +0.41 | -0.73 | FEW_INDICATORS, BAD_EXIT_QUALITY |
| 8 | BORDERLINE | 5 | BB | f1bdfe7f8ad9 | 2 | 100.0 | +1.48 | 47.8 | -9.02 | -21.8 | 65.2 | -1.75 | +5.65 | -7.40 | FEW_INDICATORS, BAD_EXIT_QUALITY, TAIL_MAE |
| 9 | BORDERLINE | 5 | CDE | ceb9fe0512dc | 2 | 100.0 | +2.41 | 45.0 | -6.65 | -14.0 | 82.5 | +3.86 | +1.14 | +2.72 | FEW_INDICATORS |
| 10 | BORDERLINE | 5 | BOIL | 9044dc2c67a3 | 2 | 100.0 | +1.15 | 67.3 | -8.45 | -26.7 | 32.7 | -0.30 | -2.46 | +2.16 | FEW_INDICATORS, TAIL_MAE |
| 11 | BORDERLINE | 4 | BMA | 0c978464f9dd | 2 | 100.0 | +7.82 | 65.9 | -7.37 | -20.8 | 48.8 | +8.01 | +14.20 | -6.20 | FEW_INDICATORS |
| 12 | BORDERLINE | 3 | CEF | fe84c0ad85d8 | 2 | 100.0 | +2.06 | 70.4 | -2.19 | -6.2 | 73.2 | +2.07 | +3.11 | -1.04 | FEW_INDICATORS |
| 13 | BORDERLINE | 3 | BWXT | f195725cb792 | 2 | 100.0 | +1.63 | 50.9 | -4.05 | -11.6 | 89.1 | +1.96 | +0.50 | +1.46 | FEW_INDICATORS |
| 14 | BORDERLINE | 3 | ALGT stage2 | 402f72d48c3c | 2 | 100.0 | +2.63 | 70.5 | -3.64 | -15.6 | 9.8 | +2.87 | +3.31 | -0.44 | FEW_INDICATORS |
| 15 | BORDERLINE | 3 | AEIS | 6e26f08a7c6d | 2 | 100.0 | +2.30 | 55.2 | -5.72 | -11.7 | 61.2 | +0.54 | +5.65 | -5.11 | FEW_INDICATORS |
| 16 | BORDERLINE | 3 | CMC | 4f6ee2739add | 2 | 100.0 | +2.64 | 86.4 | -3.37 | -11.5 | 12.3 | +3.22 | +3.29 | -0.06 | FEW_INDICATORS |
| 17 | HEALTHY | 2 | CAPR | a51d615a0ff1 | 3 | 91.6 | +1.79 | 58.5 | -7.35 | -38.9 | 43.6 | +0.11 | +1.03 | -0.92 |  |
| 18 | HEALTHY | 2 | ACMR | 44c1e02681c4 | 3 | 91.6 | +2.43 | 58.1 | -7.34 | -33.6 | 59.5 | +0.87 | +3.61 | -2.74 |  |
| 19 | HEALTHY | 2 | CIEN | 2ed675d30868 | 2 | 100.0 | +3.28 | 50.8 | -4.66 | -11.2 | 42.9 | +0.66 | +10.63 | -9.98 | FEW_INDICATORS |
| 20 | HEALTHY | 2 | FIX | cab7d458767d | 2 | 100.0 | +3.80 | 81.0 | -3.24 | -9.5 | 43.8 | +4.53 | +4.34 | +0.19 | FEW_INDICATORS |
| 21 | HEALTHY | 2 | ADPT | 78c31f1ca209 | 2 | 100.0 | +0.19 | 60.2 | -6.88 | -19.8 | 41.5 | -0.97 | +2.70 | -3.67 | FEW_INDICATORS |
| 22 | HEALTHY | 1 | ARKW | 296c057b4ef7 | 3 | 79.5 | +0.10 | 46.3 | -5.71 | -17.7 | 31.7 | -0.44 | -0.76 | +0.32 |  |
| 23 | HEALTHY | 1 | AAP | 71dcdeb19ec0 | 3 | 78.4 | +0.05 | 39.7 | -3.83 | -14.6 | 60.3 | -1.67 | +1.07 | -2.75 |  |
| 24 | HEALTHY | 1 | BCS | 5e7da5a74b01 | 4 | 58.0 | +1.32 | 53.4 | -4.18 | -12.5 | 82.8 | +1.53 | +2.00 | -0.47 |  |
| 25 | HEALTHY | 0 | CRS | 8695c9ce3320 | 3 | 77.0 | +3.38 | 74.3 | -5.47 | -17.4 | 32.9 | +3.82 | +6.24 | -2.41 |  |
| 26 | HEALTHY | 0 | ANET | fe220620802b | 3 | 73.8 | +1.86 | 72.8 | -4.10 | -13.0 | 21.0 | +2.46 | +0.25 | +2.22 |  |

전체 세부 수치는 CSV에 포함했다.

## 4. CE 상대 위치

CE는 26개 중 과적합 의심 1위다.

| 항목 | CE 값 | 해석 |
| --- | ---: | --- |
| rank | 1 / 26 | 최상위 위험군 |
| final_score / threshold | 1.0093 | threshold 턱걸이 |
| 양수 기여 지표 | `rsi 1.725`, `bb 0.848`, `events 0.106` | 사실상 RSI+BB 중심 |
| top2 점수 비중 | 96.0% | 소수 지표 의존 |
| direct trades | 79 | 동일 rule_hash 매칭 |
| direct avg PnL | -1.02% | 음수 |
| direct win rate | 41.8% | 낮음 |
| stop_loss / time_out / take_profit / trailing | 28 / 42 / 5 / 2 | stop+timeout 88.6% |
| avg MAE / avg MFE | -5.57% / +4.67% | MFE가 MAE를 못 이김 |
| IS avg / OOS avg | -0.12% / -5.12% | OOS 급악화 |
| IS-OOS avg gap | +5.00%p | 큰 괴리 |

판정:

```text
CE = OVERFIT_SUSPECT
```

CE는 “느슨한 threshold + 소수 지표 의존 + OOS 악화 + 나쁜 exit 품질” 가설에 가장 잘 맞는다. 특히 live 점수는 `events +0.106`이 없으면 threshold 아래로 떨어지는 턱걸이였고, direct exit history도 평균 음수와 stop/time_out 과다로 나쁘다.

## 5. BOIL 상대 위치

BOIL은 26개 중 10위, `BORDERLINE`이다.

| 항목 | BOIL 값 | 해석 |
| --- | ---: | --- |
| rank | 10 / 26 | 중상위 위험군 |
| final_score / threshold | 1.2544 | CE보다 여유 있음 |
| 양수 기여 지표 | `bb 2.000`, `rsi 0.516` | 완전한 BB+RSI 2지표 의존 |
| top2 점수 비중 | 100.0% | 소수 지표 의존 확정 |
| direct trades | 55 | 동일 rule_hash 매칭 |
| direct avg PnL | +1.15% | 평균은 양수 |
| direct win rate | 67.3% | 승률 양호 |
| avg MAE / avg MFE | -8.45% / +11.47% | 꼬리 위험 큼 |
| min PnL | -26.75% | 큰 tail loss |
| stop_loss / time_out / take_profit / breakeven | 15 / 3 / 13 / 23 | stop+timeout 32.7% |
| IS avg / OOS avg | -0.30% / -2.46% | 양쪽 모두 나쁨, OOS 더 나쁨 |
| IS-OOS avg gap | +2.16%p | 큰 과적합 괴리는 아님 |

판정:

```text
BOIL = BORDERLINE
```

BOIL은 CE와 달리 direct exit 전체 평균과 승률은 나쁘지 않다. 따라서 “rule 자체 과적합”보다는 **BB+RSI 단독 의존 + 레버리지/천연가스 상품의 tail-risk** 쪽에 가깝다. 즉 BOIL은 과적합 1순위가 아니라, 손절 시 크게 깨지는 상품 구조/꼬리위험 후보로 보는 것이 더 정확하다.

## 6. CE·BOIL과 같은 패턴을 공유하는 개체

패턴을 엄격하게 정의하면 다음과 같다.

```text
소수 지표 집중: top2_share >= 90% 또는 positive_indicator_count <= 2
IS/OOS 괴리 큼: gap_score >= 2
exit 품질 나쁨: exit_quality_score >= 3
```

이 기준을 모두 만족하는 후보:

```text
CE only
```

BOIL은 `소수 지표 집중`과 `TAIL_MAE`는 맞지만, `exit_quality_score=2`이고 direct avg PnL/win rate가 양수라 엄격한 bad exit 조건을 통과하지 않는다. 따라서 CE와 BOIL은 둘 다 “거래하면 안 되는 느낌”은 공유하지만, 진단상 원인이 다르다.

- CE: 과적합/룰 품질 붕괴형
- BOIL: 소수 지표 + 고변동 tail-risk형

## 7. 주요 후보별 해석

### OVERFIT_SUSPECT 7개

```text
CE, BKSY, ADMA, BTBT, BMI, CBRL, ALGT(stage3)
```

- CE: 가장 명확한 overfit suspect. OOS avg -5.12%, direct avg -1.02%, stop+timeout 88.6%.
- BKSY: top2 100%, win 42.5%, avg MAE -8.58%, min -21.7%, stop+timeout 83.8%.
- ADMA: top2 100%, IS avg +3.12%에서 OOS avg -2.20%로 악화, min -29.4%.
- BTBT: top2 100%, IS/OOS win gap 21.7%p, avg MAE -8.85%, min -28.5%.
- BMI: top2 100%, IS avg +2.85%에서 OOS avg -4.84%로 큰 괴리.
- CBRL: direct avg -0.55%, win 36.8%, stop+timeout 82.4%.
- ALGT stage3: direct avg -0.29%, win 24.0%, stop+timeout 82.7%.

### BORDERLINE 중 별도 주의

```text
BOIL, BB, CDE, BWXT
```

- BOIL: 평균 성과는 양수지만 avg MAE -8.45%, min -26.7%로 tail-risk가 크다.
- BB: avg MAE -9.02%, min -21.8%, win 47.8%.
- CDE: win 45.0%, stop+timeout 82.5%, 하지만 direct avg PnL은 +2.41%.
- BWXT: stop+timeout 89.1%, direct avg PnL은 +1.63%.

## 8. 최종 판정

이번 스캔은 다음 가설을 부분적으로 지지한다.

```text
느슨한 threshold + 소수 지표 단독 의존은 일부 개체에서 과적합/품질 저하의 증상으로 나타난다.
```

하지만 이 조건만으로 전체 유형을 막으면 안 된다. 이전 OOS gate 검증에서 BB+RSI/margin 컷은 live 적용 권고가 나오지 않았다. 이번 스캔도 같은 결론이다. **유형 전체 차단이 아니라 개체별 진단/deny-list 후보 선별에 쓰는 편이 맞다.**

실무적 결론:

1. CE는 명확한 `OVERFIT_SUSPECT`다.
2. BOIL은 `OVERFIT_SUSPECT`라기보다 `BORDERLINE tail-risk`다.
3. CE와 같은 엄격한 패턴을 공유하는 후보는 현재 26개 중 CE뿐이다.
4. 추가로 개체별 점검 우선순위는 `BKSY, ADMA, BTBT, BMI, CBRL, ALGT(stage3)`다.
5. 이번 임계값은 관찰 분포 기반의 진단 기준이며, 라이브 적용 전에는 별도 OOS/실전 기반 검증이 필요하다.
