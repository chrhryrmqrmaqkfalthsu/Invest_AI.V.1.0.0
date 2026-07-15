# C1-S 명시화 리팩터링 정적 검증 결과

> **최종 상태: `ABORTED_BITWISE_MISMATCH` — 코드 변경 미적용, 두 대상 파일 원상복구 완료**

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 대상 v4 run: `data/_system/analysis/stage3_aap_overlap_entry_v4_20260715/AAP`
- 수행: C1-S 임시 적용, py_compile, 단위 검증, 세 fold-best 전후 bitwise 비교, 실패 원인 분리, 코드 원상복구
- 미수행: GA, 재학습, 파라미터 탐색, 원천 데이터 수정
- 최종 코드 diff: **없음**

## 경로 정정

지시서의 `engine/learning/evaluator.py`는 프로젝트에 존재하지 않는다. 요청한 진입 분기(line 453~502)는 실제로 다음 파일에 있다.

```text
scripts/research/stage23_rework_20260713/engine/strategies/evaluator.py
```

사전 측정의 diff 초안도 이 실제 경로를 대상으로 했으므로, 임시 검증은 `engine/strategies/evaluator.py`와 `engine/learning/entry_fitness_threadsafe.py` 두 파일에만 수행했다. 불변 게이트 실패 후 둘 다 원 SHA로 복구했다.

---

# STEP 0 — 현행 배선 인용

## 0.1 Strict 진입 신호

`engine/strategies/evaluator.py:496~502`

```python
quality_score = raw_score * market_adjustment

if strict_entry:
    should_buy = interval_result.passed
    reasons = interval_result.reasons + reasons
else:
    should_buy = quality_score >= rb.signal_threshold
```

Strict schema에서는 quality와 무관하게 5개 interval pass가 `should_buy`가 된다.

## 0.2 Signal-scaled quality=0 → amount=0

`engine/strategies/evaluator.py:553~571`

```python
strategy = rb.position_sizing_strategy

elif strategy == "signal_scaled":
    ratio_signal = min(signal_score / max(rb.signal_threshold, 0.1), 2.0)
    ratio = rb.base_position_ratio * min(ratio_signal * rb.signal_multiplier, 1.0)

return position_limit_krw * max(min(ratio, 1.0), 0.0)
```

`signal_score=quality_score=0.0`이면 `ratio_signal=0.0`, `ratio=0.0`, 최종 `amount_krw=0.0`이다.

## 0.3 Zero-size 주문 미생성

`engine/learning/entry_fitness_threadsafe.py:112~117`

```python
amount_krw = calc_position_size_krw(rb, signal.score, position_limit_krw)
entry_price = float(plan["entry_price"])
shares = int(amount_krw / entry_price) if entry_price > 0 else 0
if shares <= 0:
    index += 1
    continue
```

현행 암묵적 veto는 다음 순서다.

```text
strict interval pass
→ should_buy=True
→ signal_scaled quality=0
→ amount=0
→ shares=0
→ shares<=0 guard
→ 주문 미생성
```

## 0.4 상단 이동이 단순 동치가 아닌 이유

신규 주문 생성만 보면 상단 C1-S와 기존 zero-size guard는 동치다. 그러나 `SignalResult.should_buy`는 신규 진입뿐 아니라 보유 중 strict interval-break에도 재사용된다.

`engine/learning/execution_mode_backtest.py:84~89`

```python
"should_buy": bool(getattr(sig, "should_buy", False)),
"strict_entry": bool(getattr(sig, "strict_entry", False)),
"strict_interval_pass": (
    bool(getattr(sig, "should_buy", False))
    if bool(getattr(sig, "strict_entry", False))
    else None
),
```

`engine/strategies/exit_simulator.py:232~242`

```python
value = point.get("strict_interval_pass")
if isinstance(value, bool):
    return value
should_buy = point.get("should_buy")
return should_buy if isinstance(should_buy, bool) else None
```

따라서 evaluator 상단에서 C1-S를 `should_buy`에 합치면 quality=0 strict interval pass day가 보유 중에는 interval fail로 바뀐다. 신규 주문 결과는 같아도 청산 결과는 같지 않을 수 있다.

---

# STEP 1 — C1-S 임시 적용과 즉시 철회

## 1.1 임시 적용 조건

```text
strict interval pass
AND (
    position_sizing_strategy != "signal_scaled"
    OR quality_score > 0
)
```

안전 기본안대로 기존 `shares<=0` guard는 유지했다. 추가로 thread-safe 경로에도 같은 조건의 방어적 skip을 넣었다.

## 1.2 임시 diff

```diff
--- a/engine/strategies/evaluator.py
+++ b/engine/strategies/evaluator.py
@@
     if strict_entry:
-        should_buy = interval_result.passed
+        sizing_strategy = str(
+            getattr(rb, "position_sizing_strategy", "fixed") or "fixed"
+        ).strip().lower()
+        quality_alignment_pass = (
+            sizing_strategy != "signal_scaled"
+            or quality_score > 0.0
+        )
+        should_buy = interval_result.passed and quality_alignment_pass
         reasons = interval_result.reasons + reasons
+        if interval_result.passed and not quality_alignment_pass:
+            reasons.append(
+                "strict_entry: signal_scaled quality<=0, non-executable"
+            )
```

```diff
--- a/engine/learning/entry_fitness_threadsafe.py
+++ b/engine/learning/entry_fitness_threadsafe.py
@@
         if not signal.should_buy:
             index += 1
             continue
+
+        sizing_strategy = str(
+            getattr(rb, "position_sizing_strategy", "fixed") or "fixed"
+        ).strip().lower()
+        if (
+            signal.strict_entry
+            and sizing_strategy == "signal_scaled"
+            and signal.quality_score <= 0.0
+        ):
+            index += 1
+            continue
```

## 1.3 임시 diff 규모

| 파일 | 삽입 | 삭제 |
|---|---:|---:|
| `engine/strategies/evaluator.py` | 12 | 1 |
| `engine/learning/entry_fitness_threadsafe.py` | 11 | 0 |
| 합계 | 23 | 1 |

불변 검증 실패 직후 위 diff는 모두 철회했다. 최종 repository에는 남아 있지 않다.

---

# STEP 2 — 정적 검증과 불변 게이트

## 2.1 기본 검증

| 검증 | 결과 |
|---|---|
| 임시 수정본 `py_compile` | PASS |
| 복구본 `py_compile` | PASS |
| `git diff --check` | PASS |
| signal-scaled ∧ quality=0 | 임시 수정에서 `should_buy=False`, 주문 없음 |
| signal-scaled ∧ quality>0 | 임시 수정에서 진입 유지 |
| fixed ∧ quality=0 | 임시 수정에서 진입 유지 |
| legacy schema/gene_scope=legacy | canonical JSON SHA bitwise 동일 |
| genetic.py AST | SHA 불변 |

단위 조건 자체는 모두 통과했다. 실패는 보유 중 청산 의미의 간접 변화에서 발생했다.

## 2.2 Legacy·mutation helper 불변

| 대상 | 전 SHA | 임시 수정 후 SHA | 결과 |
|---|---|---|---|
| legacy signal canonical JSON | `c76601b19dd8e5914b6e0c0b527c984e5280d3140e6ad5fec0953d84132ddb69` | 동일 | PASS |
| `genetic.py` AST | `4f055cf69f1753910ef7d9a837d3213cd8337156fe8ac7b463abac018c66ff11` | 동일 | PASS |

## 2.3 v4 fold-best 전후 결과

float 값은 IEEE-754 float64 bit pattern으로 canonical JSON을 만든 뒤 SHA256을 비교했다.

| fold | 체결수 전→후 | 승률 전→후 | 평균손익 전→후 | fitness 전→후 | 체결·손익 SHA | 판정 |
|---|---:|---:|---:|---:|---|---|
| train_1 | 20→20 | 95.0%→95.0% | 4.4773562110837055→동일 | 1.1724113242558079→동일 | 동일 | PASS |
| train_2 | 15→15 | 86.66666666666667%→동일 | 6.754039899323874→동일 | 1.6321081616005721→동일 | 동일 | PASS |
| train_3 | 13→13 | 100.0%→100.0% | **4.989818767186908→4.7833654889215484** | **1.5883405825352652→1.6369738911310319** | **불일치** | **FAIL** |

### Canonical SHA

| fold | 항목 | 전 SHA | 임시 수정 후 SHA | 일치 |
|---|---|---|---|---|
| train_1 | trade list | `45c51ecd4ea403e87fdffa944f4c7d7adc4efa970fb74d9d32aa7c5f0396042d` | 동일 | 예 |
| train_1 | metrics+fitness | `bdb30d25dbf0b318e649bc6442436642f3b8a38015de5761c7de46df49f04001` | 동일 | 예 |
| train_2 | trade list | `996e331e11296869818ebba945e1ac8132d8c1fe9c37a31d59000786a6b0b80c` | 동일 | 예 |
| train_2 | metrics+fitness | `351d899e58f1056ec6d19b2c3619b1f85aaf8b3b8f3eb8063af87fdc19928e28` | 동일 | 예 |
| train_3 | trade list | `3438fdb7f21d6c82c74ccff8a210439ec910cbbb2f43ddd088c1fe46fff652bb` | `4d30b90d6d4aa64d6cc51dfed571642eb9e68ad1ed0d54e3f221d1cd3872a995` | 아니오 |
| train_3 | metrics+fitness | `c1478bb6bd81981ae46ff495310e78ff830b7c8e0d963dceeea3c55b825154e3` | `39699fc206dce8244e0637c95ec2173ec8d743b21d4f6cacae988976005392cb` | 아니오 |

## 2.4 Fold별 필수 케이스

| 케이스 | 기대 | 임시 수정 결과 |
|---|---|---|
| train_1 | 변화 없음 | 체결·손익·fitness bitwise 동일 |
| train_2 fixed quality=0 5건 | 모두 보존 | 5건 보존, 전체 15건 bitwise 동일 |
| train_3 quality=0 17일 | 신규 주문 제외, 13건 유지 | 17일 explicit veto, 13건 유지 |
| train_3 기존 13건 손익·fitness | bitwise 동일 | **불일치** |

## 2.5 직접 변경된 거래

13건 중 1건의 청산 경로가 바뀌었다.

| 항목 | 현행 | 임시 C1-S |
|---|---|---|
| 신호일 | 2024-11-04 | 동일 |
| 진입일 | 2024-11-05 | 동일 |
| 청산일 | 2024-11-12 | **2024-11-07** |
| 청산가 | 40.0 | **39.0** |
| 보유일 | 5 | **2** |
| 청산 사유 | entry_interval_break | 동일 label |
| 실현손익률 | 7.330704697986577% | **4.646812080536913%** |

2024-11-06은 strict interval 자체는 pass지만 quality=0인 날이다. 현행 tape에서는 `strict_interval_pass=True`; 임시 C1-S에서는 `should_buy=False`가 그대로 `strict_interval_pass=False`로 직렬화되어 기존 2024-11-05 포지션을 조기 청산시켰다.

## 2.6 Thread-safe 단일포지션 결과

`entry_fitness_threadsafe`의 fold별 metrics+fitness SHA는 세 fold 모두 동일했다.

| fold | 전후 거래수 | metrics+fitness SHA | 결과 |
|---|---:|---|---|
| train_1 | 5→5 | `1ff0786efc61b0e25ee386f62a4ddfa2f8b3c4846fa41ac24a4a222cd382a053` | 동일 |
| train_2 | 7→7 | `02fa3c91bb09d8339e2115c4901e60b7d5b15db798dc1f919f6060ffcb1ee71c` | 동일 |
| train_3 | 7→7 | `55e744b1a7d0e604627c9314ac60fe48b6e5709ad090f0352d7a7ab1214cafcc` | 동일 |

그러나 v4 독립 동시진입 13건 재현에서 train_3가 실패했으므로 전체 불변 게이트는 FAIL이다.

## 2.7 결론

```text
ABORTED_BITWISE_MISMATCH
```

체결수만 같고 손익·fitness가 달라졌다. 지시서의 핵심 원칙에 따라 리팩터링으로 인정할 수 없으며 코드 변경을 적용하지 않았다.

---

# STEP 3 — SHA·diff·복구 기록

## 3.1 코드 SHA

| 파일 | 수정 전 SHA | 임시 수정 SHA | 최종 복구 SHA | 최종 상태 |
|---|---|---|---|---|
| `engine/strategies/evaluator.py` | `435b87aa999884527062963ca00a5fece63acd47c92916966442a22830965d01` | `67523f3fcbefd7e639c54cdcdb01e9034662bfcea9fe9a2e59aa7484c62205e5` | `435b87aa999884527062963ca00a5fece63acd47c92916966442a22830965d01` | 원복 |
| `engine/learning/entry_fitness_threadsafe.py` | `6ec29acfeac41a37732927630b05f59eab251a76480b5f18e8c8c07d796455f0` | `ed487026163ab9cef55f03dd85b115f03430ffead79e9f398899d3cdd12f92e2` | `6ec29acfeac41a37732927630b05f59eab251a76480b5f18e8c8c07d796455f0` | 원복 |

최종 두 코드 파일은 수정 전과 byte-for-byte 동일하다.

## 3.2 안전한 후속 구조 제안 — 적용하지 않음

C1-S를 bitwise 보존하려면 `strict_interval_pass`와 `entry_order_eligible`를 분리해야 한다.

```text
strict_interval_pass = interval_result.passed
entry_order_eligible = strict_interval_pass AND C1-S
```

- 보유 중 interval-break: `strict_interval_pass` 사용
- 신규 주문 생성: `entry_order_eligible` 사용
- `should_buy`를 어느 의미로 유지할지는 전체 호출처 감사 후 결정

이 구조는 `execution_mode_backtest.py`와 signal tape schema 변경이 필요하므로, 이번 지시서의 “두 코드 파일 외 수정 금지” 범위에서는 안전하게 구현할 수 없다. 따라서 제안만 기록하고 적용하지 않았다.

---

# 보호파일·daemon·Git 감사

## 보호파일 시작·종료 SHA

| 파일 | 시작 SHA256 | 종료 SHA256 | 상태 |
|---|---|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` | 동일 | 불변 |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` | 동일 | 불변 |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` | 동일 | 불변 |

## Daemon

```text
PID: 494330
state: Sl
start: Sat Jul 11 20:16:00 2026
starttime_ticks: 36014393
command: live_candidate_slots.py daemon --interval 60
상태: 유지
```

## Git 기록

```text
작업 시작 HEAD:
e5b1f8d2da7e3845e1f5d72e85c1c3e2f3f6219b

수정 전 백업 commit:
0a61e24

백업 메시지:
C1-S 명시화 리팩터링 전 기준점 백업: evaluator 진입 분기·thread-safe zero-size guard·v4 재현 SHA 상태를 고정

코드 수정 commit:
없음 — bitwise mismatch로 임시 변경 철회

분석 산출물 commit:
a869fa61c3261428b5bb0f5bf30f73ab9d9dc98f

산출물 commit 메시지:
C1-S 명시화 리팩터링 중단 보고: train_3 청산·손익·fitness bitwise 불일치를 확인해 코드 원복과 실패 원인을 기록
```

최종 작업 트리는 분석 제출물 외 코드 diff가 없어야 한다. 위 산출물 commit SHA를 반영한 메타데이터 commit은 최종 제출 메시지에 기록한다.

## 산출물 SHA

최종 `readout.md` SHA256은 같은 폴더의 `SHA256SUMS.txt`를 정본으로 한다.
