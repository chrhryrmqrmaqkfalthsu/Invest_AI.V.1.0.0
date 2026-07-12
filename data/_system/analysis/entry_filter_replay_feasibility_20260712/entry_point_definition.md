# 진입 필터 학습용 replay 시점 정의

## 채택 정의

진입 필터의 학습 표본은 다음으로 정의한다.

```text
각 rulebook × 각 거래일 T에 대해
과거 데이터 df[:T+1]만 사용해 evaluate_signal() 실행
→ should_buy=True인 모든 날짜를 학습 후보 시점으로 기록
```

즉 실제 포지션 보유 여부, 자본 한도, 종목 중복, max_positions, 순위 탈락과 무관하게 **개체 자체가 진입 의사를 낸 모든 시점**을 수집한다.

## 왜 실제 체결 시점이 아닌가

필터의 목적은:

```text
원개체 should_buy
→ 5일 진입필터
→ 통과 여부 결정
```

이다. 따라서 포트폴리오·보유상태 때문에 나중에 제거된 신호도 필터 학습 대상이어야 한다.

실제 체결 또는 backtest entry만 사용하면 다음 편향이 남는다.

- 이미 포지션 보유 중인 연속 신호 누락
- max_positions로 밀린 신호 누락
- 같은 ticker의 다른 entity와 충돌한 신호 누락
- 진입 후 긴 보유기간에 따라 표본 수가 달라지는 holding-period 편향

## 기존 엔진에서의 구현

`engine/central/signal_collector.py::signal_for_date()`는:

1. ticker OHLCV를 로드하고
2. 평가일 index를 찾고
3. rulebook을 복원하고
4. `df.iloc[:idx+1]`만 `evaluate_signal()`에 전달하고
5. `should_buy`, score, threshold, components, price를 반환한다.

포지션 ledger를 참조하지 않으므로 연속·보유중 신호도 모두 재생된다.

`engine/central/backtester.py`는 그 이후 allocation·ledger·max_positions를 적용한다. 따라서 필터 학습용 signal universe는 backtester의 실제 entry 결과가 아니라 **SignalCollector 출력 직후**에서 채집해야 한다.

## 5일 feature 경계

신호일을 D0라고 할 때 필터 feature는:

```text
D-6 Close
D-5~D-1 High/Low/Close
```

만 사용한다.

D0의 Open/High/Low/Close/Volume은 사용하지 않는다. Replay 신호 생성은 D0 완료봉을 보고 should_buy를 계산하지만, 후단 5일 필터는 명시적으로 D-1까지만 slice한다.

이 정의는 “원개체가 D0 신호를 냈을 때 그 직전 상태만으로 추가 선별한다”는 실험 구조다.

## Label

각 replay should_buy 날짜 D0에 대해:

```text
signal_price = Close[D0]
future_max_high = max(High[D+1], High[D+2])
label = 1 if future_max_high / signal_price - 1 >= 0.03 else 0
```

D+1·D+2가 모두 저장돼 있지 않은 최신 신호는 제외한다.

## 재현성 요구사항

정확한 원실험 parity를 위해서는 다음을 고정해야 한다.

- 당시 rulebook JSON과 hash
- 당시 OHLCV snapshot 또는 동일 corporate-action 처리
- 당시 indicator 계산 버전
- market_history의 lag 규칙
- ticker sentiment/news topic 원본
- Event on/off 정책
- sector/vix fallback
- direct event 가중치

현재 `SignalCollector`는 이 구조를 지원하지만, 과거 artifact가 일부 누락되면 동일 날짜 should_buy를 완전히 재현하지 못한다.

## 최종 정의

학습 시점:

### **모든 독립 should_buy 발생 시점**

실행 가능 진입 성과를 검증할 때는 별도로:

### **포트폴리오·보유상태·allocation 통과 시점**

을 계산한다.

두 표본을 혼합하지 않는다.
