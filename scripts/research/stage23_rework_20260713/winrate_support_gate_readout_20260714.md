# 승률 게이트 + 최소거래수 결합 수정 readout

- 작업일: 2026-07-14
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 범위: 코드 수정 + 정적 검증만 수행
- GA/백테스트 재학습: 미실행
- 수정 전 백업 커밋: `9953e3f`

## STEP 0 — 기존 결합 상태와 1건 best 발생 경로

### 기존 entry fitness 게이트

수정 전 `engine/learning/execution_mode_backtest.py::_apply_entry_scope_fitness()`의 실격 조건은 다음이었다.

```text
disqualified = (trade_count <= 0) OR (win_rate < 60%)
```

즉 거래가 1건 이상이면 support 하한 없이 승률만 검사했다. 거래 1건에서 1승이면 승률 100%이므로 fitness gate를 통과했다.

### 기존 qualify support 하한

`engine/pipeline/stage3_gate.py::Stage3QualifyConfig`에는 `min_trades=5`가 존재하지만, 이 값은 후보를 각 fold에 cross-fold 평가한 뒤 최종 qualify pass/fail을 판정하는 단계에서만 적용된다.

```text
qualify fold pass =
    trade_count >= 5
    AND member_score >= 10
    AND expectancy_pct >= 2%
```

GA가 세대 내에서 개체를 선택하는 entry fitness 단계에는 이 5건 기준이 연결되지 않았다. 따라서 두 조건은 기존에 AND 결합돼 있지 않고 서로 다른 시점에 따로 동작했다.

### train_1 best가 거래 1건으로 선택된 이유

AAP 새 fitness 정식 실행의 train_1 fold-best 진단은 다음과 같았다.

- 거래 수: 1
- 승: 1
- 승률: 100%
- 하루당 수익 primary: 2.9304509209
- MAE 감점: 0
- 기존 `win_rate_gate_pass`: true
- 기존 최종 fitness: 2.9304509209

이 개체는 **최종 qualify를 통과한 것이 아니라**, support 검사가 없는 GA fitness 단계에서 best로 선택된 뒤 fold-best 감사 로그에 남은 것이다. 이후 cross-fold qualify에서는 거래 수 5건 하한을 포함한 별도 기준이 적용됐고 전체 결과는 `all3=0`이었다.

근본 원인은 다음과 같다.

```text
GA selection gate: trade_count > 0 AND win_rate >= 60%
qualify final gate: trade_count >= 5 AND member_score >= 10 AND expectancy >= 2%
```

두 support 기준이 분리돼 있어 GA가 극소수 고승률 개체로 도피할 수 있었다.

## STEP 1 — 결합 게이트 수정

entry scope fitness 통과 조건을 다음으로 변경했다.

```text
entry_gate_pass =
    (trade_count >= 10)
    AND
    (win_rate_pct >= 60.0)
```

둘 중 하나라도 실패하면 fitness는 기존과 동일하게 `-1_000_000_000.0`으로 실격된다.

### N_min = 10 선택 근거

- 사용자 권고 범위 8~10의 상단값을 채택했다.
- 1년 fold에서 10건은 대략 월 1회에 가까운 최소 활동성이다.
- 직전 best의 1건, 5건, 6건 표본을 모두 차단한다.
- 기존 qualify 5건보다 엄격해 GA selection 단계에서 거래 회피를 먼저 억제한다.
- 10건·60%도 통계적 유의성을 증명하는 표본은 아니다. 이번 값은 승률의 확정적 통계 검정선이 아니라 strict-AND 환경에서 사용할 실전 최소 support 하한이다.

기존 `Stage3QualifyConfig.min_trades=5`는 변경하지 않았다. entry fitness의 10건 gate가 먼저 작동하고, 최종 qualify의 5건 기준은 기존 호환성을 위해 그대로 유지된다.

## 수정 파일

| 파일 | 변경 여부 | 내용 |
|---|---|---|
| `engine/learning/execution_mode_backtest.py` | 수정 | entry fitness에 거래수 10건 + 승률 60% AND gate 추가 |
| `engine/learning/genetic.py` | 변경 없음 | gate 판정 위치가 아니며 `gene_scope='legacy'` 기본값 유지 |
| `engine/pipeline/stage3_gate.py` | 변경 없음 | 기존 최종 qualify support 5건 유지 |

## Diff 요약

1. 상수 추가

```text
ENTRY_FITNESS_MIN_TRADES = 10
```

2. 기존 실격식 교체

```text
기존:
trade_count <= 0 OR win_rate < 60%

변경:
NOT (trade_count >= 10 AND win_rate >= 60%)
```

3. 진단 필드 추가

- `trade_count`
- `min_trade_count`
- `trade_count_gate_pass`
- `win_rate_threshold_pass`
- `entry_gate_rule`
- `entry_gate_pass`
- `disqualification_reasons`
- `fitness_before_entry_gate`
- `primary_objective_trade_count_neutral`

4. 하위 로그 호환

기존 downstream 로그가 읽는 `win_rate_gate_pass`는 **결합 gate 결과의 호환 alias**로 유지했다. 따라서 거래 1건·승률 100%도 `win_rate_threshold_pass=true`이지만 `win_rate_gate_pass=false`와 `entry_gate_pass=false`로 기록된다.

## STEP 2 — 주목표와 거래 회피 확인

주목표는 계속 다음 거래별 평균이다.

```text
mean(net_realized_pnl_pct / max(holding_days, 1))
```

이 값은 거래 수 자체를 보상하거나 감점하지 않는다. 동일한 10거래 패턴을 두 번 반복한 20거래 개체와 10거래 개체의 primary가 모두 `0.2`로 동일함을 정적 검증했다.

따라서:

- 거래 수를 줄여 평균 승률을 높이는 도피는 N_min=10 gate가 차단한다.
- N_min을 정확히 채운 뒤 높은 승률을 만드는 개체는 여전히 가능하다.
- 이는 의도된 하한 동작이며, 거래 수를 주목표에 별도 가산하지 않아 과도한 매매를 보상하지 않는다.
- 향후 재학습에서는 10건 경계에 population이 몰리는지 별도로 관찰해야 한다.

## 정적 검증

### py_compile

```text
engine/learning/execution_mode_backtest.py: PASS
engine/learning/genetic.py: PASS
```

### 경계값 검증

| 입력 | 결과 |
|---|---|
| 1건, 1승, 승률 100% | 실격 `-1e9` |
| 9건, 9승, 승률 100% | 실격 `-1e9` |
| 10건, 6승, 승률 60% | 통과, fitness `0.2` |
| 10건, 5승, 승률 50% | 실격 `-1e9` |
| 20건, 승률 60%, 10건 패턴 반복 | 통과, primary `0.2` |

추가 확인:

- 정확히 60.0%는 통과한다.
- 10건 미만은 승률과 무관하게 실격된다.
- 10건 이상이어도 승률 60% 미만이면 실격된다.
- 기존 qualify `min_trades=5`는 불변이다.
- scope marker가 없는 legacy swing fitness는 기존 공식 결과와 동일했다.
- `gene_scope` 기본값 `legacy`는 변경하지 않았다.

## SHA 전후

| 파일 | 수정 전 SHA-256 | 수정 후 SHA-256 |
|---|---|---|
| `engine/learning/execution_mode_backtest.py` | `e2b4cb157da1a0d4be81ffaa850c8eb52725297c10bd6c3b0997711b964859cd` | `6a78019280d761eed422841b8d24bc2b5683a1e17977e6d5d2f2d1adae4ba827` |
| `engine/learning/genetic.py` | `28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f` | `28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f` |

## 보호 대상과 daemon

보호 파일은 읽기만 했고 시작·종료 SHA가 동일하다.

- `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce`
- `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`
- `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611`

Daemon PID `494330`은 동일 시작 시각의 `Sl` 상태로 유지됐다.
