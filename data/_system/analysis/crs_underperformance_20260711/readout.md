# CRS 최초신호 후 부진 원인 조사

## 최종 요약

```text
고점 진입 가설: PARTIAL_SUPPORT
Event/v3/BOIL 밖 사례: YES
개별 악재 인과: UNRESOLVED
정확한 최초 신호 사유 전체: UNRECOVERABLE
```

CRS는 5·10·20일 고점에서 산 것은 아니지만, **신호 당일 고점의 0.125% 아래**에서 처음 포착됐다. 신호 후 약 50분 동안 소폭 더 오른 뒤 장 마감 직전 -2% 구간까지 밀렸고, 다음 날 첫 관찰 가격은 신호 대비 -4.44% 갭다운이었다.

따라서 관찰상 가장 직접적인 설명은:

```text
당일 고점 근처 진입
→ 장 마감 전 약화
→ 다음 날 갭다운
```

이다. 다만 다일 신고가 추격은 아니며, 개별 뉴스가 하락을 직접 유발했다고 증명할 자료도 없다.

## 1. CRS 기본 정보

```text
candidate_id=stage3:CRS:8695c9ce3320
first_signal_at=2026-07-09T17:20:33.590054+00:00
first_signal_at_et=2026-07-09 13:20:33 America/New_York
first_signal_price=600.8599853515625
first_final_score=2.971797614887265
threshold=2.5574757832651467
score margin=+0.41432183162211844 (+16.20%)
```

Alpaca read-only 최신 체결:

```text
price=579.14
timestamp=2026-07-10T19:59:38.272913+00:00
exchange code=V
return from first signal=-3.6148164099917834%
elapsed=26.6513 hours
```

이 조사는 Alpaca live account의 `StockLatestTradeRequest`와 IEX daily/minute bars를 read-only로 조회했다.

### 최초 가격 source 제한

`first_signal_price` 자체는 state에 정확히 남아 있지만 source 필드는 저장되지 않았다.

코드상 `elite_shadow_trader._latest_price()`는:

```text
yfinance 1m prepost
→ 실패 시 daily Close
```

순서로 가격을 선택한다. 신호 직후 Alpaca IEX 17:21 bar가 600.85라 저장 가격 600.86과 거의 일치하지만, 해당 최초 가격의 exact provider는 `UNRECOVERABLE`이다.

## 2. 최초 진입 사유

### 정확한 사유 전체

```text
UNRECOVERABLE
```

`live_slots_state.json::first_seen_signals`에는 다음만 저장됐다.

- first signal timestamp
- first signal price
- first final score

다음은 저장되지 않았다.

- first signal reasons
- component scores
- active Event flags
- market score/sector score/VIX snapshot
- market adjustment

따라서 현재 사유나 이후 Event snapshot을 최초 신호에 소급하지 않았다.

### 남은 데이터로 가능한 부분 진단

신호 당시 loader 코드와 같은 방식으로 2026-07-08 완성 일봉까지 재구성한 기술·뉴스 부분:

| 항목 | 진단 기여 |
|---|---:|
| 정배열 | +1.0734 |
| MACD | 0 |
| RSI | +1.6476 |
| BB근접 | +1.9411 |
| 거래량 | 0 |
| 전체 뉴스 | +0.0611 |
| 토픽 뉴스 | -1.3137 |
| 합계, Event 없음·시장 중립 | 3.4095 |

저장된 실제 최초 점수는 2.9718로 0.4377 낮다. 이 차이를 당시 Event와 시장 보정 사이에 배분할 snapshot이 없어 `UNRESOLVED`다.

현재 state에 남은 이유:

```text
정배열(+1.07)
RSI 56∈[39,67](+1.65)
BB근접(+1.94)
전체톤(-0.23)(-0.10)
토픽뉴스(+0.97)
```

은 2026-07-11 이후 평가값이며 최초 신호 이유가 아니다.

## 3. 고점 진입 가설

### 당일 위치

Alpaca IEX 기준:

```text
previous close=587.03
signal=600.86 (+2.36%)
signal-day open=595.82
signal-day high=601.61
signal distance from day high=-0.125%
```

신호는 당일 고점에 매우 가까웠다.

### 5·10·20일 고점

신호 전 완성 일봉 기준:

```text
5-session high=625.94, signal -4.01%
10-session high=625.94, signal -4.01%
20-session high=625.94, signal -4.01%
```

따라서 5·10·20일 고점 매수는 아니다.

Range 위치:

```text
5-session range=48.17%
10-session range=53.00%
20-session range=80.13%
```

Run-up:

```text
5-session first close 대비=-1.53%
10-session first close 대비=+3.35%
20-session first close 대비=+14.68%
```

즉 단기 신고가 추격은 아니지만 한 달가량 오른 뒤 20일 range 상단부에서 당일 반등 고점 근처에 진입했다.

## 4. MA·BB 위치

신호 당시 evaluator가 사용했을 가능성이 가장 높은 2026-07-08 완성 일봉 입력:

```text
MA5=600.9260
MA20=581.2125
MA60=488.6108
MA5 > MA20 > MA60=true
```

저장 신호 가격 위치:

```text
MA5 대비=-0.011%
MA20 대비=+3.38%
MA60 대비=+22.97%
```

장기 추세에서는 상당히 위였지만 단기 MA5와는 거의 같은 수준이었다.

### BB근접의 의미

코드상 long 후보의 `BB근접`은 상단 돌파가 아니다.

```text
Close <= BB_lower × bb_proximity
```

CRS 룰북:

```text
bb_proximity=1.1402337701369225
BB_lower=528.3554
BB_middle=581.2125
BB_upper=634.0696
pass threshold=602.4486
```

실제 evaluator row의 2026-07-08 종가 587.63은 밴드 56.07% 위치였다. 저장 신호 가격을 같은 밴드에 놓으면 68.59% 위치다.

그럼에도 14.02% proximity 때문에 602.45 이하가 모두 통과한다. 즉 `BB근접(+1.94)`은 상단 돌파 오독이 아니라 **lower-band 조건이 지나치게 넓어 밴드 상단 쪽 가격도 허용한 것**이다.

이 점은 오늘의 Event/v3/BOIL과 별개인 진입 위치 취약점 후보다.

## 5. 신호 후 경로

Alpaca IEX 1분봉은 거래가 없는 분이 빠질 수 있어 아래 crossing은 “첫 관찰” 시각이다.

```text
2026-07-09 17:21 UTC  600.85   거의 동일
2026-07-09 18:11 UTC  601.61   +0.125%, session high
2026-07-09 19:40 UTC  594.72   first observed -1.02%
2026-07-09 19:56 UTC  588.545  first observed -2.05%
2026-07-09 19:59 UTC  590.515  -1.72%
2026-07-10 13:38 UTC  574.16   -4.44%, gap down
2026-07-10 13:40 UTC  569.315  -5.25%, next-session low
2026-07-10 18:17 UTC  585.95   -2.48%, partial recovery
2026-07-10 19:59 UTC  579.14   -3.61%
```

신호 직후 즉시 붕괴한 것은 아니다. 약 50분 뒤 고점을 냈고, 하락은 장 마감 20분 전부터 뚜렷해졌다. 큰 손실 확대는 다음 날 갭다운에서 발생했다.

## 6. Event·v3·BOIL

### Event OFF

저장 비교 snapshot:

```text
off score=4.660072469611355
threshold=2.5574757832651467
pass=true
```

CRS는 `EVENT_AMPLIFIED`였지만 Event가 없어도 통과한다.

### v3

```text
final_p99_weightless_block_status=PASS
fail components=none
BB reachability=REACHABLE
volume reachability=REACHABLE
```

### BOIL

```text
final BOIL-exclusive target membership=false
vol_group=MID_VOL
weight_volume_surge=1.233207754645207
```

결론:

```text
Event OFF PASS
v3 PASS
BOIL PASS
```

CRS 부진은 오늘 다룬 게이트 세 축 밖이다.

## 7. 악재와 단순 변동 구분

### 남아 있는 악재 단서

Raw CRS news cache에는 신호 전에:

- 사상 최고가·과매수·고평가 경고
- 내재가치 대비 고평가와 내부자 매도 언급
- Russell 지수 제외와 기계적 매도 가능성

이 남아 있다.

그러나 특정 기사가 장 후반 하락이나 다음 날 갭다운을 일으켰다는 인과 증거는 없다.

Evaluator는 1일 feature lag를 사용했고, 신호 technical date가 2026-07-08이라 실제 뉴스 선택은 2026-07-07 행이었다.

```text
selected sentiment_avg=+0.1488
2026-07-08 negative row는 최초 판정에 직접 사용되지 않음
```

뉴스 lag가 악재를 늦게 반영했을 가능성은 있으나 원인으로 확정할 수 없다.

### 단순 변동 가능성

재구성 ATR은 23.3871이고 신호 대비 최신 손실폭은 21.72달러, 약 0.929 ATR이다.

따라서 -3.61%는 부진하지만 CRS의 당시 변동성 대비 극단적 이탈이라고 단정할 수 없다.

## 8. 유사패턴 단서

### ACMR

```text
previous close 대비 signal=+8.99%
signal-day high 대비=-1.43%
20-session run-up=+31.32%
stored return=-3.79%
```

CRS보다 더 강한 gap/chase 단서가 있다.

### BNTX

```text
previous close 대비 signal=+0.04%
signal-day high 대비=-1.12%
20-session run-up=+6.73%
stored return=-2.69%
```

고점 진입 공통성은 약하다. BNTX는 별도로 v3 FAIL이어서 CRS와 게이트 프로필도 다르다.

표본이 3개뿐이므로 공통 결함을 추론할 수 없다.

## 9. 최종 판정

```text
고점 진입: 당일 기준 YES, 다일 기준 NO
가설 전체: PARTIAL_SUPPORT
가격 경로: 장 마감 전 약화 + 다음 날 gap down
Event/v3/BOIL: 모두 PASS, 게이트 밖 사례
개별 악재 원인: UNRESOLVED
정확한 최초 사유: UNRECOVERABLE
확정 가능한 결함: 없음
추가 검증 후보: intraday chase 위치, permissive BB proximity, news lag
```

한 사례로 기준을 바꿀 수는 없지만, CRS는 Event/v3/BOIL 이후 남는 **진입 위치·뉴스 시차 축**을 검증할 필요가 있다는 단서를 제공한다.

## 산출물

- `crs_basic_and_reasons.csv`
- `high_entry_validation.csv`
- `post_signal_path.csv`
- `gate_outside_assessment.md`
- `similar_pattern_clues.csv`
- `readout.md`

운영 코드·설정 변경: 0건
