# 실적/이벤트 데이터 소스 가용성 조사 감사

## 실행 범위

```text
GA 실행: 0건
학습 실행: 0건
백테스트 실행: 0건
정식 코드 수정: 0건
원본 데이터 수정: 0건
외부 원시 응답 저장: 0건
```

## 읽은 기존 파일

```text
data/_system/market_history.csv
data/_system/market_history_v2.csv
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
engine/adapters/us_stock.py
```

## 입력 SHA-256

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

## market_history_v2 검사

```text
shape: 2,196행 × 28열
기간: 2020-06-01 ~ 2026-06-05
has_earnings_shock=1: 648일
기업 식별 컬럼: 없음
기업별 실적일: 없음
EPS estimate/reported/surprise: 없음
revision: 없음
```

## yfinance 접근성 검사

```text
yfinance version: 1.4.0
symbols: AAP, POWI
raw response persisted: false
```

검사 API:

```text
Ticker.calendar
Ticker.get_earnings_dates(limit=12/40/100)
Ticker.earnings_dates
Ticker.earnings_estimate
Ticker.eps_revisions
Ticker.eps_trend
Ticker.growth_estimates
```

모든 API는 AAP·POWI에서 응답했다.

## 과거 실적 coverage

```text
AAP dedup earnings rows: 99
AAP range: 2002-02-20 ~ 2026-08-13
AAP sample-period events: 26
AAP sample-period surprise rows: 25

POWI dedup earnings rows: 94
POWI range: 2002-04-25 ~ 2026-08-06
POWI sample-period events: 26
POWI sample-period surprise rows: 25
```

## 최소 MI 검사

```text
L2 label rows: 3,006
AAP: 1,503
POWI: 1,503
positive rate: 34.6640053227%
```

라벨:

```text
max(High[D+1], High[D+2])
>= Open[D0] × (1 + sqrt(2) × RV20_pct[D-1] / 100)
```

Null 검사:

```text
종목별 라벨 circular shift
반복: 100회
random seed: 20260713
```

주요 결과:

```text
within_5d_before_earnings bias-adjusted MI: 0.006043888619 bits, p=0.009900990099
within_3d_before_earnings bias-adjusted MI: 0.005320946263 bits, p=0.009900990099
within_10d_before_earnings bias-adjusted MI: 0.005155581996 bits, p=0.009900990099
```

## Look-ahead 제한

`get_earnings_dates()`의 과거 날짜는 최종 관측 발표일이다. 과거 각 날짜에 시장이 알고 있던 일정 snapshot은 아니다.

따라서 이번 MI는 정보원 가용성·잠재 정보량 검사이며 production-safe point-in-time backtest로 간주하지 않는다.

과거 `eps_revisions`, `eps_trend` snapshot은 현재 소스에서 확보되지 않았다.

## 사전 백업

```text
backup/pre_earnings_source_availability_20260712T233839Z.tar.gz
SHA-256:
1d700e1d79ec5b6796cef3b01f5cefbad7c1b80c9e8072d100918de4b59d505b

backup/pre_earnings_source_availability_20260712T233839Z.manifest.sha256
SHA-256:
23b5e96d75944d75f3495da790bffc215179fc34f1e5b68fd514bc9272bf54a6
```

Backup manifest 검증은 PASS다.

## 보호값

```text
.env
da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

live_candidate_slots.py
259d3bec12901591c84cd1ad9aec01612d914c9120c0976b54bb34adfe684dbb

signal_collector.py
fc0768235189c5a6f95926d2c4f42aa78401e11b8fa2a8ab95992515a700f497
```

Daemon PID 494330은 유지했다.
