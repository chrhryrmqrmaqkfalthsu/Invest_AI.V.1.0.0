# 개체 replay 기반 진입 시점 정의

## 채택 정의

필터 학습용 진입 시점은 **실제 주문이 체결된 시점이 아니라, 개체가 독립적으로 `should_buy=True`를 낸 모든 거래일**로 정의한다.

```text
entity_should_buy(ticker, date) == True
```

이 정의는 포지션 보유 여부, 포트폴리오 슬롯, 동일 종목 중복 보유, 현금 부족, 순위 탈락과 무관하다.

## 채택 근거

진입 필터의 위치는 다음과 같다.

```text
기존 rulebook should_buy
→ 5일 진입필터
→ 실제 진입·배분·포지션 규칙
```

따라서 학습 표본은 필터가 실제로 마주칠 수 있는 모든 `should_buy` 발생일이어야 한다. 실제 체결 또는 저장 trade만 사용하면 다음 편향이 생긴다.

- 이미 보유 중인 기간의 연속 `should_buy` 누락
- 포트폴리오 슬롯·현금·동일 종목 제한으로 탈락한 신호 누락
- exit 규칙과 보유기간에 의해 신호 표본 밀도가 달라지는 진입 편향
- 강한 연속 신호가 한 번의 entry로 축약되는 표본 편향

## 구분해야 할 세 단계

### 1. Raw entity signal

```text
should_buy = final_score >= rulebook.signal_threshold
```

필터 학습의 권장 표본이다.

### 2. Filter-confirmed signal

```text
should_buy
AND entry_filter_pass
```

향후 shadow 또는 BLOCK 판단 대상이다.

### 3. Executable entry

```text
filter-confirmed signal
AND portfolio/position/allocation constraints
```

실거래·포트폴리오 성능 검증에는 필요하지만 진입필터 label 학습 표본으로 쓰면 안 된다.

## 기존 엔진 재사용 방식

`engine/central/signal_collector.py::SignalCollector.signal_for_date()`는:

1. 해당 ticker의 과거 OHLCV를 읽고
2. 대상 날짜 index를 찾고
3. `df.iloc[:idx+1]`만 `evaluate_signal()`에 전달하고
4. `SignalSnapshot.should_buy`를 반환한다.

`collect()`와 `engine/central/backtester.py::build_signal_cache()`는 날짜별 모든 entity의 `should_buy=True`를 모을 수 있다.

이 경로는 포지션 ledger를 사용하지 않으므로 보유 중 연속 신호도 재현 가능하다.

## 5일 feature 및 label 결합

신호일을 D0라고 할 때:

```text
raw should_buy 판단: D0까지의 원래 rulebook 입력
진입필터 feature: D-6 Close + D-5~D-1 완료봉만
label: max(High[D+1], High[D+2]) / signal_price - 1 >= 3%
```

사용자 지정 필터는 D0 gap·D0 candle을 사용하지 않으므로, raw rulebook 신호가 D0 정보로 생성됐더라도 필터 feature 자체는 D-1에서 절단한다.

## 신호가격 선택

재현 pipeline에서는 두 가격을 모두 저장해야 한다.

- `signal_close`: D0 Close
- `next_open_fill`: D+1 Open

현재 연구 label과의 연속성을 위해 기본 label 기준은 D0 Close로 유지하되, 실거래 적용 타당성 비교를 위해 D+1 Open 기준 label도 보조 컬럼으로 생성하는 것이 바람직하다.

## 누수 방지 요건

- 일별 replay는 `df.iloc[:idx+1]`만 평가기에 전달
- 5일 필터 feature는 별도로 `df.iloc[idx-6:idx]` 사용
- train에서만 quantile·gene 학습
- stress/OOS frozen 검증
- D+1/D+2 high는 label 계산에만 사용
- 현재 수정된 이벤트 정책을 과거에 소급할지, 당시 학습 정책을 재현할지 명시적으로 version pin

## 현재 parity 문제

현재 `SignalCollector`로 57개 rulebook을 재생한 결과 저장 entry date 3,430개 중 1,258개만 정확히 일치했다.

따라서 full replay 기능은 존재하지만, 현재 코드·market/news context가 원래 Stage2/Stage3 학습 당시와 동일하다는 보장은 없다.

필터 재학습 전에 다음 sanity gate가 필요하다.

```text
저장 entry_signal_date에서 replay should_buy 재현율 >= 99%
AND
replayed score/threshold ratio 차이 허용오차 이내
```

이 gate가 실패하면 추가 신호 수를 진짜 연속·보유중 신호로 해석할 수 없다.
