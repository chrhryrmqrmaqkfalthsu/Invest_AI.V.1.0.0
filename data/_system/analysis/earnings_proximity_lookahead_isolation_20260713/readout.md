# Earnings proximity look-ahead 오염 격리

# 최종 판정: **LOOKAHEAD_SUSPECT**

정확한 최종 실적일을 사용한 proximity 신호는 재현됐지만, 각 시점에 이미 확인된 마지막 실적일에 고정 90일만 더한 point-in-time-safe 러프 일정에서는 정보량의 약 31.7%만 남았다.

이 결과는 정확한 날짜 신호가 실제로 오염됐음을 직접 증명하지는 않는다. 그러나 과거 시점별 일정 snapshot이 없는 상태에서는 해당 신호를 production-safe alpha로 간주할 수 없다는 의미다.

따라서 earnings context는 폐기하지 않되, 정확한 미래 실적일 기반 bullish feature 통합은 중단하고 **넓은 blackout/risk-control 계약**으로 후퇴한다.

## 1. 비교 계약

### 정확한 날짜 버전

```text
각 D0 cutoff에서 yfinance가 현재 반환하는
다음 최종 관측 실적일까지 남은 날짜
```

위험:

```text
실적일이 과거에 변경됐더라도
현재 반환되는 최종 발표일을 과거에서 이미 알고 있었다고 가정할 수 있음
```

### 러프 일정 버전

```text
cutoff 이전에 이미 끝난 마지막 실제 실적일
+ 고정 90 calendar days
```

중요 조건:

```text
미래의 실제 실적일 사용: 0
현재 upcoming calendar 사용: 0
종목별 미래 일정 사용: 0
```

러프 버전이 사용하는 개별 날짜는 이미 발생해 확인된 마지막 실적일뿐이다.

## 2. 일정 안정성 조사

### AAP

```text
실제 이벤트: 26개
기간: 2020-02-18 ~ 2026-05-21
분기 간격: 77 ~ 106일
중앙값: 91일
90일 대비 평균 절대오차: 7.20일
90일 ±5일 이내: 32%
주말 이벤트: 0
```

### POWI

```text
실제 이벤트: 26개
기간: 2020-01-30 ~ 2026-05-07
분기 간격: 84 ~ 98일
중앙값: 91일
90일 대비 평균 절대오차: 3.48일
90일 ±5일 이내: 68%
주말 이벤트: 0
```

AAP 일정은 POWI보다 훨씬 불규칙했다. 고정 90일 예측이 AAP의 실제 proximity를 정확히 재현하기 어려운 구조다.

같은 세션에서 `get_earnings_dates(limit=100)`을 반복 조회한 결과 날짜·EPS·surprise 불일치는 0건이었다. `limit=12/40/100`의 겹치는 구간도 불일치가 0건이었다.

하지만 이것은 현재 응답의 일관성만 확인한다. 과거 각 날짜에 공지됐던 일정이 변경됐는지는 확인할 수 없다.

```text
과거 point-in-time 일정 변경 이력: 없음
현재 응답 내부 불일치: 없음
사후 변경 여부 직접 판별: 불가능
```

## 3. 정확한 날짜 대 러프 90일 MI

종목별 라벨 circular shift 200회로 null을 계산했다.

| Window | 정확 날짜 bias-adjusted MI | 러프 90일 bias-adjusted MI | 보존 비율 | 정확 p | 러프 p |
|---|---:|---:|---:|---:|---:|
| 3일 전 | 0.005278 | 0.002133 | 40.41% | 0.0050 | 0.0448 |
| 5일 전 | 0.005941 | 0.001557 | 26.21% | 0.0050 | 0.0796 |
| 10일 전 | 0.005197 | 0.001512 | 29.09% | 0.0050 | 0.1244 |

합계:

```text
정확 날짜 3·5·10일 MI 합: 0.016416 bits
러프 90일 3·5·10일 MI 합: 0.005202 bits
러프/정확 비율: 31.69%
```

판정 기준:

```text
러프/정확 비율 >= 50%
러프 window 중 p<0.05가 최소 2개
```

실제:

```text
비율 31.69%: FAIL
유의한 러프 window 1개: FAIL
```

## 4. 종목별 차이

### AAP

정확한 날짜 proximity는 양의 lift를 보였다.

```text
3일 전 정확 lift: +17.44%p
5일 전 정확 lift: +17.59%p
10일 전 정확 lift: +9.33%p
```

러프 90일에서는 3·5일 window의 방향이 뒤집혔다.

```text
3일 전 러프 lift: -7.97%p
5일 전 러프 lift: -2.69%p
10일 전 러프 lift: +3.57%p
```

정확 window와 러프 window의 활성 날짜 Jaccard도 낮았다.

```text
3일: 8.75%
5일: 9.48%
10일: 29.69%
```

### POWI

POWI에서는 러프 일정도 양의 lift를 유지했다.

```text
3일 전 러프 lift: +21.47%p
5일 전 러프 lift: +18.18%p
10일 전 러프 lift: +12.73%p
```

하지만 정확 날짜 MI보다 약했고, 두 종목 공통 재현이 아니라 POWI 편중 결과였다.

## 5. 85·90·95일 민감도

고정 주기를 85·90·95일로 바꿔 확인했다.

| 고정 주기 | 3·5·10일 bias-adjusted MI 합 | p<0.05 window |
|---|---:|---:|
| 85일 | 0.000327 | 0개 |
| 90일 | 0.005402 | 0개 |
| 95일 | 0.004156 | 2개 |

95일에서 일부 p값이 살아났지만 사실상 POWI가 전부 만들었다.

```text
95일·5일 window AAP MI: 약 0.000409
95일·10일 window AAP MI: 약 0.000050
```

따라서 고정 주기를 약간 바꾸면 두 종목 공통 신호가 회복된다고 볼 수 없다.

## 6. 넓은 러프 blackout

마지막 확인 실적일 이후 cycle age만 사용한 넓은 구간도 검사했다.

| 구간 | Bias-adjusted MI | p |
|---|---:|---:|
| 80~100일 | 0.002525 | 0.0348 |
| 75~105일 | 0.001340 | 0.1244 |
| 70~110일 | 0.001495 | 0.1144 |

80~100일은 pooled 관점에서 일부 정보가 있었지만 AAP MI가 거의 0이어서 alpha feature로는 부족하다.

넓은 blackout은 예측 feature가 아니라 실적 일정 불확실성에 대한 위험 통제로만 사용할 수 있다.

## 7. 판정

### PROXIMITY_ROBUST가 아닌 이유

- 러프 일정이 정확 일정 MI의 31.7%만 보존
- 러프 3·5·10일 중 유의 window는 1개뿐
- AAP의 러프 3·5일 lift가 음수로 반전
- exact/rough 활성 날짜 중첩이 AAP에서 매우 낮음
- 85·95일 민감도도 두 종목 공통 재현에 실패

### LOOKAHEAD_SUSPECT의 의미

```text
look-ahead 오염이 확정됨: NO
정확 날짜 신호가 production-safe임이 증명됨: NO
정확 날짜 없이 신호가 충분히 재현됨: NO
```

따라서 정확한 과거 실적일 proximity는 연구상 관측치로만 보존하고 GA 또는 정식 feature에 넣지 않는다.

# 최종 판정: **LOOKAHEAD_SUSPECT**

## 8. 안전한 첫 feature 정의

```text
feature name:
earnings_schedule_uncertain_blackout

value = 1 when:
70 <= days_since_last_confirmed_earnings <= 110

value = 0 otherwise
```

계약:

```text
미래 exact earnings date 사용 금지
bullish alpha로 사용 금지
entry block 또는 risk-control로만 사용
마지막 실제 실적 발표가 확인된 이후에만 cycle 재시작
```

70~110일은 이번 표본의 실제 분기 간격 77~106일을 모두 포괄하는 보수적 범위다. 이 feature 자체의 alpha MI가 강하다는 뜻은 아니며, 일정 불확실 구간에 새 진입을 피하는 보호 계약이다.

실시간 current calendar를 사용하려면 조회 시점이 포함된 snapshot을 저장하고 이후 변경 이력을 추적해야 한다.

## 9. 다음 단계

1. 날짜별 earnings calendar snapshot 누적 설계
2. 최초 공지일·변경일·최종 발표일을 분리한 audit log 설계
3. exact proximity alpha 통합은 snapshot 이력이 쌓일 때까지 보류
4. 우선 실험은 넓은 blackout이 거래 손실과 gap risk를 줄이는지 검증
5. historical revision은 point-in-time 공급원 확보 전 계속 보류

갈래 B는 유지하지만, 첫 형태는 proximity alpha가 아니라 **blackout risk-control**이다.
