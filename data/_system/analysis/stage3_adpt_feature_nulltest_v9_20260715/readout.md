# ADPT feature null-test v9 — 종목지표 3 + 상대강도 2

## 결론

노트북 `DESKTOP-TO74AR2`에서 10개 run이 모두 완료됐다. VM `invest-bot`은 Dask 연결 확인, staging, 상태 확인, 산출물 회수, 최종 readout/SHA/git만 담당했다.

판정 요약:

| 후보 | 판정 | 요약 |
|---|---|---|
| `trend_chop20` | **FEATURE_HAS_SIGNAL** | REAL만 all3=1을 만들었고 train_2/train_3 pass도 SHUFFLED보다 높다. 단 train_3 trade<8 탈락률은 악화되어 caveat 유지. |
| `atr14_pct` | FEATURE_IS_NOISE | REAL은 all3=0인데 SHUFFLED가 all3=1. train_3 trade<8도 REAL이 크게 나쁘다. |
| `range_pct_rank60` | FEATURE_IS_NOISE | SHUFFLED가 all2=83, train_1/3 pass 및 trade<8 병목에서 압도적으로 우세. |
| `rs_peer_ret20` | FEATURE_IS_NOISE | REAL all2는 많지만 SHUFFLED가 all3=3을 만들었다. REAL이 신호라고 볼 수 없다. |
| `rs_xbi_ret20` | FEATURE_IS_NOISE | REAL이 fold-best 수익률은 높지만 all2/pass/trade<8은 SHUFFLED가 우세. |

이번 ADPT v9 기준으로 **단독 채택 후보는 `trend_chop20` 1개뿐**이다. 다만 `trend_chop20`도 train_3 trade<8 탈락률이 `62.00%`로 높아, 바로 조합 확장하기보다는 all3 후보 클러스터 해부와 OOS 보존 구간 검증 전 caveat를 유지한다.

## 실행 host / 방식

| 항목 | 값 |
|---|---|
| 실제 compute host | `DESKTOP-TO74AR2` |
| 노트북 Python | `C:\dask310\Scripts\python.exe` |
| staging root | `C:\kingmaker_adpt_feature_nulltest_v9` |
| VM 역할 | launch/status/retrieve/readout/SHA/git |
| 실행 방식 | notebook-local subprocess 10개 순차 실행, 각 run `--workers 28` |
| Dask 용도 | 노트북 접속 확인, staging, status, 회수만 사용 |
| silent VM fallback | 없음 |

10개 run 모두 returncode 0:

| # | feature | variant | elapsed sec |
|---:|---|---|---:|
| 1 | trend_chop20 | REAL | 872.15 |
| 2 | trend_chop20 | SHUFFLED | 884.54 |
| 3 | atr14_pct | REAL | 885.65 |
| 4 | atr14_pct | SHUFFLED | 850.80 |
| 5 | range_pct_rank60 | REAL | 808.18 |
| 6 | range_pct_rank60 | SHUFFLED | 818.68 |
| 7 | rs_peer_ret20 | REAL | 802.77 |
| 8 | rs_peer_ret20 | SHUFFLED | 609.80 |
| 9 | rs_xbi_ret20 | REAL | 422.25 |
| 10 | rs_xbi_ret20 | SHUFFLED | 398.15 |

## STEP 0 — 데이터 검증

ADPT 및 peer/XBI cache는 모두 `data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/`에서 읽었다. 외부 다운로드/auto-fetch/regenerate는 사용하지 않았다.

| ticker | 역할 | SHA256 | rows | train rows | coverage | train OHLCV null | 채택 |
|---|---|---|---:|---:|---|---:|---|
| ADPT | 대상 | `13fb9f982e8efa29e4ee6dd3ffb585d7fb5d578c3c5099e8c76409ca0ada9503` | 1527 | 751 | 2020-05-18~2026-06-15 | 0 | YES |
| NTRA | peer | `e159704672f8d034714a079ef6eb3780dbc737471f0690a6f0cfd4d4b6899675` | 1527 | 751 | 2020-05-18~2026-06-15 | 0 | YES |
| GH | peer | `03dbb90e9d61333810e7b13062eddd94bbed2d7752ecafd977a4f54bed0d7ab9` | 1527 | 751 | 2020-05-18~2026-06-15 | 0 | YES |
| PSNL | peer | `c0c4f0acec782a053d44c1e73d2718f0c38876ea81726902e6796bec8a0f420f` | 1527 | 751 | 2020-05-18~2026-06-15 | 0 | YES |
| VCYT | peer | `fb0a12e93615ee7da169e53d0a766c80a372cbcd1e255f847fc3f2056fb4267a` | 1527 | 751 | 2020-05-18~2026-06-15 | 0 | YES |
| NEO | peer | `6efc7a768ee6cf78e9c4a59a566fa5c3bdadb3276dd6b7397540ca11419556c3` | 1526 | 751 | 2020-05-18~2026-06-12 | 0 | YES |
| CDNA | peer | `a756c13add1b4bb6046ca8d006940205baea2c144455c77664a6c1ec1ee30911` | 1527 | 751 | 2020-05-18~2026-06-15 | 0 | YES |
| XBI | 업종 ETF | `6f0412b2722f9e6b6a5e2c3deae49ea2c0d51f9070ccfce57778a9bb100884e0` | 1526 | 751 | 2020-05-18~2026-06-12 | 0 | YES |

채택 peer basket: `NTRA, GH, PSNL, VCYT, NEO, CDNA`.

학습 구간은 `2022-07-01~2025-06-30` 3-fold만 사용했다. `recent_1y = 2025-07~2026-06`는 OOS 보존 조건에 따라 절대 포함하지 않았다.

## feature 정의

공통: 모든 후보는 raw daily series 산출 후 기존 5개 feature와 동일하게 D-5 trading-day shift를 적용했다.

| 후보 | 정의 |
|---|---|
| `trend_chop20` | `abs(Close.pct_change(20)) / rolling_sum(abs(ret1), 20)` |
| `atr14_pct` | `ATR_pct` 컬럼 사용, 없으면 `ATR / Close * 100` |
| `range_pct_rank60` | `(High-Low)/Close*100`의 60일 rolling percentile rank, `min_periods=20` |
| `rs_peer_ret20` | `ADPT_ret20 - equal_weight_mean(peer_ret20)`, 각 ticker close 100-rebasing 후 20일 수익률 |
| `rs_xbi_ret20` | `ADPT_ret20 - XBI_ret20`, ADPT/XBI close 100-rebasing 후 20일 수익률 |

D-5 feature series SHA:

| feature | REAL SHA | SHUFFLED SHA |
|---|---|---|
| trend_chop20 | `cd69b1e5fc1ff28b0c01cac0527c36c217576625fc50adcba3c944f055546b9f` | `a88febce25c8f5d37a3fdb212e0e75ddf00f587eb46fd0aeb111d09e5e4a0168` |
| atr14_pct | `74308af952d168c69f805af5aa0fb2de5bd61dd034cae73bfa9c9fe09dd4a4f8` | `9a5d39cfe8d20df7f7c47a5b54dd98e572f22fff40a739b8f334ec46941de627` |
| range_pct_rank60 | `5e8e194215793ab29ed6fd35953f10c09b625e31c7f845591352217c93d10b20` | `e2c273d7f5c077275c76a1e8e5a0f2b203578de761fea887c8e9793c7a14ddd9` |
| rs_peer_ret20 | `827dccbef75f530a72b3861376ce1904141758fb36d28206167eca2f46952fb7` | `72cb13b5e5913f309d2e672cf28d8ef5597fe6abfca839a7c8904a89d99c2c13` |
| rs_xbi_ret20 | `fbb79bcbfe07a5eff3fa73d5b91db4d8a4da5bd8357f238d7695fde87b0fb330` | `06e2165cc0f7f4c43d9f349815b570587aa411bcd66b374b59412e5217e96e9d` |

## STEP A/B — 코드 변경 및 정적 검증

원본 source는 수정하지 않았다. 산출물 폴더에 runtime helper만 추가했다.

| 항목 | 결과 |
|---|---|
| helper file | `data/_system/analysis/stage3_adpt_feature_nulltest_v9_20260715/run_adpt_feature_nulltest_v9.py` |
| helper SHA | `6da5a6097bcaf589c9a4d5e36e715cf8a3ca7ba6f1921b10b01bd3fe746512ba` |
| py_compile | PASS |
| 5 후보 × REAL/SHUFFLED verify-only | PASS |
| strict-AND feature count | 6 |
| entry/exit / should_buy / legacy / fixed-sizing | 원본 불변 |
| EEC penalty | v5 target 6 / floor 0.5 강제 |
| mutation helper AST SHA | `aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d` |
| interval-break 영향 | 새 feature가 strict-AND에 들어가므로 interval-break 청산에 영향 가능. 기록만 하고 차단하지 않음. |

Source SHA:

| file | SHA256 |
|---|---|
| `scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aap_eec_penalty_v5_host.py` | `5e7d83665b3cfa936e74dc9e275449661b4a6ad8408cd4952e6a2657d7a9361f` |
| `scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py` | `41b3ccbb8e073034f673e1273fa796cd009df78cae08ff80f5dda8b77f7a3a81` |
| `scripts/research/stage23_rework_20260713/engine/learning/genetic.py` | `28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f` |

## STEP C — REAL vs SHUFFLED 비교

### 핵심 비교표

| feature | judgment | REAL all3/all2/all1/all0 | SHUFFLED all3/all2/all1/all0 | REAL fold pass t1/t2/t3 | SHUFFLED fold pass t1/t2/t3 | REAL t2/t3 trade<8 | SHUFFLED t2/t3 trade<8 |
|---|---|---:|---:|---:|---:|---:|---:|
| trend_chop20 | FEATURE_HAS_SIGNAL | 1/13/275/11 | 0/15/255/30 | 107/94/103 | 102/85/98 | 19.33%/62.00% | 23.00%/32.00% |
| atr14_pct | FEATURE_IS_NOISE | 0/64/219/17 | 1/19/249/31 | 161/95/91 | 89/100/101 | 0.67%/61.33% | 4.00%/31.00% |
| range_pct_rank60 | FEATURE_IS_NOISE | 0/11/259/30 | 0/83/197/20 | 106/87/88 | 162/86/115 | 23.00%/63.67% | 1.33%/0.67% |
| rs_peer_ret20 | FEATURE_IS_NOISE | 0/42/243/15 | 3/20/251/26 | 143/83/101 | 114/85/101 | 2.00%/61.33% | 1.33%/57.33% |
| rs_xbi_ret20 | FEATURE_IS_NOISE | 0/17/264/19 | 0/29/257/14 | 112/86/100 | 121/88/106 | 24.33%/60.00% | 29.00%/29.33% |

### fold-best 상세

| feature | variant | fold-best fitness | EEC | win % | avg ret % | trade count |
|---|---|---:|---:|---:|---:|---:|
| trend_chop20 | REAL | 0.987 / 1.388 / 3.487 | 4.67 / 5.88 / 8.53 | 92.9 / 85.0 / 88.9 | 6.49 / 4.44 / 7.57 | 14 / 20 / 18 |
| trend_chop20 | SHUFFLED | 1.340 / 1.139 / 3.439 | 4.57 / 4.50 / 6.13 | 89.5 / 83.3 / 92.9 | 6.20 / 5.13 / 9.48 | 19 / 12 / 14 |
| atr14_pct | REAL | 0.865 / 1.388 / 3.769 | 3.60 / 5.88 / 8.76 | 100.0 / 85.0 / 88.2 | 6.11 / 5.15 / 9.70 | 12 / 20 / 17 |
| atr14_pct | SHUFFLED | 1.073 / 1.107 / 3.641 | 4.83 / 6.55 / 6.72 | 100.0 / 75.0 / 88.2 | 5.63 / 4.42 / 9.02 | 13 / 12 / 17 |
| range_pct_rank60 | REAL | 1.230 / 1.970 / 3.269 | 5.12 / 6.26 / 6.15 | 92.3 / 84.6 / 88.2 | 5.86 / 6.79 / 8.88 | 13 / 13 / 17 |
| range_pct_rank60 | SHUFFLED | 2.061 / 0.934 / 3.679 | 6.42 / 5.26 / 9.00 | 88.2 / 100.0 / 88.9 | 6.36 / 4.28 / 9.08 | 17 / 11 / 18 |
| rs_peer_ret20 | REAL | 0.500 / 0.722 / 3.769 | 2.27 / 4.59 / 8.76 | 100.0 / 94.1 / 88.2 | 6.70 / 5.00 / 10.50 | 15 / 17 / 17 |
| rs_peer_ret20 | SHUFFLED | 0.943 / 0.848 / 3.769 | 4.24 / 6.08 / 8.76 | 100.0 / 82.6 / 88.2 | 6.69 / 4.82 / 9.26 | 12 / 23 / 17 |
| rs_xbi_ret20 | REAL | 1.067 / 1.239 / 3.248 | 2.85 / 4.83 / 7.76 | 93.3 / 100.0 / 80.0 | 9.05 / 5.28 / 9.46 | 15 / 13 / 15 |
| rs_xbi_ret20 | SHUFFLED | 1.357 / 0.649 / 3.096 | 4.33 / 8.00 / 6.33 | 100.0 / 81.2 / 89.5 | 6.03 / 4.35 / 8.53 | 13 / 16 / 19 |

## 후보별 판정 근거

### trend_chop20 — FEATURE_HAS_SIGNAL

REAL은 all3=1을 만들었고 SHUFFLED는 all3=0이다. fold pass도 REAL `107/94/103`이 SHUFFLED `102/85/98`보다 전 fold에서 높다. fold-best win-rate와 EEC도 무난하다. 단, train_3 trade<8 탈락률이 REAL `62.00%`로 SHUFFLED `32.00%`보다 나쁘다. 따라서 단독 신호 후보로는 살아났지만, trade-count 병목 caveat는 남는다.

### atr14_pct — FEATURE_IS_NOISE

REAL은 all3=0, SHUFFLED는 all3=1이다. REAL이 train_1 pass와 train_2 trade<8에서는 좋지만, 핵심인 all3와 train_3 trade<8에서 SHUFFLED가 우세하다. 예측 신호라고 보기 어렵다.

### range_pct_rank60 — FEATURE_IS_NOISE

SHUFFLED가 all2=83으로 REAL all2=11보다 훨씬 높다. train_3 trade<8도 REAL 63.67% vs SHUFFLED 0.67%로 REAL이 크게 악화된다. population 수준에서 noise 판정이다.

### rs_peer_ret20 — FEATURE_IS_NOISE

REAL은 all2=42로 SHUFFLED all2=20보다 많지만, SHUFFLED가 all3=3을 만들었다. REAL train_3 trade<8도 61.33%로 높다. 상대강도 peer basket이 ADPT에서 단독 예측력을 보였다고 볼 수 없다.

### rs_xbi_ret20 — FEATURE_IS_NOISE

REAL fold-best 평균수익률은 SHUFFLED보다 높지만, all2와 fold pass, train_3 trade<8는 SHUFFLED가 우세하다. all3도 양쪽 0이므로 단독 신호 근거 부족이다.

## AAP v8 대비 비교

AAP v8에서는 `trend_chop20`, `atr14_pct`, `range_pct_rank60`이 모두 FEATURE_IS_NOISE였고, `rs_peer3_ret20`만 FEATURE_AMBIGUOUS였다. ADPT v9에서는 `trend_chop20`이 all3=1을 만들며 유일하게 살아났다. 반면 `atr14_pct`, `range_pct_rank60`, 상대강도 계열은 ADPT에서도 SHUFFLED가 같거나 더 강해 여전히 noise로 보는 것이 맞다.

| feature family | AAP v8 | ADPT v9 |
|---|---|---|
| trend_chop20 | FEATURE_IS_NOISE | FEATURE_HAS_SIGNAL |
| atr14_pct | FEATURE_IS_NOISE | FEATURE_IS_NOISE |
| range_pct_rank60 | FEATURE_IS_NOISE | FEATURE_IS_NOISE |
| peer relative ret20 | FEATURE_AMBIGUOUS (`rs_peer3_ret20`) | FEATURE_IS_NOISE (`rs_peer_ret20`) |
| XBI relative ret20 | not tested | FEATURE_IS_NOISE |

## 다음 단계 권고

1. `trend_chop20` all3 후보를 별도로 클러스터 해부한다. all3가 건강한 분산 신호인지, 특정 국면 몰빵인지 확인해야 한다.
2. `trend_chop20`만 ADPT 단독 후보로 보류하고, 나머지 4개는 단독 feature 확장 후보에서 제외한다.
3. recent_1y OOS는 아직 보존되어 있다. 다음 검증은 train_1/2/3에서 선별된 `trend_chop20` 후보를 recent_1y에 한 번만 대는 방식이 적절하다.
4. 다음 실행부터 CPU를 더 채우려면 후보별 독립 run을 batch 병렬로 돌리고, 총 worker 합계가 28을 넘지 않게 `4 runs × 7 workers` 또는 `2 runs × 14 workers`로 나누는 것이 낫다. 이번 실행은 안전한 순차 실행이었다.

## 보호파일 / daemon

보호파일 시작·종료 SHA 동일:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6dbb86c6347548e9f4c611` |

- daemon PID `494330` 유지 확인.
- 산출 전 backup commit: `e3bfb7c`.
