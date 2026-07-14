# 5개 feature 신호 존재 여부 — 6,174종목 null 검정

- 작업일: 2026-07-14
- TRAIN만 선택·점수·null에 사용
- OOS·STRESS는 TRAIN 순위 확정 후 참고만 사용
- GA·백테스트·재학습 없음
- 최종 판정: **WEAK**

## STEP 0 — 인벤토리

OHLCV source:
`data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache`

| 구분 | 종목 수 |
|---|---:|
| Root universe | 6,174 |
| OHLCV 정상 로드 | 5,758 |
| Cache 실패·누락 | 416 |
| TRAIN 경계 미충족 | 157 |
| 내부 거래일 결측 | 1 (`MBAY`) |
| **TRAIN 751세션 완전 커버** | **5,600** |

TRAIN 세션: train_1 251일, train_2 250일, train_3 250일. Snapshot tree SHA:
`d79ddc31f307a02c219cb754f4596356fac4a9ee362cb3648d347f2b49486d5d`

STRESS는 기존 파이프라인 그대로다.

```text
파일: scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
상수: STRESS_PERIOD, EXIT_CHECK_PERIOD
label: stress_pre_2022h1
start: 종목별 data_start (대상 범위 2020-05-18~2022-06-10)
end: 2022-06-30
```

요청 OOS는 2025-07-01~2026-07-06이나 snapshot 최대 종료일은 2026-06-15다. 2026-07-06 완전 커버 종목은 0개이므로 종목별 실제 종료일까지 참고만 했다.

## 계산 정의

```text
raw OHLCV → calc_indicators → 5개 feature → 전체 시계열 shift(5)
신호 D → D+1 Open 진입 → 7거래일 보유 → D+8 Open 청산
Open 부재 시 Close, commission_rate 0.0005
승 = 비용 차감 실현수익 > 0.5%
일수익 = 실현수익 / 7
```

TRAIN 5,600종목 전체 pooled 분포의 feature별 10분위로 총 50개 단일-feature 조건을 고정했다. Fold별·종목별 튜닝은 없다. Fold당 신호 3건 미만이면 무효다.

```text
일관성 점수 = 3 fold 승률 최솟값
보조 수익 = 3 fold 평균 일수익 최솟값
```

실제 종목×조건 280,000개 중 유효 점수는 255,543개(91.27%)였다. 실제 일관성 승률은 평균 12.05%, 중앙값 10.53%, q95 31.58%, q99 40.00%, 최대 80.00%다.

## STEP 3 — Null

Seed `2026071401`, 각 100회.

1. Block null: fold 내부 5거래일 block 순서 셔플, block 내부 순서 유지.
2. Cross-ticker null: 매 반복 random-cycle derangement로 feature A와 다른 종목 B의 동일 날짜 수익 연결.

```text
Block null 28,000,000점
Cross-ticker null 28,000,000점
합계 56,000,000점
VM 6-process, ticker index 병합
총 47.83초
```

Null q95/q99는 100회 반복 quantile의 중앙값이다.

| Feature | 실제 q95 | Block q95/p | Cross q95/p | 실제 q99 | Block q99/p | Cross q99/p | 판정 |
|---|---:|---:|---:|---:|---:|---:|---|
| ALL | 31.58% | 31.58%/0.5446 | 31.25%/0.0099 | 40.00% | 39.39%/0.1485 | 40.00%/0.9802 | 없음 |
| **ma_trend** | **33.33%** | **32.00%/0.0099** | **31.25%/0.0099** | **42.86%** | **40.00%/0.0099** | **41.18%/0.0099** | **강함** |
| macd_hist | 31.58% | 31.82%/1.0000 | 31.03%/0.0099 | 40.00% | 40.00%/1.0000 | 40.00%/0.9802 | 없음 |
| rsi | 31.43% | 31.25%/0.0198 | 31.25%/0.1782 | 40.00% | 39.13%/0.0396 | 40.00%/0.9703 | 부분 |
| bb_position | 31.25% | 31.25%/0.9505 | 31.25%/0.9307 | 39.29% | 38.89%/0.0297 | 39.29%/0.7327 | 부분 |
| volume_ratio | 31.43% | 31.43%/0.5842 | 31.37%/0.4851 | 38.46% | 38.46%/0.8317 | 38.46%/0.8416 | 없음 |

`ma_trend` 실제 tail은 block q95/q99를 5.53%/1.42%, cross q95/q99를 6.00%/1.24% 비율로 초과했다. 가장 강한 구간은 `ma_trend < -6.026192`이며 유효 종목 1,888개, 실제 q95 43.15%, q99 52.94%다. 다만 해당 구간의 종목별 최소 일수익 중앙값은 -0.1633%/일이라 구간 전체가 양의 기대값인 것은 아니다.

## STEP 4 — 판정

```text
WEAK
```

`ma_trend`만 두 null의 q95·q99를 모두 p<=0.05로 넘었고, ALL pooled 분포는 실패했다. RSI·BB는 한 null 또는 한 tail에서만 약하다.

1. 전 5,600종목 GA 직행 근거는 부족하다.
2. 2단계를 진행한다면 TRAIN-only `ma_trend` tail 통과 종목으로 제한해야 한다.
3. RSI·BB·MACD·Volume은 feature 확장·표현 변경이 우선이다.

## 참고 OOS·STRESS

TRAIN 상위 20개를 TRAIN만으로 정한 뒤 참고 계산했다.

```text
OOS 양의 평균 일수익 12/20, 승률 60% 이상 8/20
OOS 평균 일수익 단순평균 +0.0461%
STRESS 양의 평균 일수익 10/20, 승률 60% 이상 3/20
```

상위 TRAIN 종목도 OOS·STRESS에서 일관되게 유지되지 않아 `WEAK` 판정과 모순되지 않는다.

## 보호 상태

시작·종료 SHA 동일:

```text
.env da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce
market_history.csv 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
market_history_v2.csv b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

Daemon PID `494330`, 상태 `Sl`, 시작 시각 `Sat Jul 11 20:16:00 2026` 유지. Source code·OHLCV·market data는 수정하지 않았다.

사전 백업 커밋: `7f16a53`
