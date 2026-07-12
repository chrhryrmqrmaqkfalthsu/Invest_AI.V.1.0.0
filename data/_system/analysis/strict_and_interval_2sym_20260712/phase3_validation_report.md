# Strict-AND interval Phase 3 검증 리포트

# 최종 판정: `STRICT_AND_NO_SURVIVOR`

Strict-AND interval은 구조 게이트를 완전히 통과했고 신호 소멸도 발생하지 않았다. Stress gate와 OOS gate 통과 개수는 기존 floored pilot보다 증가했지만, 동일 후보가 두 gate를 동시에 통과하지 못해 Survivor는 0/6이다.

## 1. 실행 설정

```text
종목: AAP, POWI
train split: 종목당 3개
병렬 worker: 6
population: 36
최대 generation: 12
early stop: 5
기술 feature lag: D-5
시장 context lag: D-1 이하
진입: D+1 open
최대 보유: 7거래일
fitness: mean(pnl_pct / max(holding_days, 1))
```

Strict-AND feature:

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

## 2. 사전 구조 게이트

| 대상 | 개체 수 | invalid | 결과 |
|---|---:|---:|---|
| 초기 랜덤 개체 | 1,000 | 0 | PASS |
| 교배·변이 개체 | 1,000 | 0 | PASS |

```text
편측 interval: 0
NaN/Inf interval: 0
outside domain: 0
high<=low: 0
min-width 위반: 0
near-full 제한 위반: 0
```

학습된 최종 interval 30개의 최소 폭은 `0.119739558`이며, 폭 0.98 이상 near-full interval은 0개다.

## 3. 후보별 결과

| ticker | split | Train coverage | Stress coverage | Stress precision | OOS coverage | OOS precision | Stress gate | OOS gate | Survivor |
|---|---|---:|---:|---:|---:|---:|---|---|---|
| AAP | train_1 | 25.50% | 17.72% | 32.14% | 20.31% | 40.38% | PASS | FAIL | NO |
| AAP | train_2 | 21.20% | 25.00% | 21.52% | 18.75% | 45.83% | FAIL | FAIL | NO |
| AAP | train_3 | 25.60% | 27.53% | 29.89% | 20.70% | 60.38% | FAIL | PASS | NO |
| POWI | train_1 | 30.68% | 45.25% | 39.86% | 15.23% | 33.33% | PASS | FAIL | NO |
| POWI | train_2 | 23.20% | 32.28% | 34.31% | 25.00% | 68.75% | FAIL | PASS | NO |
| POWI | train_3 | 14.80% | 11.08% | 28.57% | 5.86% | 53.33% | FAIL | PASS | NO |

Gate 집계:

```text
Train gate: 6/6
Stress gate: 2/6
OOS gate: 3/6
Survivor: 0/6
```

Stress와 OOS를 모두 통과한 후보는 없다.

```text
Stress PASS / OOS FAIL: 2
Stress FAIL / OOS PASS: 3
Stress FAIL / OOS FAIL: 1
Stress PASS / OOS PASS: 0
```

## 4. Coverage와 precision 집계

| 지표 | 기존 floored pilot | Strict-AND | 변화 |
|---|---:|---:|---:|
| Stress 평균 precision | 43.26% | 32.47% pooled | -10.79%p |
| OOS pooled precision | 58.06% | 51.66% | -6.40%p |
| OOS coverage | 18.45% | 17.64% 평균 | -0.81%p |
| Stress gate | 0/6 | 2/6 | +2 |
| OOS gate | 1/6 | 3/6 | +2 |
| Survivor | 0/6 | 0/6 | 변화 없음 |

Signal extinction 기준은 coverage `<=0.20%`였다.

```text
Signal-extinction 후보: 0/6
최저 Stress coverage: 11.08%
최저 OOS coverage: 5.86%
```

따라서 이번 5-feature strict-AND는 신호를 없애지는 않았다. 기존 12-feature 전체 strict-AND에서 관찰된 0.20% 수준의 coverage 붕괴도 발생하지 않았다.

## 5. 거래 성과

| 구간 | 총 거래 | 평균 거래 승률 | 평균 expectancy | 평균 MDD | 평균 일효율 fitness |
|---|---:|---:|---:|---:|---:|
| Train | 126 | 65.60% | +1.4134% | -8.3223% | +0.4553%/보유일 |
| Stress | 171 | 49.13% | -0.2280% | -24.0887% | -0.0627%/보유일 |
| OOS | 102 | 51.52% | +0.3760% | -15.6541% | +0.0309%/보유일 |

긍정적인 OOS 사례:

```text
AAP train_3 origin:
OOS precision 60.38%, expectancy +1.0003%, MDD -11.57%
Stress precision 29.89%로 Stress gate 실패

POWI train_2 origin:
OOS precision 68.75%, expectancy +3.2717%, MDD -11.11%
Stress precision 34.31%로 Stress gate 실패
```

Stress를 통과한 두 후보는 OOS에서 무너졌다.

```text
AAP train_1 origin: OOS precision 40.38%
POWI train_1 origin: OOS precision 33.33%
```

## 6. 청산 동작

대부분의 거래는 보유 중 strict-AND interval 이탈로 청산됐다.

관찰된 exit reason:

```text
interval_break
max_holding_7d
stop_loss_atr
stop_gap
fold_end_mark_to_market
```

익절·trailing exit는 발생하지 않았다. 보유 중 signal evaluation 값이 모든 후보의 train/stress/OOS 결과에 기록됐다.

일부 Stress MDD는 매우 깊었다.

```text
POWI train_1 origin Stress MDD: -48.53%
POWI train_2 origin Stress MDD: -36.70%
```

MDD 사고/방치 분류 임계값은 설계대로 아직 적용하지 않았다. 일별 손실 집중도와 interval 청산 신호 진단 구조만 구현된 상태다.

## 7. Reporting correction

최초 보고의 `best_train_fitness`는 train 후보 선택 후 Stress/OOS backtest가 동일 Rulebook의 `fitness` 필드를 갱신하면서 마지막 평가값으로 표시됐다.

다음 방식으로 선택 시점 값을 복원했다.

```text
selected_train_fitness = max(generation_best for fitness_history)
```

수정 영향:

```text
수정된 필드: best_train_fitness 표시값만
후보 선택: 변화 없음
학습 interval: 변화 없음
coverage/precision/trade/gate/verdict: 변화 없음
GA 재실행: 없음
```

상세값은 `reporting_correction.md`에 기록했다.

## 8. 결론

이번 1차 strict-AND mechanics는 다음 목적을 달성했다.

```text
합산 상쇄 원천 차단
편측·NaN·도달 domain 밖 interval 생성 차단
보유 중 매일 신호 재평가
신호 기반 청산
coverage 소멸 회피
```

그러나 일반화 성능은 개선되지 않았다.

```text
Stress precision 하락
OOS precision 하락
Survivor 0/6 유지
```

따라서 다음 단계에서 strict-AND 자체를 폐기할 근거는 없지만, 현재 5-feature 조합과 D-5 설정을 그대로 확대할 근거도 없다. Grouped/floored mechanics를 즉시 추가하기 전에 Stress와 OOS gate가 서로 엇갈리는 원인을 feature별 interval·regime·MDD 경로로 분해해야 한다.
