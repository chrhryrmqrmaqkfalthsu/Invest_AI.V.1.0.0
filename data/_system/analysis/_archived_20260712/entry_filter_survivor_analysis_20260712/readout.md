# 진입 필터 survivor 1개 원인 분석 — 사인 분석 + 조건 민감도

- 분석일: 2026-07-12
- 재학습 없이 기존 replay 산출물 재분석
- 대상 entity: 57개
- 기존 최종 survivor: AEVA 1개
- 최종 판정: **NICHE_ONLY**
- 라이브 연결·설정·daemon 변경: 0

## 1. 결론

AEVA만 살아남은 주된 이유는 단순히 20%p precision-gap 조건이 엄격해서가 아니다.

실제 병목은:

1. Stress precision 자체가 유지되지 않음
2. OOS precision 자체도 유지되지 않음
3. 두 구간을 동시에 견디는 개체가 거의 없음
4. 일부 precision이 좋아 보이는 개체는 최소 통과표본이 부족함

이었다.

20%p gap 허용치를 25·30·35·40%p로 완화해도 원래 최소표본과 stress/OOS precision floor를 유지하면 survivor는 계속 AEVA 1개였다.

따라서 현재 이중 gate가 과도하게 엄격해 survivor를 인위적으로 1개로 만든 것으로 볼 수 없다.

## 2. 탈락 사인 집계

원인들은 한 개체에 중복될 수 있다.

| 원인 | 개체 수 | 57개 대비 |
|---|---:|---:|
| Stress precision 미달 | 41 | 71.93% |
| OOS precision 미달 | 38 | 66.67% |
| Train→Stress gap 20%p 초과 | 37 | 64.91% |
| Train→OOS gap 20%p 초과 | 27 | 47.37% |
| OOS 최소 통과표본 미달 | 19 | 33.33% |
| Stress 최소 통과표본 미달 | 16 | 28.07% |
| Train precision 미달 | 10 | 17.54% |
| Train 표본 부족 | 6 | 10.53% |

가장 큰 병목은 **Stress 일반화 붕괴**다.

계층형 primary cause로 한 원인만 지정하면:

| Primary cause | 개체 수 |
|---|---:|
| Stress precision 실패 | 20 |
| Stress 최소표본 실패 | 16 |
| Train gate 실패 | 10 |
| Train 자체 불가 | 6 |
| Stress gap만 실패 | 2 |
| OOS precision 실패 | 1 |
| OOS 최소표본 실패 | 1 |
| Survivor | 1 |

즉 train을 통과한 뒤 처음 맞는 stress gate에서 대부분이 이미 제거된다.

## 3. 왜 gap 완화가 효과가 없는가

Precision gap은 상대 조건이다.

```text
train_precision - validation_precision <= 허용폭
```

하지만 많은 개체는 gap 이전에 validation precision 자체가 floor보다 낮다.

예:

- AMSC stress precision 28.57%
- AVAV stress precision 26.09%
- ALGT stress precision 35.71%
- FIX stress precision 30.00%

이들은 gap 허용폭을 늘려도 stress precision floor를 통과하지 못한다.

따라서 20%p→40%p 완화에도 double-gate survivor가 늘지 않았다.

## 4. Near-miss 개체

### DDOG — 가장 가까운 진짜 near-miss

- Train precision: 75.51%
- Stress precision: 69.23%, pass 26
- OOS precision: 80.00%, pass 10
- Precision gap: 양쪽 모두 통과
- 유일한 실패: OOS 최소 pass 13개 요구 대비 10개

Quality는 유지됐지만 최소표본이 3개 부족하다.

그러나 사용자 전제에 따라 최소표본은 권장 완화 대상이 아니다. DDOG를 살리려면 AMSC/AVAV와 같은 얇은 표본 착시 위험을 다시 받아들여야 한다.

### BB

- Stress precision 100%지만 pass 1개
- OOS precision 66.67%, pass 3개

명백한 thin-sample 사례다. Near-miss처럼 보여도 survivor 후보로 볼 수 없다.

### CMC

- Stress precision 60%, pass 5
- OOS precision 50%, pass 4
- 양쪽 최소표본 미달

양쪽 모두 표본이 얇다.

### FIX·ALGT

OOS는 강하지만 stress가 붕괴했다.

- FIX: stress 30%, OOS 100%
- ALGT: stress 35.71%, OOS 83.33%

단일 OOS gate로는 살아나지만 regime-robust filter는 아니다.

### BELFB

- Stress 100%, pass 4
- OOS 33.33%, pass 3

Stress-only 완화가 만드는 대표적인 허수 survivor다.

## 5. Gap 민감도

원래 최소표본과 precision floor를 유지한 double gate 결과:

| Gap 허용폭 | Survivor |
|---:|---:|
| 20%p | 1 |
| 25%p | 1 |
| 30%p | 1 |
| 35%p | 1 |
| 40%p | 1 |

모든 경우 AEVA만 생존했다.

따라서 합리적인 gap 재조정으로 survivor가 확대된다는 근거는 없다.

## 6. 단일 gate 민감도

### Stress-only

20~35%p 조건에서:

- AEVA
- BELFB
- DDOG

3개가 살아난다.

하지만 원래 OOS quality까지 유지한 개체는 AEVA 1개뿐이다.

- BELFB: OOS precision 33.33%
- DDOG: OOS precision 80%지만 최소표본 3개 부족

즉 2개 증가는 robust survivor가 아니다.

### OOS-only

다음 5개가 살아난다.

- ALGT
- FIX
- AEVA
- BE
- DB

OOS pooled precision은 80.43%, pass 46개다.

그러나 stress quality도 유지한 개체는 AEVA 1개뿐이다. 나머지 4개는 특정 OOS regime에만 맞는 개체다.

따라서 OOS-only 선택은 현재 OOS 구간에 대한 사후 선택 위험이 있다.

## 7. 최소표본 완화 진단

최소표본을 절반 또는 절대 3개로 낮추면:

- CMC
- AEVA
- DDOG

3개로 늘어난다.

OOS pooled precision은 80.65%다.

하지만 원래 OOS 최소표본을 충족하는 것은 AEVA뿐이다.

즉 survivor 증가 2개는 사용자가 경계한 “표본 얇을 때 좋아 보이는” 유형이다. 이 시나리오는 진단용일 뿐 권고하지 않는다.

## 8. AMSC·AVAV 재확인

로그 기반에서는 두 개체가 survivor였지만 replay universe에서:

### AMSC

- Train 86.05%
- Stress 28.57%
- OOS 57.89%
- Stress pass 14, OOS pass 19

단순 표본 부족이 아니라 stress precision 붕괴다.

### AVAV

- Train 62.96%
- Stress 26.09%
- OOS 36.36%
- Stress pass 23, OOS pass 11

표본이 확대되자 양쪽 검증 precision이 모두 붕괴했다.

따라서 이전 survivor는 로그 기반 entry-selection과 얇은 표본의 착시였다는 해석이 강화된다.

## 9. 접근 성격 판정

### **NICHE_ONLY**

근거:

- 57개 중 robust survivor 1개
- gap 40%p까지 완화해도 double-gate survivor 증가 0
- Stress-only 증가분 2~3개 중 반대 구간 robust 개체 0
- OOS-only 증가분 4개 중 stress robust 개체 0
- 최소표본 완화 증가분은 thin-sample 개체
- 기존 survivor AMSC·AVAV는 replay에서 붕괴

즉 compact 5일 path pattern으로 2거래일 내 +3%를 선별하는 방식은 현재 evidence상 전체 rulebook에 공통 적용할 범용 도구가 아니다.

특정 개체에서만 반복 가능한 패턴이 존재하는 **개체 특화 보조 필터**에 가깝다.

## 10. 권고

- 현재 double gate 유지
- Gap 허용폭 완화 불필요
- 최소표본 완화 금지
- 단일 OOS gate 사용 금지
- AEVA만 frozen shadow 연구 대상으로 유지
- DDOG는 추가 prospective 표본이 3개 이상 누적된 뒤 재평가
- 시스템 전체 적용을 원하면 개체별 compact filter가 아니라 pooled/hierarchical model 또는 feature 확장이 별도 연구 대상

BLOCK 승격 근거는 없다.

## 산출물

- `death_cause_breakdown.csv`
- `near_miss_candidates.csv`
- `sensitivity_scenarios.csv`
- `readout.md`
- `immutability_check.csv`
- `manifest.sha256`
