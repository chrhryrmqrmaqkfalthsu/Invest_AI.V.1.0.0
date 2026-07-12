# 연속/보유중 신호 학습 포함 여부 종합 판정

## 최종 판정

| 층 | 판정 | 핵심 근거 |
|---|---|---|
| 2층 replay 필터 | **CLEAN** | 날짜별 position-independent replay에 연속 클러스터와 실제 로그 보유구간 신호가 존재 |
| 1층 Stage2/Stage3 개체 | **HAS_GAP** | 공통 백테스트가 진입 후 청산+cooldown 다음 날짜로 점프해 보유중 should_buy를 평가하지 않음 |

**결론:** 2층 필터는 CLEAN이지만 1층 개체는 HAS_GAP이다. 필터 표본 재정의보다 개체의 position-independent 재학습 또는 연속신호 보조목표 도입을 우선 검토해야 한다.

## Step 1 — replay universe 실측

| 항목 | 값 |
|---|---:|
| replay should_buy 행 | 18,245 |
| 로그 기반 행 | 3,430 |
| 동일 candidate/date 교집합 | 1,258 |
| replay에만 존재 | 16,987 |
| 로그에만 존재 | 2,172 |
| 단순 행수 차이 | 14,815 |
| strict superset 여부 | 아니오 |

`replay에만 존재`하는 새 신호의 정확한 집합 수는 **16,987개**다. 단순 행수 차이 14,815개와 다른 이유는 현재 evaluator/context 재평가에서 과거 로그 신호 2,172개가 재현되지 않았기 때문이다.

### 연속 클러스터

| 항목 | 값 |
|---|---:|
| 전체 클러스터 | 5,926 |
| 1일 클러스터 | 3,311개 / 3,311신호 |
| 2일 클러스터 | 868개 / 1,736신호 |
| 3일+ 클러스터 | 1,747개 / 13,198신호 |
| 2일 이상 클러스터 | 2,615개 |
| 2일 이상 클러스터 소속 신호 | 14,934개 |
| 최장 연속 | 81 거래일 |

### 진입 로그 사이·보유 중 신호

| 항목 | 값 |
|---|---:|
| replay-only 상세행 | 16,987 |
| 실제 로그 보유구간 내부 | 9,184 |
| 연속 로그 진입일 사이 | 16,147 |
| 같은 연속 클러스터에서 로그 진입 뒤 후속 신호 | 3,584 |
| 위 증거의 합집합 | 16,183 |

따라서 Step 1 판정은 **replay가 연속/보유중 신호를 INCLUDED**다.

## Step 2 — replay 포지션 의존성

`SignalCollector`는 날짜별 순수 evaluator이며 포지션 상태를 받지 않는다. replay universe 생성 루프도 전체 거래일을 순회해 `snap.should_buy`만 검사한다. 상세 코드 위치는 `replay_position_dependency.md`에 기록했다.

## Step 3 — Stage2/Stage3 원 학습 표본 정의

두 단계 모두 저장된 거래행을 supervised sample로 직접 학습하는 구조는 아니다. rulebook을 **상태ful 단일포지션 백테스트**에 넣고 체결 거래 성과를 GA fitness로 사용한다.

- flat 상태: 매 거래일 `evaluate_signal()` 실행.
- `should_buy=True`: 거래를 만들고 청산을 시뮬레이션.
- 보유 상태: `engine/learning/execution_mode_backtest.py:337-340`에서 청산 인덱스 뒤 `cooldown_days=1`까지 점프하므로 중간 should_buy를 평가하지 않는다.
- 저장: 실제 거래만 `trades.jsonl`/`rl_replay_trades.jsonl`에 남고 보유중·cooldown 신호 원 표본은 저장하지 않는다.

현재 57개 개체(Stage2 8, Stage3 49) 모두 코드로 확인 가능하며 판정은 **HAS_GAP**이다. 이는 “미리 저장된 진입 로그만 다시 학습했다”는 뜻은 아니지만, 요청한 관점에서 보유중 연속 should_buy가 개체 fitness의 독립 표본으로 들어가지 않았다는 뜻이다.

## 해석과 우선순위

2층 필터 표본은 position gap을 메웠으므로 이 문제만을 이유로 replay 필터부터 다시 만들 필요는 없다. 1층 개체는 holding/cooldown 신호를 목적함수에서 보지 않으므로, 반복 지속성·신호 밀도·연속성까지 학습하려면 position-independent 전일자 signal objective 또는 auxiliary loss를 추가한 개체 재학습을 검토해야 한다.

## 제한사항

현재 replay는 과거 실행 당시 evaluator/context의 완전 복원이 아니다. 로그 전용 2,172개가 이 drift를 보여준다. 따라서 2층 CLEAN은 **포지션 상태 때문에 날짜가 스킵되는가**에 대한 판정이며 역사적 신호 완전 재현성 판정은 아니다.
