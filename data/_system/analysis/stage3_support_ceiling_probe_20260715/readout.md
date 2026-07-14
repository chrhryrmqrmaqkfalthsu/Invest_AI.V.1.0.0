# AAP 비중복 체결 support 상한 측정 readout

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 분석 대상: `data/_system/analysis/stage3_aap_tradecount_factor_v3_20260715/AAP/NOTEBOOK_MAX/`
- 대상 종목: AAP
- 대상 fold: train_1 / train_2 / train_3
- 방식: 기존 v3 fold-best 산출물과 동일 OHLCV snapshot을 사용한 순수 재집계
- GA·백테스트·재학습: 실행하지 않음
- 코드·시장 데이터 수정: 없음
- 분석 시작 HEAD: `e8f3353e62e379ea0d9726ad403af9b0c0d0b685`
- 분석 전 백업 커밋: `2c461f1`

## 최종 판정

**SUPPORT_CEILING_CONFIRMED**

fold-best의 strict-AND joint pass day는 25 / 29 / 32일이지만, v3와 동일한 실제 entry-phase 청산·cooldown 순서를 적용하면 비중복 체결 가능 거래는 각각 **12 / 11 / 12건**이다.

세 fold 모두 12건 기준의 ±2 범위에 들어온다. 따라서 기존 v2와 v3에서 반복된 11~12건은 단순히 fitness가 최소 거래수 경계에 붙은 결과만이 아니라, 현재 strict-AND 신호가 보유·청산·cooldown을 거친 뒤 제공하는 실질적인 체결 support 상한에 가깝다.

특히 일반화 병목 fold인 train_2는 29개 joint pass day 중 18일이 기존 거래의 보유 또는 cooldown에 흡수되어 실제로는 11건만 체결됐다.

## STEP 0 — 입력 데이터 확인

### Fold-best 개체

v3 산출물에서 다음 fold-best hash와 5-feature interval을 확인했다.

| fold | candidate hash | trade count |
|---|---|---:|
| train_1 | `a323a7da1507335d3e600d333ce53c281f0ecb5085b8d02d332cbc1a9086c7a4` | 12 |
| train_2 | `3458571c823af0daba0c97ab8a3fb6bc7278b6463628b6e28e574b678149d5f8` | 11 |
| train_3 | `a099fd3046c9be09aaa6130887f08d1f2ec43240273cf5d48ddf125af63ab017` | 12 |

입력 위치:

- interval·rulebook: `qualify_candidate_rulebooks.jsonl`
- joint-pass 날짜·fold-best 요약: `fold_best_summary.json`
- 실제 진입·청산 경로: `fold_best_trade_level.jsonl`

### 가격·feature 시계열

가격 source는 v3 manifest와 동일하다.

```text
path: data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
SHA-256: 6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717
rows: 1526
first date: 2020-06-08
last date: 2026-07-06
```

전체 일별 feature tape는 v3 결과에 원행 형태로 저장되지는 않았으나, 동일 SHA의 OHLCV에서 v3 코드와 동일한 지표 공식을 적용해 재구성 가능했다.

적용 방식:

1. MA5·MA20·MA60, MACD histogram, RSI, Bollinger band, Volume ratio를 동일 공식으로 계산
2. 전체 시계열에서 5거래일 `shift(5)` 적용
3. 그 다음 fold 날짜 범위 절단
4. rulebook의 hard domain, q01~q99 empirical domain, low/high interval을 순서대로 fail-closed 평가

따라서 feature 재계산은 요청된 D-5 snapshot 정의와 일치한다.

### 실제 entry-phase 청산 규칙

fold-best rulebook 자체의 `max_holding_days` 필드는 20이지만 Stage3 entry-phase 실행 context가 다음 값을 강제한다.

```text
ENTRY_PHASE_MAX_HOLDING_DAYS = 7
cooldown_days = 1
entry = D+1 Open
exit priority:
  1. provisional ATR stop
  2. strict interval break
  3. provisional max holding 7
```

이번 fold-best 35개 거래는 모두 `entry_interval_break`로 2~5거래일 안에 청산됐다. ATR stop이나 7일 상한 청산은 발생하지 않았다.

## STEP 1 — Joint pass day 독립 재현

| fold | v3 기록 | D-5 재계산 | 날짜 목록 exact match |
|---|---:|---:|---|
| train_1 | 25 | 25 | PASS |
| train_2 | 29 | 29 | PASS |
| train_3 | 32 | 32 | PASS |

누락 날짜와 추가 날짜는 세 fold 모두 0개다.

따라서 이후 support 계산은 v3 run이 사용한 strict-AND 신호일과 정확히 같은 날짜 집합을 사용한다.

## STEP 2 — 실제 비중복 체결 사건 수

처리 방식:

1. 날짜순 첫 joint-pass day를 signal day로 선택
2. D+1 Open 진입
3. v3 로그의 실제 exit date까지 보유
4. exit 다음 거래일 1일을 cooldown으로 처리
5. 보유·cooldown 창에 포함된 joint-pass day는 기존 거래에 흡수
6. 그 이후 첫 실행 가능 pass day를 다음 거래로 선택

실제 fold-best trade log의 signal·entry·exit 순서와 교차검증했다.

| fold | joint pass day | 비중복 체결 거래 | 보유 중 흡수 | cooldown 흡수 | 총 흡수 | 기타 미체결 | 체결/joint 비율 |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 25 | **12** | 8 | 4 | **12** | 1 | 48.00% |
| train_2 | 29 | **11** | 14 | 4 | **18** | 0 | 37.93% |
| train_3 | 32 | **12** | 14 | 6 | **20** | 0 | 37.50% |

### train_1 기타 미체결 1일

`2023-01-06`은 strict interval 5개를 모두 통과했지만 보유·cooldown에는 포함되지 않았다.

해당 날짜의 legacy quality component는 전부 0이었다.

```text
ma_align = 0
MACD event = 0
RSI quality gate = 0
BB quality gate = 0
volume quality gate = 0
raw quality score = 0
```

fold-best가 `signal_scaled` position sizing을 사용하므로 quality score 0에서 포지션 크기가 0이 되어 실제 주문이 생성되지 않았다. 다음 pass day인 `2023-01-09`에는 RSI quality component가 1이어서 거래가 생성됐다.

따라서 train_1은 다음과 같이 정확히 분해된다.

```text
25 joint pass days
= 12 executed trades
+ 12 held/cooldown absorbed days
+ 1 zero-position day
```

### train_2 강조

train_2는 29개 joint-pass day가 있어 표면상 support가 넓어 보이지만 실제 순차 체결은 11건뿐이다.

```text
29 joint pass days
= 11 executed trades
+ 14 held absorbed days
+ 4 cooldown absorbed days
```

따라서 train_2의 11건은 GA가 우연히 거래수를 적게 선택한 것이 아니라, 해당 fold-best 신호의 시간적 군집 때문에 생긴 실질 체결 support다.

## STEP 3 — 사건 클러스터 분석

클러스터 기준:

- consecutive joint-pass day 간 trading-session gap이 `<= 8`이면 같은 사건
- 8 = entry-phase 7거래일 보유 상한 + cooldown 1거래일
- gap은 달력일이 아니라 AAP OHLCV trading-session index로 계산

Effective event count:

```text
1 / Σ(cluster의 joint-pass-day 비중²)
```

이는 각 클러스터가 전체 거래 후보일에서 차지하는 집중도를 측정한다.

### train_1

- cluster 수: **5**
- effective event count: **4.084967**

| # | 시작 | 종료 | pass day | 월별 분포 |
|---:|---|---|---:|---|
| 1 | 2022-07-01 | 2022-08-04 | 8 | 2022-07: 6, 2022-08: 2 |
| 2 | 2022-10-12 | 2022-10-31 | 6 | 2022-10: 6 |
| 3 | 2023-01-06 | 2023-01-24 | 6 | 2023-01: 6 |
| 4 | 2023-02-07 | 2023-02-07 | 1 | 2023-02: 1 |
| 5 | 2023-04-18 | 2023-04-26 | 4 | 2023-04: 4 |

### train_2

- cluster 수: **5**
- effective event count: **4.062802**

| # | 시작 | 종료 | pass day | 월별 분포 |
|---:|---|---|---:|---|
| 1 | 2023-08-25 | 2023-08-25 | 1 | 2023-08: 1 |
| 2 | 2023-11-27 | 2023-12-12 | 6 | 2023-11: 1, 2023-12: 5 |
| 3 | 2024-01-04 | 2024-01-25 | 9 | 2024-01: 9 |
| 4 | 2024-02-14 | 2024-02-26 | 5 | 2024-02: 5 |
| 5 | 2024-04-29 | 2024-05-22 | 8 | 2024-04: 1, 2024-05: 7 |

### train_3

- cluster 수: **5**
- effective event count: **3.792593**

| # | 시작 | 종료 | pass day | 월별 분포 |
|---:|---|---|---:|---|
| 1 | 2024-07-01 | 2024-07-08 | 4 | 2024-07: 4 |
| 2 | 2024-07-19 | 2024-08-27 | 12 | 2024-07: 6, 2024-08: 6 |
| 3 | 2024-10-30 | 2024-11-11 | 2 | 2024-10: 1, 2024-11: 1 |
| 4 | 2025-03-26 | 2025-04-02 | 5 | 2025-03: 4, 2025-04: 1 |
| 5 | 2025-04-30 | 2025-05-21 | 9 | 2025-04: 1, 2025-05: 8 |

## STEP 4 — 판정 근거

판정 규칙:

```text
SUPPORT_CEILING_CONFIRMED
비중복 체결 사건 수가 12 ± 2
```

관측값:

```text
train_1 = 12
train_2 = 11
train_3 = 12
```

세 fold가 모두 조건을 만족하므로 판정은 `SUPPORT_CEILING_CONFIRMED`다.

추가로 gap 기반 독립 사건은 각 fold 5개뿐이고 effective event count도 약 3.79~4.08이다. 즉 11~12개 체결이 존재하더라도 완전히 별개의 시장 사건 11~12개에서 나온 것이 아니라, 약 4개 수준의 큰 신호 군집 안에서 조기 interval-break 후 재진입이 반복된 구조다.

## 해석

1. 거래수 factor만 높여도 20건 후보가 나오기 어려운 이유가 확인됐다.
2. raw joint-pass day 25~32일은 시간적으로 밀집되어 38~48%만 독립 거래가 된다.
3. train_2는 가장 심한 일반화 병목이며, 29일 중 18일이 기존 거래에 흡수된다.
4. 단순 profit concentration이나 factor 조정만으로는 underlying event support를 늘릴 수 없다.
5. 다음 구조 변경 대상은 strict-AND interval 자체, temporal de-clustering, 또는 독립 사건 수를 직접 fitness/gate에 넣는 방식이어야 한다.

## 보호·무결성

분석 시작 보호 SHA:

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

분석 종료 시 다시 비교하며 세 파일은 출력 대상이 아니고 수정하지 않는다.

Daemon 기준:

```text
PID: 494330
starttime_ticks: 36014393
command: live_candidate_slots.py daemon --interval 60
```

분석 시작 Git 상태:

```text
branch: feat/intraday-reversal-ga
working tree: clean
```
