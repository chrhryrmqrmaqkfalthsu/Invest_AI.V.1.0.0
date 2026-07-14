# new fitness v2 원본 swing 장치 복원 1단계 Readout

- 대상: AAP Stage3 entry-scope new fitness v2
- 비교 기준: 기존 notebook official v2, seed `2026071401`
- 백업 커밋: `01edf0e`
- 실행 소스 커밋: `ea32616b5772a1dc9139ad3c7385ff367f09a1cd`
- 변경 파일: `scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py`
- 변경 범위: 거래수 factor + profit concentration penalty만

## 1. 결론

이번 복원은 **수익 집중도는 뚜렷하게 낮췄지만, fold-best의 최소 거래수 경계 수렴과 cross-fold 일반화 실패는 해결하지 못했다.**

- fold-best 거래수: `12/12/12 → 12/12/13`
- all3/all2/all1/all0: `0/0/191/109 → 0/0/212/88`
- final population 정확히 12건: `168/300 → 114/300`
- final population 14~19건: `30/300 → 53/300`
- final population 20~24건: `0/300 → 2/300`
- train_3 fold-best profit concentration, PnL% fallback: `0.5966 → 0.2435`
- train_2 통과 후보 effective event count: `16.98 → 15.88`

판정:

| 항목 | 판정 |
|---|---|
| population 거래수 분포 | 부분 완화 |
| fold-best 12건 경계 | 완화 부족 |
| 단일 수익 기여 집중 | 명확히 완화 |
| train_2 통과 후보 사건 집중 | 소폭 악화 |
| cross-fold 일반화 | 개선 없음 |

---

## 2. 원본 정의 대조

원본: `engine/learning/backtest.py`

### 거래수 factor

```python
if trade_count < 5:
    trade_factor = 0.10
elif trade_count < 10:
    trade_factor = 0.35
elif trade_count < 20:
    trade_factor = 0.70
elif trade_count <= 80:
    trade_factor = 1.00
else:
    trade_factor = max(0.65, 1.0 - (trade_count - 80) / 250.0)
```

조사 readout과 실제 코드가 일치했다.

- 12건에는 불연속이 없다.
- 10~19건 전체가 `0.70`이다.
- 20건에서 `0.70 → 1.00` 불연속 점프가 있다.

따라서 원본 factor는 엄밀한 연속 함수가 아니라 구간형 step factor이며, 12~19건 사이에는 거래수 증가에 따른 추가 기울기가 없다.

### Profit concentration penalty

```python
concentration = _clamp(float(profit_concentration or 0.0), 0.0, 1.0)
if concentration <= 0.50:
    return 0.0
return _clamp((concentration - 0.50) / 0.25 * 20.0, 0.0, 20.0)
```

```text
profit_concentration = max single positive PnL / total positive PnL
```

원본 계산은 `pnl_krw`를 우선하고, 없으면 `pnl_pct`를 사용한다.

| concentration | penalty |
|---:|---:|
| `<=0.50` | 0 |
| `0.50~0.75` | 0→20 선형 |
| `>=0.75` | 20 |

---

## 3. 구현

entry-scope fitness만 다음처럼 변경했다.

```text
trade_adjusted_primary
  = mean(net pnl_pct / max(holding_days, 1)) × original_trade_factor

fitness_before_complexity
  = trade_adjusted_primary
  - 기존 MAE penalty
  - 기존 realized-loss penalty
  - original concentration penalty

final fitness
  = complexity penalty 적용 후
    trade_count >= 12 AND win_rate >= 60%이면 인정
    아니면 -1e9
```

factor는 raw primary에만 곱했다. 기존 MAE·실현손실 벌점 강도는 줄이지 않았다.

유지한 요소:

- 일평균 순수익 primary
- MAE -2% 초과분 벌점
- 실현손실 -1% 초과분 벌점
- 순수익 `>+0.5%` 승리 정의
- 최소 12건·승률 60% hard gate
- all3 hard gate
- strict-AND·OOD fail-closed
- execution semantics와 mutation hint

변경하지 않은 요소:

- cross-fold fitness
- 사건 단위 검증
- strict-AND 조정
- Jaccard 위치
- Stage2 legacy swing 경로

---

## 4. 검증

통과:

- `git diff --check`
- Python compile
- factor 경계값 probe
- concentration 경계값 probe
- 12거래 entry-scope 합성 probe
- legacy non-entry swing 동일성 probe
- 병렬 재현성 probe
- new fitness activation probe

검증값:

```text
factor: 4=0.10, 5=0.35, 10=0.70, 12=0.70,
        19=0.70, 20=1.00, 80=1.00, 81=0.996, 하한=0.65
penalty: 0.500=0, 0.625=10, 0.750=20, 1.000=20
```

legacy swing 경로는 동일 입력에서 직접 `_calc_fitness_swing()`을 호출한 값과 일치했다.

---

## 5. 정식 실행

기존 v2와 동일 설정:

| 항목 | 값 |
|---|---:|
| ticker | AAP |
| seed | `2026071401` |
| qualify | `100 population × 40 generations × 3 folds` |
| notebook workers | 28 |
| host | `DESKTOP-TO74AR2` |
| market cutoff | 2026-07-10 |
| RNG | parent process only |
| merge | input index order |

복원 산출물:

```text
data/_system/analysis/stage3_aap_newfitness_v2_swing_restore_20260715/AAP/NOTEBOOK_MAX
```

비교 기준:

```text
data/_system/analysis/stage3_aap_newfitness_v2_20260714/AAP/NOTEBOOK_MAX
```

노트북 bundle 준비 중 세 시도는 `.bak`, `loguru`, `yfinance` 누락으로 GA 시작 전에 종료됐다. 모두 generation row가 0이었다. 최종 판정에는 완전 실행된 PID 27340 결과만 사용했다. 필요한 패키지는 실행 전용 isolated vendor에만 설치했고 노트북 전역 환경은 변경하지 않았다.

---

## 6. 거래수 비교

### Fold-best

| fold | 기존 | 복원 |
|---|---:|---:|
| train_1 | 12 | 12 |
| train_2 | 12 | 12 |
| train_3 | 12 | 13 |

정확한 `12/12/12` 고정은 깨졌지만 여전히 최소 경계에 가깝다. 20건 factor 구간에 도달한 fold-best는 없다.

### Final population 300개

| 구간 | 기존 | 복원 | 변화 |
|---|---:|---:|---:|
| 12 미만 | 66 | 55 | -11 |
| 정확히 12 | 168 | 114 | -54 |
| 정확히 13 | 36 | 76 | +40 |
| 14~19 | 30 | 53 | +23 |
| 20~24 | 0 | 2 | +2 |
| 평균 | 11.85 | 12.24 | +0.39 |
| 중앙값 | 12 | 12 | 0 |
| 최대 | 16 | 21 | +5 |

```text
정확히 12건: 56.0% → 38.0%
12~13건:     68.0% → 63.3%
14~19건:     10.0% → 17.7%
20~24건:      0.0% →  0.7%
```

population은 다소 넓어졌지만 상위 선택은 여전히 12~13건에 몰렸다. 특히 train_2는 최대 거래수가 기존과 동일한 16건이었다.

---

## 7. Cross-fold 결과

| 구분 | 기존 | 복원 | 변화 |
|---|---:|---:|---:|
| all3 | 0 | 0 | 0 |
| all2 | 0 | 0 | 0 |
| all1 | 191 | 212 | +21 |
| all0 | 109 | 88 | -21 |

한 fold 통과 후보는 늘었지만 두 fold 이상 통과한 후보는 여전히 없다. 이번 변경은 cross-fold 정보를 fitness에 넣지 않았으므로 local-fold 개선이 일반화로 연결되지 않았다.

---

## 8. Train_2 날짜 집중도

`entry_date`를 사건 대리 단위로 사용했다.

```text
effective event count = 1 / Σ(date_share²)
```

### Train_2 fold-best

| 지표 | 기존 | 복원 |
|---|---:|---:|
| 거래·unique dates | 12 / 12 | 12 / 12 |
| active months | 8 | 8 |
| top1/top3/top5 | 8.33/25.00/41.67% | 동일 |
| effective count | 12.00 | 12.00 |

각 거래일이 달라 단순 날짜 중복은 없다. 인접 날짜를 동일 사건으로 묶는 clustering은 이번 범위에 포함하지 않았다.

### Train_2 전체 300개 후보 풀

| 지표 | 기존 | 복원 | 변화 |
|---|---:|---:|---:|
| 총 entry occurrences | 2,083 | 2,365 | +282 |
| unique dates | 115 | 119 | +4 |
| active months | 12 | 11 | -1 |
| top1 | 4.80% | 3.76% | -1.04%p |
| top3 | 14.07% | 11.04% | -3.03%p |
| top5 | 23.09% | 18.18% | -4.91%p |
| effective count | 30.67 | 36.27 | +5.60 |

전체 탐색 풀은 분산됐다. 기존 상위 날짜에 있던 `2023-10-24` 84회는 복원 후 상위 10개 날짜에서 사라졌다.

### Train_2 통과 후보 78개

| 지표 | 기존 | 복원 | 변화 |
|---|---:|---:|---:|
| 총 entries | 980 | 986 | +6 |
| unique dates | 49 | 46 | -3 |
| active months | 11 | 11 | 0 |
| top1 | 7.55% | 7.71% | +0.16%p |
| top3 | 22.45% | 23.12% | +0.67%p |
| top5 | 37.04% | 38.44% | +1.40%p |
| effective count | 16.98 | 15.88 | -1.10 |

실제 통과 후보군에서는 날짜 집중도가 소폭 악화됐다. 따라서 사건 의존이 완화됐다고 판정할 수 없다.

---

## 9. Profit concentration

기존 v2는 concentration을 diagnostics에 기록하지 않았으므로 양쪽 fold-best 거래의 양의 `pnl_pct`로 원본 fallback 정의를 재계산했다.

| fold | 기존 | 복원 | 변화 |
|---|---:|---:|---:|
| train_1 | 0.2099 | 0.2004 | -0.0096 |
| train_2 | 0.2517 | 0.2224 | -0.0293 |
| train_3 | 0.5966 | 0.2435 | -0.3531 |

복원 후 원본 `pnl_krw` 우선 logged 값은 `0.1853 / 0.2224 / 0.2436`이며 fold-best penalty는 모두 0이었다.

기존 train_3 concentration `0.5966`에 원본 벌점을 가상 적용하면 약 `7.7294`점이다. 복원 후 final population 300개 중 11개에 실제 concentration penalty가 발생했고, 이 중 3개는 concentration 0.75 이상이었다.

따라서 concentration 장치는 실제 selection에 작동했으며 특히 train_3 단일 이익 의존 best를 제거했다.

주의: 원본 0~20점 벌점은 최대 100점 이상 swing score에 맞춰진 값이다. new fitness primary는 대체로 1~3이므로 동일 상수는 상대적으로 매우 강하다. 이번에는 지시대로 원본 상수를 그대로 사용했다.

---

## 10. Best·mean fitness와 시간

Final generation:

| fold | 기존 best | 복원 best | 기존 mean | 복원 mean |
|---|---:|---:|---:|---:|
| train_1 | 0.980930 | 0.754600 | -259,999,999.408 | -309,999,999.740 |
| train_2 | 1.461870 | 1.071156 | -189,999,999.248 | -269,999,999.658 |
| train_3 | 2.808397 | 1.532700 | -319,999,998.694 | -229,999,999.113 |

mean에는 hard gate 탈락값 `-1e9`가 포함된다. 복원 후에는 primary에 0.70 factor가 적용되므로 기존 fitness와 동일 척도의 직접 성능 비교로 해석하면 안 된다.

| 실행 | elapsed |
|---|---:|
| 기존 v2 | 762.93초 |
| 복원 | 483.38초 |
| 변화 | -279.56초, -36.6% |

후보 경로와 탈락 분포가 달라졌으므로 시간 감소를 성능 개선 증거로 보지 않는다.

---

## 11. 원인 진단

1. 원본 factor는 12~19건에서 모두 0.70이라 12→19 구간의 증가 보상이 없다.
2. 20건부터 1.00이지만 final population에서 20건 이상은 두 개뿐이었다.
3. 동일 factor 구간에서는 평균 일수익이 높은 12건 후보가 평균이 낮은 다거래 후보를 계속 이긴다.
4. concentration은 단일 수익 기여만 측정하며 동일 시장 사건의 여러 날짜 진입은 잡지 못한다.
5. cross-fold 성과가 fitness에 없으므로 all2·all3 실패가 진화 압력으로 돌아가지 않는다.

---

## 12. 최종 판정과 다음 후보

### 12건 경계

**완화 부족.** Population 분포는 넓어졌지만 fold-best는 `12/12/13`이다.

### 단일 수익 의존

**명확히 완화.** 특히 train_3 concentration이 `0.5966 → 0.2435`로 감소했다.

### 사건 의존

**완화 확인 불가.** Train_2 통과 후보의 top1·3·5 비중은 증가했고 effective count는 감소했다.

### 일반화

**개선 없음.** `all2=0`, `all3=0`이다.

다음 실험 후보는 분리해야 한다.

1. 12 경계 자체를 겨냥하려면 원본 step 대신 `12에서 0.70 → 20에서 1.00`의 smooth ramp를 단일 변경으로 시험한다.
2. 그다음 cross-fold fitness를 별도 시험한다. 예: `mean(fold_score)-λ×std` 또는 `min(fold_score)`.
3. 사건 집중은 별도 단계에서 independent event count, active month, top1·3·5 event share, effective event count를 검증한다.

---

## 13. 무결성·Git

Stage2 legacy swing SHA는 작업 전후 동일하다.

```text
engine/learning/backtest.py
734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e

scripts/research/stage23_rework_20260713/engine/learning/backtest.py
734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e
```

변경 파일 SHA:

```text
0ed2cec35731ddc7ff06ae6860495ffbf748b997ad06b3458cb2b07e0ef5924b
```

보호 파일 SHA는 불변이다.

```text
.env
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

실행 summary에서 `protected_unchanged=true`, `daemon_unchanged=true`를 확인했다.

산출물 `SHA256SUMS.txt`는 Windows CRLF 형식이므로 Linux에서는 CR을 제거한 입력 스트림으로 검증했다. 15개 산출물이 모두 `OK`였고 원본 manifest는 수정하지 않았다.

Git:

```text
01edf0e  수정 전 백업
 ea32616  구현 및 push
```
