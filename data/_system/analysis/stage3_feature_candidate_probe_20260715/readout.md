# Stage3 AAP feature 확장 후보 독립성 분석

## 실행 사실

- 실행 host: `invest-bot`
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 범위: read-only 분석. 코드 수정, 데이터 수정, GA, 재학습, 백테스트 실행 없음.
- 분석 입력:
  - AAP OHLCV: `data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv`
  - v6 fold-best trade log: `data/_system/analysis/stage3_aap_eec_relax_v6_20260715/AAP/fold_best_trade_level.jsonl`
  - v6 cross-fold matrix: `data/_system/analysis/stage3_aap_eec_relax_v6_20260715/AAP/qualify_cross_fold_matrix.jsonl`
  - benchmark: `benchmark_SPY_ohlcv.csv`, `benchmark_QQQ_ohlcv.csv`

## STEP 0 — 데이터·기존 feature 확인

### 입력 SHA

| 파일 | SHA256 |
|---|---|
| AAP OHLCV | `6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717` |
| SPY benchmark OHLCV | `3f0e999acf7c2778d47b6d25b3a2edcce6c2e030cae64f8e759cbf89cded5825` |
| QQQ benchmark OHLCV | `b6523573bbcda36875ce1e89f051dd6cd238edcba592190b679b93b0e0c8a932` |
| v6 fold-best trade log | `1f4248cd3e1f51e864e46587e34c663925727f673c30e1deb9430d8fe8b5273d` |

상대강도용 지수 benchmark는 SPY/QQQ가 가용하다. 동종 종목 peer OHLCV, 예: GPC/ORLY/AZO, 는 작업 경로에서 발견되지 않았다. 따라서 peer-relative 후보는 **INSUFFICIENT_DATA**로만 표기하고 계산하지 않았다.

### 기존 5개 feature 정합

기존 strict entry feature는 신호일 D의 값이 아니라 D-5 거래일 행에서 추출된다. 코드 기준:

- D-5 lag 선언: `evaluator.py:29`
- D-5 행 사용: `evaluator.py:59-78`
- feature 정의: `evaluator.py:86-118`
- strict interval fail-closed: `evaluator.py:261-282`

기존 5개 feature:

| feature | 정의 |
|---|---|
| `ma_trend` | `0.5 * [(MA5/MA20-1) + (MA20/MA60-1)] * 100` |
| `macd_hist` | `MACD_hist / Close * 100` |
| `rsi` | RSI |
| `bb_position` | `(Close-BB_lower)/(BB_upper-BB_lower)` |
| `volume_ratio` | `Volume_ratio` |

정합성 점검:

| 항목 | 값 |
|---|---:|
| D-5 기존 feature series SHA256 | `d9d33f8d7120a7a9d764986b6a2643b0ba200485d63178a1db909f06e0c58ccc` |
| 후보 D-5 series SHA256 | `59c4e9d015ea47be63c22cf6209c9ce4ca7a08e1f16d3dfd8d7b9188d43691c2` |
| v6 fold-best trade log 대조 거래 수 | 51 |
| entry_features mismatch | 0 |

결론: OHLCV에서 재계산한 기존 5개 D-5 feature series는 v6 fold-best 로그의 `entry_features`와 일치한다.

## STEP 1 — 후보 지표 계산 정의

후보를 진입 판단에 넣지 않고, D-5 shift를 적용한 일별 series로만 별도 계산했다.

| 그룹 | 후보 | 정의 / 파라미터 |
|---|---|---|
| 국면/추세 전환 | `adx14` | Wilder 방식 ADX 14. 추세 강도. 방향성 없음. |
| 국면/추세 전환 | `adx14_delta5` | `ADX14 - ADX14.shift(5)`. 추세 강도 변화. |
| 국면/추세 전환 | `ma20_slope5_pct` | `(MA20 / MA20.shift(5) - 1) * 100`. MA20 단기 slope. |
| 국면/추세 전환 | `ma_trend_delta10` | `ma_trend - ma_trend.shift(10)`. 기존 ma_trend 변화량. |
| 국면/횡보 | `trend_chop20` | `abs(Close.pct_change(20)) / rolling_sum(abs(ret1),20)`. 0에 가까울수록 chop/왕복, 1에 가까울수록 일방 추세. |
| 변동성 | `atr14_pct` | `ATR14 / Close * 100`. |
| 변동성 | `atr14_pct_rank60` | 60일 rolling percentile rank of `atr14_pct`, min_periods=20. |
| gap | `gap_abs_pct` | `abs(Open / prev_close - 1) * 100`. |
| gap | `gap_abs_mean20` | 20일 평균 `gap_abs_pct`, min_periods=5. |
| range | `range_pct_rank60` | 60일 rolling percentile rank of `(High-Low)/Close*100`, min_periods=20. |
| 상대강도 | `rs_spy_ret20` | AAP 20d return - SPY 20d return, pct point. |
| 상대강도 | `rs_spy_ret60` | AAP 60d return - SPY 60d return, pct point. |
| 상대강도 | `rs_spy_line_slope20` | `(AAP/SPY).pct_change(20) * 100`. |
| 상대강도 | `rs_qqq_ret20` | AAP 20d return - QQQ 20d return, pct point. |
| 상대강도 | `rs_qqq_ret60` | AAP 60d return - QQQ 60d return, pct point. |
| peer 상대강도 | peer-relative return | GPC/ORLY/AZO 등 OHLCV 미확보로 계산 보류. |

## STEP 2 — 독립성 분석

### 유효성 / 분포 점검

모든 계산 가능 후보는 valid count 1452~1521, missing 0.33~4.85% 범위로 결측 과다는 없었다. 상수 series도 없었다.

| 후보 | valid | missing% | std | median | p25~p75 |
|---|---:|---:|---:|---:|---:|
| adx14 | 1520 | 0.39 | 11.244 | 24.767 | 18.732~32.710 |
| adx14_delta5 | 1515 | 0.72 | 5.070 | -0.703 | -3.413~3.058 |
| ma20_slope5_pct | 1516 | 0.66 | 3.289 | 0.038 | -1.834~1.524 |
| ma_trend_delta10 | 1511 | 0.98 | 4.167 | 0.200 | -2.100~2.403 |
| trend_chop20 | 1501 | 1.64 | 0.166 | 0.215 | 0.104~0.324 |
| atr14_pct | 1521 | 0.33 | 1.178 | 3.463 | 2.622~4.447 |
| atr14_pct_rank60 | 1502 | 1.57 | 0.309 | 0.533 | 0.217~0.767 |
| gap_abs_pct | 1520 | 0.39 | 1.661 | 0.516 | 0.223~0.968 |
| gap_abs_mean20 | 1516 | 0.66 | 0.424 | 0.703 | 0.566~0.938 |
| range_pct_rank60 | 1502 | 1.57 | 0.290 | 0.517 | 0.267~0.767 |
| rs_spy_ret20 | 1492 | 2.23 | 13.050 | -1.367 | -7.799~4.179 |
| rs_spy_ret60 | 1452 | 4.85 | 21.572 | -3.219 | -18.285~5.895 |
| rs_spy_line_slope20 | 1492 | 2.23 | 12.733 | -1.333 | -7.717~4.191 |
| rs_qqq_ret20 | 1493 | 2.16 | 13.825 | -1.839 | -8.696~4.660 |
| rs_qqq_ret60 | 1453 | 4.78 | 22.515 | -4.062 | -19.536~6.493 |

### Pearson: 후보 vs 기존 5개 feature

| 후보 | ma_trend | macd_hist | rsi | bb_position | volume_ratio |
|---|---:|---:|---:|---:|---:|
| adx14 | -0.139 | -0.038 | -0.174 | -0.042 | -0.056 |
| adx14_delta5 | -0.022 | -0.225 | -0.043 | -0.051 | -0.025 |
| ma20_slope5_pct | 0.839 | 0.376 | 0.758 | 0.486 | 0.015 |
| ma_trend_delta10 | 0.350 | 0.869 | 0.554 | 0.575 | 0.018 |
| trend_chop20 | -0.021 | -0.088 | -0.007 | 0.037 | -0.011 |
| atr14_pct | -0.246 | -0.255 | -0.333 | -0.280 | 0.003 |
| atr14_pct_rank60 | -0.349 | -0.363 | -0.549 | -0.530 | 0.017 |
| gap_abs_pct | 0.003 | -0.033 | -0.044 | -0.062 | 0.496 |
| gap_abs_mean20 | -0.035 | -0.101 | -0.137 | -0.079 | -0.012 |
| range_pct_rank60 | -0.111 | -0.123 | -0.174 | -0.186 | 0.368 |
| rs_spy_ret20 | 0.739 | 0.523 | 0.757 | 0.571 | -0.022 |
| rs_spy_ret60 | 0.809 | 0.054 | 0.600 | 0.320 | -0.020 |
| rs_spy_line_slope20 | 0.738 | 0.523 | 0.760 | 0.575 | -0.021 |
| rs_qqq_ret20 | 0.707 | 0.509 | 0.730 | 0.548 | -0.023 |
| rs_qqq_ret60 | 0.772 | 0.056 | 0.581 | 0.307 | -0.018 |

### Spearman: 후보 vs 기존 5개 feature

| 후보 | ma_trend | macd_hist | rsi | bb_position | volume_ratio |
|---|---:|---:|---:|---:|---:|
| adx14 | -0.081 | -0.001 | -0.132 | -0.072 | -0.077 |
| adx14_delta5 | 0.047 | -0.125 | 0.019 | -0.031 | -0.046 |
| ma20_slope5_pct | 0.783 | 0.350 | 0.780 | 0.602 | 0.022 |
| ma_trend_delta10 | 0.187 | 0.879 | 0.494 | 0.640 | 0.028 |
| trend_chop20 | -0.005 | -0.006 | 0.003 | 0.031 | -0.010 |
| atr14_pct | -0.225 | -0.096 | -0.319 | -0.259 | -0.055 |
| atr14_pct_rank60 | -0.357 | -0.398 | -0.554 | -0.539 | -0.052 |
| gap_abs_pct | -0.052 | -0.013 | -0.078 | -0.076 | 0.145 |
| gap_abs_mean20 | -0.118 | -0.058 | -0.191 | -0.150 | -0.047 |
| range_pct_rank60 | -0.113 | -0.134 | -0.178 | -0.185 | 0.352 |
| rs_spy_ret20 | 0.636 | 0.452 | 0.764 | 0.684 | -0.006 |
| rs_spy_ret60 | 0.826 | -0.049 | 0.624 | 0.364 | -0.010 |
| rs_spy_line_slope20 | 0.636 | 0.453 | 0.765 | 0.685 | -0.006 |
| rs_qqq_ret20 | 0.583 | 0.425 | 0.698 | 0.623 | -0.005 |
| rs_qqq_ret60 | 0.756 | -0.040 | 0.575 | 0.341 | -0.004 |

### 후보별 기존 feature 최대 상관

| 후보 | Pearson max abs | top old | Spearman max abs | top old | 독립성 메모 |
|---|---:|---|---:|---|---|
| adx14 | 0.174 | rsi | 0.132 | rsi | 독립 |
| adx14_delta5 | 0.225 | macd_hist | 0.125 | macd_hist | 독립 |
| ma20_slope5_pct | 0.839 | ma_trend | 0.783 | ma_trend | 중복 |
| ma_trend_delta10 | 0.869 | macd_hist | 0.879 | macd_hist | 중복 |
| trend_chop20 | 0.088 | macd_hist | 0.031 | bb_position | 강한 독립 |
| atr14_pct | 0.333 | rsi | 0.319 | rsi | 독립 |
| atr14_pct_rank60 | 0.549 | rsi | 0.554 | rsi | 부분 독립 |
| gap_abs_pct | 0.496 | volume_ratio | 0.145 | volume_ratio | 독립, Pearson은 outlier 영향 |
| gap_abs_mean20 | 0.137 | rsi | 0.191 | rsi | 강한 독립 |
| range_pct_rank60 | 0.368 | volume_ratio | 0.352 | volume_ratio | 독립 |
| rs_spy_ret20 | 0.757 | rsi | 0.764 | rsi | 중복 경계 초과 |
| rs_spy_ret60 | 0.809 | ma_trend | 0.826 | ma_trend | 중복 |
| rs_spy_line_slope20 | 0.760 | rsi | 0.765 | rsi | 중복 |
| rs_qqq_ret20 | 0.730 | rsi | 0.698 | rsi | 경계선 독립 |
| rs_qqq_ret60 | 0.772 | ma_trend | 0.756 | ma_trend | 중복 |

### 후보끼리의 중복

Pearson 절대값 0.75 이상 후보쌍:

| 후보 1 | 후보 2 | Pearson |
|---|---|---:|
| ma20_slope5_pct | rs_spy_ret20 | 0.885 |
| ma20_slope5_pct | rs_spy_line_slope20 | 0.886 |
| ma20_slope5_pct | rs_qqq_ret20 | 0.849 |
| rs_spy_ret20 | rs_spy_line_slope20 | 0.999 |
| rs_spy_ret20 | rs_qqq_ret20 | 0.985 |
| rs_spy_ret60 | rs_qqq_ret60 | 0.983 |
| rs_spy_line_slope20 | rs_qqq_ret20 | 0.984 |

결론: 상대강도 20일 계열은 서로 거의 같은 정보를 담고, MA20 slope와도 많이 겹친다. 상대강도 60일은 기존 ma_trend와 더 겹친다. 상대강도를 남긴다면 `rs_qqq_ret20` 하나만 경계선 후보로 보되, 우선순위는 낮다.

## STEP 3 — train_2 공백 커버 가능성

기준 기간: train_2 = 2023-07-01 ~ 2024-06-30.

이전 분석에서 train_2 실패 all2는 median `ma_trend` interval `[-8.1718, -2.3806]`에 갇혔다. 이 interval을 train_2 D-5 feature series에 대입하면:

| 항목 | 일수 |
|---|---:|
| train_2 전체 거래일 | 250 |
| 실패 all2 median ma_trend interval 안 | 62 |
| 실패 all2 median ma_trend interval 밖 | 188 |

즉 train_2의 75.2% 날짜는 실패 all2의 ma_trend interval 기준으로 막힌다.

비교 샘플:

| 샘플 | 설명 | 수 |
|---|---|---:|
| fold-best train_2 success dates | v6 train_2 fold-best entry signal dates | 19 |
| all2 fail train_2 entry dates | all2 57개가 train_2에서 실제 낸 entry dates, count-weighted | 132 |

all2 fail train_2 entry month 분포: 2023-08 116건, 2023-10 3건, 2023-11 13건.

### 후보별 train_2 분리력

`Cliff delta`는 fold-best train_2 entry dates 값이 all2 fail train_2 entry dates 값보다 큰지/작은지를 보는 비모수 효과 크기다. 절대값이 클수록 두 집단을 잘 구분한다.

| 후보 | success median | fail median | Cliff delta | success percentile in train_2 | train_2 공백 커버 메모 |
|---|---:|---:|---:|---:|---|
| adx14 | 33.156 | 23.248 | 0.429 | 62.0 | 독립이나 분리력 약함 |
| adx14_delta5 | -2.642 | -1.272 | -0.292 | 36.8 | 약함 |
| ma20_slope5_pct | 1.125 | 0.352 | 0.588 | 66.4 | 분리력은 있으나 기존 ma_trend와 중복 |
| ma_trend_delta10 | -1.519 | 3.586 | -0.998 | 34.8 | 분리력은 크나 기존 macd_hist와 중복 |
| trend_chop20 | 0.167 | 0.066 | 0.664 | 33.6 | 독립 + 실패 2023-08 저-chop/일방성 구간 구분 가능 |
| atr14_pct | 3.424 | 3.054 | 0.796 | 30.4 | 독립 + 실패 구간보다 높은 ATR 조건 구분 |
| atr14_pct_rank60 | 0.300 | 0.217 | 0.251 | 29.2 | 독립이나 분리력 약함 |
| gap_abs_pct | 0.574 | 0.941 | -0.593 | 53.6 | 독립 + 과도 gap 구간 회피 후보 |
| gap_abs_mean20 | 0.698 | 0.636 | 0.544 | 46.4 | 독립 + 완만한 분리력 |
| range_pct_rank60 | 0.333 | 0.717 | -0.665 | 30.4 | 독립 + 과도 intraday range 구간 회피 후보 |
| rs_spy_ret20 | 0.045 | 3.130 | -0.372 | 59.6 | 기존 rsi와 중복 경계, 약함 |
| rs_spy_ret60 | 10.645 | -47.030 | 0.963 | 75.2 | 분리력 크나 기존 ma_trend와 중복 |
| rs_spy_line_slope20 | 0.046 | 3.155 | -0.372 | 59.6 | rs_spy_ret20과 사실상 동일 |
| rs_qqq_ret20 | -0.199 | 5.706 | -0.511 | 60.0 | 경계선 독립, 상대강도 20일 중 하나만 남길 수 있음 |
| rs_qqq_ret60 | 11.690 | -50.346 | 0.962 | 77.2 | 분리력 크나 기존 ma_trend와 중복 |

해석:

- `trend_chop20`, `atr14_pct`, `gap_abs_pct`, `range_pct_rank60`은 기존 5개 feature와 낮은 상관이면서 train_2 success/fail entry dates를 분리한다.
- `ma20_slope5_pct`, `ma_trend_delta10`, RS 60일 계열은 train_2 분리력은 크지만 기존 ma_trend/macd/rsi와 높은 상관이라 null-test 전에 중복 위험이 크다.
- 상대강도는 데이터는 확보됐지만 20일 계열끼리 매우 중복이다. `rs_qqq_ret20`만 경계선 후보로 남길 수 있으나, top 3에는 넣지 않는다.

## STEP 4 — 후보별 판정

판정 기준:

- `INDEPENDENT_AND_USEFUL`: 기존 5개와 최대 절대 상관이 대체로 0.75 미만이고 train_2 success/fail 분리력도 확인됨.
- `REDUNDANT`: 기존 5개 또는 후보끼리 상관이 높아 새 정보 가능성이 낮음.
- `INSUFFICIENT_DATA`: 필요한 데이터 미확보.
- `INDEPENDENT_BUT_WEAK_TRAIN2_EVIDENCE`: 상관은 낮지만 train_2 공백 커버 근거가 약함. 이 보조 라벨은 독립 후보를 잘못 REDUNDANT로 분류하지 않기 위해 사용했다.

| 후보 | 판정 | 근거 |
|---|---|---|
| adx14 | INDEPENDENT_BUT_WEAK_TRAIN2_EVIDENCE | max old corr 0.174/0.132로 독립. Cliff 0.429로 기준 미달. |
| adx14_delta5 | INDEPENDENT_BUT_WEAK_TRAIN2_EVIDENCE | max old corr 0.225/0.125. Cliff -0.292로 약함. |
| ma20_slope5_pct | REDUNDANT | ma_trend와 Pearson 0.839, Spearman 0.783. |
| ma_trend_delta10 | REDUNDANT | macd_hist와 Pearson 0.869, Spearman 0.879. |
| trend_chop20 | INDEPENDENT_AND_USEFUL | max old corr 0.088/0.031, Cliff 0.664. |
| atr14_pct | INDEPENDENT_AND_USEFUL | max old corr 0.333/0.319, Cliff 0.796. |
| atr14_pct_rank60 | INDEPENDENT_BUT_WEAK_TRAIN2_EVIDENCE | max old corr 0.549/0.554, Cliff 0.251. |
| gap_abs_pct | INDEPENDENT_AND_USEFUL | max old corr 0.496 Pearson, 0.145 Spearman; Cliff -0.593. |
| gap_abs_mean20 | INDEPENDENT_AND_USEFUL | max old corr 0.137/0.191, Cliff 0.544. |
| range_pct_rank60 | INDEPENDENT_AND_USEFUL | max old corr 0.368/0.352, Cliff -0.665. |
| rs_spy_ret20 | REDUNDANT | rsi와 0.757/0.764, rs_spy_line_slope20과 0.999. |
| rs_spy_ret60 | REDUNDANT | ma_trend와 0.809/0.826. |
| rs_spy_line_slope20 | REDUNDANT | rs_spy_ret20과 0.999, rsi와 0.760/0.765. |
| rs_qqq_ret20 | INDEPENDENT_AND_USEFUL | 경계선. old max 0.730/0.698, Cliff -0.511. 다만 RS20 계열끼리 중복 커서 낮은 우선순위. |
| rs_qqq_ret60 | REDUNDANT | ma_trend와 0.772/0.756, rs_spy_ret60과 0.983. |
| peer-relative return | INSUFFICIENT_DATA | peer OHLCV 미확보. |

## 다음 단계 후보 권고

### 1순위: `trend_chop20`

- 기존 5개와 거의 무상관: Pearson max 0.088, Spearman max 0.031.
- train_2 분리력: success median 0.167 vs fail median 0.066, Cliff delta 0.664.
- 의미: ma_trend가 막던 train_2에서 “일방 하락/실패 반등”과 “되돌림/왕복이 있는 구간”을 분리할 가능성.
- 다음 단계: 구현 후 null-test에서 기존 5개 + `trend_chop20` 단독 추가 효과 검증.

### 2순위: `atr14_pct`

- 기존 5개와 낮은 상관: Pearson max 0.333, Spearman max 0.319.
- train_2 분리력: success median 3.424 vs fail median 3.054, Cliff delta 0.796.
- 의미: 단순 momentum이 아니라 변동성 regime 자체를 entry 조건에 제공한다.
- 주의: ATR rank보다 raw `atr14_pct`가 train_2 분리력이 더 강했다.

### 3순위: `range_pct_rank60`

- 기존 5개와 낮은 상관: Pearson max 0.368, Spearman max 0.352.
- train_2 분리력: success median 0.333 vs fail median 0.717, Cliff delta -0.665.
- 의미: 실패 all2가 몰린 2023-08의 과도 intraday range/불안정 regime 회피에 도움 가능.
- `gap_abs_pct`도 유사 후보지만 raw gap은 outlier 영향이 있어 rank 기반 range를 우선한다.

### 보류 후보

- `rs_qqq_ret20`: 상대강도 데이터는 있고 train_2 분리력도 있으나 기존 rsi/ma_trend와 경계선 상관이고 RS20 후보끼리 중복이 크다. top 3 null-test 이후 2차 후보로 권고.
- ADX 계열: 독립성은 좋지만 train_2 공백 커버 근거가 약해 우선순위 낮음.
- MA slope/ma_trend_delta/RS60: 분리력은 있어도 기존 5개와 중복 위험이 크므로 이번 확장 1차 타겟에서는 제외.

## 보호파일 / daemon / git 기록

시작 SHA:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330`: 시작 시 유지 확인.
- 산출물 작성 전 기준점 백업 commit: `424fe8b`.
- 종료 SHA, readout SHA, daemon 상태, git 상태는 `SHA256SUMS.txt`에 기록한다.
