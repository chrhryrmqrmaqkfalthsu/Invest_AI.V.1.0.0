# Entry phase 7일 고정 cap 영향 분석

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 분석 대상: `data/_system/analysis/stage3_aap_tradecount_factor_v3_20260715/AAP/NOTEBOOK_MAX/`
- 대상 종목: AAP
- 대상 fold: train_1 / train_2 / train_3
- 방식: 기존 v3 fold-best·동일 OHLCV·동일 strict-AND 날짜를 사용한 read-only 반사실 재집계
- GA·백테스트·재학습: 실행하지 않음
- 코드·시장 데이터 수정: 없음
- 분석 시작 HEAD: `3d62f517026eba41402ff99efbe9015790719b35`
- 분석 전 백업 커밋: `edd5402`

## 최종 판정

**CAP_IRRELEVANT**

현행 7일 cap을 10일·15일·20일 또는 각 fold-best 개체의 `rb.max_holding_days=20`으로 바꿔도 비중복 체결 수는 다음과 같이 전혀 변하지 않았다.

```text
train_1 = 12
train_2 = 11
train_3 = 12
```

원인은 명확하다. v3 fold-best의 실제 35개 거래는 모두 `entry_interval_break`로 청산됐고 최대 보유일은 각각 3 / 5 / 5일이었다. 따라서 7일 cap은 한 번도 실제 청산을 결정하지 않았으며, 더 큰 cap으로 바꿔도 exit date·cooldown·다음 진입 순서가 그대로다.

즉 기존 `12 / 11 / 12` support 결과는 **7일 고정 cap의 인공물은 아니다.** 현재 후보에서는 strict interval-break와 신호 군집이 실제 support를 결정한다.

---

## STEP 0 — 현재 주입 지점과 원본 비교

### 현재 rework의 고정 7일 cap

파일:

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py:35
```

```python
ENTRY_PHASE_MAX_HOLDING_DAYS = 7
```

entry phase 실행 context:

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py:389-416
```

```python
@contextmanager
def _entry_phase_execution_context() -> Iterator[None]:
    original_builder = _execution_backtest._build_daily_signal_tape
    original_simulate_exit = _execution_backtest.simulate_exit
    state: dict[str, Any] = {}

    def build_tape(*args: Any, **kwargs: Any) -> Any:
        tape = original_builder(*args, **kwargs)
        state["signal_tape"] = tape
        return tape

    def simulate_entry_exit(*args: Any, **kwargs: Any) -> Any:
        tape = state.get("signal_tape")
        if tape is None:
            raise RuntimeError("entry-phase daily signal tape was not built before simulate_exit")
        kwargs["entry_phase_exit"] = True
        kwargs["entry_phase_signal_tape"] = tape
        kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
        return original_simulate_exit(*args, **kwargs)
```

### Entry-phase 청산 배선 전체

파일:

```text
scripts/research/stage23_rework_20260713/engine/strategies/exit_simulator.py:342-344
```

```python
entry_phase_exit: bool = False,
entry_phase_signal_tape: Any = None,
entry_phase_max_holding_days: int = ENTRY_PHASE_PROVISIONAL_MAX_HOLDING_DAYS,
```

보유 상한 선택:

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

entry phase 청산 우선순위:

```text
1. entry_provisional_atr_stop
2. entry_interval_break
3. entry_provisional_max_holding
```

ATR stop:

```python
if entry_phase_exit:
    stop_price = float(position.stop_price)
    if snap.low is not None and float(snap.low) <= stop_price:
        return _build_trade(
            ...
            exit_reason=ENTRY_PHASE_STOP_REASON,
            ...
        )
```

Strict interval-break:

```python
interval_pass = _strict_interval_pass_from_tape(entry_phase_signal_tape, i, row)
if interval_pass is False:
    next_idx = i + 1
    if next_idx < len(df):
        return _build_trade(
            ...
            exit_date=next_row.name,
            exit_reason=ENTRY_PHASE_INTERVAL_BREAK_REASON,
            holding_days=next_idx - entry_idx,
            ...
        )
```

7일 max holding:

```python
if holding_days >= holding_cap:
    return _build_trade(
        ...
        exit_reason=ENTRY_PHASE_TIMEOUT_REASON,
        holding_days=holding_days,
        ...
    )
```

### 원본 가변 보유일

파일:

```text
engine/strategies/exit_simulator.py:338
```

```python
for i in range(
    entry_idx + 1,
    min(entry_idx + int(rb.max_holding_days) + 1, len(df)),
):
```

원본 Rulebook 기본값:

```text
engine/strategies/rulebook.py:73
```

```python
max_holding_days: int = 20
```

원본 GA 범위:

```text
engine/strategies/rulebook.py:190
```

```python
"max_holding_days": (5, 30)
```

### 관련 변경 커밋

Rework 시작점 원본 복사:

```text
47097c0fdd271c185282d92e6fc0119651968327
20260712~13 재설계·분석·백업 산출물을 전부 제거하고 정식 Stage2·Stage3 원본 166개를 stage23_rework_20260713에 SHA 일치 복사
```

7일 cap·entry 전용 exit 도입:

```text
2af14aa7bc32f164072c83f7e84b8b7371e3fe16
Stage23 복사본 exit simulator에 entry 전용 ATR손절·strict interval break 다음날 시가·7일 cap 경로를 추가하고 기존 14-field 청산은 기본값으로 보존
```

Stage3 entry phase 실제 배선:

```text
f6641fa93e2236ab7653911599b468c6959bc532
Stage23 Stage3 wrapper에 fold 실측 domain·entry-scope GA·daily tape 기반 provisional 청산을 배선하고 exit·validate 원본 경로를 보존
```

---

## STEP 1 — Read-only support 재측정

### 입력 무결성

가격 source:

```text
path: data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
SHA-256: 6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717
```

동일 OHLCV에서 다음 순서로 feature를 재구성했다.

1. MA5·MA20·MA60
2. MACD histogram
3. RSI
4. Bollinger position
5. Volume ratio
6. 전체 시계열 `shift(5)`
7. fold 절단
8. hard domain + q01/q99 + candidate low/high strict-AND

Joint pass day 재현:

| fold | v3 기록 | 재계산 | 날짜 exact match |
|---|---:|---:|---|
| train_1 | 25 | 25 | PASS |
| train_2 | 29 | 29 | PASS |
| train_3 | 32 | 32 | PASS |

### Fold-best 개체 보유일

세 fold-best의 rulebook 값은 모두 동일했다.

```text
rb.max_holding_days = 20
```

실제 v3 거래 로그:

| fold | 거래 수 | 최대 실제 보유일 | exit reason |
|---|---:|---:|---|
| train_1 | 12 | 3 | entry_interval_break 12건 |
| train_2 | 11 | 5 | entry_interval_break 11건 |
| train_3 | 12 | 5 | entry_interval_break 12건 |

`entry_provisional_atr_stop`과 `entry_provisional_max_holding`은 0건이었다.

### 반사실 계산의 정합성

이번 비교는 청산 로직 중 cap 값만 바꾸고 다음은 유지한다.

- strict interval-break
- provisional ATR stop
- D+1 Open entry
- cooldown 1거래일
- 동일 fold-best interval
- 동일 joint-pass 날짜

모든 실제 청산이 5일 이내 interval-break였으므로, 테스트 cap 7·10·15·20은 모두 청산보다 뒤에 있다. 따라서 cap-only 반사실에서 각 거래의 exit date는 변경되지 않는다.

첫 거래의 exit가 같으면 cooldown 종료와 다음 진입 후보도 같고, 이 관계가 fold 마지막 거래까지 반복된다. 따라서 전체 순차 체결 결과가 동일하다는 결론은 기존 로그로 정확히 확정할 수 있으며 전체 백테스트 재실행이 필요하지 않다.

### 시나리오별 결과

Effective event count는 각 시나리오에서 cluster gap을 `holding cap + cooldown 1일`로 두고 계산했다.

#### train_1

| 보유 cap | joint pass | 비중복 체결 | held 흡수 | cooldown 흡수 | 기타 미체결 | cluster 수 | effective event count |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 현행 | 25 | **12** | 8 | 4 | 1 | 5 | 4.084967 |
| 10 | 25 | **12** | 8 | 4 | 1 | 4 | 3.787879 |
| 15 | 25 | **12** | 8 | 4 | 1 | 4 | 3.787879 |
| 20 | 25 | **12** | 8 | 4 | 1 | 4 | 3.787879 |
| 개체값 20 | 25 | **12** | 8 | 4 | 1 | 4 | 3.787879 |

train_1의 기타 미체결 1일은 기존 probe에서 확인한 `2023-01-06` zero-position day다.

#### train_2

| 보유 cap | joint pass | 비중복 체결 | held 흡수 | cooldown 흡수 | 기타 미체결 | cluster 수 | effective event count |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 현행 | 29 | **11** | 14 | 4 | 0 | 5 | 4.062802 |
| 10 | 29 | **11** | 14 | 4 | 0 | 5 | 4.062802 |
| 15 | 29 | **11** | 14 | 4 | 0 | 3 | 1.808602 |
| 20 | 29 | **11** | 14 | 4 | 0 | 3 | 1.808602 |
| 개체값 20 | 29 | **11** | 14 | 4 | 0 | 3 | 1.808602 |

train_2는 일반화 병목 fold이지만 cap을 7에서 20으로 늘려도 다음 값이 모두 동일하다.

```text
비중복 체결 = 11
held 흡수 = 14
cooldown 흡수 = 4
```

따라서 train_2의 11건은 7일 timeout 때문이 아니다. 5일 이내 strict interval-break와 신호 군집으로 결정된다.

#### train_3

| 보유 cap | joint pass | 비중복 체결 | held 흡수 | cooldown 흡수 | 기타 미체결 | cluster 수 | effective event count |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 현행 | 32 | **12** | 14 | 6 | 0 | 5 | 3.792593 |
| 10 | 32 | **12** | 14 | 6 | 0 | 4 | 2.797814 |
| 15 | 32 | **12** | 14 | 6 | 0 | 4 | 2.797814 |
| 20 | 32 | **12** | 14 | 6 | 0 | 3 | 2.245614 |
| 개체값 20 | 32 | **12** | 14 | 6 | 0 | 3 | 2.245614 |

### 전체 요약

| 보유 cap | train_1 | train_2 | train_3 |
|---:|---:|---:|---:|
| 7 | 12 | 11 | 12 |
| 10 | 12 | 11 | 12 |
| 15 | 12 | 11 | 12 |
| 20 | 12 | 11 | 12 |
| 개체값 20 | 12 | 11 | 12 |

### 7일이 체결 수를 최대화·최소화했는가?

아니다.

- 20일에서 7일로 cap을 줄여도 체결 수는 늘지 않았다.
- 7일에서 20일로 늘려도 체결 수는 줄지 않았다.
- 테스트된 모든 cap에서 binding trade count는 0이었다.

따라서 7일은 현재 fold-best에서 체결 수를 최대화하거나 최소화한 값이 아니라 **비활성 상한**이었다.

### Effective event count 변화 해석

Cap을 늘리면 cluster gap 기준도 `cap+1`로 커지므로 서로 떨어진 pass-day 군집이 기술적으로 합쳐진다. 이 때문에 effective event count는 일부 fold에서 감소한다.

그러나 이는 cluster 정의가 바뀐 결과이며 실제 exit·체결·흡수 수 변화가 아니다.

```text
execution support = 불변
설명용 cluster EEC = cap-dependent
```

---

## STEP 2 — 판정

판정 코드:

```text
CAP_IRRELEVANT
```

근거:

1. cap 7·10·15·20·개체값 20에서 비중복 체결이 모두 12 / 11 / 12다.
2. held·cooldown 흡수 수도 모든 시나리오에서 같다.
3. 실제 최대 보유일은 3 / 5 / 5로 7일보다 짧다.
4. 모든 거래가 `entry_interval_break`로 끝났다.
5. cap timeout과 provisional ATR stop은 한 번도 발생하지 않았다.

따라서 7일 고정 cap은 코드상 rework 변경이 맞지만, 현재 v3 fold-best support 수를 만든 직접 원인은 아니다.

기존 support 진단은 유지된다.

```text
현재 12 / 11 / 12 support 상한의 직접 원인
= strict interval-break + 시간적으로 군집된 strict-AND 신호 + cooldown
```

단, 이것은 **현재 v3 fold-best 3개 개체에 대한 결론**이다. 새로운 GA가 다른 interval을 찾거나 실제 보유가 7일에 도달하는 후보를 만들 경우 cap의 영향은 달라질 수 있다.

---

## STEP 3 — 코드 원복 diff 초안

이번 지시에서는 실제 코드를 수정하지 않았다.

### 옵션 A — cap만 원본 가변값으로 복원

**권장 옵션.** 다음 실험에서 한 변수만 바꾸려면 이 방식이 가장 정확하다.

유지:

- `entry_phase_exit=True`
- daily signal tape
- strict interval-break
- provisional ATR stop
- cooldown 1일

제거·변경:

- wrapper의 고정 `ENTRY_PHASE_MAX_HOLDING_DAYS = 7`
- 고정값 주입
- simulator의 7일 기본 fallback

Diff 초안:

```diff
--- a/scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py
+++ b/scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py
@@
-ENTRY_PHASE_MAX_HOLDING_DAYS = 7
@@
         kwargs["entry_phase_exit"] = True
         kwargs["entry_phase_signal_tape"] = tape
-        kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
         return original_simulate_exit(*args, **kwargs)
```

```diff
--- a/scripts/research/stage23_rework_20260713/engine/strategies/exit_simulator.py
+++ b/scripts/research/stage23_rework_20260713/engine/strategies/exit_simulator.py
@@
-ENTRY_PHASE_PROVISIONAL_MAX_HOLDING_DAYS = 7
@@
-    entry_phase_max_holding_days: int = ENTRY_PHASE_PROVISIONAL_MAX_HOLDING_DAYS,
+    entry_phase_max_holding_days: Optional[int] = None,
@@
-    holding_cap = (
-        max(1, int(entry_phase_max_holding_days))
-        if entry_phase_exit
-        else int(rb.max_holding_days)
-    )
+    holding_cap = int(rb.max_holding_days)
+    if entry_phase_exit and entry_phase_max_holding_days is not None:
+        holding_cap = max(1, int(entry_phase_max_holding_days))
```

효과:

- wrapper가 override하지 않으면 entry phase도 `rb.max_holding_days` 사용
- 테스트용 override 기능은 유지 가능
- strict interval-break·ATR stop은 그대로여서 cap 단독 효과 측정 가능

현재 v3 후보에서는 이 변경만 적용해도 support가 바뀌지 않을 것으로 예상된다. 이는 이번 read-only 분석에서 cap이 non-binding임을 확인했기 때문이다.

### 옵션 B — Entry phase 청산 전체를 원본 경로로 복원

제거:

- `entry_phase_exit=True`
- `entry_phase_signal_tape` 주입
- `entry_phase_max_holding_days` 주입
- strict interval-break
- provisional ATR stop
- provisional max holding reason

유지:

- 원본 `evaluate_exit(...)`
- `rb.max_holding_days`
- 원본 14-field exit policy
- cooldown 1일

Wrapper diff 방향:

```diff
     def simulate_entry_exit(*args: Any, **kwargs: Any) -> Any:
-        tape = state.get("signal_tape")
-        if tape is None:
-            raise RuntimeError(...)
-        kwargs["entry_phase_exit"] = True
-        kwargs["entry_phase_signal_tape"] = tape
-        kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
         return original_simulate_exit(*args, **kwargs)
```

이 옵션이 원본 청산 구조에는 가장 가깝지만 다음 세 요소가 동시에 바뀐다.

1. 보유 cap
2. interval-break
3. provisional ATR stop

따라서 7일 cap 하나의 효과를 검증하는 다음 단계에는 부적합하다. 실제 원본 behavior 복원 목적일 때 별도 실험으로 사용해야 한다.

### 옵션 C — 가변 cap + strict interval-break 유지 + provisional ATR stop 제거

목적:

- entry interval의 지속성은 exit에 계속 반영
- 보유 상한은 rulebook 값으로 복원
- entry phase 전용 ATR stop만 제거

이 옵션은 옵션 A보다 원본에 가깝지만 ATR stop까지 바뀌므로 단일 변수 실험은 아니다.

### 제안 우선순위

```text
1순위: 옵션 A
2순위: 옵션 B를 별도 원본 복원 실험으로 수행
3순위: 옵션 C는 ATR stop 영향 분리가 필요할 때만 수행
```

이번 결과상 cap 자체는 현재 후보 support에 영향을 주지 않았다. 따라서 실제 support 원인을 더 분명히 가르려면 다음 실험은 cap이 아니라 **strict interval-break 제거/유지 비교**가 더 정보 가치가 높다.

---

## 보호·무결성

분석 시작 보호 SHA:

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

분석 시작 Git 상태:

```text
branch: feat/intraday-reversal-ga
working tree: clean
```
