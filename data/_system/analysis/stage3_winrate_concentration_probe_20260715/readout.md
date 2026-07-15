# AAP win-rate gate·시간 집중 read-only 분석

- 대상: `data/_system/analysis/stage3_aap_overlap_entry_v4_20260715/AAP/`
- 데이터: v4 qualify 300후보×3fold, v3/v4 fold-best, 동일 AAP OHLCV
- GA·백테스트·재학습: 실행하지 않음
- 시작 HEAD: `d9f715e13503f4174bdc79fba4121a3959188089`
- 분석 전 백업 commit: `db59696`

## 최종 판정: `MIXED`

train_1·train_2에서는 win-rate 60% 통과군의 EEC가 탈락군보다 낮고 fold-best 거래 60%가 한 승리 cluster에 집중됐다. train_3은 win-rate gate의 EEC 억제 효과가 거의 없고 strict pass 30일 중 17일이 quality score 0에 따른 position size 0으로 미체결됐다.

## STEP 0 — 입력·D-5 정합성

| 입력 | SHA-256 |
|---|---|
| v4 readout | `d59a537322da2ad966cd6fc0da9bea62d39c929052a64cafda3bd378129a1e07` |
| fold-best summary | `6da118e3b78aaef7e7de576e10113991026d7fc598d9718b7faad8eba2e9bf7c` |
| fold-best trades | `82f69b3aa465cb66521b341bdc3158e29f58db4fc526efe2f525271f96e02968` |
| candidate rulebooks | `cbeddef553c09a6e46d6534f16c6f44c5decd09e16a3e9adb24908eb6c1623c6` |
| cross-fold matrix | `84d5aa4173c436355f3bc5371ead1fead761a0369a57d4f299dc154227ad6b8f` |
| AAP OHLCV | `6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717` |

Fold-best hash: train_1 `17032e9b...ab3f8`, train_2 `ef516521...204f`, train_3 `35b536dd...1866`.

D-5 series는 `evaluator.py:59-118`의 날짜별 추출과 `run_stage3_aggressive.py:322-357`의 전체 `shift(5)`를 독립 계산했다.

```text
vectorized SHA = 0331aa572acbab3ebcf28bda625b3e643ec5a20a48249c1e2272609433b53629
direct SHA     = 0331aa572acbab3ebcf28bda625b3e643ec5a20a48249c1e2272609433b53629
```

48개 trade snapshot과 exact match, mismatch 0, 최대 절대 오차 0.0.

## STEP 1 — win-rate gate 병목

| fold | trade≥8 | win<60 탈락 | win≥60 통과 | qualify pass |
|---|---:|---:|---:|---:|
| train_1 | 295 | 180 | 115 | 101 |
| train_2 | 286 | 124 | 162 | 154 |
| train_3 | 296 | 122 | 174 | 155 |

탈락 후보 win-rate histogram:

| 구간 | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| 0–<20 | 10 | 1 | 0 |
| 20–<30 | 23 | 2 | 0 |
| 30–<40 | 66 | 10 | 3 |
| 40–<45 | 12 | 14 | 12 |
| 45–<50 | 1 | 15 | 24 |
| 50–<55 | 13 | 33 | 66 |
| 55–<60 | 55 | 49 | 17 |

중앙 win rate는 37.50% / 52.94% / 53.38%. 55–60% 비중은 30.56% / 39.52% / 13.93%. train_1은 저승률 전반에 넓고 train_2가 threshold 바로 아래 후보 비중이 가장 크다.

후보별 실제 `entry_dates`를 OHLCV trading index gap≤8로 cluster화했다.

| fold | 탈락 거래수 med | 통과 거래수 med | 탈락 EEC med | 통과 EEC med | EEC 변화 |
|---|---:|---:|---:|---:|---:|
| train_1 | 18 | 20 | 3.2821 | 2.6350 | **-19.71%** |
| train_2 | 16 | 16 | 4.7295 | 2.3753 | **-49.78%** |
| train_3 | 51 | 30 | 3.7317 | 3.7048 | -0.72% |

win rate–EEC Spearman ρ: `-0.6661 / -0.2331 / +0.3479`. train_1·2에서는 승률 통과가 사건 다양성 감소와 동행한다. train_3에서는 아니다.

## STEP 2 — 시간 집중

`EEC=1/Σ(cluster share²)`, cluster gap≤8 거래일.

| fold | v3 EEC | v4 EEC | v3 최대 share | v4 최대 share | v4 Σshare² |
|---|---:|---:|---:|---:|---:|
| train_1 | 4.0850 | 2.2989 | 32.00% | **60.00%** | 0.4350 |
| train_2 | 4.0628 | 2.5281 | 31.03% | **60.00%** | 0.3956 |
| train_3 | 3.7926 | 4.3689 | 37.50% | 36.67% | 0.2289 |

### train_1 v4 clusters

| 기간 | trades | 승/패 |
|---|---:|---:|
| 2022-07-01 | 1 | 1/0 |
| 2022-10-07~10 | 2 | 1/1 |
| **2022-12-16~2023-01-04** | **12** | **12/0** |
| 2023-04-05~13 | 5 | 5/0 |

최대 cluster가 concentration의 82.76%, 전체 승리의 63.16%를 차지한다.

### train_2 v4 clusters — 기존 11건 병목 fold

| 기간 | trades | 승/패 |
|---|---:|---:|
| 2023-07-25 | 1 | 1/0 |
| 2023-08-09 | 1 | 0/1 |
| 2023-08-22 | 1 | 0/1 |
| 2023-09-12~13 | 2 | 2/0 |
| **2023-10-23~2023-11-06** | **9** | **9/0** |
| 2024-06-26 | 1 | 1/0 |

최대 cluster가 concentration의 91.01%, 전체 승리의 69.23%를 차지한다. 이를 제외하면 4승2패=66.67%. v4에서 거래는 11→15건으로 늘었지만 gate 집중 효과는 세 fold 중 가장 강하다.

### train_3 v4 clusters

joint/trade 수: `11/4, 1/0, 3/3, 6/3, 1/0, 1/1, 1/1, 6/1`. 실제 13건은 7개 cluster에 분산되고 전부 +0.5% 승리다.

- joint-pass EEC: `4.368932`
- actual-trade EEC: `5.827586`

## STEP 3 — train_3 미체결 17일

Guard 근거: `execution_mode_backtest.py:835-878`, strict 판정 `evaluator.py:498-509`, sizing `evaluator.py:542-571`.

17일 모두 strict 5-feature PASS, D-5 finite, D+1 가격 존재. legacy quality component 합 0 → amount 0 → shares 0.

| 원인 | 건수 |
|---|---:|
| **position_size=0** | **17** |
| 후속 gate | 0 |
| 데이터 결측 | 0 |
| 기타 | 0 |

날짜: `2024-07-15, 07-16, 07-26, 07-29, 07-30, 07-31, 08-19, 09-25, 11-06, 11-07, 11-08, 2025-03-26, 05-12, 05-13, 05-14, 05-15, 05-16`.

```text
30 joint pass = 13 trades + 17 zero-position + 0 held/cooldown + 0 other
```

## STEP 4 — fold 판정

| fold | gate EEC 감소 | 최대 cluster share | 판독 |
|---|---:|---:|---|
| train_1 | -19.71% | 60% | gate+구조 |
| train_2 | -49.78% | 60% | gate 영향 최강+구조 |
| train_3 | -0.72% | 36.67% | win gate 비제한, size=0 병목 |

최종 코드: **MIXED**.

- `WINRATE_GATE_LIMITING` 기여: train_1·2 통과군 EEC가 19.71%, 49.78% 감소.
- `CONCENTRATION_STRUCTURAL` 기여: train_1·2 fold-best 거래 60%가 단일 cluster에 집중.
- 별도 guard: train_3 strict pass의 56.67%가 quality score 0으로 제거.

## 보호·상태

시작·종료 보호 SHA 동일:

```text
da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

Daemon PID `494330`, 시작 `Sat Jul 11 20:16:00 2026`, command `live_candidate_slots.py daemon --interval 60`, 유지 확인. Branch `feat/intraday-reversal-ga`; 최종 산출물 commit은 전달 메시지에 기록한다.
