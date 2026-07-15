# AAP quality score 0 → 주문 0 sizing 배선 진단

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 대상: v4 `train_3` fold-best + 동일 AAP OHLCV
- GA·백테스트·재학습: 실행하지 않음
- 분석 시작 HEAD: `4376165378155ee6febc9eebb5c27363c50a9b2c`
- 분석 전 백업 commit: `c727adbae3b094e8fc216f8874b06b88359987d0`

## 최종 판정

**SIZING_IS_REWORK_BUG**

원본 legacy에서는 `score=0`이 positive `signal_threshold`를 통과하지 못해 sizing 함수까지 도달하지 않았다. Rework commit `90ed1111`이 strict interval 통과를 진입 조건으로, legacy quality score를 사이징·정렬·진단용으로 분리했지만, 원본 `signal_scaled` 수식을 floor 없이 그대로 재사용했다. 그 결과 quality=0이 주문 크기 0이 되고, 다시 `shares<=0` guard에서 진입을 차단한다.

이는 commit이 명시한 “strict-AND로만 진입”과 모순된다. Quality가 단순 사이즈 조절 변수가 아니라 숨은 진입 veto로 작동하기 때문이다.

---

## STEP 0 — 배선 위치

### Rework signal 계산

`engine/strategies/evaluator.py:453-502`

```python
raw_score = sum(components.values())
...
quality_score = raw_score * market_adjustment

if strict_entry:
    should_buy = interval_result.passed
else:
    should_buy = quality_score >= rb.signal_threshold
```

`engine/strategies/evaluator.py:507-519`

```python
return SignalResult(
    should_buy=should_buy,
    score=quality_score,
    ...
    quality_score=quality_score,
    strict_entry=strict_entry,
)
```

Strict schema에서는 `should_buy`가 quality threshold와 분리되지만, `SignalResult.score`에는 quality score가 그대로 들어간다.

### Position sizing

`engine/strategies/evaluator.py:542-571`

```python
def calc_position_size_krw(rb, signal_score, position_limit_krw):
    strategy = rb.position_sizing_strategy

    if strategy == "fixed":
        ratio = rb.base_position_ratio

    elif strategy == "signal_scaled":
        ratio_signal = min(
            signal_score / max(rb.signal_threshold, 0.1),
            2.0,
        )
        ratio = rb.base_position_ratio * min(
            ratio_signal * rb.signal_multiplier,
            1.0,
        )

    return position_limit_krw * max(min(ratio, 1.0), 0.0)
```

`signal_score=0`이면:

```text
ratio_signal = 0 / max(threshold, 0.1) = 0
ratio = base_position_ratio × min(0 × multiplier, 1) = 0
amount = position_limit × 0 = 0
```

### 주문 미생성 체인

`engine/learning/execution_mode_backtest.py:835-859`

```python
if not sig.should_buy:
    i += 1
    continue

amt_krw = calc_position_size_krw(
    rb,
    sig.score,
    position_limit_krw,
)
entry_price = float(plan["entry_price"])
shares = int(amt_krw / entry_price) if entry_price > 0 else 0
if shares <= 0:
    i += 1
    continue
```

정확한 체인:

```text
strict intervals PASS
→ sig.should_buy=True
→ sig.score=quality_score=0
→ signal_scaled amount=0
→ shares=0
→ shares<=0 continue
→ 주문·거래 미생성
```

Thread-safe entry fitness도 동일하다.

`engine/learning/entry_fitness_threadsafe.py:93-117`

```python
if not signal.should_buy:
    index += 1
    continue
...
amount_krw = calc_position_size_krw(
    rb,
    signal.score,
    position_limit_krw,
)
shares = int(amount_krw / entry_price) if entry_price > 0 else 0
if shares <= 0:
    index += 1
    continue
```

따라서 이 배선은 최종 fold-best 재생뿐 아니라 entry GA fitness 평가에도 동일하게 영향을 준다.

---

## STEP 1 — 원본 대비 및 의도 판별

### 원본 legacy signal path

원본 `engine/strategies/evaluator.py:223-225`

```python
final_score = raw_score * market_adjustment
should_buy = final_score >= rb.signal_threshold
```

원본 `signal_threshold`:

```text
기본값: 2.0
GA 범위: 1.5 ~ 4.0
```

근거: `engine/strategies/rulebook.py:60,181`.

따라서 원본에서는 quality/final score 0이 언제나 `should_buy=False`다.

원본 `engine/learning/backtest.py:611-620`

```python
if not sig.should_buy:
    i += 1
    continue

amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
entry_price = float(df.iloc[i]["Close"])
shares = int(amt_krw / entry_price) if entry_price > 0 else 0
if shares <= 0:
    i += 1
    continue
```

원본 `engine/learning/execution_mode_backtest.py:265-289`도 같은 순서다.

### 원본 vs rework

| 항목 | 원본 legacy | Rework strict entry |
|---|---|---|
| 진입 조건 | `score >= threshold` | 5개 interval strict-AND |
| quality=0에서 `should_buy` | False | Interval PASS이면 True |
| quality=0이 sizing에 도달 | 불가능 | 가능 |
| signal-scaled amount | sizing 미호출 | 0 |
| 실제 주문 | 없음 | 없음 |
| 주문이 없는 이유 | 명시적 signal gate | 숨은 sizing veto |

원본도 결과적으로 quality=0이면 거래하지 않았지만, **strict pass 후 sizing이 0이라서 거래하지 않은 구조는 아니었다**. 원본은 score threshold 자체가 진입 조건이었다.

### Git 근거

| Commit | 시점 | 내용 | 판독 |
|---|---|---|---|
| `59b8a47b4023106070f3afb8d12ae8128b435004` | 2026-05-25 | 원본 evaluator·learning 도입 | `signal_scaled` 수식과 threshold-first chain 최초 도입 |
| `47097c0fdd271c185282d92e6fc0119651968327` | 2026-07-13 00:24 UTC | 정식 원본을 rework에 SHA 일치 복사 | sizing·entry loop 원본 유지 |
| `90ed1111bef5f244f2317414a83d9c63fbfd4b36` | 2026-07-13 06:53 UTC | schema v2 strict-AND 도입, quality score를 사이징용으로 분리 | 새로 strict pass와 score threshold를 분리했지만 sizing floor 추가 없음 |
| `b6347f49ad472761a34fd69cb5f00a7c2a1dbac3` | 2026-07-14 02:03 UTC | thread-safe entry fitness 추가 | 동일 zero-size skip을 entry GA 경로에 복제 |

`90ed1111` commit 메시지:

```text
Stage23 복사본 evaluator를 schema v2 strict-AND와 raw OOD fail-closed로 전환하고 뉴스·시장 합산점수를 사이징용 quality score로 분리
```

동일 commit의 module 설명:

```text
strict entry schema v2+: 5개 연속 feature interval의 strict-AND로만 진입
뉴스·시장·이벤트 합산 점수는 quality score로 보존하며 사이징·정렬·진단에만 사용
```

또한 `calc_position_size_krw` docstring은 다음을 명시한다.

```text
Strict interval 통과 여부는 포지션 크기 계산 전에
evaluate_signal().should_buy로 이미 결정된다.
```

그러나 score 0이 amount 0을 만들면 quality가 최종적으로 진입 여부를 다시 결정한다. 이는 “사이징에만 사용”과 “strict-AND로만 진입” 두 설명 모두와 충돌한다.

### 판정

- 원본 `signal_scaled` 자체는 의도된 기존 기능이다.
- 원본의 전제는 sizing에 들어오는 score가 이미 threshold 이상이라는 것이었다.
- Rework가 이 전제를 깨고 score 0을 새로 도달 가능하게 만들었다.
- 해당 변화 시 floor·base fallback·명시적 quality veto 중 어느 것도 추가하지 않았다.
- score 0 strict-pass 사례를 검증하는 테스트도 확인되지 않았다.

따라서 sizing 함수 단독 버그가 아니라 **strict entry와 기존 sizing 사이의 rework 배선 버그**로 판정한다.

Legacy 경로는 `strict_entry=False`에서 계속 `quality_score >= threshold`를 사용하므로, 수정 시 strict 또는 entry scope에만 분기하면 bitwise 불변 유지가 가능하다.

---

## STEP 2 — train_3 미체결 17일 정량 확인

### Fold-best sizing 설정

```text
position_sizing_strategy = signal_scaled
base_position_ratio      = 0.6190268334113553
signal_multiplier        = 1.9228820965542657
signal_threshold         = 2.0
weight_ma_align          = 1.0
weight_macd_golden       = 1.0
weight_rsi_zone          = 1.0
weight_bb_near_lower     = 1.0
weight_volume_surge      = 1.0
rsi zone                 = 31.4891923018 ~ 41.6630947238
bb_proximity             = 1.05
volume_surge_ratio       = 1.5
use_news_global          = False
use_market_entry_adjustment = False
```

### 날짜별 5개 legacy quality component

모든 값은 부동소수점상 정확히 `0.0`이었다. 0에 가까운 양수가 아니다.

| 날짜 | MA | MACD | RSI | BB | Volume | raw | quality | amount | shares |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024-07-15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-07-16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-07-26 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-07-29 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-07-30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-07-31 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-08-19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-09-25 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-11-06 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-11-07 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2024-11-08 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025-03-26 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025-05-12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025-05-13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025-05-14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025-05-15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2025-05-16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### 각 component가 0인 원인

| Component | 조건 | 17일 결과 |
|---|---|---|
| `ma_align` | 현재일 `Aligned_bull` | 17/17 False |
| `macd` | 현재일 `MACD_golden` | 17/17 False |
| `rsi` | 현재일 RSI가 31.4892~41.6631 | 17/17 False |
| `bb` | 현재일 BB lower 근접 | 17/17 False |
| `volume` | 현재일 Volume_ratio ≥ 1.5 | 17/17 False |
| `news` | global news weight | `use_news_global=False`, 전부 0 |
| `news_topics` | topic contribution | 전부 0 |
| `events` | event contribution | 전부 0 |
| market adjustment | quality 배수 | `use_market_entry_adjustment=False`, 전부 1.0 |

RSI는 2024-09-25의 `29.4939`만 zone 아래였고 나머지 16일은 모두 zone 위였다. Volume ratio 범위는 `0.7290~1.3922`로 모두 1.5 미만이었다.

정확한 분해:

```text
30 strict joint-pass days
= 13 executed trades
+ 17 exact-zero quality/position days
+ 0 held/cooldown absorbed
+ 0 data-missing/other
```

### D-5·가격 정합성

OHLCV SHA:

```text
6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717
```

D-5 전체 feature series를 vectorized `shift(5)`와 날짜별 직접 추출로 비교했다.

```text
vectorized SHA:
0331aa572acbab3ebcf28bda625b3e643ec5a20a48249c1e2272609433b53629

direct extraction SHA:
0331aa572acbab3ebcf28bda625b3e643ec5a20a48249c1e2272609433b53629
```

48개 v4 trade snapshot과 exact match, mismatch 0, 최대 절대 오차 0.0.

---

## STEP 3 — 판정 코드

```text
SIZING_IS_REWORK_BUG
```

근거 요약:

1. 원본 legacy에서 score 0은 positive threshold 때문에 sizing에 도달하지 않는다.
2. `signal_scaled`는 threshold 이상 score를 전제로 만들어졌다.
3. `90ed1111`이 strict pass와 quality threshold를 분리하면서 score 0을 새로 도달 가능하게 했다.
4. Sizing 본문은 원본 수식을 그대로 유지했고 zero floor가 없다.
5. 따라서 quality가 “사이즈 조절”을 넘어 숨은 진입 veto가 됐다.
6. train_3에서 이 경로가 실제 17일, strict pass의 56.67%를 제거했다.

명시적으로 확인되지 않은 항목:

- Author가 quality 0 strict-pass를 의도적으로 거래 제외하려 했다는 테스트·문서·commit 설명: **미확인**.
- 반대로 모든 strict pass에 반드시 최소 주문을 내야 한다는 별도 테스트: **미확인**.

다만 commit의 “strict-AND로만 진입”과 “quality는 사이징·정렬·진단에만 사용”이라는 명시적 문구가 현재 zero-size veto와 충돌하므로, 전체 배선 판정은 ambiguous가 아니라 bug다.

---

## STEP 4 — 수정 옵션 diff 초안

실제 코드는 수정하지 않았다.

### 옵션 1 — Strict entry에 최소 sizing floor

```diff
--- a/engine/learning/execution_mode_backtest.py
+++ b/engine/learning/execution_mode_backtest.py
@@
 amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
+if (
+    sig.strict_entry
+    and rb.position_sizing_strategy == "signal_scaled"
+    and sig.should_buy
+    and amt_krw <= 0.0
+):
+    floor_ratio = 0.10
+    amt_krw = position_limit_krw * rb.base_position_ratio * floor_ratio
```

동일 분기를 `entry_fitness_threadsafe.py`에도 적용해야 한다.

| 영향 | 내용 |
|---|---|
| 장점 | Quality 기반 비례 sizing 유지, exact-zero만 구조적으로 복구 |
| 단점 | floor 10%가 새 hyperparameter가 됨 |
| train_3 | 17일 모두 최소 명목으로 진입 가능 |
| 다른 fold | zero-quality strict pass만 추가 거래 |
| legacy | `sig.strict_entry` 조건으로 bitwise 불변 가능 |
| 위험 | 작은 명목 거래가 trade count를 늘리지만 독립 fixed-notional 평가에서 PnL 기여가 작아 fitness 해석이 달라질 수 있음 |

### 옵션 2 — Strict pass는 quality와 무관하게 base size

```diff
--- a/engine/learning/execution_mode_backtest.py
+++ b/engine/learning/execution_mode_backtest.py
@@
-amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
+if sig.strict_entry:
+    ratio = max(min(rb.base_position_ratio, 1.0), 0.0)
+    amt_krw = position_limit_krw * ratio
+else:
+    amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
```

Thread-safe entry path에도 동일 적용.

| 영향 | 내용 |
|---|---|
| 장점 | “strict interval은 진입 조건” 의미를 가장 직접적으로 보존 |
| 단점 | strict entry에서 `signal_scaled` gene 효과가 사라짐 |
| train_3 | 17일뿐 아니라 모든 strict trade가 동일 base ratio 사용 |
| 다른 fold | 거래 수는 zero-quality 날짜만 늘지만 거래별 명목 분포 전체가 변함 |
| legacy | strict 분기로 완전 분리 가능 |
| 위험 | position/context quality gene의 진화 의미가 약화되고 기존 v4 fitness와 직접 비교가 어려워짐 |

### 옵션 3 — Quality는 fitness에만 반영

```diff
--- a/engine/learning/execution_mode_backtest.py
+++ b/engine/learning/execution_mode_backtest.py
@@
-if sig.strict_entry:
-    amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
+if sig.strict_entry:
+    amt_krw = position_limit_krw * clamp(rb.base_position_ratio, 0.0, 1.0)
 else:
     amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
```

```diff
--- a/engine/learning/execution_mode_backtest.py
+++ b/engine/learning/execution_mode_backtest.py
@@ _apply_entry_scope_fitness(...)
+quality_term = aggregate_trade_signal_quality(...)
+fitness += QUALITY_WEIGHT * quality_term
```

| 영향 | 내용 |
|---|---|
| 장점 | 진입·명목·quality 목적을 완전히 분리 |
| 단점 | Fitness 정의 변경이 필요해 가장 침습적 |
| train_3 | 17일 base-size 거래 복구, quality는 후보 선별에만 사용 |
| 다른 fold | Entry GA 순위와 gate 통과 분포가 크게 변할 수 있음 |
| legacy | `_entry_scope_active` 내부에만 추가하면 불변 가능 |
| 위험 | Quality proxy를 fitness에 넣으면 실제 수익·MAE와 중복 보상하거나 proxy overfit을 유발할 수 있음 |

### 권고 우선순위

수익성·정확성·안정성 기준:

1. **옵션 1**: 영향 범위가 가장 좁고 zero-veto만 제거한다.
2. 옵션 2: strict 설계 의미는 가장 명확하지만 sizing gene 의미가 크게 변한다.
3. 옵션 3: 구조적으로 가장 깨끗하지만 fitness 변경까지 포함해 단일 변수 실험이 아니다.

실제 적용 시에는 다음 검증이 필요하다.

- `gene_scope='legacy'` deterministic hash exact match
- train_3 17일의 `shares>0`
- train_1·2 fold-best 거래수·EEC·승률 변화
- floor 명목이 trade-count gate를 형식적으로만 늘리는지 여부
- mutation bias·strict interval·exit logic 불변

---

## 보호·Git 상태

시작 보호 SHA:

```text
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce  .env
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38  data/_system/market_history.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611  data/_system/market_history_v2.csv
```

Daemon 시작 상태:

```text
PID: 494330
start: Sat Jul 11 20:16:00 2026
command: live_candidate_slots.py daemon --interval 60
```

종료 보호 SHA·daemon·Git 상태와 최종 산출물 commit은 최종 검증 후 기록한다.
