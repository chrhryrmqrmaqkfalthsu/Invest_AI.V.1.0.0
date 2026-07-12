# 개체 replay 기반 진입 필터 학습 — 실현성 확인

- 조사일: 2026-07-12
- 라이브 변경: 0
- 최종 판정: **FEASIBLE_REUSE — 단, historical parity 복원 선행 필요**

## 결론

기존 엔진을 재사용해 각 rulebook을 과거 모든 거래일에 직접 평가하고, 포지션 상태와 무관한 모든 `should_buy=True` 시점을 재생성하는 것은 가능하다.

핵심 재사용 경로:

- `engine/central/signal_collector.py::SignalCollector.signal_for_date`
- `engine/central/signal_collector.py::SignalCollector.collect`
- `engine/central/backtester.py::build_signal_cache`

이 경로는 날짜별로 `evaluate_signal(rulebook, df.iloc[:idx+1], ...)`을 호출하며, 포지션 ledger를 통과하기 전 raw entity signal을 반환한다. 따라서 보유 중 연속 신호와 실제 배분에서 탈락한 신호까지 수집할 수 있다.

다만 현재 코드와 현재 market/news context로 과거를 재생했을 때 저장 entry signal과의 일치율이 낮았다. 따라서 곧바로 학습에 쓰기보다 당시 학습 코드·event 정책·market context를 version pin해 parity를 먼저 복원해야 한다.

## 기존 replay 엔진 판정

### 재사용 가능

`SignalCollector`는 요구 목적에 가장 적합하다.

- 입력: EntityRecord, rulebook, ticker OHLCV, 시장·섹터·VIX·sentiment context
- 기간: 임의 start/end 날짜 loop
- 출력: date, should_buy, score, threshold, strength, components, price
- 포지션 영향: 없음
- 연속 신호: 재현 가능
- 보유 중 신호: 재현 가능
- lookahead discipline: `df.iloc[:idx+1]`

`build_signal_cache()`는 이를 전체 날짜×개체에 대해 한 번에 precompute한다.

### 단독 사용 부적합

`engine/portfolio/daily_signal_replay.py`는 이미 저장된 lot의 entry~exit 구간만 replay한다. full history의 모든 should_buy를 찾는 엔진이 아니라 entry parity와 보유기간 signal decay 진단용이다.

Stage2/Stage3 trade backtester도 포지션 상태로 신규 진입을 억제하므로 trade 출력만 사용하면 현재와 같은 진입 편향이 유지된다.

## 진입 시점 정의

필터 학습용 표본은 다음으로 확정한다.

```text
원래 rulebook이 해당 날짜에 should_buy=True
```

실제 주문 가능 여부, 포지션 보유, 자본·슬롯·순위는 포함하지 않는다.

이유는 필터 위치가:

```text
rulebook should_buy → 진입필터 → 실제 진입규칙
```

이기 때문이다.

## 현 방식 대비 표본 차이

현재 적격 rulebook 57개를 2021-01-01~2026-07-02의 1,368개 거래일에 replay했다.

- 저장 entry signal: 3,430개
- replay should_buy: 18,245개
- raw 배수: 5.32배
- replay-only: 16,987개
- exact overlap: 1,258개
- logged-only: 2,172개

실행 시간은 약 94.24초였다.

AMSC:

- 로그 72개
- replay 621개

AVAV:

- 로그 42개
- replay 718개

CRS:

- 로그 70개
- replay 155개

표본 확대 가능성은 매우 크다.

## 중요한 parity 문제

저장 entry signal 3,430개 중 현재 replay가 같은 날짜를 재현한 것은 1,258개, 36.68%뿐이다.

이는 다음 중 하나 이상이 원인일 수 있다.

- 학습 당시와 현재 evaluator 코드 차이
- direct Event 정책 차이
- market/sector/VIX history revision 또는 결측 fallback 차이
- ticker sentiment·topic feature 보존 차이
- Stage2와 Stage3 원본 context 구축 방식 차이
- 현재 cache와 당시 OHLCV/indicator 계산 버전 차이

따라서 16,987개 replay-only 신호를 전부 “보유 중 누락 신호”라고 볼 수 없다. 현재 값은 상한 추정이다.

## 선행 sanity gate

실제 필터 재학습 전 다음을 통과해야 한다.

1. 각 저장 `entry_signal_date`에서 rulebook hash와 원본 snapshot 로드
2. 당시 evaluator/event/market context version 고정
3. 저장 `entry_signal_score`·threshold와 replay 값 비교
4. entry-date should_buy 재현율 최소 99%
5. score/threshold ratio 허용오차 통과
6. 불일치 개체 제외 또는 원인 복원

`daily_signal_replay.py`에 이미 entry strength mismatch gate가 있으므로 이 로직을 full-history collector 앞단 sanity check로 재사용할 수 있다.

## 신규 구축 범위

완전 신규 backtester는 필요하지 않다. 필요한 것은 연구용 orchestration layer다.

1. 현재 적격 rulebook 또는 고정된 historical universe 로드
2. rulebook별 원본 evaluator/context version 선택
3. `SignalCollector`로 모든 날짜 raw should_buy 생성
4. 신호일 D-1까지의 5일 feature 생성
5. D+1/D+2 high로 +3% label 부착
6. train-only 개체별 GA 학습
7. stress·OOS frozen 이중 gate
8. 실제 entry 로그와 raw replay의 표본 차이·연속 run length 기록

추가 권장 출력:

- `all_should_buy_signals.csv`
- `entry_parity_report.csv`
- `consecutive_signal_runs.csv`
- `held_period_signal_count.csv`
- `replay_filter_survivors.jsonl`

## 실현성 판정

### **FEASIBLE_REUSE**

이유:

- 모든 날짜의 raw should_buy를 생성하는 엔진 존재
- 필요한 OHLCV·5일 feature 계산 가능
- position-independent signal 수집 가능
- 57개×1,368일 replay가 약 94초로 실행 가능
- train/stress/OOS 분리도 기존 구조로 가능

단, 현재 상태에서 바로 재학습하면 historical context drift가 섞인다.

따라서 정확한 실행 순서는:

```text
historical parity 복원
→ all-should-buy ledger 생성
→ 5일/+3% label 부착
→ GA 재학습
→ stress/OOS 검증
```

이다.
