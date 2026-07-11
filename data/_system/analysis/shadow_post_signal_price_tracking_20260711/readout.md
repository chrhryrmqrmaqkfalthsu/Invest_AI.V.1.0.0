# shadow 개체 신호 후 주가 추적

## 범위

대상은 현재 보존된 `shadow_direct_event` JSONL의 모든 `elite_shared` row다.

검색 경로:

```text
data/_system/analysis/shadow_direct_event/*.jsonl
```

현재 표본:

```text
elite_shared row: 1
unique candidate: 1
```

## 1. 그룹 분류

분류 기준:

```text
DROP_ON_OFF    = pass_on=true, pass_off=false
SURVIVE_ON_OFF = pass_on=true, pass_off=true
```

현재 결과:

| 그룹 | row 수 | 고유 개체 수 |
|---|---:|---:|
| DROP_ON_OFF | 1 | 1 |
| SURVIVE_ON_OFF | 0 | 0 |

유일한 개체:

```text
candidate_id=stage3:BTBT:363898884d44
ticker=BTBT
group=DROP_ON_OFF
shadow_timestamp=2026-07-11T12:33:09.799605+00:00
score_on=18.273542025757642
score_off=1.8154976300972685
event_component=14.919817861651637
threshold=1.911257702358741
pass_on=true
pass_off=false
```

근거:

```text
data/_system/analysis/shadow_direct_event/shadow_direct_event_20260711.jsonl line 13
```

## 2. 신호 가격 보존 여부

Shadow JSONL에는 다음은 저장돼 있다.

- candidate ID
- timestamp
- score ON/OFF
- Event component
- threshold
- pass ON/OFF
- market score

하지만 `price` 필드는 저장돼 있지 않다.

따라서 BTBT의 정확한 shadow 신호 가격은 현재 산출물만으로 소급할 수 없다.

판정:

```text
RETROACTIVE_SIGNAL_PRICE_UNAVAILABLE
```

`live_slots_state.json`에는 BTBT의 7월 10일 마지막 live-slot 평가 가격이 다음처럼 남아 있다.

```text
last_seen_at=2026-07-10T19:59:53.001495+00:00
price=1.6950000524520874
```

그러나 이 값은 shadow timestamp인 7월 11일 12:33 UTC의 evaluator가 실제 사용한 가격이라는 직접 증거가 아니므로 신호 가격으로 대체하지 않았다.

## 3. candle 소스

사용한 가격 소스:

```text
GET /api/real/candles/BTBT?interval=1m
```

응답 candle의 source 필드:

```text
public_1m_candle_loader
```

반환된 1분봉 수:

```text
2,785
```

신호 시점 이전 마지막 가용 candle:

```text
timestamp=2026-07-10T23:59:00+00:00
close=1.70
source=public_1m_candle_loader
```

이 값은 정확한 신호 가격이 아니라:

```text
LAST_AVAILABLE_CANDLE_BEFORE_SIGNAL
```

로만 기록했다.

## 4. 신호 후 가격 추적 가능 여부

Shadow timestamp:

```text
2026-07-11T12:33:09.799605+00:00
```

이는 토요일이다.

Candle API의 최신 candle:

```text
2026-07-10T23:59:00+00:00
```

따라서 분석 시점에는 shadow timestamp 이후의 시장 candle이 한 건도 없다.

현재 가용성:

| 구간 | 가용 여부 |
|---|---|
| +1시간 | 불가 |
| +4시간 | 불가 |
| +1일 | 불가 |
| +2일 | 불가 |

이는 가격 데이터 누락을 임의 보간한 것이 아니라, 신호 이후 시장 거래가 아직 발생하지 않은 상태다.

## 5. 그룹별 대조

### DROP_ON_OFF

```text
row 수=1
고유 개체 수=1
정확한 신호 가격 보존=0
신호 후 가격 가용=0
```

개체:

```text
BTBT / stage3:BTBT:363898884d44
```

현재 관찰 가능한 사실은:

- Event를 포함하면 통과
- Event를 끄면 미통과
- Event component가 14.9198
- 신호 이후 시장 가격은 아직 없음

까지다.

### SURVIVE_ON_OFF

```text
row 수=0
고유 개체 수=0
```

비교할 개체가 없다.

## 6. 해석 제한

현재 표본으로는 그룹 성과를 비교하거나 Event OFF의 유불리를 판단할 수 없다.

이유:

1. 전체 `elite_shared` 표본이 1건
2. SURVIVE 그룹 표본이 0건
3. 정확한 shadow 신호 가격이 저장되지 않음
4. 신호가 주말에 발생해 이후 candle이 없음
5. 평균·승률·상대 성과를 계산할 수 있는 최소 관찰 수가 없음

따라서 본 산출물은 통계 결과가 아니라 최초 관찰 row의 분류와 가격 추적 가능 상태를 기록한 것이다.

## 7. 후속 축적 시 동일 기준

향후 새 `elite_shared` row가 쌓이면 각 row마다 다음을 갱신해야 한다.

- DROP_ON_OFF / SURVIVE_ON_OFF 분류
- 정확한 신호 가격 보존 여부
- 신호 이후 첫 가용 candle
- +1h, +4h, +1d, +2d 시점별 실제 candle timestamp와 close
- 각 구간 가격 변동률
- 그룹별 row 수와 가격 가용 표본 수

정확한 추적을 위해서는 shadow row에 실제 evaluator `price`를 함께 저장하는 것이 필요하지만, 이번 작업은 읽기 전용이므로 코드 변경은 하지 않았다.

## 산출물

- `data/_system/analysis/shadow_post_signal_price_tracking_20260711/shadow_classification.csv`
- `data/_system/analysis/shadow_post_signal_price_tracking_20260711/post_signal_price_tracking.csv`
- `data/_system/analysis/shadow_post_signal_price_tracking_20260711/group_comparison.csv`
- `data/_system/analysis/shadow_post_signal_price_tracking_20260711/readout.md`

운영 코드·설정 변경: 0건
