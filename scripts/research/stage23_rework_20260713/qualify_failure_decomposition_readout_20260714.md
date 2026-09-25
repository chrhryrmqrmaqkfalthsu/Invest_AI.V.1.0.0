# Qualify 탈락 원인 분해 — 기존 baseline 로그 전용

- 분석일: 2026-07-14
- 대상: `stage3_baseline_light_2sym_20260713`의 AAP / POWI
- 제약 준수: GA·백테스트 재실행 없음, 기존 코드·데이터·보호 파일 수정 없음
- 분석 방식: 기존 JSON/JSONL/run.log와 runner의 기록 정책만 읽어 집계
- 수정 전 Git 기준: `06339970133e922f5ac809ea4f79cee089d00381`
- daemon PID 494330: 분석 시작 시 `Sl`, 유지 확인

## 1. 기록 수준 인벤토리

### 결론

기록 수준은 **“fold별 GA best에 대한 trade-level + signal 통계, 전체 9개 cross-fold 후보에 대한 집계 요약”**이다.

| 확인 항목 | 남아 있음 | 범위 / 한계 |
|---|---|---|
| 개체별 chromosome trade-level | 부분적으로 있음 | `trade_level_details.jsonl`은 각 fold의 **GA best 1개**, 총 3개 best만 기록. 9개 후보 전원의 거래는 없음 |
| fold별 요약 지표 | 있음 | `qualify_result.json`에 split별 pass count와 member score 분포가 있음. 후보 hash별 trade_count·win_rate·expectancy 행렬은 없음 |
| 신호 통계 | 있음 | `signal_statistics.jsonl`에 fold별 GA best의 strict-AND 통과일 수와 5개 feature 단독 통과율이 있음 |
| qualify checkpoint / population / rulebook 본문 | 없음 | baseline 디렉터리에 checkpoint, candidate matrix, population, qualify rulebook 파일 없음 |
| all3 / all2 / all1 후보별 pass vector | 부분적으로만 복원 가능 | early-stop 전에 평가된 split의 aggregate count만 남음. 미평가 split 때문에 최종 all2/all1 정확값은 복원 불가 |

근거 파일:

- `AAP|POWI/qualify_result.json`
- `AAP|POWI/signal_statistics.jsonl`
- `AAP|POWI/trade_level_details.jsonl`
- `AAP|POWI/run.log`
- `AAP|POWI/manifest.json`

`manifest.json`의 `qualify_individual_policy`는 `audit_summary_then_discard`이며, baseline runner는 qualify 종료 후 각 fold GA의 best만 재감사하고 개체 본문을 폐기한다.

## 2. Qualify 기준

동시 충족 조건:

- `trade_count >= 5`
- `member_score >= 10`
- `expectancy_pct >= 2.0`
- 동일 후보가 3개 train fold를 모두 통과

주의: 아래 fold 성과 표는 **각 fold에서 따로 최적화된 GA best**의 결과다. 동일 chromosome을 세 fold에 적용한 cross-fold 행렬이 아니다.

## 3. 핵심 갈림길: 신호 고갈(A) vs fold 일반화 실패(B)

### AAP

| fold | eligible 일 | strict-AND | strict 통과율 | 실제 거래 | 승률 | 기대값 | 2% 대비 |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 251 | 13 | 5.18% | 6 | 83.33% | 1.845849% | -0.154151%p |
| train_2 | 250 | 41 | 16.40% | 11 | 81.82% | 2.767014% | +0.767014%p |
| train_3 | 250 | 21 | 8.40% | 10 | 70.00% | 1.989539% | -0.010461%p |

판독:

- fold-best의 실제 거래수는 6/11/10으로 모두 support 하한 5를 넘는다. 따라서 **순수한 신호 고갈/거래수 미달만으로 설명되지는 않는다.**
- train_1은 strict 통과율 5.18%, 실제 거래 6건으로 여유가 작아 tightness 영향은 있다.
- train_1과 train_3의 기대값은 2% 문턱을 각각 0.154%p, 0.010%p 못 넘었다. 특히 train_3은 사실상 경계값 실패다.
- cross-fold 후보 9개는 train_1에서 전부 탈락해 이후 split 평가가 생략됐다. 후보별 실패 항목이 저장되지 않아, 9개 각각이 trade_count와 expectancy 중 무엇 때문에 탈락했는지는 분해할 수 없다.
- 승률 83.3%/81.8%/70.0%와 기대값 1.85%/2.77%/1.99%는 POWI만큼 급격한 fold 붕괴는 아니다.

**AAP 판정: `MIXED / INCONCLUSIVE`**

신호가 완전히 말라붙지는 않았고 fold-best support도 충족했다. 다만 train_1의 낮은 신호 밀도와 거의 모든 거래에서 발생한 interval-break가 기대값을 문턱 아래로 누른 정황이 있다. 작은 interval 완화가 도움이 될 가능성은 있으나, 후보별 cross-fold 행렬이 없어 `CAUSE_A_TOO_TIGHT`로 확정할 수는 없다.

### POWI

| fold | eligible 일 | strict-AND | strict 통과율 | 실제 거래 | 승률 | 기대값 | 2% 대비 |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 251 | 20 | 7.97% | 12 | 66.67% | 1.518476% | -0.481524%p |
| train_2 | 250 | 36 | 14.40% | 12 | 50.00% | 1.597910% | -0.402090%p |
| train_3 | 250 | 41 | 16.40% | 13 | 38.46% | 0.855719% | -1.144281%p |

판독:

- fold-best의 거래수는 12/12/13으로 support 부족이 아니다.
- strict-AND 통과일도 20/36/41로 절대 신호 고갈이 아니다.
- 승률이 66.7% → 50.0% → 38.5%, 기대값이 1.52% → 1.60% → 0.86%로 악화된다.
- cross-fold 후보 9개 중 train_1 통과는 2개였지만 train_2 통과는 0개였다. 같은 후보군이 다음 fold에서 재현되지 않았다.

**POWI 판정: `CAUSE_B_INFO_LACK`**

신호와 support는 충분하지만 성과가 fold 간 재현되지 않는다. strict-AND 완화만으로 3-fold 일반화 문제를 해결할 근거는 약하며, 기존 ENTANGLED/feature 정보 부족 결론을 재확인한다.

## 4. Feature 병목

### AAP

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio | 최저 통과 feature |
|---|---:|---:|---:|---:|---:|---|
| train_1 | 46.61% | 33.47% | 43.03% | 54.18% | 40.64% | macd_hist |
| train_2 | 60.40% | 39.20% | 74.00% | 80.00% | 74.00% | macd_hist |
| train_3 | 51.60% | 56.00% | 53.20% | 32.00% | 52.40% | bb_position |
| 3-fold 단순 평균 | 52.87% | 42.89% | 56.74% | 55.39% | 55.68% | macd_hist |

- 주 병목은 train_1·2의 `macd_hist`, train_3의 `bb_position`이다.
- `ma_trend`는 세 fold 모두 최저가 아니며 AAP qualify의 주 병목으로 보이지 않는다.

### POWI

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio | 최저 통과 feature |
|---|---:|---:|---:|---:|---:|---|
| train_1 | 58.17% | 63.35% | 40.24% | 53.78% | 56.57% | rsi |
| train_2 | 54.40% | 51.20% | 45.20% | 54.00% | 55.20% | rsi |
| train_3 | 28.80% | 57.20% | 62.40% | 81.20% | 60.00% | ma_trend |
| 3-fold 단순 평균 | 47.12% | 57.57% | 49.28% | 63.00% | 57.26% | ma_trend |

- train_1·2의 국소 병목은 `rsi`다.
- train_3에서는 `ma_trend`가 28.8%로 급락해 명확한 병목이다.
- 따라서 `ma_trend`의 OOS 괴리는 POWI train_3에서 다시 나타났지만, 전 fold 공통 단일 병목은 아니다.

## 5. Strict-AND와 interval tightness의 별도 영향

| 종목 | high-quality지만 strict 차단 | quality override | interval-break exit |
|---|---:|---:|---:|
| AAP | 10 / 103 / 66 | 0 | 26 / 27 거래 |
| POWI | 86 / 23 / 13 | 0 | 36 / 37 거래 |

- strict-AND는 quality score가 높아도 우회되지 않았다.
- entry 신호뿐 아니라 provisional exit에서도 interval-break가 거의 전 거래를 종료시켰다.
- 따라서 interval tightness는 **신호 수 감소뿐 아니라 보유기간 단축과 기대값 저하 경로**로도 작용했을 가능성이 있다.
- 다만 로그만으로 interval을 완화했을 때의 반사실 성과는 계산할 수 없으므로 효과 크기는 확정할 수 없다.

## 6. Best 개체의 3-fold 근접도

### AAP

- unique candidate: 9
- 평가된 cross-fold split: train_1만
- train_1 pass: 0
- early stop: train_1 zero-pass
- 평가된 범위에서 최대 통과 fold 수: 0
- 최종 all2/all1: **복원 불가**. train_2·3 cross-fold 평가가 실행되지 않았기 때문이다.

fold-local best 기준으로는 train_3 기대값이 2%에 0.010%p 모자라 매우 근접했지만, 이는 동일 후보의 3-fold 근접도를 뜻하지 않는다.

### POWI

- unique candidate: 9
- 평가된 cross-fold split: train_1, train_2
- train_1 pass: 2
- train_2 pass: 0
- early stop: train_2 zero-pass
- 평가된 두 split 기준: 1-fold 통과 후보 2개, 0-fold 통과 후보 7개, 2-fold 통과 후보 0개
- 최종 all3: 0 확정
- 최종 all2/all1: train_3 cross-fold 미평가로 정확값 복원 불가

즉 POWI에는 “한 fold까지는 넘은” 후보 2개가 있었지만, 두 번째 fold까지 이어진 후보는 없었다. 약간의 문턱 조정만으로 all3에 가까웠다고 볼 증거는 없다.

## 7. 최종 판정

| 범위 | 판정 | 핵심 이유 |
|---|---|---|
| AAP | `MIXED / INCONCLUSIVE` | support는 충족하나 train_1 신호 밀도가 낮고 기대값이 경계 미달. 후보별 cross-fold 상세 부재로 A/B 확정 불가 |
| POWI | `CAUSE_B_INFO_LACK` | 거래수 충분, 신호 존재, train_1 통과 후보가 train_2에서 전멸, 승률·기대값 fold 악화 |
| 2종목 종합 | `MIXED / INCONCLUSIVE` | POWI는 B가 명확하지만 AAP는 tightness와 경계값 실패가 혼재하고 로그 한계가 있음 |

운영적 해석:

1. POWI는 strict-AND 완화보다 feature 정보 확장/ENTANGLED 해소가 우선이다.
2. AAP는 근본 실패로 단정할 수준은 아니며, interval/exit tightness를 별도 실험 대상으로 볼 여지는 있다. 그러나 현 로그만으로 완화 효과를 보증할 수 없다.
3. 다음 실행에서 원인 분해를 완결하려면 9개 후보 × 3 folds의 `trade_count`, `win_rate`, `expectancy_pct`, `member_score`, pass/fail reason과 후보별 신호 통계를 early-stop 전에 저장해야 한다.

## 8. 무결성 메모

- 기존 baseline 산출물은 수정하지 않았다.
- 새 readout의 SHA-256은 동명 `.sha256` 파일에 별도 기록한다.
- 입력 산출물 SHA는 기존 `SHA256SUMS.txt`를 기준으로 유지한다.
