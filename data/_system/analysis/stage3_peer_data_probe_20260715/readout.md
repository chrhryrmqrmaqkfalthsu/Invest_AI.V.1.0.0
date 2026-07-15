# Stage3 AAP peer OHLCV 데이터 존재·정합 확인

## 실행 사실

- 실행 host: `invest-bot`
- 작업 위치: `scripts/research/stage23_rework_20260713/` 및 `data/_system/`
- 범위: read-only 확인. 코드 수정, 데이터 수정, auto-fetch, 외부 다운로드, GA, 재학습 없음.
- 산출물 작성 전 기준점 백업 commit: `38d64a9`

## 결론

판정: **PEER_DATA_ABSENT**

저장소 전체에서 AAP 동종 peer로 볼 수 있는 GPC, ORLY, AZO, LKQ, CPRT, DORM, MNRO, KMX, AN, PAG, LAD, SAH, GPI, ABG, CVNA, CARG, DRVN, MUSA, CASY의 종목별 OHLCV는 발견되지 않았다. 따라서 peer-relative feature는 현재 저장소 데이터만으로는 계산할 수 없고, 데이터 수집/스냅샷 생성이 별도 선행 과제다.

지수 benchmark는 SPY/QQQ가 존재한다. SPY/QQQ는 AAP 3-fold 기간 전체를 날짜 누락 없이 커버하므로 index-relative feature는 계산 가능하다. 다만 이는 peer-relative가 아니라 market/index-relative다.

## STEP 0 — 전수 검색 결과

### 종목별 OHLCV snapshot

검색 위치: `data/`, `scripts/research/stage23_rework_20260713/`

주요 OHLCV 디렉터리:

- `data/_system/analysis/ohlc_snapshot_20260707/`
- `data/_system/analysis/entry_quality_stops_regime_20260707/`

`ohlc_snapshot_20260707/ohlc_snapshot_manifest.csv`는 91개 ticker를 가진다. 이 manifest에서 동종 peer 후보와 정확히 일치하는 ticker는 AAP뿐이었다.

### 정확 매칭 파일 목록

| 구분 | ticker | 발견 경로 |
|---|---|---|
| 대상 종목 | AAP | `data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv` |
| peer | GPC | 없음 |
| peer | ORLY | 없음 |
| peer | AZO | 없음 |
| peer | LKQ | 없음 |
| peer | CPRT | 없음 |
| peer | DORM | 없음 |
| peer | MNRO | 없음 |
| peer | KMX | 없음 |
| peer | AN | 없음 |
| peer | PAG | 없음 |
| peer | LAD | 없음 |
| peer | SAH | 없음 |
| peer | GPI | 없음 |
| peer | ABG | 없음 |
| peer | CVNA | 없음 |
| peer | CARG | 없음 |
| peer | DRVN | 없음 |
| peer | MUSA | 없음 |
| peer | CASY | 없음 |
| index | SPY | `data/_system/analysis/ohlc_snapshot_20260707/benchmark_SPY_ohlcv.csv`; `data/_system/analysis/entry_quality_stops_regime_20260707/benchmark_SPY_ohlcv.csv` |
| index | QQQ | `data/_system/analysis/ohlc_snapshot_20260707/benchmark_QQQ_ohlcv.csv`; `data/_system/analysis/entry_quality_stops_regime_20260707/benchmark_QQQ_ohlcv.csv` |
| index | IWM | 없음 |
| index | DIA | 없음 |
| index | XLY | 없음 |
| index | XRT | 없음 |
| index | CARZ | 없음 |

주의: 넓은 문자열 검색에서는 `ANET_ohlcv.csv`, `CAN_ohlcv.csv`가 `AN` 문자열을 포함해 잡혔지만, 정확 ticker 매칭 기준으로 `AN_ohlcv.csv`는 없다.

### market_history 계열 확인

`market_history.csv` / `market_history_v2.csv` 및 백업 파일들은 멀티 종목 OHLCV 테이블이 아니었다. symbol/ticker 컬럼이 없다.

| 파일 | row 수 | column 수 | symbol/ticker 컬럼 | 성격 |
|---|---:|---:|---:|---|
| `data/_system/market_history.csv` | 1759 | 12 | 없음 | 날짜별 market regime / sector score |
| `data/_system/market_history.csv.before_6y` | 500 | 12 | 없음 | 백업 market regime |
| `data/_system/market_history.csv.empty_20260712_bak` | 0 | 0 | 없음 | 빈 백업 파일 |
| `data/_system/market_history_v2.csv` | 2196 | 28 | 없음 | 날짜별 뉴스/이벤트 aggregate |
| `data/_system/market_history_v2.csv.bak` | 637 | 28 | 없음 | 백업 뉴스/이벤트 aggregate |
| `data/_system/market_history_v2.csv.before_expand` | 637 | 22 | 없음 | 백업 뉴스/이벤트 aggregate |
| `data/_system/market_history_v2.csv.before_expanded_has` | 637 | 22 | 없음 | 백업 뉴스/이벤트 aggregate |
| `data/_system/market_history_v2.csv.old` | 637 | 28 | 없음 | 백업 뉴스/이벤트 aggregate |

`data/` 하위 `.parquet` 파일도 발견되지 않았다.

## STEP 1 — 발견 데이터 정합성

### 사용 가능한 OHLCV/benchmark 정합표

| symbol | 경로 | SHA256 | 파일 mtime UTC | rows | 기간 | OHLCV 완비 | null OHLCV | duplicate date |
|---|---|---|---|---:|---|---:|---:|---:|
| AAP | `data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv` | `6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717` | 2026-07-07 16:56:12 +0000 | 1526 | 2020-06-08~2026-07-06 | YES | 0 | 0 |
| SPY | `data/_system/analysis/ohlc_snapshot_20260707/benchmark_SPY_ohlcv.csv` | `3f0e999acf7c2778d47b6d25b3a2edcce6c2e030cae64f8e759cbf89cded5825` | 2026-07-07 19:38:09 +0000 | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 |
| QQQ | `data/_system/analysis/ohlc_snapshot_20260707/benchmark_QQQ_ohlcv.csv` | `b6523573bbcda36875ce1e89f051dd6cd238edcba592190b679b93b0e0c8a932` | 2026-07-07 19:38:09 +0000 | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 |
| SPY duplicate | `data/_system/analysis/entry_quality_stops_regime_20260707/benchmark_SPY_ohlcv.csv` | `3f0e999acf7c2778d47b6d25b3a2edcce6c2e030cae64f8e759cbf89cded5825` | 2026-07-07 19:38:09 +0000 | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 |
| QQQ duplicate | `data/_system/analysis/entry_quality_stops_regime_20260707/benchmark_QQQ_ohlcv.csv` | `b6523573bbcda36875ce1e89f051dd6cd238edcba592190b679b93b0e0c8a932` | 2026-07-07 19:38:09 +0000 | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 |

SPY/QQQ duplicate 파일은 byte-identical이다.

### AAP 3-fold 날짜 정합

| symbol | train_1 rows / AAP rows | train_1 missing AAP dates | train_2 rows / AAP rows | train_2 missing AAP dates | train_3 rows / AAP rows | train_3 missing AAP dates |
|---|---:|---:|---:|---:|---:|---:|
| AAP | 251/251 | 0 | 250/250 | 0 | 250/250 | 0 |
| SPY | 251/251 | 0 | 250/250 | 0 | 250/250 | 0 |
| QQQ | 251/251 | 0 | 250/250 | 0 | 250/250 | 0 |

전체 2020-06-08~2026-07-06 기준으로는 SPY가 AAP 날짜 대비 14일 차이, QQQ가 13일 차이를 보인다. 그러나 stage3 AAP의 train_1/2/3 기간에서는 SPY/QQQ 모두 AAP 거래일과 완전히 일치한다.

### peer 데이터 정합성

동종 peer OHLCV가 없으므로 다음 항목은 확인 불가다.

| 항목 | 상태 |
|---|---|
| peer 시작~끝 기간 | 데이터 없음 |
| peer row 수 | 데이터 없음 |
| peer OHLCV 컬럼 완비 | 데이터 없음 |
| peer 결측 여부 | 데이터 없음 |
| peer AAP 3-fold 날짜 정합 | 데이터 없음 |
| peer 거래정지/상장폐지 구간 | 데이터 없음 |

## STEP 2 — peer-relative 계산 가능성 판정

판정: **PEER_DATA_ABSENT**

근거:

1. 종목별 snapshot manifest 91개 ticker 중 peer 후보와 정확히 일치하는 ticker는 AAP뿐이다.
2. 저장소 전체 파일명 정확 검색에서 GPC, ORLY, AZO, LKQ, CPRT, DORM, MNRO, KMX, AN, PAG, LAD, SAH, GPI, ABG, CVNA, CARG, DRVN, MUSA, CASY OHLCV가 없다.
3. `market_history*` 파일군은 날짜별 시장/뉴스 aggregate이며 멀티 종목 OHLCV가 아니다.
4. `.parquet` 기반 숨은 OHLCV 파일도 data 하위에서 발견되지 않았다.

### 사용 가능한 상대강도 범위

| feature 종류 | 가능 여부 | 사용 가능 데이터 | 비고 |
|---|---:|---|---|
| AAP vs SPY | 가능 | SPY benchmark OHLCV | index-relative 가능 |
| AAP vs QQQ | 가능 | QQQ benchmark OHLCV | index-relative 가능 |
| AAP vs peer basket | 불가 | peer OHLCV 없음 | 별도 수집 필요 |
| AAP vs sector/retail ETF | 불가 | XLY/XRT/CARZ 없음 | 별도 수집 필요 |

### 다음 단계 권고

현재 저장소 데이터만 쓸 경우:

1. peer-relative feature는 구현하지 않는다.
2. index-relative 후보만 2차 보조 feature로 둘 수 있다.
   - `rs_qqq_ret20 = AAP 20d return - QQQ 20d return`
   - `rs_spy_ret20 = AAP 20d return - SPY 20d return`
3. 직전 독립성 분석 기준으로 RS20 계열은 후보끼리 중복이 크므로, 하나를 고른다면 `rs_qqq_ret20` 하나만 보류 후보로 유지한다.

peer-relative feature를 정말 검증하려면 선행 데이터 수집 대상은 다음 순서가 적절하다.

1. 핵심 auto parts retail peer: `ORLY`, `AZO`, `GPC`
2. adjacent auto parts / salvage / services: `LKQ`, `DORM`, `CPRT`, `MNRO`
3. broader auto retail/dealer는 AAP와 사업 구조가 달라 2차 후보: `KMX`, `AN`, `PAG`, `LAD`, `SAH`, `GPI`, `ABG`, `CVNA`, `CARG`

계산 방식 초안, 데이터 확보 후:

- 각 peer의 adjusted 또는 동일 기준 OHLCV Close로 20d/60d return 계산.
- peer basket return은 사용 가능한 핵심 peer의 equal-weight median 또는 mean.
- feature 예시:
  - `rs_peer_ret20 = AAP_ret20 - peer_basket_ret20`
  - `rs_peer_ret60 = AAP_ret60 - peer_basket_ret60`
  - `rs_peer_line_slope20 = pct_change(AAP_close / peer_basket_close, 20)`
- AAP와 peer basket의 거래일 intersection을 사용하고, stage3 fold 기간에서 missing AAP dates가 0인지 fail-closed 검증한다.

## 보호파일 / daemon / git 기록

시작 SHA:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

종료 시에도 동일 SHA와 daemon PID `494330`을 재확인한다. readout SHA, 보호파일 SHA, daemon 상태, git 상태는 `SHA256SUMS.txt`에 기록한다.
