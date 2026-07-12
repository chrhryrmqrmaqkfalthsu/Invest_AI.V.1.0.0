# 개체 replay 기반 진입 필터 학습 — 실현성 확인

- 조사일: 2026-07-12
- 판정: **FEASIBLE_REUSE**
- 단서: 정확한 원실험 parity를 위해 replay context 보강 필요
- 라이브 코드·설정·daemon 변경: 0

## 1. 최종 결론

기존 엔진을 재사용해 각 rulebook이 과거 모든 거래일에 내린 `should_buy=True`를 포지션 상태와 무관하게 재생성할 수 있다.

핵심 재사용 경로:

```text
EntityRecord(rulebook, ticker)
→ SignalCollector.signal_for_date()
→ evaluate_signal(rulebook, df.iloc[:T+1], context)
→ SignalSnapshot.should_buy
```

`SignalCollector`는 ledger나 보유상태를 보지 않는다. 따라서:

- 연속 신호
- 이미 보유 중인 날짜의 반복 신호
- 포트폴리오 한도 때문에 실제 진입하지 못한 신호
- 동일 ticker/entity 충돌로 탈락한 신호

까지 수집 가능하다.

따라서 신규 replay 엔진을 처음부터 만들 필요는 없다.

## 2. 기존 replay 엔진

### SignalCollector

파일:

`engine/central/signal_collector.py`

핵심 위치:

- `SignalSnapshot`: lines 20-35
- cache-only OHLCV 로더: lines 37-83
- market/ticker context: lines 85-127
- `signal_for_date()`: lines 212-261
- 모든 should-buy 수집: lines 263-269

각 날짜에 대해:

```text
evaluate_signal(
  rulebook,
  df.iloc[:idx+1],
  market_score,
  sector_score,
  vix_level,
  news_sentiment,
  event_flags,
  topic_features,
)
```

를 실행한다.

### Daily signal replay

파일:

`engine/portfolio/daily_signal_replay.py`

이 모듈은 이미 다음 안전 원칙을 명시한다.

- decision date를 canonical replay 축으로 사용
- `df.iloc[:T+1]`만 전달
- Stage2 parity 기본값은 Event OFF
- Event-enabled replay는 diagnostic 전용
- entry score·threshold parity 검사

다만 기본 artifact 경로가 과거 `honest_full_6174` Stage2에 고정돼 있어 현재 Stage2/Stage3 rulebook 전체에 쓰려면 입력 경로 parameterization이 필요하다.

### Central backtester

파일:

`engine/central/backtester.py`

이 경로는 SignalCollector 출력 뒤에 다음을 적용한다.

- open position ledger
- max positions
- ticker/entity exposure
- allocation ranking
- 실제 fill/exit

필터 학습용 “모든 should_buy” 표본은 backtester의 entry 결과가 아니라 SignalCollector 직후에서 채집해야 한다.

## 3. 진입 시점 정의

채택 정의:

### **원개체가 독립적으로 should_buy=True를 낸 모든 거래일**

이 정의가 적합한 이유:

- 필터는 원개체 signal 뒤에 직렬로 붙음
- 실제 포지션 상태는 필터 자체의 예측 품질과 무관함
- 보유기간이 긴 개체가 적은 표본만 남는 편향을 제거함
- consecutive signal을 모두 포함함

실제 체결 가능 entry는 후속 포트폴리오 검증에서 별도 산출해야 한다.

## 4. 5일 feature와 누수

각 replay signal date를 D0라고 할 때:

```text
원개체 signal 생성: df[:D0+1]
필터 feature: D-6 Close + D-5~D-1 High/Low/Close
label: D+1, D+2 High
```

필터 feature는 D0 candle을 사용하지 않는다.

따라서 기존 `five_day_feature_spec_20260712`의 compact path feature를 그대로 적용할 수 있다.

- 5개 일별 수익률
- 5일 누적수익률
- 상승·하락일 수
- 최근 상승→하락
- 5일 고점·저점
- 고점 경과일
- close_pos5
- pullback_from_high5_pct
- 최대 단일 상승일
- fade_after_surge_score

D0 gap, ETF gap, flow/orderbook은 제외 가능하다.

## 5. 실제 표본 차이 측정

현재 적격 rulebook 57개를 대상으로 2021-01-01~2026-07-02를 직접 replay했다.

- 평가 entity-session: 77,937
- 독립 should_buy replay: 18,245
- 기존 로그 entry signal: 3,430
- replay / log 비율: **5.319배**
- replay에만 존재: 16,987
- 양쪽 겹침: 1,258
- 로그에만 존재: 2,172

단순히 replay 18,245 - log 3,430 = 추가 연속신호라고 해석하면 안 된다.

겹침률:

- 기존 로그 기준: 36.68%
- replay 기준: 6.90%

즉 큰 차이에는 다음이 함께 포함된다.

1. 보유중·연속 should_buy 추가
2. 원 Stage2/Stage3 실험과 현재 SignalCollector의 context 차이
3. Event/news/topic 데이터 보존 차이
4. market history·sector·VIX fallback 차이
5. indicator 재계산 버전 차이
6. 원실험 당시 code version과 현재 evaluator 차이
7. current eligible rulebook을 과거 전체 기간에 고정 적용하는 생존자 선택 편향

따라서 18,245개는 **실현 가능한 독립 replay 표본 규모의 상한 추정치**이며, 원실험의 정확한 should_buy ledger로 확정할 수는 없다.

## 6. 왜 기존 로그와 parity가 낮은가

현재 진단 replay는:

- 현재 저장된 eligible rulebook JSON
- cache-only OHLCV
- 현재 indicator 계산
- 현재 `evaluate_signal()`
- `use_llm_events=False`
- 현재 market/ticker context loader

를 사용했다.

반면 `rl_replay_trades.jsonl`은 각 Stage2/Stage3 학습 당시의:

- 당시 evaluator 코드
- 당시 Event/news 정책
- 당시 market context
- 당시 exit/position loop
- 당시 artifact snapshot

에서 생성됐다.

특히 실제 trade 로그는 `should_buy=True`여도 이미 보유 중이면 신규 row를 기록하지 않는다.

따라서 낮은 overlap은 replay 엔진 부재가 아니라 **원실험 계약을 현재 그대로 복원하지 않은 상태**를 의미한다.

## 7. 재사용 가능한 범위

### 그대로 재사용 가능

- 날짜별 OHLCV slice
- indicator 재계산
- Rulebook 복원
- evaluate_signal 호출
- should_buy/score/threshold/component 기록
- position-independent signal collection
- D-1 feature 생성
- D+1/D+2 label 연결

### 보강 필요

- 각 Stage2/Stage3 artifact가 생성된 evaluator commit 고정
- artifact별 Event on/off 설정 복원
- 당시 market/news/topic cache 지정
- raw OHLCV snapshot pinning
- corporate-action 처리 고정
- replay parity report 생성
- 로그와 겹치지 않는 날짜의 원인 코드 분해

## 8. 권장 학습 파이프라인

```text
1. Rulebook artifact와 생성 commit 고정
2. Ticker OHLCV snapshot 고정
3. Market/news/event input contract 고정
4. 모든 거래일에 evaluate_signal replay
5. should_buy=True 날짜 전부 signal ledger 저장
6. 각 날짜에 D-5~D-1 compact feature 부착
7. D+1/D+2 high로 +3% label 생성
8. entity별 train-only GA 학습
9. stress AND OOS survivor gate
10. 별도로 position-aware executable-entry backtest
11. Shadow prospective 검증
```

필수 신규 artifact:

- `all_should_buy_signals.jsonl`
- `replay_input_manifest.json`
- `parity_vs_original_trades.csv`
- `feature_label_dataset.csv`
- `survivors.jsonl`

## 9. 실현 가능성 판정

### **FEASIBLE_REUSE**

기존 `SignalCollector`와 `daily_signal_replay`를 재사용하면 모든 독립 should_buy 시점을 수집할 수 있다.

신규 구축이 필요한 것은 replay 엔진 자체가 아니라:

- 현재 Stage2/Stage3 artifact 입력 adapter
- artifact별 historical context 고정
- parity 감사
- all-signal ledger writer

다.

정확한 원실험 parity 없이도 새로운 frozen 연구계약으로 replay 학습은 가능하다. 다만 그 경우 결과는 “원로그 복원”이 아니라 **현재 rulebook을 현재 evaluator 계약으로 과거에 재평가한 신규 실험**으로 명시해야 한다.

## 10. 결론

현 로그 기반 3,430개보다 훨씬 큰 signal universe를 만들 수 있다. 실제 진단에서는 18,245개 should_buy가 재생됐다.

그러나 로그와의 낮은 overlap 때문에 즉시 이 표본으로 GA를 재학습하는 것보다 먼저 다음을 해야 한다.

1. replay contract 고정
2. 원로그 parity 원인 분해
3. 허용 가능한 parity 기준 설정
4. 그 후 5일→2일+3% GA 재학습

실현 가능성은 높으며, 신규 엔진보다는 기존 엔진 재사용·보강이 정확하고 안정적이다.
