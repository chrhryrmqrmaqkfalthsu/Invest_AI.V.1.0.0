# Earnings proximity look-ahead 격리 감사

## 실행 범위

```text
GA 실행: 0건
학습 실행: 0건
백테스트 실행: 0건
정식 코드 수정: 0건
원본 데이터 수정: 0건
외부 원시 응답 저장: 0건
```

## 입력

```text
data/_system/market_history.csv
data/_system/market_history_v2.csv
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
data/_system/analysis/earnings_event_source_availability_20260713/
engine/adapters/us_stock.py
```

입력 SHA-256:

```text
market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611

AAP_ohlcv.csv
6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717

POWI_ohlcv.csv
bd683b376899ff9784eb86a807bd154f74502c8f0bb025ceb120b3e16b8fb400

engine/adapters/us_stock.py
04fc99f7551299d91aefb457bcd435ba415ceec8b7dc8dedadd39e6c140e5adf
```

## 외부 조회

```text
provider: yfinance 1.4.0
symbols: AAP, POWI
API: get_earnings_dates(limit=12/40/100)
repeat query: limit=100
raw response persisted: false
```

현재 세션 반복 조회와 서로 다른 limit 응답의 겹치는 구간에서 날짜·EPS estimate·reported EPS·surprise 불일치는 0건이었다.

이 검사는 현재 응답 일관성만 확인한다. 과거 시점별 calendar snapshot은 제공되지 않아 과거 schedule 변경 여부를 직접 검증하지 못했다.

## 일정 안정성

```text
AAP actual events: 26
AAP interval range: 77~106 days
AAP median: 91 days
AAP mean absolute error from 90 days: 7.20 days
AAP intervals within ±5 days of 90: 32%

POWI actual events: 26
POWI interval range: 84~98 days
POWI median: 91 days
POWI mean absolute error from 90 days: 3.48 days
POWI intervals within ±5 days of 90: 68%

weekend events: 0
intervals <70 days: 0
intervals >110 days: 0
```

## 비교 계약

정확 버전:

```text
현재 yfinance가 반환하는 다음 final-observed earnings date
```

러프 버전:

```text
cutoff 이전 마지막 confirmed actual earnings date + 90 calendar days
```

러프 버전에서 미래 exact date와 현재 upcoming calendar는 사용하지 않았다.

## 표본

```text
AAP rows: 1,503
POWI rows: 1,503
total rows: 3,006
```

L2 라벨:

```text
max(High[D+1], High[D+2])
>= Open[D0] × (1 + sqrt(2) × RV20_pct[D-1] / 100)
```

## MI 및 null

```text
estimator: sklearn.feature_selection.mutual_info_classif
binary features: discrete_features=True
unit: bits
matched comparison circular shifts: 200
cycle sensitivity circular shifts: 100
random seed: 20260713
```

핵심 결과:

```text
exact 3/5/10-day bias-adjusted MI sum:
0.016415513848 bits

rough 90-day 3/5/10-day bias-adjusted MI sum:
0.005202175898 bits

rough/exact ratio:
31.690606496%

exact p<0.05 windows: 3
rough p<0.05 windows: 1
```

판정 규칙:

```text
PROXIMITY_ROBUST requires:
rough/exact ratio >= 50%
and rough p<0.05 windows >= 2
```

두 조건 모두 실패했다.

## 민감도

```text
85-day cycle bias-adjusted sum: 0.000326613476 bits
90-day cycle bias-adjusted sum: 0.005402434023 bits
95-day cycle bias-adjusted sum: 0.004155992687 bits
```

95일 주기의 유의 결과는 POWI 편중이며 AAP MI는 거의 0이었다.

## 판정

```text
LOOKAHEAD_SUSPECT
```

이 판정은 실제 오염을 확정하지 않는다. point-in-time schedule 이력 없이 exact-date alpha를 production-safe로 간주할 수 없다는 뜻이다.

## 안전 계약

```text
earnings_schedule_uncertain_blackout = 1
when 70 <= days_since_last_confirmed_earnings <= 110
```

미래 exact date를 사용하지 않고 entry block/risk-control로만 사용한다.

## 사전 백업

```text
backup/pre_earnings_lookahead_isolation_20260712T234820Z.tar.gz
SHA-256:
3c14953208c10e1d97e3e52da3ecc9f0983fd1c8defca3be1cd9b39cd8a5c9bd

backup/pre_earnings_lookahead_isolation_20260712T234820Z.manifest.sha256
SHA-256:
ddad513b2a92278dc950eb6bc00fbebb655a8ba7cf567668a938165b3f28c448
```

Backup manifest 검증은 PASS다.

## 보호값

```text
.env
da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611

live_candidate_slots.py
259d3bec12901591c84cd1ad9aec01612d914c9120c0976b54bb34adfe684dbb

signal_collector.py
fc0768235189c5a6f95926d2c4f42aa78401e11b8fa2a8ab95992515a700f497
```

Daemon PID 494330은 유지했다.
