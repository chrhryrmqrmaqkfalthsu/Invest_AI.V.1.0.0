# 원시 ^GSPC·XLK regime 정보량 분석 감사

## 실행 범위

```text
GA 실행: 0건
학습 실행: 0건
백테스트 실행: 0건
정식 코드 수정: 0건
원시 fetch 데이터 파일 저장: 0건
```

분석은 Yahoo fetch 결과를 메모리에서만 사용했다.

## 조회 심볼

```text
^GSPC
^VIX
XLK
XLF
XLE
XLV
XLY
XLI
```

조회 결과:

```text
^GSPC: 1,759행, 2019-07-11 ~ 2026-07-10
^VIX: 1,761행, 2019-07-10 ~ 2026-07-10
XLK/XLF/XLE/XLV/XLY/XLI: 각 1,759행, 2019-07-11 ~ 2026-07-10
```

## 입력 파일

```text
data/_system/market_history.csv
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
data/_system/analysis/regime_sector_feature_information_20260712/summary.json
```

입력 SHA-256:

```text
market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

AAP_ohlcv.csv
6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717

POWI_ohlcv.csv
bd683b376899ff9784eb86a807bd154f74502c8f0bb025ceb120b3e16b8fb400
```

## 표본과 시점

```text
AAP: 1,459행
POWI: 1,459행
총 2,918행
기간: 2020-09-09 ~ 2026-07-01
L2 positive rate: 34.5784784099%
```

시점 정렬:

```text
기존 가격 feature: D-5 거래행
원시 시장 feature: latest raw market date <= D0-1 calendar day
미래누출 행: 0
```

## 원시 feature 수

```text
기존 가격 feature: 5개
원시 시장·섹터 feature: 33개
총 정보량 검사 feature: 38개
```

원시 feature 범주:

```text
^GSPC 수익률·MA·실현변동성
XLK 수익률·MA·실현변동성
VIX 수준·변화·MA
XLK-^GSPC 상대수익률
6섹터 XLK rank·z-score·dispersion·breadth
```

## MI 방법

```text
estimator: sklearn.feature_selection.mutual_info_classif
neighbors: 5
random_state: 20260712
unit: bits
```

종목별 시계열 null:

```text
AAP·POWI 라벨 독립 circular shift
반복: 100회
최소 shift: 30행
```

## Proxy 직접 대조

기준 proxy:

```text
종목 평균 결합 Top-5: 0.096728195827 bits
bias-adjusted 결합 Top-5: 0.057091553515 bits
bias-adjusted 순증분: 0.037570301508 bits
```

원시 가격:

```text
종목 평균 결합 Top-5: 0.114341857467 bits
bias-adjusted 결합 Top-5: 0.054775847790 bits
bias-adjusted 순증분: 0.035254595784 bits
```

차이:

```text
종목 평균 결합: +0.017613661641 bits
bias-adjusted 결합: -0.002315705725 bits
bias-adjusted 순증분: -0.002315705725 bits
```

## 판정 규칙

`RAW_RECOVERS`는 다음을 모두 만족해야 했다.

```text
종목 평균 결합 개선 >= 0.012901846931 bits
bias-adjusted 결합 개선 >= 0.010000000000 bits
bias-adjusted 순증분 개선 >= 0.010000000000 bits
cross-ticker 유의·저중복 raw feature >= 1개
```

결과:

```text
첫 조건: PASS
둘째 조건: FAIL
셋째 조건: FAIL
넷째 조건: PASS
최종 판정: RAW_SAME
```

## 사전 백업

```text
backup/pre_raw_spy_xlk_information_20260712T232807Z.tar.gz
SHA-256:
7e9d9ee985d45bf1135e376f40515a1258c1fe2cd2b9255a2281f0a3025509e9

backup/pre_raw_spy_xlk_information_20260712T232807Z.manifest.sha256
SHA-256:
27b95266bc385eb825d57d10553d695fddbd1b45fe29ae15f83ab5bad7701724
```

Manifest 검증은 PASS다.

## 라이브 보호값

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
