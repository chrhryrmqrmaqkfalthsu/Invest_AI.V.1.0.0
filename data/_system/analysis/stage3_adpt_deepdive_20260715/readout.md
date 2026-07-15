# Stage 3 기간 정합성 + ADPT 거래 실태 + feature 여지 조사 — read-only

## STEP 1 — ADPT 실제 투자 성과 요약

주의: 수익률은 `entry_price → exit_price` 가격 기반 gross return이다. 수수료·슬리피지·세금은 미반영이므로 실전 수익률은 이보다 낮다. 금액 환산은 1회 거래당 동일 10,000 USD 명목 기준이다. GA/재학습/새 백테스트는 실행하지 않고 기존 fold-best trade ledger를 읽어 재계산했다.

| fold | trades | avg hold | median hold | min/max hold | avg ret % | median ret % | win % | payoff | MDD % | total pct-pts | compounded % | max loss % | max gain % | exit reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| train_1 | 16 | 2.50 | 2.00 | 2/5 | 5.46 | 4.89 | 100.00 | N/A | 0.00 | 87.33 | 132.62 | +1.62 | 12.78 | interval-break 16 |
| train_2 | 19 | 2.11 | 2.00 | 2/3 | 5.07 | 4.60 | 73.68 | 4.86 | -3.78 | 96.41 | 149.15 | -3.78 | 17.30 | interval-break 19 |
| train_3 | 20 | 2.15 | 2.00 | 2/3 | 6.93 | 5.09 | 90.00 | 9.18 | -0.84 | 138.65 | 269.56 | -0.84 | 21.17 | interval-break 20 |
| ALL | 55 | 2.24 | 2.00 | 2/5 | 5.86 | 5.05 | 87.27 | 4.87 | -3.78 | 322.39 | 2041.93 | -3.78 | 21.17 | interval-break 55 |

핵심 해석:

- ADPT fold-best는 매우 짧은 보유 전략이다. 전체 median holding은 2일이고, 최대도 5일이다.
- 모든 청산이 `entry_interval_break`다. ATR stop, cooldown, 기타 청산은 원장상 발생하지 않았다.
- train_2가 가장 약한 구간이다. 승률 73.68%, MDD -3.78%, 최대 손실 -3.78%지만 payoff 4.86으로 손익비가 좋다.
- train_3는 평균 수익률 6.93%, 승률 90%, MDD -0.84%로 가장 균형이 좋다.

## STEP 0 — 데이터·객체·기간 규정 검증

### ADPT 객체 존재

| 대상 | 경로 | 확인 |
|---|---|---|
| fold-best 3개 거래 원장 | `data/_system/analysis/stage3_multiticker_v5_probe_20260715/ADPT/fold_best_trade_level.jsonl` | 존재, 55 rows |
| fold-best summary | `data/_system/analysis/stage3_multiticker_v5_probe_20260715/ADPT/fold_best_summary.json` | 존재 |
| all3 후보 | `qualify_cross_fold_matrix.jsonl`, `qualify_candidate_rulebooks.jsonl` | 존재, 1개 |
| OHLCV cache | `data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/ADPT.pkl` | 존재 |

ADPT fold-best hashes:

| fold | candidate_hash |
|---|---|
| train_1 | `1b4db2d534374cf60e03ed2a0177edcfc2c8b000b3d860a8be6351025ce42633` |
| train_2 | `55c9381b72245c640bafd9096be4ec9bf8556663d58741aedb8e69442a060526` |
| train_3 | `3627625692c71a9ff047054cadb1a90db4bac13366dd786f29e0d61ce0d1e529` |

ADPT all3 candidate:

| candidate_hash | cross-fold object SHA |
|---|---|
| `3c950cfa5f239b1530cb312ad4b224b74ad1e7d8922d5dbe1c6e0146774231d6` | `cf8615fe097218846c331db95a931ce95914ae27b3e9a7a600f99aaed3688d53` |

Data SHA:

| item | SHA / value |
|---|---|
| ADPT OHLCV cache SHA | `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503` |
| ADPT D-5 feature matrix SHA | `7ae6f2107da975d4c43c628705be74719f7203ee3469a3a205c23eaa2588ea60` |
| ADPT OHLCV rows | 1527 |
| ADPT OHLCV coverage | 2020-05-18 ~ 2026-06-15 |

### Stage 3 기간 정합성

코드 기준 정의:

- `scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001:75-83`에 `TRAIN_SPLITS`와 `RECENT_1Y_PERIOD`가 정의돼 있다.
- `TRAIN_SPLITS`는 `train_1=2022-07-01~2023-06-30`, `train_2=2023-07-01~2024-06-30`, `train_3=2024-07-01~2025-06-30`이다.
- `RECENT_1Y_PERIOD`는 `2025-07-01~data_end`다.
- `PURE_OOS_VALIDATION_PERIODS`는 `train_1`, `train_2`, `recent_1y`다. 같은 파일 `:84-90` 및 `engine/pipeline/stage3_gate.py:14`, `:236-238`에서 final/profile OOS 쪽은 `recent_1y`를 요구한다.
- `scripts/research/run_stage3_aap_newfitness_official.py:562-565`는 이번 qualify cross-fold pass count를 `train_1`, `train_2`, `train_3`에 대해 집계한다.
- `scripts/research/run_stage3_aap_tradecount_factor_v3_host.py:495`에도 이번 실행 설명이 `qualify: population 100 / generations 40 × train_1·train_2·train_3`로 기록돼 있다.

실제 ADPT v5 다종목 run 기간:

| fold | start | end | 사용 여부 |
|---|---|---|---|
| train_1 | 2022-07-01 | 2023-06-30 | qualify/fold-best 사용 |
| train_2 | 2023-07-01 | 2024-06-30 | qualify/fold-best 사용 |
| train_3 | 2024-07-01 | 2025-06-30 | qualify/fold-best 사용 |
| recent_1y | 2025-07-01 | 2026-06-15 | 이번 ADPT fold-best/all3 산출에는 미사용 |

판정: **최근 1년 규정과 이번 학습/qualify run은 일치하지 않는다.** 이번 ADPT 다종목 v5 run은 최근 1년(`2025-07-01~2026-06-15`)이 아니라 과거 3개 1년 fold(`2022-07-01~2025-06-30`)로 qualify/fold-best를 만들었다. ADPT OHLCV에는 recent_1y OOS 여지가 존재하지만, 이번 산출물은 그 기간의 성과를 검증하지 않는다.

## STEP 1 — 거래 원장

상세 거래별 원장은 `adpt_trade_ledger.md`에 저장했다. 핵심 원장 source는 기존 canonical file:

`data/_system/analysis/stage3_multiticker_v5_probe_20260715/ADPT/fold_best_trade_level.jsonl`

## STEP 2 — ADPT feature 발화 실태

### fold-best rulebook interval support

| fold | eligible days | strict-AND pass days | ma_trend pass | macd pass | rsi pass | bollinger pass | volume pass |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 251 | 18 | 123 | 120 | 146 | 105 | 93 |
| train_2 | 250 | 22 | 174 | 179 | 171 | 204 | 139 |
| train_3 | 250 | 20 | 108 | 162 | 96 | 84 | 114 |

각 feature는 fold별로 모두 발화한다. AAP에서 관찰했던 특정 feature, 특히 ma_trend가 사실상 무발화하는 문제는 ADPT fold-best에서는 보이지 않는다.

### fold-best interval ranges

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio |
|---|---|---|---|---|---|
| train_1 | [-15.211, -0.429] | [-1.993, 0.053] | [29.986, 51.930] | [0.011, 0.435] | [0.482, 0.895] |
| train_2 | [-14.248, -0.003] | [-2.904, 1.074] | [39.581, 64.563] | [-0.074, 0.772] | [0.718, 1.114] |
| train_3 | [-0.171, 6.978] | [-3.095, 0.435] | [48.915, 58.481] | [0.384, 0.687] | [0.340, 0.965] |

### leave-one-out support

`leave-one-out`은 해당 feature 하나를 빼고 나머지 4개 feature만 strict-AND 했을 때 pass day 수다. 값이 strict-AND pass days와 가까울수록 그 feature가 추가 병목으로 작동한다.

| fold | strict-AND | without ma_trend | without macd | without rsi | without bollinger | without volume |
|---|---:|---:|---:|---:|---:|---:|
| train_1 | 18 | 29 | 26 | 18 | 19 | 45 |
| train_2 | 22 | 45 | 39 | 59 | 23 | 60 |
| train_3 | 20 | 37 | 22 | 22 | 21 | 35 |

해석:

- train_1은 rsi가 병목에 가깝다. rsi를 빼도 pass day가 18로 그대로다.
- train_2는 bollinger가 가장 병목에 가깝다. bollinger를 빼도 22 → 23으로 거의 늘지 않는다.
- train_3는 bollinger/rsi/macd가 병목에 가깝다. 특히 bollinger 제외 시 20 → 21, macd/rsi 제외 시 20 → 22뿐이다.
- volume은 train_1/2/3에서 빼면 pass day가 비교적 많이 늘어나므로 독립 필터 역할을 한다.

### ADPT gate bottleneck

| fold | candidates | trade<8 count | trade<8 rate | win-rate<60 count | win-rate<60 rate | both gates pass | qualify pass |
|---|---:|---:|---:|---:|---:|---:|---:|
| train_1 | 300 | 2 | 0.67% | 170 | 56.67% | 128 | 117 |
| train_2 | 300 | 82 | 27.33% | 140 | 46.67% | 78 | 76 |
| train_3 | 300 | 169 | 56.33% | 18 | 6.00% | 113 | 106 |

ADPT에도 trade<8 병목은 존재한다. 특히 train_3에서 56.33%, train_2에서 27.33%다. 반면 train_1은 trade<8이 거의 없다. `quality=0 zero-size day`라는 day-level 기록은 발견되지 않았다. 다만 final audit의 `ce_boil_zero=true`는 one-sided/missing-domain/validator/quality-override 문제가 없다는 뜻으로, zero-size day와는 다른 항목이다.

## STEP 3 — feature 확장 여지 예비 판단

판정: **INCONCLUSIVE_NEEDS_NULLTEST**

근거:

- ADPT는 기존 5 feature가 모두 발화한다. AAP식 ma_trend 무발화 공백은 없다.
- fold-best 성과는 이미 짧은 보유일·높은 승률·낮은 MDD로 양호하다. feature 확장이 “필수”라고 말할 근거는 약하다.
- 다만 train_2와 train_3에서 candidate-level trade<8 탈락이 크다. 새 feature가 단순히 더 좁은 필터가 되면 거래 수 병목을 악화시킬 수 있지만, 반대로 국면/변동성/relative strength로 기존 병목 feature와 다른 정보를 제공한다면 support quality를 개선할 여지는 있다.
- 기존 AAP null-test에서 `trend_chop20`, `atr14_pct`, `range_pct_rank60`, `rs_peer3_ret20`은 뚜렷한 신호를 보이지 않았지만, ADPT에 대한 직접 null-test는 아직 없다.

후보별 예비 판단:

| 후보 | ADPT 예비 판단 | 근거 |
|---|---|---|
| trend_chop20 | 보류 | ADPT는 strong trend/mean-reversion 국면이 섞여 있고 train_3에서 trend regime별 성과 차이가 있었으나, 기존 feature가 이미 충분히 발화한다. |
| atr14_pct | 보류 | train_2/3에서 high-vol 구간이 거래에 포함된다. 위험/국면 필터로 쓸 수는 있으나 거래 수를 더 줄일 수 있다. |
| range_pct_rank60 | 보류 | bollinger가 train_2/3 병목에 가깝기 때문에 range/rank 계열이 중복일 가능성이 있다. |
| rs_peer3_ret20 | 보류/상대적으로 흥미 있음 | ADPT는 biotech/healthcare 성격이라 peer-relative가 독립 정보를 줄 가능성이 있지만, 적절한 peer basket 정의가 선행되어야 한다. |

다음 단계가 필요하다면 ADPT 전용 REAL/SHUFFLED null-test가 맞다. 현재 read-only 근거만으로는 `FEATURE_EXPANSION_PROMISING_FOR_ADPT`를 확정할 수 없다.

## 보호파일 / daemon / git

보호파일 시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- 실제 host: `invest-bot`.
- 산출 전 backup commit: `bd5ee22`.
