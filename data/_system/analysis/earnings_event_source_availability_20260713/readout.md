# 실적/이벤트 데이터 소스 가용성 조사

# 최종 판정: **EARNINGS_AVAILABLE**

갈래 B인 실적 context는 착수 가능하다. AAP·POWI 모두 yfinance에서 과거 실적일, 당시 EPS 예상치, 실제 EPS, surprise 이력을 메모리로 조회할 수 있었고, 최소 earnings-proximity feature가 L2 라벨에 대해 두 종목 공통의 양의 정보량을 보였다.

다만 현재 확인된 데이터는 범위가 나뉜다.

```text
과거 실적일·EPS surprise: 사용 가능
현재 upcoming calendar·estimate·revision: 사용 가능
과거 point-in-time calendar snapshot: 없음
과거 revision 이력: 없음
```

따라서 바로 가능한 것은 earnings proximity 및 사후 surprise 연구다. 과거 revision backtest는 현재 소스로는 불가능하며, production proximity backtest는 실적일 변경에 따른 look-ahead를 막는 point-in-time 일정 이력이 선행돼야 한다.

## 1. market_history_v2.csv

파일:

```text
data/_system/market_history_v2.csv
```

현황:

```text
2,196행 × 28열
기간: 2020-06-01 ~ 2026-06-05
has_earnings_shock=1: 648일
```

실적 관련 컬럼은 시장 전체 이벤트 플래그인 다음 하나뿐이다.

```text
has_earnings_shock
```

없는 항목:

```text
ticker / symbol
기업별 earnings date
EPS estimate
reported EPS
surprise
estimate revision
```

따라서 `market_history_v2.csv`는 AAP·POWI 개별 실적 context 소스로 사용할 수 없다. `has_earnings_shock` 648일은 시장 뉴스 분류 결과이며 특정 기업의 실적 일정 또는 surprise가 아니다.

## 2. 기존 파이프라인

정식 코드에는 다음 wrapper가 있다.

```text
engine/adapters/us_stock.py
USStockAdapter.fetch_earnings_calendar()
```

이 함수는 yfinance의 현재 `Ticker.calendar`만 반환한다. 과거 실적일, surprise 이력, revision 이력은 현재 adapter 계약에 노출되지 않는다.

## 3. 외부 소스 접근성

환경의 yfinance 버전:

```text
1.4.0
```

AAP:

```text
현재 예정 실적일: 2026-08-13
과거 실적일 dedup: 99개
범위: 2002-02-20 ~ 2026-08-13
분석 기간 주변 이벤트: 26개
surprise 보유 이벤트: 25개
```

POWI:

```text
현재 예정 실적일: 2026-08-06
과거 실적일 dedup: 94개
범위: 2002-04-25 ~ 2026-08-06
분석 기간 주변 이벤트: 26개
surprise 보유 이벤트: 25개
```

조회 가능한 API:

```text
Ticker.calendar
Ticker.get_earnings_dates(limit=100)
Ticker.earnings_dates
Ticker.earnings_estimate
Ticker.eps_revisions
Ticker.eps_trend
Ticker.growth_estimates
```

모든 조회는 메모리에서만 수행했고 원시 응답은 저장하지 않았다.

## 4. 데이터 이력 범위

### 사용 가능한 과거 이력

`get_earnings_dates()`는 다음을 과거 이벤트별로 반환했다.

```text
Earnings Date
EPS Estimate
Reported EPS
Surprise(%)
```

따라서 다음 연구는 가능하다.

```text
earnings proximity
실적 전후 window
직전 surprise
surprise decay
```

### 현재 snapshot만 가능한 항목

```text
earnings_estimate
eps_revisions
eps_trend
growth_estimates
```

이 항목은 현재 시점의 `0q`, `+1q`, `0y`, `+1y` snapshot이다. 과거 날짜별 revision snapshot은 제공되지 않는다.

따라서 다음은 불가능하다.

```text
2021년 당시 30일 revision
2022년 당시 analyst count 변화
과거 시점별 EPS trend 변화
```

현재 API 응답을 과거 전체에 복제해 backtest하면 명백한 look-ahead가 된다.

## 5. 최소 earnings-proximity MI

L2 라벨:

```text
max(High[D+1], High[D+2])
>= Open[D0] × (1 + sqrt(2) × RV20_pct[D-1] / 100)
```

표본:

```text
AAP: 1,503행
POWI: 1,503행
총: 3,006행
positive rate: 34.6640%
```

각 D0의 cutoff는 D0-1 calendar day로 두고 다음 feature를 계산했다.

```text
days to next earnings
days since last earnings
nearest earnings distance
1·3·5·10·20일 전후 window
직전 surprise
surprise decay
```

### 두 종목 공통으로 살아남은 실적 전 proximity

| Feature | AAP MI | POWI MI | Null 보정 평균 MI | empirical p |
|---|---:|---:|---:|---:|
| 5일 이내 실적 전 | 0.004458 | 0.009262 | 0.006044 | 0.0099 |
| 3일 이내 실적 전 | 0.003213 | 0.009065 | 0.005321 | 0.0099 |
| 10일 이내 실적 전 | 0.002695 | 0.010378 | 0.005156 | 0.0099 |

세 window 모두 AAP와 POWI에서 MI가 양수였고, 종목별 라벨 circular shift 100회 기준 p=0.0099였다.

연속형 `days_to_next_clip90`은 관측 MI가 더 컸다.

```text
AAP MI: 0.023403 bits
POWI MI: 0.005662 bits
bias-adjusted mean: 0.008540 bits
empirical p: 0.0891
```

연속형 거리 자체보다 3~10일의 실적 전 window가 더 안정적으로 나타났다.

### Surprise

```text
last_surprise_pct bias-adjusted MI: 0
surprise_decay_30d bias-adjusted MI: 0
```

과거 surprise 데이터는 조회 가능하지만 이번 2종목 최소 시험에서는 시계열 null을 통과하지 못했다. 첫 연구 범위는 surprise보다 proximity가 우선이다.

## 6. 핵심 제한: point-in-time 일정

이번 최소 MI는 yfinance가 현재 반환하는 최종 관측 실적일을 과거 날짜에 정렬했다.

실적 발표일은 사전에 변경될 수 있으므로, 최종 발표일을 과거 모든 시점에서 이미 알고 있었다고 가정하면 일정 변경분에 look-ahead가 생긴다.

따라서 결과 해석은 다음으로 제한한다.

```text
실적 일정이라는 정보원의 잠재 정보량: 확인
production-safe historical calendar: 아직 미확보
```

갈래 B를 실제 파이프라인에 넣기 전에 다음 중 하나가 필요하다.

```text
과거 point-in-time earnings calendar 공급원
또는 오늘부터 날짜별 calendar snapshot 누적
또는 일정 변경 가능성을 보수적으로 처리하는 blackout-only 계약
```

## 7. 판정

### EARNINGS_AVAILABLE 근거

- AAP·POWI 모두 과거 실적일과 EPS surprise 이력 조회 성공
- 분석 기간 내 종목당 26개 실적 이벤트 확보
- 모든 L2 행에서 이전·다음 실적일까지 거리 계산 가능
- 실적 전 3·5·10일 proximity가 두 종목 모두 양의 MI
- circular-shift null을 통과한 proximity window 존재

### 아직 불가능한 범위

- 과거 point-in-time 일정 backtest
- 과거 estimate revision backtest
- `market_history_v2.csv`만으로 기업별 실적 context 구성

최종 판정:

# **EARNINGS_AVAILABLE**

갈래 B로 진행하되 1차 범위는 다음으로 제한한다.

```text
1. earnings proximity / blackout feature
2. point-in-time 일정 수집·검증 계약
3. surprise는 secondary post-event feature
4. revision은 historical source 확보 전 보류
```

유니버스 확대 갈래 A는 폐기하지 않지만, earnings-context pilot 결과 전까지 후순위로 둔다.
