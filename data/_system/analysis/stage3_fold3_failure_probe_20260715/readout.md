# Stage3 AAP fold 실패 정밀 분석 + 청산가 기준 확인

## 실행 사실

- 실행 host: `invest-bot`
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 분석 입력: v5/v6 AAP 산출물 + `data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv`
- 수행 범위: read-only 분석. GA, 재학습, 백테스트, 소스 코드 수정 없음.

## STEP 0 — 존재 확인

| 항목 | 상태 |
|---|---:|
| v5 fold-best trade log / cross-fold matrix | OK |
| v6 fold-best trade log / cross-fold matrix / qualify result | OK |
| AAP OHLCV | OK |
| `execution_mode_backtest.py` | OK |
| `engine/backtest/exit_simulator.py` | MISSING |
| 실제 import 대상 `engine/strategies/exit_simulator.py` | OK |

`execution_mode_backtest.py`는 `from engine.strategies.exit_simulator import simulate_exit`를 사용한다. 실제 기준 파일은 `engine/strategies/exit_simulator.py`다. 코드 인용: `execution_mode_backtest.py:40-42`.

## STEP 0 — 진입가 / 청산가 기준 직접 답

### v5/v6 entry-scope 실제 기준

`entry_fitness_threadsafe.py` 기본값은 `entry_execution_mode="t_plus_1_open"`, `exit_execution_mode="conservative_core"`, `fold_exit_policy="fold_end_mark_to_market"`, `live_hard_stop_guard=True`다. 코드 인용: `entry_fitness_threadsafe.py:39-43`.

같은 파일은 `simulate_exit(..., entry_phase_exit=True, entry_phase_signal_tape=signal_tape, entry_phase_max_holding_days=...)`를 넘긴다. 코드 인용: `entry_fitness_threadsafe.py:120-135`.

따라서 v5/v6 entry-scope fitness의 실제 진입가는 **신호일 종가가 아니라 다음 거래일 시가**다. `t_plus_1_open`이면 `signal_idx + 1` 행의 `Open`을 `entry_price`로 쓴다. 코드 인용: `execution_mode_backtest.py:378-400`.

실제 로그도 일치한다. v6 첫 fold-best 거래는 `entry_signal_date=2022-07-01`, `entry_date=2022-07-05`, `entry_price=172.350006104`였고, OHLCV에서 2022-07-05의 `Open=172.350006`이다.

일반 entry mode 기준:

| mode | 진입가 |
|---|---|
| `close`, `same_close`, `legacy_close` | 신호일/체결일 동일 행 `Close` |
| `t_plus_1_open`, `next_open` | 다음 거래일 `Open`; 없으면 해당 행 `Close` fallback |

코드 인용: close 경로 `execution_mode_backtest.py:363-377`, T+1 open 경로 `execution_mode_backtest.py:378-400`.

### 청산가 기준

| exit reason | 트리거 판단 | 체결가 |
|---|---|---|
| `entry_provisional_atr_stop` | 보유일 `Low <= stop_price` | gap-down이면 당일 `Open`, 아니면 `stop_price` |
| `entry_interval_break` | 보유일 종가 기준 strict interval fail | 다음 거래일 `Open`; 없으면 다음 거래일 `Close` fallback |
| `entry_provisional_max_holding` | 보유상한 도달 | 해당일 `Close` |
| 일반 `time_out` fallback | 보유상한 종료 | 마지막 행 `Close` |

코드 인용: stop fill `exit_simulator.py:245-249`, stop trigger/fill `exit_simulator.py:409-432`, interval break next open `exit_simulator.py:434-461`, max holding close `exit_simulator.py:463-480`, fallback close `exit_simulator.py:567-581`.

일반 `conservative_core`에서는 `evaluate_exit`가 고가/저가를 **트리거 판정**에 쓴다. stop/trailing/breakeven은 `low <= trigger`, take-profit은 `high >= target`이다. 단, `take_profit_enabled`는 기본 false다. 체결가는 high/low가 아니라 trigger 또는 gap open 기반이다. 코드 인용: trigger 판정 `exit_policy.py:512-516`, reason 우선순위 `exit_policy.py:532-564`, gap-aware basis `exit_policy.py:334-353`, slippage 적용 `exit_policy.py:356-380`, simulator fill 채택 `exit_simulator.py:528-550`.

판정: **EXECUTION_REALISTIC_WITH_DAILY_OHLC_TRIGGER_CAVEAT**

- 청산 체결가는 당일 고가(high)가 아니다.
- v5/v6 entry-phase의 핵심 청산은 `next open`, `stop/open`, 또는 `close`다.
- stop/trailing/take-profit trigger 판정에는 일봉 `Low`/`High`가 쓰인다. 이는 일봉 백테스트의 intraday touch 가정이다. 체결가를 고가로 잡아 수익을 사후 최적화하는 구조는 아니지만, 같은 봉 내부 순서까지는 알 수 없으므로 caveat가 있다.
- v5/v6 fold-best 대부분의 `entry_interval_break`는 종가 판정 후 다음 거래일 시가 체결이라 현실 체결 가능성 쪽이다.

## STEP 1 — fold별 통과/실패 구조

v6 qualify 300개 후보 pass-count 분포:

| all3 | all2 | all1 | all0 |
|---:|---:|---:|---:|
| 0 | 61 | 200 | 39 |

v6 all2 61개의 실패 fold:

| 실패 fold | all2 개체 수 |
|---|---:|
| train_1 | 4 |
| train_2 | 57 |
| train_3 | 0 |

통과/실패 패턴:

| 통과 fold | 실패 fold | 개체 수 |
|---|---|---:|
| train_1 + train_3 | train_2 | 57 |
| train_2 + train_3 | train_1 | 4 |

결론: **train_3가 all2의 공통 실패 지점이라는 가설은 v6 기준으로 성립하지 않는다.** v6 all2에서 train_3 실패는 0개다. 가장 자주 실패한 fold는 train_2다.

전체 300개 후보 기준:

| fold | pass | fail |
|---|---:|---:|
| train_1 | 80 | 220 |
| train_2 | 94 | 206 |
| train_3 | 148 | 152 |

전체 기준으로도 train_3는 세 fold 중 실패가 가장 적다.

v5와 비교:

| run | all2 수 | all2 실패 train_1 | all2 실패 train_2 | all2 실패 train_3 |
|---|---:|---:|---:|---:|
| v5 target6/floor0.5 | 4 | 0 | 1 | 3 |
| v6 target4/floor0.7 | 61 | 4 | 57 | 0 |

v5에서는 all2가 4개뿐이라 train_3 실패가 보였지만, v6 완화 후에는 병목이 train_2로 이동했다.

## STEP 2 — 실패 fold의 시기·국면 특정

가장 자주 실패한 fold는 **train_2 = 2023-07-01 ~ 2024-06-30**이다.

train_2에서 실패한 all2 57개는 모두 `entry_trade_count_below_8`에 걸렸다.

| 지표 | train_2 실패 all2 57개 |
|---|---:|
| trade_count=1 | 1 |
| trade_count=2 | 39 |
| trade_count=3 | 15 |
| trade_count=4 | 2 |
| trade_count median | 2 |
| win_rate median | 0.0% |
| fail metric `entry_trade_count_below_8` | 57/57 |
| fail metric `expectancy_pct` | 57/57 |
| fail metric `member_score` | 9/57 |

즉 주된 실패는 “거래가 많이 떴는데 틀림”보다 **신호가 너무 적게 뜨고, 그 적은 신호도 2023년 8월에 몰려 손실/저승률이 난 구조**다.

train_2 실패 all2의 entry month 분포:

| month | entry 수 |
|---|---:|
| 2023-08 | 116 |
| 2023-10 | 3 |
| 2023-11 | 13 |

대표 all2 개체: `cf4228e5bcd13f7f3a8236c7b06c7e0ac6e988a907d993345470c09b4fd748d8`

| fold | pass | trade_count | win_rate | expectancy | final_fitness |
|---|---:|---:|---:|---:|---:|
| train_1 | pass | - | - | - | 0.6367 |
| train_2 | fail | 3 | 0.0% | -2.0915 | -1000000000 |
| train_3 | pass | - | - | - | -0.4132 |

이 대표 개체의 train_2 entry date는 `2023-08-11`, `2023-08-18`, `2023-08-21`뿐이었다. EEC는 1.0, 최대 클러스터 비중은 100%다. 이는 all3가 아니라 실패 fold 내부의 과소거래/몰빵 상태다.

v6 fold-best는 각 fold 내부에서는 통과했다.

| fold | 거래수 | wins | win_rate | sum pnl% | mean pnl% | 비승리 거래 |
|---|---:|---:|---:|---:|---:|---|
| train_1 | 19 | 18 | 94.7% | 78.77 | 4.15 | 2023-05-08, -0.353% |
| train_2 | 19 | 16 | 84.2% | 100.31 | 5.28 | 2023-12-01 -0.456%, 2024-01-24 +0.010%, 2024-05-09 +0.252% |
| train_3 | 13 | 13 | 100.0% | 65.12 | 5.01 | 없음 |

train_2 fold-best entry month는 2023-11 2건, 2023-12 1건, 2024-01 6건, 2024-02 3건, 2024-04 4건, 2024-05 3건이다. 반면 all2 실패 개체의 train_2 거래는 2023-08에 집중되어 있었다.

AAP OHLCV 기준 fold별 시장 국면:

| fold | 기간 | close return | max drawdown | annualized vol | mean ATR% | median range% | MA200 아래 일수 |
|---|---|---:|---:|---:|---:|---:|---:|
| train_1 | 2022-07-01~2023-06-30 | -59.51% | -69.72% | 49.16% | 3.16% | 2.36% | 100.0% |
| train_2 | 2023-07-01~2024-06-30 | -10.74% | -35.14% | 41.92% | 3.75% | 3.06% | 71.2% |
| train_3 | 2024-07-01~2025-06-30 | -22.14% | -52.96% | 78.89% | 4.74% | 3.69% | 89.6% |

train_2는 train_1/3보다 하락폭은 완만하지만 AAP가 장기 하락/MA200 아래에서 간헐적 반등과 재하락을 반복한 구간이다. fold-best는 2023-11~2024-05의 반등성 구간을 포착했고, all2 실패 개체들은 주로 2023-08의 초기 하락/실패 반등 구간에만 진입했다.

## STEP 3 — feature 공백 특정

strict entry feature는 신호일 D의 값이 아니라 D-5 거래일 행에서 추출된다. 코드 인용: `evaluator.py:1-6`, `evaluator.py:59-78`.

5개 feature:

| feature | 정의 |
|---|---|
| `ma_trend` | `0.5 * [(MA5/MA20-1) + (MA20/MA60-1)] * 100` |
| `macd_hist` | `MACD_hist / Close * 100` |
| `rsi` | RSI |
| `bb_position` | `(Close-BB_lower)/(BB_upper-BB_lower)` |
| `volume_ratio` | `Volume_ratio` |

코드 인용: `evaluator.py:62-67`, 계산부 `evaluator.py:86-115`.

strict interval은 5개 feature가 모두 learned interval 안에 들어와야 통과한다. interval 밖이면 fail-closed다. 코드 인용: `evaluator.py:261-282`.

train_2 실패 all2의 feature support:

| feature | 실패 all2 median interval | train_2 feature p25/median/p75 | 단일 support median |
|---|---|---|---:|
| ma_trend | [-8.1718, -2.3806] | -6.8107 / -1.3017 / 4.3209 | 62일 |
| macd_hist | [-0.3118, 1.4282] | -0.5024 / 0.0397 / 0.9141 | 120일 |
| rsi | [26.3043, 54.5472] | 37.9726 / 46.4749 / 56.2976 | 172일 |
| bb_position | [0.4754, 1.0924] | 0.2013 / 0.4679 / 0.7506 | 115일 |
| volume_ratio | [0.7191, 1.2807] | 0.7878 / 0.9539 / 1.1520 | 169일 |

5개 feature를 모두 AND로 묶은 joint support:

| train_2 joint support days | 후보 수 |
|---:|---:|
| 4 | 2 |
| 6 | 47 |
| 7 | 4 |
| 8 | 3 |
| 9 | 1 |

median joint support는 6일뿐이다. trade_count gate가 8건 이상이므로, 이 구조는 대부분 fail-closed로 이어진다.

strict 순서상 첫 실패 feature 집계:

| 첫 실패 feature | count |
|---|---:|
| ma_trend | 10,684 |
| macd_hist | 1,822 |
| bb_position | 1,265 |
| volume_ratio | 107 |
| rsi | 21 |

가장 큰 공백은 `ma_trend`다. 실패 all2의 median ma_trend interval은 음수 추세 `[-8.17, -2.38]`에 고정되어 있는데, train_2의 median ma_trend는 `-1.30`, p75는 `+4.32`다. train_2에서는 약한 반등/상방 전환 국면이 많아지는데, train_1/3에 맞춘 음수 추세 interval이 이를 많이 배제한다.

v6 fold-best entry feature median:

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio |
|---|---:|---:|---:|---:|---:|
| train_1 fold-best | -4.7495 | 0.8179 | 48.9487 | 0.6042 | 0.9199 |
| train_2 fold-best | 4.3163 | -0.3770 | 50.7785 | 0.4496 | 0.7952 |
| train_3 fold-best | -6.3437 | 0.7381 | 38.5388 | 0.2389 | 1.1033 |

train_2 fold-best는 train_1/3과 반대로 **양의 ma_trend + 음의 macd_hist + 낮은 volume_ratio** 조합을 쓴다. 즉 train_2를 살리는 feature 공간은 “강한 하락 중 반등”이 아니라 “단기적으로 이미 위로 돌아선 뒤 눌림/재진입”에 가깝다.

데이터 기반 가설:

1. **국면 전환 feature 부족**: 현재 5개 feature는 상태값 자체만 보고, 하락 추세에서 반등 전환이 지속되는지를 직접 표현하지 않는다.
2. **상대강도/벤치마크 공백**: train_2는 train_3보다 변동성이 낮고 drawdown이 얕다. 종목이 시장/섹터 대비 덜 무너지는지, 반등이 상대적으로 강한지를 보는 feature가 필요할 수 있다.
3. **변동성 regime 공백**: ATR%, gap%, range expansion 같은 위험 regime이 strict entry interval에 직접 없다.
4. **event/earnings shock 공백**: AAP는 개별 종목 이슈 영향이 큰 구간이 있다. 이 항목은 현재 산출물만으로 직접 검증한 것은 아니므로 가설이다.

## STEP 4 — 판정·권고

| 후보 원인 | 판정 | 근거 |
|---|---|---|
| 3rd fold(train_3) 공통 실패 | 기각 | v6 all2 61개 중 train_3 실패 0개; 전체 후보도 train_3 pass 148로 가장 높음 |
| 특정 시기 집중 | 부분 채택 | train_2 실패 all2 entry가 2023-08에 116건 집중 |
| 시장 국면 불일치 | 채택 | 실패 all2는 음수 ma_trend interval, train_2 fold-best는 양수 ma_trend/음수 macd 조합으로 통과 |
| feature 신호 부재 | 강하게 채택 | train_2 all2 실패 57/57이 trade_count_below_8; joint support median 6일로 gate 8 미달 |

최종 판정: **train_3 실패가 아니라 train_2의 feature-space mismatch + strict-AND support 부족이 병목**이다.

다음 feature 확장 우선순위:

1. **국면 전환/추세 변화 feature**: `ma_trend_slope_5d`, `ma_trend_delta_10d`, `MA20 slope`, `MA5-MA20 spread change`.
2. **상대강도 feature**: AAP 20d/60d return minus SPY/QQQ, sector-relative return, downtrend resilience score.
3. **변동성/gap regime feature**: ATR_pct percentile, daily range percentile, gap_abs_pct, post-gap stabilization.
4. **event/earnings shock feature**: earnings shock flag, large overnight gap after earnings, post-event drift. 이 항목은 가설이다.

## 보호파일 / daemon / git 상태 기록

시작 SHA:

| 파일 | SHA256 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` |

분석 입력 SHA:

| 파일 | SHA256 |
|---|---|
| `AAP_ohlcv.csv` | `6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717` |
| v5 `fold_best_trade_level.jsonl` | `e894915aef2f9b26dc0917c3b458a48411383884ca4f0fb218cf8b35ef0cc0d6` |
| v6 `fold_best_trade_level.jsonl` | `1f4248cd3e1f51e864e46587e34c663925727f673c30e1deb9430d8fe8b5273d` |

종료 시에도 보호파일 SHA와 daemon PID `494330`을 재확인한다. git 상태와 readout SHA는 `SHA256SUMS.txt`에 기록한다.
