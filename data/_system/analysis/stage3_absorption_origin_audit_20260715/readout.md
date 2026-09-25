# held/cooldown 흡수 로직 출처·정합성 감사

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 범위: 코드·git 이력·기존 문서 열람만
- GA·백테스트·재학습·분석 실행: 없음
- 코드·시장 데이터 수정: 없음
- 분석 시작 HEAD: `3e206b913cdeb21dd0287ee0426647dd11a67816`
- 분석 전 백업 커밋: `23d18bd`

## 최종 판정

**ABSORPTION_CHANGED_IN_REWORK**

다만 정확한 의미는 다음과 같다.

1. **한 번에 한 포지션만 허용하고, 청산 후 cooldown까지 신규 진입을 건너뛰는 기본 흡수 구조는 원본부터 존재했다.**
2. **cooldown 기본값 1일도 원본과 rework가 동일하다.**
3. 그러나 rework Stage3 entry phase는 원본의 rulebook 기반 보유·청산 경로를 그대로 쓰지 않고, **고정 7거래일 cap + strict interval-break + entry 전용 ATR stop**을 새로 주입했다.
4. 따라서 “보유·cooldown 중 pass day가 별도 진입이 되지 않는다”는 구조 자체는 원본이지만, **어떤 날짜가 얼마나 오래 흡수되는지를 결정하는 보유·청산 규칙은 rework에서 변경됐다.**

판정 정의상 `ABSORPTION_IS_ORIGINAL`은 값과 구조가 모두 같아야 하므로 적용할 수 없다.

---

## STEP 1 — 현재 rework 구현 위치

### 1. cooldown 기본값 1일

파일:

```text
scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py:771
```

```python
cooldown_days: int = 1,
```

### 2. 한 번에 한 포지션만 허용하는 순차 실행 구조

파일:

```text
scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py:814-874
```

핵심 흐름:

```python
while i < n:
    ...
    if not sig.should_buy:
        i += 1
        continue
    ...
    trade_obj = simulate_exit(
        rb,
        df_exit,
        entry_idx,
        ...
    )
```

`simulate_exit(...)`가 현재 포지션의 청산 시점을 반환할 때까지 하나의 거래를 완성한 뒤 다음 인덱스로 이동한다. 포지션 객체를 병렬로 여러 개 유지하거나 보유 중 새 포지션을 여는 루프는 없다.

### 3. 보유·cooldown 동안 신규 진입 차단

파일:

```text
scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py:908-937
```

```python
exit_idx = _find_df_index_by_date(df_exit, trade.get("exit_date"))
if exit_idx is None:
    exit_idx = entry_idx + 1
...
cooldown_start = int(exit_idx) + 1
cooldown_end = int(exit_idx) + max(int(cooldown_days), 0)
...
i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)
```

`cooldown_days=1`이면 다음 평가 인덱스는 `exit_idx + 2`가 된다.

따라서:

- entry부터 exit까지의 날짜는 현재 포지션 보유 기간
- exit 다음 거래일 1일은 cooldown
- 이 기간의 strict-AND pass day는 별도 진입이 되지 않음

### 4. held/cooldown 신호는 측정하지만 신규 진입에는 사용하지 않음

파일:

```text
scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py:795-810, 919-934
```

```python
signal_tape = _build_daily_signal_tape(...)
```

```python
trade["holding_signal_path"] = _signal_tape_slice(
    signal_tape,
    entry_idx,
    int(exit_idx),
    role="holding",
)
...
trade["cooldown_signal_path"] = _signal_tape_slice(
    signal_tape,
    cooldown_start,
    cooldown_end,
    role="cooldown",
)
```

이 tape는 보유·cooldown 날짜의 신호 누락을 제거하고 provisional exit 판단·진단에 쓰기 위한 것이다. entry loop의 `i` 점프는 그대로이므로, tape에 `should_buy=True`가 있어도 보유·cooldown 중 별도 거래는 생성되지 않는다.

### 5. 현재 Stage3 entry phase의 7일 보유 상한

파일:

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py:35
```

```python
ENTRY_PHASE_MAX_HOLDING_DAYS = 7
```

같은 파일의 entry 전용 execution context:

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py:401-408
```

```python
def simulate_entry_exit(*args: Any, **kwargs: Any) -> Any:
    tape = state.get("signal_tape")
    if tape is None:
        raise RuntimeError("entry-phase daily signal tape was not built before simulate_exit")
    kwargs["entry_phase_exit"] = True
    kwargs["entry_phase_signal_tape"] = tape
    kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
    return original_simulate_exit(*args, **kwargs)
```

### 6. 7일 cap과 strict interval-break 실제 적용

파일:

```text
scripts/research/stage23_rework_20260713/engine/strategies/exit_simulator.py:391-397
```

```python
holding_cap = (
    max(1, int(entry_phase_max_holding_days))
    if entry_phase_exit
    else int(rb.max_holding_days)
)

for i in range(entry_idx + 1, min(entry_idx + holding_cap + 1, len(df))):
```

entry phase의 청산 우선순위:

```text
1. entry 전용 ATR stop
2. strict interval break 후 다음 거래일 open
3. provisional max holding 7일
```

strict interval-break 코드:

```text
scripts/research/stage23_rework_20260713/engine/strategies/exit_simulator.py:434-464
```

```python
interval_pass = _strict_interval_pass_from_tape(entry_phase_signal_tape, i, row)
if interval_pass is False:
    next_idx = i + 1
    if next_idx < len(df):
        next_row = df.iloc[next_idx]
        ...
        return _build_trade(
            ...
            exit_date=next_row.name,
            ...
            exit_reason=ENTRY_PHASE_INTERVAL_BREAK_REASON,
            holding_days=next_idx - entry_idx,
            ...
        )

if holding_days >= holding_cap:
    return _build_trade(...)
```

---

## STEP 2 — 원본과 대조

### 원본 execution-mode backtest

파일:

```text
engine/learning/execution_mode_backtest.py
```

원본 cooldown 기본값:

```python
cooldown_days: int = 1,
```

원본도 거래 하나를 `simulate_exit(...)`로 끝낸 뒤 다음 인덱스로 이동한다.

```python
exit_idx = _find_df_index_by_date(df_exit, trade.get("exit_date"))
if exit_idx is None:
    exit_idx = entry_idx + 1
i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)
```

### 원본 legacy backtest

파일:

```text
engine/learning/backtest.py
```

원본 legacy 경로도 기본값이 1일이다.

```python
cooldown_days: int = 1,
```

청산 후 인덱스 점프:

```python
exit_idx = _find_df_index_by_date(df, exit_date)
if exit_idx is None:
    exit_idx = i + 1
i = max(exit_idx + 1 + cooldown_days, i + 1)
```

### 원본 보유 상한·청산 경로

원본 exit simulator는 고정 7일을 사용하지 않는다.

```text
engine/strategies/exit_simulator.py:338
```

```python
for i in range(
    entry_idx + 1,
    min(entry_idx + int(rb.max_holding_days) + 1, len(df)),
):
```

원본 Rulebook:

```text
engine/strategies/rulebook.py
```

```python
max_holding_days: int = 20
```

GA 허용 범위:

```python
"max_holding_days": (5, 30)
```

원본 Stage3 qualify·entry GA는 별도 `gene_scope="entry"`를 지정하지 않고 기본 full-rulebook GA를 호출했다. 따라서 원본의 보유 상한은 후보 rulebook의 `max_holding_days` 값에 따라 5~30 범위에서 달라질 수 있었다.

원본 청산은 `evaluate_exit(...)`가 다루는 기존 exit policy와 rulebook의 14개 exit field를 사용하고, 끝까지 청산되지 않으면 `rb.max_holding_days`에서 `time_out` 처리한다.

원본에는 다음 entry-phase 전용 요소가 없다.

- `entry_phase_exit`
- strict interval-break 청산
- entry 전용 고정 7일 cap
- entry-phase daily tape 주입

### 원본 vs rework 대조표

| 항목 | 원본 | rework Stage3 entry phase | 판정 |
|---|---|---|---|
| 한 번에 한 포지션 | 예. 거래를 완성한 뒤 다음 `i`로 이동 | 예. 같은 순차 루프 유지 | 동일 |
| 보유 중 신규 진입 | 차단. 보유 종료까지 entry loop가 진행되지 않음 | 차단. daily tape는 측정용이며 신규 포지션 생성 안 함 | 동일 |
| cooldown 기본값 | 1거래일 | 1거래일 | 동일 |
| cooldown 후 인덱스 이동 | `exit_idx + 1 + cooldown_days` | 동일 | 동일 |
| 보유 상한 | 후보 `rb.max_holding_days`, 기본 20·범위 5~30 | entry phase에서 고정 7일 override | 변경 |
| 보유 중 exit 판단 | 원본 14-field/rulebook exit policy | ATR stop → strict interval-break → 7일 cap | 변경 |
| 모든 조건 통과일 진입 | 아니오. 포지션이 없을 때 현재 `i`만 평가 | 아니오. 모든 날을 tape로 측정하지만 보유·cooldown pass는 진입 안 함 | 진입 구조 동일, 측정 방식 추가 |
| exit·validate 후속 단계 | 원본 exit policy | commit 설명상 원본 경로 보존 | entry phase만 변경 |

### 질문별 답

#### (a) 원본도 한 번에 한 포지션만 허용했는가?

**예.** 최초 백테스트부터 순차 단일 포지션 구조였고, 청산·cooldown 뒤로 인덱스를 점프했다.

#### (b) 보유 상한·cooldown 값이 같았는가?

- cooldown: **같음, 1일**
- 보유 상한: **다름**
  - 원본: `rb.max_holding_days`, 기본 20, GA 범위 5~30
  - rework entry phase: 고정 7일

#### (c) 원본은 조건 통과일 전부 진입했는가?

**아니오.** 원본도 현재 포지션이 없는 시점의 신호만 진입 후보가 됐다. 보유·cooldown 중의 조건 통과일은 별도 진입으로 처리되지 않았다.

---

## STEP 3 — 변경 이력

### 2026-05-25 — 원본 단일 포지션·cooldown 구조 도입

Commit:

```text
59b8a47b4023106070f3afb8d12ae8128b435004
feat: engine/strategies + engine/learning 완성 (GA v4)
```

최초 `engine/learning/backtest.py`부터:

```python
cooldown_days: int = 1
...
i = max(i + 1, int(exit_idx) + cooldown_days)
```

형태의 단일 거래 순차 실행이 존재했다.

### 2026-05-25 — 시장 시계열 경로 개편 후 구조 유지

Commit:

```text
c8b138673a1999855fc809674fc5ce6a88ac69de
feat: 시장 시계열 학습 + 시드 격리 + ticker 보존 fix
```

인덱스 이동 표현은 다음 형태로 정리됐지만 의미는 동일했다.

```python
i = max(exit_idx + 1 + cooldown_days, i + 1)
```

### 2026-06-09 — execution-mode backtest 추가

Commit:

```text
c1529f4b1fb4aae571f74c2f5b4c28fb76ec1464
LR8D 학습부 execution mode 게이트와 fold_end 누수 방지 검증 추가
```

새 `engine/learning/execution_mode_backtest.py`도:

```python
cooldown_days: int = 1
...
i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)
```

로 원본 단일 포지션·cooldown 구조를 그대로 유지했다.

### 2026-07-13 00:24 — rework 원본 복사

Commit:

```text
47097c0fdd271c185282d92e6fc0119651968327
20260712~13 재설계·분석·백업 산출물을 전부 제거하고 정식 Stage2·Stage3 원본 166개를 stage23_rework_20260713에 SHA 일치 복사
```

Git blob 비교:

```text
engine/learning/execution_mode_backtest.py
root parent blob = dd17be322bdf56f986ad78dde29b13c8947be4d7
rework copy blob = dd17be322bdf56f986ad78dde29b13c8947be4d7
match = YES

engine/strategies/exit_simulator.py
root parent blob = ce7e2ed3fe254c4656d68ec810a5740bde940936
rework copy blob = ce7e2ed3fe254c4656d68ec810a5740bde940936
match = YES
```

따라서 rework 시작점의 단일 포지션·cooldown 동작은 원본과 완전히 동일했다.

### 2026-07-13 07:01 — 보유·cooldown 신호 tape 추가

Commit:

```text
b2bf48525553a0bd2214299e7af6291b1c829d1f
Stage23 실행 백테스트에 전 거래일 daily signal tape를 선계산해 보유·cooldown 신호 누락을 제거하고 commit 기반 cache semantics를 분리
```

이 변경은 보유·cooldown 날짜의 신호를 **측정·기록**하도록 추가한 것이다. entry 인덱스 점프는 변경하지 않았으므로 동시 진입을 허용한 변경은 아니다.

### 2026-07-13 07:06 — entry 전용 청산·7일 cap 도입

Commit:

```text
2af14aa7bc32f164072c83f7e84b8b7371e3fe16
Stage23 복사본 exit simulator에 entry 전용 ATR손절·strict interval break 다음날 시가·7일 cap 경로를 추가하고 기존 14-field 청산은 기본값으로 보존
```

이 커밋에서 추가된 핵심:

```text
ENTRY_PHASE_PROVISIONAL_MAX_HOLDING_DAYS = 7
entry_phase_exit
entry_phase_signal_tape
entry_phase_max_holding_days
entry_interval_break
entry_provisional_atr_stop
entry_provisional_max_holding
```

이 시점부터 entry phase의 실제 보유 기간과 흡수 구간은 원본 rulebook exit 경로가 아니라 provisional 경로의 영향을 받게 됐다.

### 2026-07-13 07:15 — Stage3 entry phase에 실제 배선

Commit:

```text
f6641fa93e2236ab7653911599b468c6959bc532
Stage23 Stage3 wrapper에 fold 실측 domain·entry-scope GA·daily tape 기반 provisional 청산을 배선하고 exit·validate 원본 경로를 보존
```

이 커밋에서:

```python
ENTRY_PHASE_MAX_HOLDING_DAYS = 7
kwargs["entry_phase_exit"] = True
kwargs["entry_phase_signal_tape"] = tape
kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
```

가 Stage3 entry backtest에 실제 적용됐다.

커밋 메시지에 따르면 변경 범위는 entry phase이며, exit·validate 원본 경로는 보존했다.

### 2026-07-13 09:33 — D-5 정렬 수정

Commit:

```text
16bd40a273ec4c4a3ccd2386bcf7fc852ffc4873
무효 Stage3 실행을 D-5 lag 누락으로 격리하고 strict 기술 feature·fold domain·GA support·daily tape·interval-break를 D-5 거래일 기준으로 정렬
```

7일 cap이나 단일 포지션 구조를 새로 바꾼 것이 아니라, 이미 도입된 strict daily tape·interval-break를 D-5 기준으로 정렬한 변경이다.

---

## STEP 4 — 최종 해석

### 원본인 부분

- 한 번에 한 포지션
- 보유 중 신규 진입 차단
- 청산 후 cooldown 동안 신규 진입 차단
- cooldown 기본값 1일
- `exit_idx` 이후로 entry 인덱스를 점프하는 구조

### rework에서 바뀐 부분

- entry phase 보유 상한을 후보별 5~30일에서 고정 7일로 변경
- 원본 exit policy 대신 entry 전용 ATR stop·strict interval-break·7일 cap 사용
- 보유·cooldown 날짜의 신호를 daily tape로 선계산해 exit·진단에 사용

### 정합성 결론

“held/cooldown pass day를 별도 거래로 세지 않는다”는 원리는 정상적인 원본 설계다.

그러나 v3 support-ceiling probe에서 관측한 구체적인 흡수 결과 `12 / 11 / 12`는 rework entry phase의 strict interval-break와 고정 7일 cap을 통과한 결과다. 원본 exit policy를 적용했을 때도 동일한 날짜와 동일한 흡수 수가 나온다고 볼 근거는 없다.

따라서 가장 정확한 판정은:

```text
ABSORPTION_CHANGED_IN_REWORK
```

이며, 부연은 다음과 같다.

```text
base single-position/cooldown absorption = original
entry-phase holding/exit rules that determine the concrete absorption window = changed in rework
```

---

## 파일 SHA-256

```text
734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e  engine/learning/backtest.py
efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0  engine/learning/execution_mode_backtest.py
9d2c26e7c081ca1e8d5a6f4e5935af15adbf71921006ea76c27f788afae31a2d  engine/strategies/exit_simulator.py
35bf16dd6057ebae0e851006a2dce32d2c4893312f3293ce0bbb93d715124308  scripts/research/stage23_rework_20260713/engine/learning/execution_mode_backtest.py
37ea9550bb8870c2dce85fc6f0a9ea14cf5ed5c881312b1b0da8bf9fa0959d86  scripts/research/stage23_rework_20260713/engine/strategies/exit_simulator.py
3fb837c3a575d98260e3bc71eb45678440797a8f61188cdff366c93a0f8ebe7d  scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py
```

## 보호 상태 기준

분석 시작 SHA:

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

Daemon 기준:

```text
PID 494330
starttime: Sat Jul 11 20:16:00 2026
command: live_candidate_slots.py daemon --interval 60
```
