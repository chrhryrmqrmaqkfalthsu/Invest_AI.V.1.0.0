# Regime·섹터 feature 정보량 분석 감사

## 실행 범위

```text
GA 실행: 0건
학습 실행: 0건
백테스트 실행: 0건
외부 데이터 수집: 0건
원본 코드 수정: 0건
원본 데이터 수정: 0건
```

분석은 다음 기존 파일을 읽기만 했다.

```text
data/_system/market_history.csv
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
```

## 입력 SHA-256

```text
market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

AAP_ohlcv.csv
6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717

POWI_ohlcv.csv
bd683b376899ff9784eb86a807bd154f74502c8f0bb025ceb120b3e16b8fb400
```

## 표본

```text
AAP: 1,459행
POWI: 1,459행
총 2,918행
기간: 2020-09-09 ~ 2026-07-01
L2 positive rate: 34.5784784099%
```

시장 context는 각 D0에 대해 다음 조건으로 정렬했다.

```text
market_date <= D0 - 1 calendar day
```

미래누출 행은 0개다.

## Feature 계약

기존 5개:

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

기존 strict-AND Phase 3와 동일하게 D-5 거래행 값을 사용했다.

신규 17개:

```text
market_score
regime_code
sp500_60d
sp500_60d_delta5
sp500_60d_delta20
sp500_60d_vs_ma20
vix_level
vix_delta1
vix_delta5
vix_vs_ma20
sector_tech_score
sector_tech_delta5
sector_tech_delta20
sector_tech_vs_ma20
sector_tech_return60_proxy
sector_vs_sp500_60d_proxy
sector_minus_market_score
```

AAP와 POWI의 sector mapping은 모두 `tech`다.

## Market-history 스키마 제한

복구 파일에는 원시 S&P 500·XLK 가격이 없다.

```text
sp500_60d: S&P 500 60일 수익률
sector_tech: clip(50 + XLK 60일 수익률 × 5, 0, 100)
```

따라서 다음은 proxy다.

```text
sp500_60d_vs_ma20:
저장된 60일 수익률 - 그 값의 20일 이동평균

sector_tech_return60_proxy:
(sector_tech - 50) / 5
```

정확한 SPY·XLK 가격 MA 또는 unclipped 상대수익률로 해석하지 않았다.

## MI 방법

```text
estimator: sklearn.feature_selection.mutual_info_classif
neighbors: 5
random_state: 20260712
unit: bits
```

세 관점으로 비교했다.

```text
RAW_POOLED
TICKER_MEAN
TICKER_MEAN_BIAS_ADJUSTED
```

Bias-adjusted 값은 AAP·POWI 라벨을 각각 독립 circular shift한 null 100회의 평균을 제거했다.

## 검증 결과

```text
feature_information.csv: 22행 × 10열
robust_mi_diagnostics.csv: 22행 × 15열
three_way_information_comparison.csv: 9행 × 9열
existing_new_correlation_matrix.csv: 5행 × 18열
CSV 행 너비 오류: 0
JSON 파싱 오류: 0
```

판정 검증:

```text
same-sample ticker-mean combined > existing5: YES
same-sample ticker-mean combined > external 0.129 benchmark: NO
circular-shift adjusted increment > 0: YES
최종 판정: REGIME_MARGINAL
```

## 사전 백업

```text
backup/pre_regime_sector_information_20260712T231353Z.tar.gz
SHA-256:
266af26c1ce7ba930ac217e8a4664e2bb9006b0a43ae62b86082dc197897a8d1

backup/pre_regime_sector_information_20260712T231353Z.manifest.sha256
SHA-256:
2908abaae941854f534132c3faf3cf7838bca3d3b58142020b4113a32c617097
```

Manifest 검증은 PASS다.

## 라이브 불변

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

Daemon:

```text
PID 494330
STAT Sl
running
```
