# Central Portfolio Backtest Design v7

작성일: 2026-06-10 KST  
상태: 구현 전 설계서  
대상: `lr8d_stage1_20260609` promoted 16종목 중앙 포트폴리오 감사 및 확장 검증

---

## 0. 5문장 원칙

1. 중앙 포트폴리오 시스템은 개별 룰북의 BUY/HOLD 신호 위에 자본 배분, 갈아타기, 위험 청산 레이어를 얹는 구조다.
2. 모든 성능 비교는 동일한 전략 할당 자본과 동일한 총노출 캡에서만 유효하다.
3. 새 시뮬레이터는 `switch off + kill off + fixed_30` 설정에서 기존 16종목 독립 베이스라인과 숫자가 일치해야 한다.
4. 일봉 전략의 최종 검증은 `T일 종가까지의 정보로 신호 계산 → T+1 open 체결` 기준으로 한다.
5. 라이브 반영은 베이스라인 대비 수익률, MDD, 안정성, turnover 비용, kill 부작용까지 통과한 뒤에만 진행한다.

---

## 1. 목적

현재 라이브 universe는 `lr8d_stage1_20260609` promoted 16종목이다. 각 종목은 독립 룰북으로 BUY/HOLD 신호와 exit policy를 가진다. 현재 라이브는 종목별 독립 진입에 가깝고, 포트폴리오 전체 자본 배분이나 강한 후보로의 갈아타기 판단은 중앙에서 검증된 형태로 존재하지 않는다.

이 문서의 목적은 다음 세 가지다.

1. 기존 16종목 독립 운용을 재현하는 베이스라인을 만든다.
2. 동일 신호와 동일 exit 위에 중앙 포트폴리오 레이어를 얹어 성능을 비교한다.
3. backtest-live 괴리를 줄이기 위해 체결 시점, 총노출, no-op 재현, OOS 안정성 기준을 명시한다.

중앙 시스템은 바로 라이브 주문 로직에 넣지 않는다. 먼저 backtest에서 검증하고, 기존 16종목 독립 베이스라인보다 명확히 낫다는 증거가 있을 때만 라이브 설계로 승격한다.

---

## 2. 현재 코드에서 확인된 사실

### 2.1 신호 점수는 이미 존재한다

`engine/strategies/evaluator.py`의 `evaluate_signal()`은 다음 값을 계산한다.

```text
score
raw_score
threshold
market_adjustment
components
reasons
should_buy
```

최종 신호는 다음 구조다.

```text
raw_score = sum(components)
final_score = raw_score * market_adjustment
should_buy = final_score >= rulebook.signal_threshold
```

`engine/strategies/learned_rulebook.py`는 이 값을 `SignalResult`에 넘긴다. 따라서 BUY/HOLD 이진값뿐 아니라 `score / threshold` 기반의 연속 신호 강도를 사용할 수 있다.

### 2.2 룰북 성과값도 존재한다

`data/symbols/*/parameters.json`의 `rulebook`에는 다음 성과값이 저장돼 있다.

```text
expectancy_pct
avg_return_pct
win_rate
fitness
profit_factor
signal_threshold
trade_count
```

promoted 16종목은 `data/_system/live_universe_lr8d_stage1_manifest.json` 기준으로 확정된다.

### 2.3 학습과 backtest는 일봉이다

가격 로더는 `engine/core/data_loader.py`에서 yfinance를 사용한다.

```python
yf.download(yf_ticker, start=start, end=end, progress=False, auto_adjust=False)
```

`interval`을 지정하지 않으므로 기본 일봉이다. exit simulator도 `holding_days = i - entry_idx` 구조로 일봉 bar를 전제로 한다.

라이브는 `scripts/run_live.py` 기준 기본 60초마다 `tick_market()`을 호출하지만, 전략 timeframe 자체는 분봉이 아니다. 라이브 1분 tick은 체결/감시 주기이고, 신호 로직은 일봉 룰북 기반이다.

### 2.4 기존 backtest의 체결 가정은 비대칭이다

현재 `engine/learning/backtest.py`는 T일 종가까지 포함한 데이터로 신호를 계산하고, 같은 T일 종가로 진입한다.

```python
sig = evaluate_signal(... df.iloc[: i + 1] ...)
entry_price = float(df.iloc[i]["Close"])
```

`engine/strategies/exit_simulator.py`도 진입 가격으로 `entry_row["Close"]`를 사용한다.

반면 청산은 `ExitExecutionConfig.use_next_open=True` 기본값 때문에 trigger 발생 다음 open 기반 fill을 사용한다.

```text
진입: T close
청산: next_open 기반
```

이 구조는 no-op 재현을 위해 한 번은 그대로 재현해야 하지만, 최종 성능 판단에는 그대로 쓰면 안 된다.

---

## 3. 베이스라인 대상 16종목

기준 파일:

```text
data/_system/live_universe_lr8d_stage1_manifest.json
```

대상 promotion:

```text
lr8d_stage1_20260609
```

대상 종목:

```text
CAKE
CRWD
CW
EME
ETR
HSBC
ITT
KT
LASR
MPC
MPLX
MTB
NBIX
WAB
WELL
WPM
```

이 16종목은 중앙 시스템의 최초 감사 대상이다. 단, 이 종목들이 T+1 realistic 기준에서도 최종 survivor라는 뜻은 아니다. 차기 RUN에서 T+1 진입 정렬을 반영하면 universe 재선정이 필요할 수 있다.

---

## 4. 베이스라인 정의

### 4.1 baseline-wrapper

베이스라인은 현재 16종목을 독립으로 굴린 결과를 합산한다.

```text
각 종목별 기존 run_backtest()
→ trade rows 수집
→ 날짜순으로 합산
→ 전략 할당 자본 기준 equity curve 생성
```

기본 설정:

```text
universe: promoted 16종목
per_symbol_order_notional: 30 USD
total_exposure_cap: 480 USD 또는 600 USD 중 하나를 명시
entry/exit logic: 기존 run_backtest와 동일
switch: off
kill rule: off
sizing: fixed_30
```

### 4.2 동일 총노출 원칙

중앙 시스템과 baseline은 반드시 같은 총노출 캡을 사용한다.

```text
baseline 총노출 = 16종목 × 30 USD = 480 USD
```

현재 policy의 small amount safety에는 600 USD 총노출 캡도 존재한다.

```text
max_total_exposure_notional: 600
max_total_notional: 600
```

따라서 실험은 두 기준을 분리한다.

```text
strict exposure cap: 480 USD
live policy cap: 600 USD
```

성능 지표의 수익률 분모는 paper 계좌 전체 100k가 아니라 전략 할당 자본이다.

```text
return_pct = realized_or_marked_pnl / strategy_allocated_capital
```

노출이 다르면 수익률 비교가 무의미하다. 중앙 시스템이 더 많이 벌었더라도 더 많이 건 결과일 수 있기 때문이다.


### 4.3 live_current_proxy_baseline (현재 라이브 재현)

```text
sizing: fractional shares, 30 USD fixed notional
add_buy: OFF (disable_add_buy=True)
entry: legacy T-close 유지 (look-ahead 제거는 realistic_research_baseline에서만 적용)
exit: simulate_exit 결과를 평가한 뒤 hard-stop guard wrapper 적용
guard hit 판정: snap.low <= entry-time stop_price (일봉 intraday touch 가정)
gap-fill: open <= stop_price면 open 체결(gap-down), 아니면 stop_price 체결(intraday touch)
slippage: 0 (stress는 realistic_research_baseline에서 별도)
same-bar conflict: stop_loss 우선 (보유자 최악 가정, §5.2 일치)
guard 로직: live와 동일한 engine.core.exit_policy.apply_hard_stop_guard() 공유
```

이 baseline은 현재 라이브의 핵심 차이를 연구용 daily backtest에 주입하기 위한 것이다. 공유 exit core 자체를 바꾸지 않고 wrapper로 hard-stop guard를 적용한다.

---

## 5. no-op 재현 게이트

중앙 포트폴리오 시뮬레이터는 먼저 아무 기능도 켜지 않은 상태에서 baseline-wrapper와 숫자가 일치해야 한다.

no-op 설정:

```text
switch_enabled: false
kill_rule_enabled: false
sizing_mode: fixed_30
capital_allocator: independent_fixed
entry_execution: legacy_T_close
exit_execution: existing_exit_simulator
```

통과 조건:

```text
trade_count 동일
entry_date/exit_date 동일
entry_price/exit_price 동일 또는 허용 오차 이내
pnl_usd 합계 동일 또는 허용 오차 이내
equity curve 동일 또는 허용 오차 이내
MDD 동일 또는 허용 오차 이내
```

이 게이트가 실패하면 중앙 시스템 성능 비교는 진행하지 않는다. no-op이 baseline과 다르면, 새 시뮬레이터가 이미 다른 게임을 하고 있다는 뜻이다.

---

## 6. 체결 시점 규칙

### 6.1 legacy 재현 모드

목적: 기존 엔진과 중앙 시뮬레이터 일치성 확인.

```text
신호 계산: T close 포함
진입 체결: T close
청산 체결: 기존 exit_simulator 기준
```

이 모드는 정확성 게이트용이다. 최종 투자 성능 판단에는 사용하지 않는다.

### 6.2 realistic 검증 모드

목적: look-ahead 제거 후 실제 운용 가능성 검증.

```text
신호 계산: T close까지의 데이터
신규 진입: T+1 open
갈아타기 매도: T+1 open
갈아타기 매수: T+1 open
kill rule 전량 청산: T+1 open
```

T+1 open이 없는 마지막 bar에서는 신규 진입하지 않는다.

### 6.3 청산 체결 정합 이슈

현재 backtest exit는 기본적으로 next_open 기반이고, live exit adapter는 `use_next_open=False`다. 따라서 청산 체결은 별도 정합 이슈로 남긴다.

검증 축:

```text
exit_fill_mode = next_open_base
exit_fill_mode = trigger_price_base
exit_fill_mode = stress_slippage
```

최종 라이브 승격 전에는 backtest와 live의 exit 체결 가정 차이를 줄여야 한다.

---

## 7. 신호 강도 정의

### 7.1 live_strength

현재 시점의 신호 강도:

```text
live_strength = score / threshold
```

방어 규칙:

```text
threshold <= 0 이면 live_strength는 0으로 처리
BUY가 아닌 신호는 신규 진입 후보에서 제외
```

분류 초안:

```text
약신호:     live_strength < 1.20
보통신호:   1.20 <= live_strength < 1.50
강신호:     1.50 <= live_strength < 2.00
초강신호:   live_strength >= 2.00
```

### 7.2 historical_quality

과거 룰북 품질은 `expectancy_pct`의 cross-sectional percentile로 계산한다.

```text
historical_quality = percentile_rank(expectancy_pct within candidate universe)
```

기본 universe는 promoted 16종목이다. 추가 실험에서는 전체 survivor 93종목 percentile도 비교할 수 있다.

### 7.3 signal_power

중앙 시스템의 최종 후보 비교 점수:

```text
signal_power = historical_quality * live_strength
```

v1에서는 단순 곱으로 시작한다. 이후 `fitness`, `profit_factor`, `trade_count`를 추가 가중할 수 있지만, 초기 실험에서는 식을 단순하게 유지한다.

---

## 8. 갈아타기 규칙

갈아타기는 보유 종목을 무작정 자주 바꾸는 기능이 아니다. 기존 보유 신호가 약해지고 신규 후보가 충분히 강할 때만 후보로 삼는다.

기본 조건:

```text
신규 후보가 BUY 상태
신규 live_strength >= 1.50
기존 보유 live_strength < 1.20
신규 signal_power >= 기존 signal_power * switch_threshold
```

스윕 후보:

```text
switch_threshold: 1.35, 1.50
```

과잉 회전을 막기 위한 조건:

```text
min_holding_days_before_switch: 3, 5, 10
max_switches_per_day: 1
same_sector_switch_penalty: optional
```

실행 순서:

```text
T close 기준 신호 계산
갈아타기 후보 선정
T+1 open에 기존 보유 매도
동일 T+1 open에 신규 후보 매수
총노출 캡 초과 금지
```

---

## 9. 사이징 규칙

비교 대상은 두 가지다.

### 9.1 fixed_30

baseline과 동일한 고정 주문금액이다.

```text
order_notional = 30 USD
```

### 9.2 score proportional

신호가 강한 종목에 더 배분하되, 총노출 캡을 넘지 않는다.

예시:

```text
raw_weight_i = max(0, signal_power_i)
weight_i = raw_weight_i / sum(raw_weight)
target_notional_i = total_exposure_cap * weight_i
```

방어 규칙:

```text
target_notional_i <= per_position_cap
총 target_notional 합계 <= total_exposure_cap
min_order_notional 미만은 주문하지 않음
```

중앙 시스템이 더 많은 노출로 이기는 착시를 막기 위해, fixed와 proportional 모두 같은 총노출 캡을 사용한다.

---

## 10. 위험 청산 kill rule

### 10.1 trailing peak 정의

포트폴리오 고점은 전체 기간 고점이 아니라 rolling trailing peak로 정의한다.

```text
portfolio_trailing_peak_N = 최근 N거래일 포트폴리오 평가액의 최고값
portfolio_drawdown_from_peak = current_equity / portfolio_trailing_peak_N - 1
```

스윕 후보:

```text
trailing_peak_lookback_days: 10, 20
drawdown_trigger: -3%, -4%, -5%, -7%
```

기본값:

```text
최근 20거래일 trailing peak 대비 -4%
```

### 10.2 손실 종목 비율

```text
losing_position_ratio = 손실 중인 보유 종목 수 / 전체 보유 종목 수
```

스윕 후보:

```text
50%, 60%, 70%
```

기본값:

```text
60%
```

### 10.3 시장 확인 조건

현재 로컬 데이터에는 SPY/QQQ OHLCV가 완전한 형태로 저장돼 있지 않다. `data/symbols/QQQ/parameters.json`은 있으나 OHLCV 파일은 확인되지 않았고, SPY는 `data/symbols/SPY`에 없다. `data/_system/market_history.csv`에는 `score`, `regime`, `sp500_60d`, `vix`가 있다.

따라서 v1 kill rule은 market_history 기반으로 검증한다.

후보:

```text
market_history.regime != bull
market_history.score < 40
vix > 25
sp500_60d < 0
```

SPY/QQQ 3거래일 수익률 조건은 OHLCV 수집 후 별도 실험으로 둔다.

### 10.4 kill rule 기본안

```text
조건 A: 최근 20거래일 trailing peak 대비 drawdown <= -4%
조건 B: 보유 종목 중 손실 종목 비율 >= 60%
조건 C: market_history.score < 40 또는 regime != bull

A, B, C가 동시에 충족되면 T+1 open에 전량 청산
청산 후 3거래일 신규 진입 금지
```

쿨다운 후보:

```text
3거래일
5거래일
market_score >= 40 회복까지
```

---

## 11. 일봉 daily loop 순서

중앙 포트폴리오 시뮬레이터의 하루 처리 순서는 고정한다.

```text
1. T일 장 시작 상태 로드
2. 전일 발생한 T+1 open 예약 주문 체결
3. 보유 포지션 mark-to-market
4. exit policy 평가 및 exit 예약
5. T close 기준 각 종목 signal 계산
6. kill rule 평가
7. 신규 진입 후보와 갈아타기 후보 산출
8. 총노출 캡과 사이징 규칙 적용
9. T+1 open 주문 예약
10. equity curve와 diagnostics 저장
```

동일 날짜에 kill rule과 갈아타기 신호가 동시에 발생하면 kill rule을 우선한다.

```text
kill rule > forced exit > switch > new entry
```

---

## 12. 스윕 격자

### 12.1 기본 격자

```text
switch_threshold: 1.35, 1.50
sizing_mode: fixed_30, signal_power_proportional
trailing_peak_lookback_days: 10, 20
drawdown_trigger: -3%, -4%, -5%, -7%
losing_position_ratio: 50%, 60%, 70%
market_confirm: none, market_score_lt_40, regime_not_bull, score_lt_40_or_regime_not_bull
cooldown_after_kill: 3, 5
min_holding_days_before_switch: 3, 5, 10
```

### 12.2 비용 민감도

```text
commission_rate: existing, 2x
slippage_bps: 0, 5, 10, 25
```

중앙 시스템은 turnover가 늘어날 수 있으므로 slippage 민감도는 필수다.

---

## 13. 검증 기준

### 13.1 baseline 대비 통과 기준

중앙 시스템 후보가 통과하려면 다음 중 하나를 만족해야 한다.

강한 통과:

```text
총수익률 >= baseline
MDD < baseline
profit factor >= baseline
turnover 비용 반영 후에도 우위 유지
```

방어형 통과:

```text
총수익률은 baseline 대비 소폭 하락 허용
단 MDD가 의미 있게 개선
손실월 수 감소
kill rule 부작용이 제한적
```

### 13.2 OOS/연도별 안정성

최소 검증 구간:

```text
2024
2025
2024~2025 combined
```

가능하면 rolling split으로 확장한다.

확인 지표:

```text
연도별 총수익률
연도별 MDD
월별 수익 분포
거래 수
승률
profit factor
평균보유일
turnover
```

### 13.3 paired daily return 비교

baseline과 중앙 시스템의 일별 수익률을 같은 날짜 축에서 비교한다.

```text
daily_return_baseline[t]
daily_return_central[t]
diff[t] = central[t] - baseline[t]
```

확인 항목:

```text
central이 특정 며칠에만 이긴 것인지
MDD 개선이 지속적인지
kill rule이 반등 초입을 놓치게 하는지
```

### 13.4 최소 거래수

중앙 시스템이 너무 적게 거래해서 우연히 좋아 보이는 경우를 막는다.

```text
central_trade_count >= baseline_trade_count * 0.5
또는 연도별 최소 거래수 기준 충족
```

정확한 기준은 baseline 결과를 본 뒤 확정한다.

---

## 14. 백로그와 별도 이슈

### 14.1 win_rate 스케일 버그

`engine/live/approval_manager.py`의 `classify_strength()`는 `win_rate >= 0.75`처럼 0~1 비율을 기대한다. 하지만 룰북 `win_rate`는 `83.3333`처럼 0~100 퍼센트로 저장된다.

현재 이 함수는 추가매수 승인 요청/재알림 경로에 연결돼 있다. `config/policy.yaml` 기준 승인형 추가매수는 명시적으로 켜져 있지 않으므로 현재 일반 주문에는 직접 영향이 없지만, 기능을 켜면 판정이 왜곡된다.

수정 원칙:

```text
classify_strength 내부에서 win_rate > 1.0 이면 /100 정규화
0~1 범위로 clamp
승인형 추가매수 재활성화 전 필수 수정
```

### 14.2 SPY/QQQ OHLCV 수집

SPY/QQQ 3거래일 수익률 또는 20일선 이탈을 kill rule에 쓰려면 OHLCV 데이터 수집이 선행되어야 한다. 현재 market_history만으로는 해당 조건을 직접 검증할 수 없다.

### 14.3 T+1 기준 차기 RUN과 종목 재선정

현재 16종목은 기존 룰북 산출물 기준 promoted universe다. 기존 backtest는 진입 T close 구조이므로 expectancy가 실현불가 프리미엄을 포함했을 수 있다.

차기 RUN 품질 개선 항목:

```text
진입 체결을 T+1 open으로 정렬
청산 체결 정합 검토
expectancy, win_rate, fitness 재산출
survivor와 promoted universe 재선정
```

중앙 시스템 v1은 현 16종목을 감사 대상으로 삼을 수 있지만, 최종 live universe는 T+1 기준 재선정 결과를 우선해야 한다.

### 14.4 cross-symbol comparability 리스크

`score / threshold`는 룰북 내부 신호 강도를 정규화하지만, 종목 간 완전히 같은 의미라고 보장되지는 않는다. v1에서는 이 리스크를 문서화하고, 성능 검증으로 통제한다.

추가 검증 후보:

```text
종목별 signal_strength 분위수 비교
score/threshold와 실제 forward return 상관
expectancy percentile 보정 전후 비교
```

### 14.5 live backtest 체결 정합

live exit adapter는 `use_next_open=False`다. backtest exit는 기본 `use_next_open=True`다. 중앙 시스템 설계와 별도로 exit 체결 정합 이슈를 추적해야 한다.

---

## 15. 구현 단계

### 단계 1. baseline-wrapper

목표:

```text
promoted 16종목 독립 run_backtest 결과 합산
전략 할당 자본 기준 equity curve 생성
총수익률, MDD, 거래수, 평균보유일, PF 산출
```

산출물:

```text
data/_system/research/central_portfolio/baseline_legacy/*.csv
summary.json
```

### 단계 2. central simulator no-op

목표:

```text
중앙 시뮬레이터가 no-op 설정에서 baseline-wrapper와 일치
```

게이트:

```text
불일치 시 switch/sizing/kill 구현 금지
```

### 단계 3. realistic baseline

목표:

```text
T+1 open 진입 기준 baseline 생성
legacy baseline과 차이 측정
```

### 단계 4. 중앙 레이어 추가

추가 기능:

```text
signal_power 계산
갈아타기
score proportional sizing
kill rule
cooldown
turnover/slippage 비용
```

### 단계 5. sweep과 리포트

목표:

```text
baseline 대비 우위 조합 선별
연도별/OOS 안정성 확인
kill 부작용 확인
```

### 단계 6. 라이브 설계 후보 작성

backtest 통과 후에만 live runner 연동 설계를 시작한다. 최초 live 적용은 자동 갈아타기가 아니라 알림/승인 후보부터 시작한다.

---

## 16. 산출물 스키마

### 16.1 trade log

```text
date
ticker
action
reason
signal_score
signal_threshold
live_strength
historical_quality
signal_power
notional
shares
fill_price
position_after
equity_after
```

### 16.2 daily portfolio state

```text
date
equity
cash
gross_exposure
net_exposure
positions_count
losing_position_ratio
portfolio_trailing_peak
drawdown_from_peak
market_score
market_regime
kill_triggered
cooldown_days_remaining
turnover_notional
```

### 16.3 sweep summary

```text
config_id
switch_threshold
sizing_mode
kill_rule_config
return_pct
mdd_pct
profit_factor
trade_count
avg_holding_days
turnover
slippage_cost
kill_count
false_kill_count
missed_rebound_cost
passed
```

---

## 17. 최종 판단 기준

중앙 시스템은 다음을 모두 만족할 때만 live 후보가 된다.

```text
1. no-op 재현 게이트 통과
2. 동일 총노출 기준 baseline 대비 우위
3. T+1 realistic 기준에서도 우위 유지
4. 2024/2025 개별 연도에서 한쪽에만 과최적화되지 않음
5. turnover와 slippage 반영 후에도 우위 유지
6. kill rule 부작용이 제한적
7. live 적용 시 자동 주문보다 알림/승인 단계부터 시작 가능
```

위 조건을 만족하지 못하면 중앙 시스템은 폐기하거나 연구 모드로 유지한다.
