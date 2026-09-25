# Fitness 재설계 코드 수정 readout

- 작업일: 2026-07-14
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 범위: 코드 수정 + 정적 검증만 수행
- GA/백테스트 재학습: **미실행**
- 수정 전 백업 커밋: `ea61c57`

## STEP 0 — 수정 지점 확인

### 1. 보유일 대비 수익 계산 위치

기존 Stage 3 entry/qualify는 `execution_mode_backtest.py`의 `_apply_fitness_mode()`에서 `fitness_mode="swing"`을 적용한다. 기존 swing fitness는 `backtest.py::_calc_fitness_swing()`을 호출해 기대값·승률·profit factor·MDD·거래수·profit concentration을 혼합한 점수였다.

따라서 기존 구현은 요청된 주목표인 **거래별 `(비용 차감 실현 수익률 / 보유일)` 평균**이 아니었다.

실현 `pnl_pct`는 `exit_simulator.py::_build_trade()`에서 매수·매도 왕복 commission을 차감한 뒤 계산된다.

### 2. MAE 존재 여부

MAE는 이미 존재했다.

- 산출 위치: `engine/strategies/exit_simulator.py`
- 초기값: `mae = 0.0`
- 갱신식: `(holding-day Low - position.avg_cost) / position.avg_cost * 100`
- 거래 기록 필드: `max_loss_during_hold`

entry scope에서는 add-buy가 비활성화되어 `position.avg_cost == entry_price`이므로 이 값은 요청된 **진입가 대비 보유 중 최저점**과 같다. 별도 OHLCV 재순회나 daily signal tape 재계산은 추가하지 않고 기존 거래 필드를 fitness에서 소비하도록 했다.

### 3. 승패 판정 위치와 정의

기존 요약은 `backtest.py::_summarize()`에서 다음과 같이 판정했다.

- 승: `pnl_pct > 0`
- 패: `pnl_pct <= 0`

`pnl_pct` 자체가 commission 차감 후 값이므로 요청 정의와 이미 일치했다. 신규 entry fitness 경로에서도 동일 정의를 직접 재계산하고 trade-level에 `fitness_win_after_cost`로 기록한다.

### 4. mutation 기존 동작

`genetic.py::mutate()`의 기본 `gene_scope`는 `legacy`였다.

- legacy: 기존 전체 numeric/categorical gene mutation
- entry: position/context gene과 strict interval low/high 쌍만 mutation
- Stage 3 `EXIT_FIELDS` 14개는 entry scope에서 고정

entry provisional exit의 대부분은 strict interval break이므로, entry scope에서 청산 시점에 직접 연결된 gene은 **5개 strict interval의 width**다. 실제 exit 14-field를 entry scope에 섞지 않고 이 interval width에만 국소 탐색 방향을 반영하도록 결정했다.

`evaluator.py`는 strict entry 신호와 position sizing만 담당하고 fitness·승패·MAE를 계산하지 않아 변경하지 않았다.

## 수정 파일

| 파일 | 변경 여부 | 역할 |
|---|---|---|
| `engine/learning/execution_mode_backtest.py` | 수정 | entry 전용 fitness, MAE 감점, 승률 gate, 7일 청산 국소탐색·진단 |
| `engine/learning/genetic.py` | 수정 | entry scope marker 전달, 다음 세대 interval mutation 방향 편향 |
| `engine/strategies/evaluator.py` | 변경 없음 | 신호 평가/position sizing만 담당 |

`exit_simulator.py`와 `backtest.py`는 위치 확인을 위해 읽기만 했고 수정하지 않았다.

## 원칙 1 — 주목표

entry scope의 GA 평가 때만 다음을 fitness 본체로 사용한다.

```text
primary = mean(
    net_realized_pnl_pct / max(holding_days, 1)
)
```

- 거래별 계산 후 평균하는 방법(a)
- `net_realized_pnl_pct`는 commission 차감 후 실현 수익률
- 당일 또는 비정상 0일 기록은 분모를 1로 fail-safe 처리
- 기대값·PF·MDD·승률 혼합점수는 entry GA의 본체에서 제거
- 기존 complexity penalty 인자가 0이 아닐 경우 기존 감점만 뒤에 유지

Stage 2/legacy 경로는 scope marker가 없으므로 기존 `_calc_fitness_swing()`을 그대로 사용한다.

## 원칙 2 — MAE 위험 패널티

거래별 기존 `max_loss_during_hold`를 `mae_pct`로 사용한다.

```text
trade_mae_excess = max(0, -2.0 - mae_pct)
mae_penalty = mean(trade_mae_excess) * 1.0
fitness_before_gate = primary - mae_penalty
```

예:

- MAE -1.5%: 감점 0
- MAE -3.0%: 이탈 1.0%p
- MAE -5.0%: 이탈 3.0%p

전체 거래 수에 따라 패널티 규모가 임의로 커지지 않도록 거래별 이탈분의 평균을 사용한다. 패널티는 본체와 합산 보상이 아닌 명시적 감산이다.

기록 필드:

- trade: `entry_fitness_daily_return_pct`, `mae_pct`, `mae_threshold_pct`, `mae_breach_pct_point`, `fitness_win_after_cost`
- result/rulebook runtime diagnostics: primary, MAE penalty, breach 거래 수, worst MAE, gate 전후 fitness

MDD 유형은 realized trade cumulative PnL의 최대 낙폭 episode를 찾아 기록한다.

- `TYPE1_ACCIDENT`: episode 손실 거래 1개이며 보유 7일 미만
- `TYPE2_NEGLECT`: 손실 거래 2개 이상 또는 손실 거래 보유 7일 이상
- `NO_DRAWDOWN`, `NO_TRADES`

유형은 현재 기록만 하며 penalty 가중치에는 사용하지 않는다.

## 원칙 3 — 승률 strict gate

entry scope에서 승패를 비용 차감 후 `pnl_pct`로 다시 계산한다.

```text
win = pnl_pct > 0
pass = win_rate >= 60.0%
```

- 60.0%는 통과
- 60.0% 미만 또는 거래 0건은 실격
- 실격 fitness: `-1_000_000_000.0`
- 승률은 점수 항목으로 더하지 않음
- selection/tournament에서 자연스럽게 최하위가 됨

## 원칙 4 — 7일 국소 탐색 mutation 편향

entry GA 평가 중 각 거래에 대해 다음 범위만 탐색한다.

```text
first candidate exit = entry index + 1
last candidate exit  = min(entry index + 7, available final index)
```

- 범위 밖 가격은 절대 조회하지 않음
- 대안 청산 가격은 각 후보 거래일의 `Open`, 결측 시 `Close`
- 기존 trade와 동일한 왕복 commission 방식으로 대안 순수익 계산
- 실제 청산보다 개선되는 대안 중 최선의 방향을 `earlier` 또는 `later`로 기록
- 이 값은 fitness·승패·MAE·실격에 사용하지 않음

다음 세대에서는 부모의 방향 힌트를 병합해 entry strict interval width mutation에만 반영한다.

- `later`: interval을 넓히는 양의 log-width mutation 확률 우세
- `earlier`: interval을 좁히는 음의 log-width mutation 확률 우세
- 방향 강도 1.0일 때 interval mutation rate는 기본값의 최대 1.75배
- 방향 선택 확률은 최대 90%
- interval center 이동은 기존 무편향 gaussian 유지
- 실제 Stage 3 exit 14-field와 `max_holding_days`는 entry scope에서 계속 고정
- 7일 cap 자체는 변경하지 않음

기본 `gene_scope='legacy'`는 유지되어 Stage 2 mutation에는 영향이 없다.

## Scope 격리 방식

`genetic.py::_evaluate_candidate()`가 `gene_scope='entry'` 평가 호출 중에만 rulebook에 임시 scope marker를 부여한다. `execution_mode_backtest.py`는 이 marker가 있을 때만 신규 fitness를 적용한다. 평가 종료 후 marker는 제거하거나 기존 값으로 복원한다.

따라서 동일 `fitness_mode='swing'`이어도:

- entry GA: 신규 일평균 수익 fitness
- legacy/Stage 2/direct backtest: 기존 swing fitness

## SHA 전후

| 파일 | 수정 전 SHA-256 | 수정 후 SHA-256 |
|---|---|---|
| `engine/learning/genetic.py` | `fd49d70f0271d541b23a7f5b0090c2281000013e8d7269fdfc4f567bbcf61305` | `28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f` |
| `engine/learning/execution_mode_backtest.py` | `12584084a5eb1ad49aaa63f50aa434f5b9795a73af889f283c380a91ab768f05` | `e2b4cb157da1a0d4be81ffaa850c8eb52725297c10bd6c3b0997711b964859cd` |
| `engine/strategies/evaluator.py` | `435b87aa999884527062963ca00a5fece63acd47c92916966442a22830965d01` | `435b87aa999884527062963ca00a5fece63acd47c92916966442a22830965d01` |

## 정적 검증

### py_compile

```text
engine/learning/genetic.py: PASS
engine/learning/execution_mode_backtest.py: PASS
engine/strategies/evaluator.py: PASS
```

실행 명령:

```text
../../../venv/bin/python -m py_compile \
  engine/learning/genetic.py \
  engine/learning/execution_mode_backtest.py \
  engine/strategies/evaluator.py
```

### 함수 단위 검증

GA를 실행하지 않고 합성 trade/result로 계산 함수만 검증했다.

- 5거래, 3승의 정확히 60% 승률: gate 통과
- 5거래, 2승의 40% 승률: `-1e9` 실격
- 합성 primary: `0.4`
- 합성 MAE penalty: `0.7`
- 최종 통과 fitness: `-0.3` = `0.4 - 0.7`
- scope marker 없는 legacy swing: 기존 공식과 동일한 `12.915`
- local search last index: 정확히 `entry+7`
- 범위 초과 없음
- later hint 양의 width 방향 비율: `89.98%`
- earlier hint 음의 width 방향 비율: `89.98%`
- base mutation 0.15, strength 1.0 hint 적용: `0.2625`
- entry numeric/categorical gene과 `EXIT_FIELDS` 교집합 없음

## 보호 대상 및 daemon

보호 파일은 읽기만 했으며 시작·종료 SHA가 동일하다.

- `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce`
- `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`
- `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611`

Daemon PID `494330`은 동일 시작 시각의 `Sl` 상태로 유지됐다.
