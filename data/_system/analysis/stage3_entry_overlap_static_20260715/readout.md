# Entry phase 다중 포지션 허용 — 정적 검증 readout

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 수정 파일: `engine/learning/execution_mode_backtest.py`
- GA·실데이터 백테스트·재학습: 실행하지 않음
- 검증 방식: synthetic unit probe + py_compile + legacy deterministic hash 비교
- 시작 HEAD: `3808b4a98924dd9d32c1152625b573a034d8d4ec`
- 수정 전 백업 커밋: `217f849`
- 코드 변경 커밋: `79bbbfa588217432a1ae59a0be0c90666faa6e28`

## STEP 0 — 기존 단일 포지션 구조

현재 entry loop는 거래를 하나 완전히 청산한 뒤 다음 평가 인덱스로 이동했다.

수정 전 코드:

```python
exit_idx = _find_df_index_by_date(df_exit, trade.get("exit_date"))
if exit_idx is None:
    exit_idx = entry_idx + 1

cooldown_start = int(exit_idx) + 1
cooldown_end = int(exit_idx) + max(int(cooldown_days), 0)
trade["cooldown_signal_path"] = _signal_tape_slice(
    signal_tape,
    cooldown_start,
    cooldown_end,
    role="cooldown",
)
trades.append(trade)

i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)
```

`cooldown_days=1`이면 다음 signal 평가 인덱스는 `exit_idx + 2`였다. 따라서 현재 포지션의 보유 기간과 청산 다음 거래일의 strict pass signal은 daily tape에는 기록되지만 entry loop에서는 건너뛰어져 별도 거래가 되지 않았다.

### Entry-scope 구분 위치

```python
entry_scope_active = _entry_scope_active(rb)
```

`_entry_scope_active(rb)`는 rulebook의 `_active_ga_gene_scope == "entry"`만 참으로 본다. 이 분기를 이용해 entry phase만 다중 포지션으로 전환하고 legacy 경로는 기존 점프를 유지했다.

## STEP 1 — 변경 내용

수정 후 다음 인덱스 결정:

```python
if entry_scope_active:
    i += 1
else:
    i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)
```

동작:

- `gene_scope='entry'`: 현재 거래의 exit date와 무관하게 다음 거래일 signal을 평가
- `gene_scope='legacy'`: 기존 단일 포지션 + 청산 후 cooldown 점프 유지
- 각 entry 거래는 기존 `simulate_exit(...)`를 독립 호출하므로 자신의 청산 규칙을 그대로 적용
- 보유 중 다른 strict pass day에도 별도 거래 생성 가능
- exit simulator는 수정하지 않음
- strict interval-break, provisional ATR stop, 보유 상한 등 청산 규칙은 변경하지 않음

### Cooldown 처리

`cooldown_days=1` 인자와 각 거래의 `cooldown_signal_path` 기록은 유지했다.

다만 entry-scope에서는 cooldown이 다른 독립 포지션의 신규 진입을 전역으로 차단하지 않는다. 각 거래별 청산 후 cooldown 구간을 진단 데이터로만 보존한다.

legacy에서는 cooldown이 기존처럼 전역 entry block으로 작동한다.

## STEP 2 — 동시 포지션 회계 처리

현재 코드에는 cash balance, portfolio equity, aggregate exposure, reserved capital ledger가 없다.

포지션 크기 계산:

```python
amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
shares = int(amt_krw / entry_price) if entry_price > 0 else 0
```

`calc_position_size_krw(...)`는 각 거래마다 독립적으로 다음 값을 반환한다.

```python
return position_limit_krw * max(min(ratio, 1.0), 0.0)
```

요약 함수도 거래별 `pnl_pct`·`pnl_krw` 배열만 집계하며 동시 포지션의 자본 점유를 계산하지 않는다.

따라서 이번 변경은 현행 회계 정의를 그대로 따른다.

```text
회계 단위: 독립 거래
명목 금액: 거래마다 동일 position_limit_krw 기준
자본 분할: 없음
현금 제약: 없음
총 노출 상한: 없음
동시 포지션 합산 명목이 position_limit_krw를 초과할 수 있음
```

이는 entry rule의 신호 품질·거래 결과를 평가하는 독립 고정명목 모델이다. 실제 포트폴리오 자본곡선이나 레버리지 제한 모델은 아니다.

## STEP 3 — 정적 검증

### Py_compile

| 검증 | 결과 |
|---|---|
| `python -m py_compile execution_mode_backtest.py` | PASS |
| pycache | 저장소 밖 `/tmp/kingmaker_pycache_multi_entry_20260715` |

### Synthetic 다중 진입 검증

조건:

- 10개 연속 entry-eligible 거래일
- 모든 날짜 `strict_entry=True`, `should_buy=True`
- fixed position sizing, positive shares
- 각 거래 보유일 5일
- cooldown 1일
- entry scope marker 활성

결과:

| 항목 | 기대 | 결과 | 판정 |
|---|---:|---:|---|
| strict pass day | 10 | 10 | PASS |
| 생성 거래 | 10 | 10 | PASS |
| 흡수된 pass day | 0 | 0 | PASS |
| 고유 signal day | 10 | 10 | PASS |
| 보유 중 신규 진입 | 발생 | 발생 | PASS |
| 최대 동시 포지션 | 2 이상 | 5 | PASS |
| 각 거래 독립 청산 | 각 entry 기준 5일 | 모두 5일 | PASS |

생성된 거래:

```text
2024-03-25 -> 2024-04-01
2024-03-26 -> 2024-04-02
2024-03-27 -> 2024-04-03
2024-03-28 -> 2024-04-04
2024-03-29 -> 2024-04-05
2024-04-01 -> 2024-04-08
2024-04-02 -> 2024-04-09
2024-04-03 -> 2024-04-10
2024-04-04 -> 2024-04-11
2024-04-05 -> 2024-04-12
```

두 번째 포지션은 첫 번째 포지션의 청산일보다 먼저 진입했고, 최대 5개 포지션이 동시에 열려 있었다.

### Strict pass와 실제 거래 수의 적용 범위

단위 검증에서는 모든 pass day가 positive position size와 유효한 entry/exit를 가지므로 `N pass = N trades`가 성립했다.

실제 실행에서는 다음 기존 fail-safe는 유지된다.

- `sig.should_buy=False`
- D+1 fill이 fold end 이후
- entry price invalid
- 계산 shares가 0
- exit simulator가 거래를 반환하지 않음

따라서 다중 포지션 변경은 보유·cooldown 흡수를 0으로 만들지만, 위 기존 사유까지 제거하는 변경은 아니다.

### Stage2 legacy bitwise 불변

수정 전후 동일 synthetic 입력에 대한 legacy 결과 전체 JSON의 SHA-256을 비교했다.

```text
수정 전: 066c5dc248daf54a4bb0de44e799fa045553fd7013bd9986a2fe70f71189bed7
수정 후: 066c5dc248daf54a4bb0de44e799fa045553fd7013bd9986a2fe70f71189bed7
```

| 항목 | 수정 전 | 수정 후 |
|---|---:|---:|
| legacy trade count | 2 | 2 |
| result/trade JSON hash | 동일 | 동일 |
| 단일 포지션 + cooldown | 유지 | 유지 |

결과: **PASS — bitwise exact**

### Mutation bias 불변

`engine/learning/genetic.py` SHA-256:

```text
28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f
```

수정 전후 동일하다.

AST 함수 본문 SHA-256:

| 함수 | SHA-256 | 결과 |
|---|---|---|
| `_attach_entry_exit_local_search` | `d04ba558c98b2d31af3cae5b65d2cb6e145da2dd2c49851c7ab6d5b80b78a0af` | 불변 |
| `_aggregate_entry_exit_mutation_hint` | `293d922a434da85add2f99b60b4654cec73299ea52de436b6181a0985bb37776` | 불변 |

## 수정 diff

```text
1 file changed, 10 insertions(+), 6 deletions(-)
```

변경 파일:

```text
scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py
```

변경 범위:

1. module docstring에서 entry/legacy scheduling 차이를 명시
2. entry-scope에서는 `i += 1`
3. legacy에서는 기존 exit+cooldown jump 유지

Exit simulator와 genetic 파일은 수정하지 않았다.

## 파일 SHA-256

수정 대상:

```text
수정 전
35bf16dd6057ebae0e851006a2dce32d2c4893312f3293ce0bbb93d715124308

수정 후
ce2b6673375a121c02a443ad24b811e2b7c00ce3de2c723ec69e132e74caf0ca
```

## 보호 파일

시작 SHA:

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

종료 시 동일 SHA를 다시 확인한다.

## Daemon·Git 기준

Daemon:

```text
PID 494330
start: Sat Jul 11 20:16:00 2026
command: live_candidate_slots.py daemon --interval 60
```

코드 커밋:

```text
79bbbfa588217432a1ae59a0be0c90666faa6e28
```

코드 커밋 후 working tree는 산출물 작성 전 clean 상태였다.
