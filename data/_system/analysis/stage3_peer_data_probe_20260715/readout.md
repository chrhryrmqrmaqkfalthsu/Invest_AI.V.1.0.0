# Stage3 AAP peer OHLCV 데이터 존재·정합 확인 — 정정본

## 정정 결론

판정: **PEER_DATA_AVAILABLE**

사용자 지적이 맞았다. 이전 보고서는 `ohlc_snapshot_20260707/`와 파일명 중심으로만 찾아서, 6000개대 종목 OHLCV가 들어 있는 `stage0/ohlcv_cache/*.pkl` 저장소를 놓쳤다. 저장소에는 AAP와 동종/인접 peer의 6년치 OHLCV 캐시가 존재한다.

사용 가능한 주 저장소:

`data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/`

- pkl 파일 수: 5,758개
- 파일 형식: ticker별 `pandas.DataFrame` pickle
- 기본 컬럼: `Open`, `High`, `Low`, `Close`, `Volume`, MA/RSI/MACD/BB/ATR/Volume_ratio 등 파생 컬럼 포함
- 기간: 대체로 2020-05-18 ~ 2026-06-12 또는 2026-06-15
- AAP stage3 3-fold 기간 train_1/train_2/train_3는 핵심 peer 모두 날짜 누락 0일로 커버

이전 `PEER_DATA_ABSENT` 판정은 **취소**한다.

## 실행 사실

- 실행 host: `invest-bot`
- 작업 위치: `scripts/research/stage23_rework_20260713/` 및 `data/_system/`
- 범위: read-only 확인. 코드 수정, 데이터 수정, auto-fetch, 외부 다운로드, GA, 재학습 없음.
- 최초 산출물 작성 전 기준점 백업 commit: `38d64a9`
- 정정보고 전 기준점 백업 commit: `9bfc6a8`

## STEP 0 — 전수 검색 재확인

### 발견된 6000개대 OHLCV 캐시

| 경로 | pkl 수 | 비고 |
|---|---:|---|
| `data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/` | 5,758 | 이번 정합성 판정의 주 기준 |
| `data/_system/research/honest_full_6174_20260610/stage0/ohlcv_cache/` | 5,753 | 이전 버전 캐시 |
| `data/_system/research/honest_full_6174_20260616_stage01_full_w2/stage0/ohlcv_cache/` | 729 | 부분 캐시 |

### 정확 매칭 peer 파일 존재

주 기준: `data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/`

| 구분 | ticker | 상태 | 경로 |
|---|---|---:|---|
| 대상 | AAP | 있음 | `.../ohlcv_cache/AAP.pkl` |
| 핵심 auto parts retail peer | GPC | 있음 | `.../ohlcv_cache/GPC.pkl` |
| 핵심 auto parts retail peer | ORLY | 있음 | `.../ohlcv_cache/ORLY.pkl` |
| 핵심 auto parts retail peer | AZO | 있음 | `.../ohlcv_cache/AZO.pkl` |
| adjacent peer | LKQ | 있음 | `.../ohlcv_cache/LKQ.pkl` |
| adjacent peer | CPRT | 있음 | `.../ohlcv_cache/CPRT.pkl` |
| adjacent peer | DORM | 있음 | `.../ohlcv_cache/DORM.pkl` |
| adjacent peer | MNRO | 있음 | `.../ohlcv_cache/MNRO.pkl` |
| broader auto retail/dealer | KMX | 있음 | `.../ohlcv_cache/KMX.pkl` |
| broader auto retail/dealer | AN | 있음 | `.../ohlcv_cache/AN.pkl` |
| broader auto retail/dealer | PAG | 있음 | `.../ohlcv_cache/PAG.pkl` |
| broader auto retail/dealer | LAD | 있음 | `.../ohlcv_cache/LAD.pkl` |
| broader auto retail/dealer | SAH | 있음 | `.../ohlcv_cache/SAH.pkl` |
| broader auto retail/dealer | GPI | 있음 | `.../ohlcv_cache/GPI.pkl` |
| broader auto retail/dealer | ABG | 있음 | `.../ohlcv_cache/ABG.pkl` |
| broader auto retail/dealer | CVNA | 있음 | `.../ohlcv_cache/CVNA.pkl` |
| broader auto retail/dealer | CARG | 있음 | `.../ohlcv_cache/CARG.pkl` |
| broader auto retail/dealer | MUSA | 있음 | `.../ohlcv_cache/MUSA.pkl` |
| broader retail/adjacent | CASY | 있음 | `.../ohlcv_cache/CASY.pkl` |
| requested/possible peer | DRVN | 없음 | - |
| index | SPY | 있음 | `.../ohlcv_cache/SPY.pkl` |
| index | QQQ | 있음 | `.../ohlcv_cache/QQQ.pkl` |

### market_history 계열 확인

`market_history.csv` / `market_history_v2.csv`는 여전히 멀티 종목 OHLCV 테이블이 아니다. symbol/ticker 컬럼이 없는 날짜별 market/news aggregate다. 6000개대 종목 OHLCV는 `market_history*`가 아니라 `stage0/ohlcv_cache/*.pkl`에 있다.

## STEP 1 — 발견 데이터 정합성

기준 fold:

- train_1: 2022-07-01 ~ 2023-06-30
- train_2: 2023-07-01 ~ 2024-06-30
- train_3: 2024-07-01 ~ 2025-06-30

기준 AAP snapshot:

`data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv`, SHA `6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717`

### 핵심 peer 정합표

모든 행은 주 캐시 `honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/` 기준이다.

| ticker | SHA256 | rows | 기간 | OHLCV 완비 | null OHLCV | duplicate dates | train_1 누락 | train_2 누락 | train_3 누락 |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| AAP | `f3982ccac813b9932b58059afe30dc554b66964dd01e312fca771f7d96f719da` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| GPC | `172528a07786e0e204ddeafb7570a185c465f13570ae19e50d6aecaa696bfeda` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| ORLY | `e37d9b5da83d517e6e77ba0f2d55ef4f07869815b24e73cc4f7259df01f8d75a` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| AZO | `42006c75744935719992fe0c423580541ba560e4ba67d3cd894d130690bd36d1` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| LKQ | `33c810ef5b300731df735d32567e450399ec3af0cc3eb130aaa4da69988aebf9` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| CPRT | `a327ece139e0cfe4b66d8428b60ea29c7a35a0fad7d98db980a81af759974cf5` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| DORM | `093813515870781db70500a55be9b1e94998caea497f088b33b4596c57e551fc` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| MNRO | `06bd26941e4e598383d595ed9dfdc5b61a6bac83e0d9eda4699f25ddee0ce015` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |

핵심 auto parts peer인 **GPC/ORLY/AZO는 AAP stage3 3-fold 기간을 전부 정합하게 커버**한다.

### broader peer / index 정합표

| ticker | SHA256 | rows | 기간 | OHLCV 완비 | null OHLCV | duplicate dates | train_1 누락 | train_2 누락 | train_3 누락 |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| KMX | `e4b5959d68268d99ac56e329d142217dd84f5f3f0a0c8a3ba7a97aa452dd14c9` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| AN | `dbbe15bfab9795133d980361d5caed6a4545099147957b0a8872ad69d07163d8` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| PAG | `388def8e2f22fe93656e1623d7cf09247ca448bfec4f4b5841af6a926ca0fa6f` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| LAD | `e731078124802e887ebe1d74dffd008b66490071d972e11d4a6085b5233c8313` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| SAH | `dfc042650f94319bd6bafc287d29e42a73969dc6c3343c9c7a527ddddae3554d` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| GPI | `3a707f5d252c3ec3ab6bc53ea5d066383ebf77d263a110ed153c3f463512378d` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| ABG | `f123c93a0bfd0d01e2d911748873bb8afd78e68c5d456ddde54497fc8d868c87` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| CVNA | `01e56748cc4f7116300e5818fb05f41c75244cbef10196de839f762b7d3784bb` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| CARG | `f5b700f8f9dabf266fe7a3e02d8859ba47a775c8aada850655ffe1e9064e0e68` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| MUSA | `3a83b81f659041382ced4a88f4de26dc4c180f849e047e890ad59b270337694a` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |
| CASY | `6b91beb3d3f94c90564b88b00567d34e1732d895b9b7de25ee81e39ed38e667d` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| SPY | `ef9cfdd484488dc6635fe542a0a7b39bfaf0c7661f7ddff8a6081af27bbe1637` | 1526 | 2020-05-18~2026-06-12 | YES | 0 | 0 | 0 | 0 | 0 |
| QQQ | `b13782d26d80191920f26a69d9ef740c0427fb861ac7c9dc34661c316f926460` | 1527 | 2020-05-18~2026-06-15 | YES | 0 | 0 | 0 | 0 | 0 |

### AAP snapshot과 cache 비교

AAP snapshot과 `AAP.pkl`은 전체 파일 기간이 다르다.

| 항목 | snapshot | pkl cache |
|---|---|---|
| 경로 | `ohlc_snapshot_20260707/AAP_ohlcv.csv` | `honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/AAP.pkl` |
| 기간 | 2020-06-08~2026-07-06 | 2020-05-18~2026-06-12 |
| stage3 train_1/2/3 날짜 누락 | 0 | 0 |
| OHLCV 동일성 | 공통 날짜에서 최대 차이 `4.84e-10` | float serialization 수준 |

즉 stage3 fold 기간에는 pkl cache를 peer-relative 계산에 사용할 수 있다. 다만 2026-06-12 이후 날짜까지 필요한 분석에는 `ohlc_snapshot_20260707`과 시점 차이가 있으므로 별도 freeze 기준을 맞춰야 한다.

## STEP 2 — peer-relative 계산 가능성 판정

판정: **PEER_DATA_AVAILABLE**

최소 1개가 아니라 핵심 peer 3개 `GPC`, `ORLY`, `AZO`가 모두 AAP 3-fold 기간을 완전히 커버한다. adjacent peer까지 포함하면 `LKQ`, `CPRT`, `DORM`, `MNRO`도 사용 가능하다.

### 권장 peer 집합

1차 핵심 peer basket:

- `GPC`
- `ORLY`
- `AZO`

2차 확장 basket:

- `GPC`, `ORLY`, `AZO`, `LKQ`, `CPRT`, `DORM`, `MNRO`

broader auto retail/dealer basket은 사업 구조가 달라 1차 feature에는 넣지 않고 비교/robustness 용도로 둔다.

- `KMX`, `AN`, `PAG`, `LAD`, `SAH`, `GPI`, `ABG`, `CVNA`, `CARG`

### 계산 방식 초안

stage3 fold 기간에서 AAP와 peer의 거래일은 모두 일치하므로 intersection 기준으로 fail-closed 정렬하면 된다.

권장 feature:

1. `rs_peer3_ret20 = AAP_ret20 - median(GPC_ret20, ORLY_ret20, AZO_ret20)`
2. `rs_peer3_ret60 = AAP_ret60 - median(GPC_ret60, ORLY_ret60, AZO_ret60)`
3. `rs_peer3_line_slope20 = pct_change(AAP_close / median_peer3_close_indexed, 20)`

여기서 `median_peer3_close_indexed`는 각 peer close를 첫 유효일 100으로 rebasing한 뒤 median을 쓰는 방식이 안전하다. 단순 가격 median은 AZO/ORLY/GPC 가격 레벨 차이 때문에 부적절하다.

권장 우선순위:

- 첫 null-test는 `rs_peer3_ret20` 하나만 추가.
- 그 다음 `rs_peer3_ret60` 또는 `rs_peer7_ret20` 확장.
- SPY/QQQ index-relative는 peer-relative와 비교용 baseline으로만 둔다.

## 보호파일 / daemon / git 기록

시작/종료 SHA 대조 대상:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

종료 시에도 동일 SHA와 daemon PID `494330`을 재확인한다. readout SHA, 보호파일 SHA, daemon 상태, git 상태는 `SHA256SUMS.txt`에 기록한다.
