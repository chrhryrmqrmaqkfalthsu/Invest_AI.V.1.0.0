# Strict-AND interval 2종목 Phase 3

# 판정: **STRICT_AND_NO_SURVIVOR**

## Structural gate

- 초기 1,000개 invalid: `0`
- 교배·변이 1,000개 invalid: `0`
- 편측/NaN interval: `0`

## 후보별 결과

| ticker | split | Train coverage | Stress coverage / precision | OOS coverage / precision | Stress gate | OOS gate | Survivor |
|---|---|---:|---:|---:|---|---|---|
| AAP | train_1 | 25.50% | 17.72% / 32.14% | 20.31% / 40.38% | True | False | False |
| AAP | train_2 | 21.20% | 25.00% / 21.52% | 18.75% / 45.83% | False | False | False |
| AAP | train_3 | 25.60% | 27.53% / 29.89% | 20.70% / 60.38% | False | True | False |
| POWI | train_1 | 30.68% | 45.25% / 39.86% | 15.23% / 33.33% | True | False | False |
| POWI | train_2 | 23.20% | 32.28% / 34.31% | 25.00% / 68.75% | False | True | False |
| POWI | train_3 | 14.80% | 11.08% / 28.57% | 5.86% / 53.33% | False | True | False |

## 집계

- 평균 Stress coverage: `26.4768%`
- pooled Stress precision: `32.4701%`
- 평균 OOS coverage: `17.6432%`
- pooled OOS precision: `51.6605%`
- signal-extinction 후보: `0/6`
- Survivor: `0/6`

## 기존 pilot 기준선

```text
Stress 평균 precision: 43.26%
선택 pooled OOS precision: 58.06%
선택 OOS coverage: 18.45%
Survivor: 0/6
```

기술 feature는 D-5 완료봉, 시장 context는 D-1 이하, 진입은 D+1 open을 사용했다.
MDD 사고/방치 분류 임계값은 적용하지 않았고 진단 로그만 기록했다.

## Reporting correction

초기 산출물의 `best_train_fitness`는 Stress/OOS 재평가가 같은 Rulebook의 `fitness`를 갱신해 마지막 period 값으로 표시됐다. 세대별 `fitness_history`의 best 최댓값으로 선택 시점 train fitness를 복원했다. 후보 선택, interval, coverage, precision, trade 결과, gate, verdict에는 영향이 없다.
