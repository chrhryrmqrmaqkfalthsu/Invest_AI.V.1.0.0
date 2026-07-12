# 5일 → 2거래일 내 +3% 진입필터 label 및 학습 사양

## 연구 범위

이번 실행의 개체 universe는 조사 시점 `build_elite_shadow_report(stage2_limit=60, stage3_limit=80)`가 반환한 라이브 적격 rulebook 57개다.

- Stage2: 8개
- Stage3: 49개
- 저장 신호 수: 3,430개
- 신호일 범위: 2021-03-05 ~ 2026-07-02

원천 신호는 각 rulebook의 `rl_replay_trades.jsonl`에 저장된 `entry_signal_date`다. 이는 저장된 backtest에서 실제 진입 기회로 기록된 `should_buy` 신호다.

주의: 포지션 보유 중이어서 신규 진입이 일어나지 않은 날짜의 이론상 `should_buy=True`까지 전부 저장한 별도 일별 signal ledger는 없다. 따라서 본 데이터는 **저장된 전체 entry signal 표본**이며, 중복 보유기간 내부의 잠재 신호는 `NOT_STORED`다.

## 시간 구간

| 구간 | 날짜 | 역할 |
|---|---|---|
| stress | 2020-01-01 ~ 2022-06-30 | 검증 전용 |
| train | 2022-07-01 ~ 2025-06-30 | GA 학습 전용 |
| OOS | 2025-07-01 ~ 저장 데이터 최신일 | 최종 검증 전용 |

저장된 최초 신호가 2021-03-05이므로 stress 구간 안의 2020년 신호는 실제 표본에 존재하지 않는다. underlying OHLCV는 2019년부터 확보됐지만 2020년 저장 entry signal은 `NOT_STORED`다.

최종 표본:

- stress: 982
- train: 1,578
- OOS: 870
- 합계: 3,430

## 신호가격 정의

Historical replay에는 신호일과 다음 날 체결가격은 저장돼 있으나 일관된 `entry_signal_price` 필드는 없다. 따라서 label 기준 신호가격은:

```text
signal_price = Close[entry_signal_date]
```

로 고정했다.

이는 기존 next-open 진입가격이 아니라 **신호가 발생한 완료 일봉의 종가**다.

## Feature boundary

신호일을 D0라고 할 때 모든 feature는 D0보다 앞선 완료 세션만 사용한다.

```text
feature input = D-6 Close + D-5~D-1 High/Low/Close
```

D0의 Open/High/Low/Close/Volume은 feature에 포함하지 않는다.

명시적 제외:

- `STK_gap_d0`
- ETF/시장 `gap_d0`
- D0 intraday 값
- flow/orderbook feature
- 뉴스·이벤트의 사후 재구성값

## 사용 feature

### 5개 일별 수익률

```text
ret_d5_pct = 100 * (Close[D-5] / Close[D-6] - 1)
...
ret_d1_pct = 100 * (Close[D-1] / Close[D-2] - 1)
```

### 5일 누적수익률

```text
cumulative_ret5_pct = 100 * (Close[D-1] / Close[D-6] - 1)
```

### 방향 일수

```text
up_days5   = count(ret > 0)
down_days5 = count(ret < 0)
```

### 최근 상승→하락 전환

```text
recent_turn_down = 1 if ret[D-2] > 0 and ret[D-1] < 0 else 0
```

### 5일 고점·저점과 고점 경과일

```text
high5 = max(High[D-5:D-1])
low5  = min(Low[D-5:D-1])
days_since_high5 = 4 - argmax(High[D-5:D-1])
```

### 5일 range 내 종가 위치

```text
close_pos5 = clip((Close[D-1] - low5) / (high5 - low5), 0, 1)
```

`high5 == low5`이면 0.5다.

### 고점 대비 pullback

```text
pullback_from_high5_pct = max(0, 100 * (high5 / Close[D-1] - 1))
```

### 최대 단일 상승일

```text
single_up_day5_pct = max(ret_d5_pct ... ret_d1_pct)
```

### 급등 후 fade

```text
first3_max = max(ret_d5_pct, ret_d4_pct, ret_d3_pct)
last2_ret  = 100 * (Close[D-1] / Close[D-3] - 1)

fade_after_surge_score
= max(0, first3_max) + max(0, -last2_ret)
```

`high5`, `low5`, `close_d1`은 감사용으로 저장하지만 GA gene의 절대가격 feature로 사용하지 않는다.

## Target/label

신호일 D0 다음의 실제 거래 세션 두 개를 D+1, D+2로 정의한다.

```text
future_max_high = max(High[D+1], High[D+2])
forward_max_return_pct = 100 * (future_max_high / signal_price - 1)

label_2d3pct = 1 if forward_max_return_pct >= 3.0 else 0
```

D0 당일 고가는 target 판정에 포함하지 않는다.

향후 두 거래일이 모두 저장되지 않은 최신 신호는 학습 dataset에서 제외한다.

## GA 개체 구조

각 rulebook마다 별도 GA를 학습한다.

Numeric feature마다 train 분포에서 empirical quantile을 만들고 개체는 다음을 가진다.

- 활성 feature mask
- 각 활성 feature의 `q_low`
- 각 활성 feature의 `q_high`
- `recent_turn_down` 사용 모드

개체 통과식:

```text
모든 활성 feature가 train에서 학습한 quantile band 안에 위치
AND
recent_turn_down 조건이 선택된 경우 해당 binary 조건 충족
```

최대 활성 numeric feature 수는 5개다.

## GA 설정

- population: 128
- maximum generations: 60
- elite: 16
- tournament size: 4
- mutation rate: 0.18
- early-stop patience: 18
- seed: `SHA256(candidate_id)` 기반 고정 seed

57개 개체 모두 train 표본 10개 이상으로 학습됐다.

## Fitness

Fitness는 train에서만 계산한다.

중심 목표:

- 통과 precision
- baseline 대비 precision lift
- positive recall
- 최소 coverage
- feature 수 복잡도 패널티

Train 최소 통과표본:

```text
max(8, ceil(train_signal_count * 0.12))
```

Train 최종 precision floor:

```text
max(0.50, train_base_rate + 0.10)
```

극단 수익률의 크기는 fitness에 사용하지 않는다. 각 신호는 `label=0/1`로만 기여하므로 ANET·CVNA 같은 대형 수익률 한 건이 fitness를 직접 확대하지 않는다.

## Stress/OOS survivor gate

Train에서 선택한 champion gene과 quantile mapping을 고정한 뒤 stress와 OOS에 그대로 적용한다.

각 검증 구간 최소 통과표본:

```text
max(3, ceil(regime_signal_count * 0.10))
```

각 구간 precision floor:

```text
max(0.45, regime_base_rate + 0.05)
```

추가 일반화 조건:

```text
train_precision - regime_precision <= 0.20
```

최종 survivor:

```text
train gate PASS
AND stress gate PASS
AND OOS gate PASS
```

어느 한 구간이라도 표본·precision·precision-gap 조건을 실패하면 탈락한다.

## 누수 방지 확인

- GA quantile 및 gene 학습: train만 사용
- stress: fitness·gene 선택에 미사용
- OOS: fitness·gene 선택에 미사용
- feature: D-1 이전 완료 세션만 사용
- target: feature 생성 후 별도로 D+1/D+2 high를 연결
- D0 gap 및 당일 캔들 feature: 제외
- flow/orderbook: 제외
- current live candidate path: 미연결
