# Strict-AND Interval GA 재설계 명세

- 작성일: 2026-07-12
- 대상: 정식 Stage 2/3 스윙 학습 파이프라인
- 상태: Phase 0 확정 명세
- 원칙: 정식·라이브 원본은 수정하지 않고 작업용 복제본에서만 구현한다.

## 배경

현재 정식 evaluator는 기술·뉴스·이벤트 component를 합산한 뒤 하나의 `signal_threshold`와 비교한다.

```text
raw_score = sum(components) + event_adjustment + optional_crash_bonus
final_score = raw_score × market_adjustment
should_buy = final_score >= signal_threshold
```

이 구조는 CE·BOIL형 실패를 생성 단계에서 막지 못한다.

- 합산 상쇄: 한 조건 미달을 다른 component가 보상한다.
- 편측 경계: 하한만 또는 상한만 있는 열린 규칙이 남는다.
- 도달불가 경계: 실제 학습 데이터에서 닿을 수 없는 scalar도 평가된다.
- 희소 조건 통과: 일부 지표만 강해도 총점이 threshold를 넘는다.

목표는 사후 gate 추가가 아니라 유전자 표현과 신호 계산 자체를 바꿔 위 실패를 원천 차단하는 것이다.

## 확정된 설계 결정

### 진입 신호

점수 합산 진입을 폐지하고 strict-AND interval로 전환한다. 모든 선택 feature가 각자의 유한한 `low/high` interval 안에 있어야 통과한다.

```python
finite = np.isfinite(x_norm)
inside = (x_norm >= low) & (x_norm <= high)
entry_pass = np.all(finite & inside, axis=1)
```

### 유전자 표현

각 feature를 단일 scalar가 아닌 `low/high` 쌍으로 표현한다.

- 상한·하한 필수, 편측 금지
- 도달가능 범위 안에서 생성
- `low < high`
- 지나치게 좁은 interval 금지
- near-full interval 남발 제한

거부 사유:

```text
open_or_nonfinite_bound
outside_normalized_domain
not_bilateral
min_width_violation
too_many_near_full_ranges
```

### 매수 판정과 포지션 크기

결정 1은 A다.

```text
진입 판정: strict-AND boolean
포지션 크기: 별도 연속 로직
```

기존 `signal_score / signal_threshold` 사이징을 유지할지, interval 중심거리·margin·별도 risk score로 대체할지는 이식 중 확정한다.

### 청산

1. `stop_loss_atr` 손절 안전장치를 유지한다.
2. 보유 중에도 진입 interval을 매 거래일 재평가한다.
3. 하나 이상의 필수 interval이 깨지면 청산한다.
4. `max_holding_days=7`로 고정하고 학습 대상에서 제외한다.
5. `take_profit_atr`, `trailing_atr` 중심 익절·트레일링은 신호 기반 청산으로 대체해 제거하는 방향이다.

### 신호 측정

보유 중에도 매 거래일 strict-AND를 측정한다. 현재 execution backtest는 진입 후 청산일까지 index를 건너뛰므로 후속 구현에서 보유기간 중 재평가가 가능하도록 바꿔야 한다.

### 이벤트 중복

문제 없음이 확인됐다. 정식 Stage 2/3는 `use_llm_events=False`이며 이번 재설계 대상이 아니다.

### FEATURE_LAG_DAYS

현재 `FEATURE_LAG_DAYS=1`이며 시장·뉴스·이벤트 context용이다. 기술지표는 D 완료봉, 진입은 D+1 open이다. D-5가 아니며 pilot 5일 feature는 별개다. 기술 feature lag 변경 여부는 미결이다.

## 이식 가능한 검증 완료 부품

연구본 `scripts/research/rolling_rediscovery/upstream_snapshot/`에서 다음이 구현·구조 검증됐다.

- `IntervalIndividual`의 feature별 `low/high`
- strict-AND mask
- pair-preserving crossover
- 경계 5종 거부
- thin-sample gate
- floored group-threshold mechanics

Floored group-threshold 규칙:

```text
최소 threshold = 2
최대 threshold = group_size - 1
1/N 무력화 방지
N/N 몰빵 방지
초기화·mutation·validation 전 과정에서 유지
```

## 미검증·보류

- 4그룹 feature 설계: mechanics는 작동했지만 survivor 0
- 12개 전체 strict-AND: OOS coverage 약 0.20%까지 소멸 위험
- `q<45` entry-quality hard gate
- 변동성 predictor same-day AND
- 5일 path production threshold

## 이식 순서

1. `rulebook.py` interval schema와 legacy migration
2. `genetic.py` interval 초기화·mutation·pair-preserving crossover·validator
3. `evaluator.py` strict-AND 진입 판정
4. boolean 진입과 연속 position-sizing 계약 분리
5. metadata hash·fitness cache·직렬화 갱신
6. schema·strict-AND smoke test
7. 인공 CE·BOIL·편측·도달불가 테스트
8. AAP·POWI 소규모 train/stress/OOS 비교
9. Stage 3 interval schema 보존 확인
10. live/replay는 OOS 확인 후 별도 이식

## 핵심 리스크

Mechanics는 검증됐지만 수익성은 미검증이다.

- strict-AND가 coverage를 죽일 수 있다.
- 연속 score 제거 시 position sizing·로그·라이브 인터페이스가 깨질 수 있다.
- legacy scalar Rulebook과 신규 interval Rulebook schema 충돌 가능성이 있다.
- 그룹 mechanics와 별개로 feature grouping 성능은 미검증이다.

1차 구현은 단순 strict-AND interval부터 시작한다. Coverage 측정 후 필요할 때만 검증된 floored group-threshold mechanics를 추가한다.

## Phase 0 작업 원칙

- 원본 정식·라이브 코드는 수정하지 않는다.
- 오늘 실험 산출물은 tar와 manifest 검증 후 archive 디렉터리로 이동한다.
- 실제 수정은 `scripts/research/redesign_workspace_20260712/` 아래 복제본에서만 수행한다.
- 원본과 복제본 SHA-256 일치를 Phase 0 종료 조건으로 삼는다.
