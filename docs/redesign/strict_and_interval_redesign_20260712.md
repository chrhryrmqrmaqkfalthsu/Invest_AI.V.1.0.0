# Strict-AND Interval GA 재설계 명세

- 작성일: 2026-07-12
- 대상: 정식 Stage 2/3 스윙 학습 파이프라인
- 상태: Phase 1·2 구현 기준 명세
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
- 정규화 domain `[0, 1]` 안에서 생성
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

포지션 크기는 interval 내부 중심 여유도를 0~1 연속값으로 계산한 `signal_quality`를 사용한다. 시장·섹터·VIX 보정은 진입 boolean을 뒤집지 않고 position sizing quality에만 반영한다.

### 청산

1. `stop_loss_atr` 손절 안전장치를 유지한다.
2. 보유 중에도 진입 interval을 매 거래일 재평가한다.
3. 하나 이상의 필수 interval이 깨지면 다음 거래 가능 시점에 신호 청산한다.
4. `max_holding_days=7`로 고정하고 학습 대상에서 제외한다.
5. `take_profit_atr`, `trailing_atr` 중심 익절·트레일링은 사용하지 않는다.

### 신호 측정

보유 중에도 매 거래일 strict-AND를 측정한다. 진입은 D+1 open이고, 보유 중 interval 이탈은 다음 거래일 open 청산을 기본으로 한다. 기간 종료 시에는 마지막 사용 가능한 close로 mark-to-market한다.

### 이벤트 중복

문제 없음이 확인됐다. 정식 Stage 2/3는 `use_llm_events=False`이며 이번 재설계 대상이 아니다.

### Feature lag

정식 context lookup의 `FEATURE_LAG_DAYS`는 계속 1이다. 시장·뉴스·이벤트는 D-1 이하 데이터를 사용한다.

이번 strict-AND 기술 feature는 재설계 실험 전용으로 **D-5 완료봉**을 사용한다.

```text
signal date D
strict-AND 기술 feature row: D-5 거래행
시장·뉴스 context: D-1 이하
진입: D+1 open
```

이는 정식 원본 `engine/core/feature_lag.py`를 변경하지 않고 workspace evaluator의 `STRICT_INTERVAL_FEATURE_LAG_DAYS=5`로 분리한다.

## Fitness 목표 — 확정

목표는 **보유일 대비 수익, 즉 자본 효율성**이다.

각 거래에 대해 다음을 계산한다.

```text
daily_efficiency_i = trade_return_pct_i / max(holding_days_i, 1)
fitness = mean(daily_efficiency_i)
```

예시:

```text
거래 A: +6.0%, 보유 3일 → +2.0%/일
거래 B: -2.0%, 보유 2일 → -1.0%/일
개체 fitness = (2.0 + -1.0) / 2 = +0.5%/보유일
```

거래가 없으면 fitness는 0으로 둔다. `holding_days=0` 기록은 분모를 1로 보정한다.

## MDD 처리 규칙 — 미결

깊은 MDD를 두 유형으로 구분한다.

### 유형 1 — 사고

단일일 급락·갭처럼 개체가 사전에 청산 신호로 대응하기 어려운 손실이다. 이 유형은 MDD 페널티에서 제외하는 방향을 검토한다.

### 유형 2 — 방치

다일 누적 하락 중에도 strict-AND 청산 신호를 탐지하지 못해 손실을 방치한 경우다. 이 유형은 도태 대상으로 본다.

### 필요한 진단 로그

- 보유일별 close-to-close 수익률
- 보유일별 누적 손익
- 최악 단일일 손실
- 전체 음의 수익 절대합 대비 최악 단일일 손실 비중
- MDD 구간 시작일·종료일·지속 거래일 수
- 각 보유일 strict-AND 재평가 결과
- interval 이탈 최초 발생일
- 실제 청산 신호 발생 여부와 청산 예정일
- gap/open 손실과 장중 stop 손실 구분
- exit reason

유형 1/2를 나누는 임계값은 **실측 후 결정**한다. 임의 숫자는 Phase 1·2 코드에 넣지 않는다.

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
- MDD 사고/방치 분류 임계값

## 1차 strict-AND feature

1차 구현은 다음 5개 연속 feature를 `[0,1]`로 정규화해 사용한다.

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

Boolean golden-cross event 자체를 필수 AND로 사용하지 않고, 연속 MACD histogram 상태를 interval로 학습한다. 이는 이벤트 희소성으로 coverage가 즉시 0에 수렴하는 것을 피하기 위한 1차 설계다.

## 이식 순서

1. `rulebook.py` interval schema와 legacy migration
2. `genetic.py` interval 초기화·mutation·pair-preserving crossover·validator
3. `evaluator.py` strict-AND 진입 판정
4. boolean 진입과 연속 position-sizing 계약 분리
5. `execution_mode_backtest.py` 보유 중 일별 재평가와 7일 상한
6. schema·strict-AND smoke test
7. 인공 CE·BOIL·편측·도달불가 테스트
8. AAP·POWI 소규모 train/stress/OOS 비교
9. Stage 3 interval schema 보존 확인
10. live/replay는 OOS 확인 후 별도 이식

## 핵심 리스크

Mechanics는 검증됐지만 수익성은 미검증이다.

- strict-AND가 coverage를 죽일 수 있다.
- D-5 feature lag가 신호 반응성을 떨어뜨릴 수 있다.
- legacy scalar Rulebook과 신규 interval Rulebook schema 충돌 가능성이 있다.
- 그룹 mechanics와 별개로 feature grouping 성능은 미검증이다.

1차 구현은 단순 strict-AND interval부터 시작한다. Coverage 측정 후 필요할 때만 검증된 floored group-threshold mechanics를 추가한다.

## Phase 0·1·2 작업 원칙

- 원본 정식·라이브 코드는 수정하지 않는다.
- 실제 수정은 `scripts/research/redesign_workspace_20260712/` 아래 복제본에서만 수행한다.
- Phase 3 전 structural gate에서 편측·NaN·domain 밖 interval이 0건이어야 한다.
