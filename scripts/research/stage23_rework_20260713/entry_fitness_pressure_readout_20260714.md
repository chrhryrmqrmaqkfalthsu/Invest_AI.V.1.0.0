# Entry fitness 진화 압력 변경 readout

- 작업일: 2026-07-14
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 변경 범위: Entry-scope fitness만 변경
- GA 재학습·전체 백테스트: 실행하지 않음
- Stage 2 legacy 경로: 불변

## STEP 0 — 수정 지점 확인

현재 Entry GA fitness는 `engine/learning/execution_mode_backtest.py`의 `_apply_entry_scope_fitness()`에서 계산된다.

1. 주목표
   - 거래별 `비용 차감 pnl_pct / max(holding_days, 1)`을 계산
   - 거래 평균을 `primary_objective_pct_per_day`로 사용
2. MAE 벌점
   - `max(0, -2.0 - mae_pct)`를 거래별 계산
   - 평균 후 주목표에서 차감
3. 승패
   - 변경 전: 비용 차감 실현수익 `pnl_pct > 0.0`
4. 게이트
   - 변경 전: `trade_count >= 10 AND win_rate_pct >= 60.0`
   - 실패 시 `-1e9`
5. mutation bias
   - `engine/learning/genetic.py`에 존재
   - 이번 작업에서 변경하지 않음
6. Stage 2
   - `gene_scope='legacy'` 기본값과 non-entry swing fitness 경로는 별도 경로

## 수정 파일

### 변경됨

```text
engine/learning/execution_mode_backtest.py
```

### 읽기·검증만 수행, 변경 없음

```text
engine/learning/genetic.py
```

## 대상 파일 SHA-256

| 파일 | 변경 전 | 변경 후 |
|---|---|---|
| `engine/learning/execution_mode_backtest.py` | `6a78019280d761eed422841b8d24bc2b5683a1e17977e6d5d2f2d1adae4ba827` | `e6901ec6e685ad8ad30499cdbb9dfac2db4ce0a6e5a731ce8f724bf86a64c21a` |
| `engine/learning/genetic.py` | `28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f` | `28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f` |

## 변경 1 — 실현손실 벌점

신규 상수:

```text
ENTRY_FITNESS_REALIZED_LOSS_THRESHOLD_PCT = -1.0
ENTRY_FITNESS_REALIZED_LOSS_PENALTY_WEIGHT = 1.0
```

거래별 벌점 초과분:

```text
max(0, -1.0 - 비용차감후실현수익률%)
```

전체 벌점:

```text
realized_loss_penalty = 거래별 초과분 평균 × 1.0
```

최종 Entry fitness의 gate 적용 전 값:

```text
primary_objective
- mae_penalty
- realized_loss_penalty
- complexity_penalty
```

MAE 벌점과 실현손실 벌점은 각각 독립적으로 계산하고 합산 차감한다.

진단 항목 추가:

```text
realized_loss_threshold_pct
realized_loss_penalty_method
realized_loss_penalty
realized_loss_breach_trade_count
total_risk_penalty
```

## 변경 2 — 승 기준 +0.5% 초과

변경 후 승 정의:

```text
비용 차감 후 거래 전체 실현수익 pnl_pct > 0.5
```

따라서 다음은 모두 패다.

```text
+0.5% 이하의 작은 이익
0%
손실
```

`fitness_win_after_cost`, `win_count`, `loss_count`, `win_rate_pct`, 60% 승률 게이트가 모두 새 정의를 사용한다.

## 변경 3 — 최소 거래수 12

변경 후 strict gate:

```text
trade_count >= 12 AND win_rate_pct >= 60.0
```

다음 중 하나라도 만족하지 못하면 기존 방식대로 fitness는 `-1_000_000_000.0`이다.

```text
거래수 12 미만
승률 60% 미만
거래 0건
```

## 주목표 유지

보유일 대비 수익 평균은 변경하지 않았다.

```text
mean(net_realized_pnl_pct / max(holding_days, 1))
```

벌점과 gate만 추가·변경됐다.

## 정적 검증 결과

| 검증 | 입력 | 기대 | 결과 |
|---|---|---|---|
| `py_compile` | 수정 파일 + `genetic.py` | 통과 | PASS |
| 실현손실 벌점 | -0.5% | 0.0 | PASS |
| 실현손실 벌점 | -1.5% | 0.5 | PASS |
| 실현손실 벌점 | -3.0% | 2.0 | PASS |
| 승패 | +0.4% | 패 | PASS |
| 승패 | +0.5% | 패 | PASS |
| 승패 | +0.6% | 승 | PASS |
| 승패 | -1.0% | 패 | PASS |
| 게이트 | 11건, 승률 100% | 실격 | PASS |
| 게이트 | 12건, 승률 60% | 통과 | PASS |
| 게이트 | 12건, 승률 58% | 실격 | PASS |
| 게이트 | 0건, 승률 100% | 실격 | PASS |
| 주목표 | 12건, 무벌점 입력 | 평균 일수익 0.8 | `0.8000000000000002`, PASS |
| 두 벌점 독립 차감 | MAE 초과 2, 실현손실 초과 2가 한 거래에 동시 발생 | 각각 평균 차감 | PASS |
| 11건 실제 Entry result | 승률 100% | `-1e9` | PASS |
| Stage 2 legacy swing | synthetic 고정 입력 | 기존 bit 유지 | PASS |
| `gene_scope` 기본값 | run_ga/random/mutate/crossover | 모두 `legacy` | PASS |

### 두 벌점 동시 적용 검증

12개 거래 중 11개는 `+1%`, 1개는 실현 `-3%`, MAE `-4%`, 보유 1일로 구성했다.

```text
primary objective        = 0.6666666666666666
MAE penalty              = 0.16666666666666666
realized-loss penalty    = 0.16666666666666666
total risk penalty       = 0.3333333333333333
fitness before gate      = 0.33333333333333337
final fitness            = 0.33333333333333337
```

두 벌점이 서로 상쇄되지 않고 각각 독립 차감됐다.

## Stage 2 legacy 불변 검증

수정 전 synthetic legacy swing 결과:

```text
fitness = 44.45
IEEE-754 hex = 404639999999999a
```

수정 후:

```text
fitness = 44.45
IEEE-754 hex = 404639999999999a
bitwise_equal = true
```

기본값:

```text
run_ga.gene_scope = legacy
random_rulebook.gene_scope = legacy
mutate.gene_scope = legacy
crossover.gene_scope = legacy
```

`genetic.py` SHA도 변경 전후 동일하므로 mutation bias는 불변이다.

## 보호 파일 SHA-256

시작과 종료 값이 동일하다.

| 파일 | 시작 | 종료 |
|---|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` | 동일 |
| `market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` | 동일 |
| `market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` | 동일 |

## Daemon

```text
PID: 494330
상태: Sl
시작 시각: Sat Jul 11 20:16:00 2026
명령: live_candidate_slots.py daemon --interval 60
```

작업 전후 동일 PID가 유지됐다.

## Git

사전 백업 커밋:

```text
ea98026 Entry fitness 실현손실 벌점·승 기준·거래수 게이트 변경 전 현재 상태 백업
```

최종 커밋은 readout SHA 검증 후 기록한다.
