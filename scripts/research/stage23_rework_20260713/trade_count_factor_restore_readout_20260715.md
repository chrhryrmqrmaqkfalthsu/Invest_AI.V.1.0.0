# 거래수 fitness 연속 factor 복원 readout

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 코드 수정 파일: `engine/learning/execution_mode_backtest.py`
- GA·백테스트·재학습: 실행하지 않음
- 시작 HEAD: `6b96d2d6ee4b5c6b943ee3b7d6f4776ba60178e3`
- 수정 전 백업 커밋: `eed0461`
- 코드 변경 커밋: `4aefd2ac7aebc0f5e42ef6d80de245c2c9c8a1df`

## STEP 0 확인

수정 전 entry-scope 구조:

```text
primary = mean(net realized pnl_pct / max(holding_days, 1))
trade_count >= 12 AND win_rate >= 60%가 아니면 -1e9
```

raw primary는 거래별 일수익률 평균이므로 거래수 자체에는 중립이었다.

원본 `engine/learning/backtest.py::_calc_fitness_swing`의 거래수 factor:

```text
trade_count < 5   -> 0.10
trade_count < 10  -> 0.35
trade_count < 20  -> 0.70
trade_count <= 80 -> 1.00
trade_count > 80  -> max(0.65, 1.0 - (trade_count - 80) / 250.0)
```

원본은 20건에서 `0.70 -> 1.00` 계단형 불연속이 있다.

수정 전 파일에는 이전 결합 작업의 profit concentration penalty도 entry-scope에 들어가 있었다. 이번 변경은 거래수 압력만 격리해야 하므로 entry-scope concentration 감점은 제거했다. legacy swing의 concentration penalty는 유지했다.

## 적용한 factor

원본 anchor를 사용하되 8·12·20 경계를 선형 연결했다.

```text
n < 8:
    factor = 0.0
    hard fail-safe로 -1e9

8 <= n < 12:
    factor = 0.35 + (n - 8) * (0.70 - 0.35) / 4

12 <= n < 20:
    factor = 0.70 + (n - 12) * (1.00 - 0.70) / 8

20 <= n <= 80:
    factor = 1.00

n > 80:
    factor = max(0.65, 1.0 - (n - 80) / 250.0)
```

주요 값:

| 거래수 | factor |
|---:|---:|
| 7 | 0.0000, 실격 |
| 8 | 0.3500 |
| 10 | 0.5250 |
| 12 | 0.7000 |
| 19 | 0.9625 |
| 20 | 1.0000 |
| 25 | 1.0000 |
| 81 | 0.9960 |
| 168 이상 | 0.6500 하한 |

원본의 `12~19 전체 0.70` 계단을 그대로 유지하지 않고, cliff 제거 요구에 따라 12에서 20까지 선형 증가시켰다. 따라서 12건은 70%만 인정되고 20건까지 매 거래마다 추가 유인이 있다.

## 유지된 요소

- win 기준: 비용 차감 실현수익 `> +0.5%`
- win-rate gate: `>= 60%`
- MAE 벌점: `-2%` 초과 이탈분 평균
- 실현손실 벌점: `-1%` 초과 손실분 평균
- complexity penalty
- all3 구조
- 80건 초과 원본 감쇠
- mutation helper·bias
- Stage2 legacy swing 경로

이번에 추가하지 않은 항목:

- profit concentration penalty
- event concentration
- cross-fold fitness
- strict-AND 변경
- Jaccard 이동

최종 entry fitness:

```text
fitness_before_gate =
    raw_primary * trade_count_factor
    - MAE penalty
    - realized-loss penalty
    - complexity penalty

trade_count < 8 OR win_rate < 60%
    -> -1e9
```

## 정적 검증

`py_compile`: PASS. pycache는 저장소 밖 `/tmp/kingmaker_pycache_trade_factor_20260715`에 생성했다.

동일 synthetic 거래 패턴(`pnl_pct=2.0`, `holding_days=2`, `MAE=-1.0`) 검증:

| 거래수 | factor | gate | fitness | 결과 |
|---:|---:|---|---:|---|
| 7 | 0.000 | FAIL | -1,000,000,000 | PASS |
| 10 | 0.525 | PASS | 0.525 | PASS |
| 12 | 0.700 | PASS | 0.700 | PASS |
| 20 | 1.000 | PASS | 1.000 | PASS |
| 25 | 1.000 | PASS | 1.000 | PASS |
| 81 | 0.996 | PASS | 0.996 | PASS |
| 168 | 0.650 | PASS | 0.650 | PASS |

`25건 동일 패턴 fitness 1.000 > 12건 fitness 0.700`으로 거래수 확대 유인을 확인했다.

### legacy swing 불변

- `engine/learning/backtest.py` SHA-256 전·후 동일
- rework 복사본도 동일
- non-entry rulebook의 `_apply_fitness_mode(..., fitness_mode="swing")` 결과가 `_calc_fitness_swing(...)` 직접 호출값과 exact equality
- 결과: PASS

### mutation 불변

백업 커밋 `eed0461`과 변경 후 AST 함수 본문 SHA 비교:

| 함수 | SHA-256 | 결과 |
|---|---|---|
| `_attach_entry_exit_local_search` | `d04ba558c98b2d31af3cae5b65d2cb6e145da2dd2c49851c7ab6d5b80b78a0af` | 동일 |
| `_aggregate_entry_exit_mutation_hint` | `293d922a434da85add2f99b60b4654cec73299ea52de436b6181a0985bb37776` | 동일 |

`engine/learning/genetic.py`는 수정하지 않았다.

## diff와 SHA

코드 diff:

```text
1 file changed, 19 insertions(+), 26 deletions(-)
```

수정 대상 SHA-256:

```text
수정 전  0ed2cec35731ddc7ff06ae6860495ffbf748b997ad06b3458cb2b07e0ef5924b
수정 후  35bf16dd6057ebae0e851006a2dce32d2c4893312f3293ce0bbb93d715124308
```

legacy swing SHA-256:

```text
734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e  engine/learning/backtest.py
734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e  scripts/research/stage23_rework_20260713/engine/learning/backtest.py
```

보호 파일 SHA는 시작·종료 동일:

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

Daemon PID `494330`은 유지됐다.

## 결론

정적 검증 기준으로 12건 경계의 구조적 유인은 제거됐다. 8건 미만만 fail-safe 실격이며, 8건부터 20건까지 factor가 연속 상승한다. 12건은 70%만 인정되고 20건부터 100%를 인정한다. 지시대로 GA·백테스트는 실행하지 않았으므로 실제 fold-best 분포 변화는 아직 측정하지 않았다.
