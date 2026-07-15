# should_buy 진입/청산 이중 용도 정적 매핑

> **전 STEP read-only. 코드·원천 데이터 수정, GA·재학습·파라미터 탐색·백테스트 실행 없음.**

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 조사 방법: `grep -RIn`, Python AST 정적 검색, `readFile`/라인 인용, 기존 C1-S 실패 readout 대조
- 검색 범위: 작업 위치 아래 모든 `*.py`
- 핵심 판정: **`SHOULD_BUY_IS_COUPLED_TO_EXIT`**
- 결론: 다양성·quality gate를 바꾸기 전에 **청산용 strict interval 판정과 신규 주문용 진입 eligibility를 별도 값으로 분리해야 한다.**

## 핵심 요약

현재 `should_buy`는 strict schema에서 다음 세 의미를 동시에 가진다.

1. 5개 strict interval이 모두 통과했는지 나타내는 원 판정
2. 신규 주문을 만들지 결정하는 진입 gate
3. daily signal tape를 통해 보유 중 interval-break 청산을 결정하는 판정

직접 결합 라인은 `execution_mode_backtest.py:86~89`이다.

```python
"strict_interval_pass": (
    bool(getattr(sig, "should_buy", False))
    if bool(getattr(sig, "strict_entry", False))
    else None
),
```

이 때문에 strict 진입 gate에 quality 조건을 섞어 `should_buy`를 바꾸면, 신규 주문뿐 아니라 보유 중 strict interval 상태도 바뀐다. `exit_simulator.py:435~449`는 false를 발견하면 다음 거래일 시가에 청산한다.

최소 선행 분리는 다음과 같다.

```text
strict_interval_pass = interval_result.passed          # 청산용, quality 독립
entry_order_eligible = strict_interval_pass AND gate   # 신규 진입용
```

`should_buy`는 호환 alias로 한시 유지할 수 있으나, 어느 의미의 alias인지 명확히 고정해야 한다.

---

# STEP 0 — 정의 위치·상위 계산 체인

## 0.1 경로 정정

프로젝트에 `engine/learning/evaluator.py`는 없다. 실제 평가기는 다음 경로다.

```text
engine/strategies/evaluator.py
```

`entry_fitness_threadsafe.py`는 `should_buy`를 계산하거나 설정하지 않고 line 93에서 읽기만 한다.

## 0.2 should_buy의 원 정의·설정 지점

| 파일·라인 | 설정 형태 | 의미 |
|---|---|---|
| `engine/strategies/evaluator.py:39~51` | `SignalResult.should_buy: bool` | canonical signal 필드 정의 |
| `engine/strategies/evaluator.py:307~320` | `should_buy=False` | 데이터 60행 미만 fail-closed |
| `engine/strategies/evaluator.py:498~500` | `should_buy = interval_result.passed` | strict schema의 원 설정. 현재 quality와 무관 |
| `engine/strategies/evaluator.py:501~502` | `should_buy = quality_score >= rb.signal_threshold` | legacy schema의 원 설정 |
| `engine/strategies/evaluator.py:507~515` | `SignalResult(should_buy=should_buy, ...)` | 결과 객체에 저장 |

Canonical 의미를 새로 계산하는 곳은 `evaluate_signal()` 한 곳이다. 나머지는 복사·직렬화·별칭 또는 읽기다.

## 0.3 should_buy 파생·별칭 설정 지점 전수 목록

| 파일·라인 | 파생값 | 설명 |
|---|---|---|
| `engine/central/signal_collector.py:20~30` | `SignalSnapshot.should_buy` | 중앙 수집용 dataclass 필드 |
| `engine/central/signal_collector.py:247~258` | `sig.should_buy → snapshot.should_buy` | 평가 결과 복사 |
| `engine/learning/backtest.py:175~184` | JSON `should_buy` | signal 전체 snapshot 진단 저장 |
| `engine/learning/execution_mode_backtest.py:76~100` | JSON `should_buy` | daily signal tape 직렬화 |
| `engine/learning/execution_mode_backtest.py:86~89` | `strict_interval_pass = sig.should_buy` | **청산 오염을 만든 의미 결합** |
| `engine/live/elite_pullback_replay.py:351` | `should_buy=False` | replay 범위 밖 placeholder |
| `engine/live/elite_pullback_replay.py:365~378` | `res.should_buy → row.should_buy` | replay row 복사 |
| `engine/live/elite_pullback_replay.py:381` | `should_buy=False` | 평가 예외 placeholder |
| `engine/live/elite_shadow_trader.py:463~475` | `res.should_buy → evaluation dict` | shadow 평가 payload |
| `engine/live/elite_signal_history.py:146~164` | local·row `should_buy` | signal history 복사 |
| `engine/live/elite_signal_history.py:237` | `last_should_buy` | history summary 별칭 |
| `engine/live/event_policy.py:124~140` | `pass_on`, `pass_off` | event on/off 진단 별칭 |
| `engine/live/s2_auto_trader.py:414` | evaluation summary | 로깅 payload에 복사 |
| `engine/strategies/exit_simulator.py:241~242` | local `should_buy` | `strict_interval_pass`가 없을 때 fallback |
| `scripts/research/run_stage3_baseline_light.py:299~306` | local `strict_pass`, `should_buy` | signal 통계 진단 별칭 |

## 0.4 Strict interval 상위 계산 체인

### Schema 선택

`engine/strategies/evaluator.py:325~342`

```python
schema_version = int(getattr(rb, "entry_interval_schema_version", 1))
strict_entry = schema_version >= STRICT_ENTRY_INTERVAL_SCHEMA_VERSION
...
entry_features = extract_entry_features(df)
...
interval_result = evaluate_entry_intervals(rb, entry_features)
```

### D-5 feature

`extract_entry_features()`는 신호일 D 기준 D-5 거래일의 다음 다섯 feature를 만든다.

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

### Strict fail-closed 판정

`engine/strategies/evaluator.py:121~289`

각 feature는 다음 순서로 검사된다.

```text
finite
→ hard mathematical domain
→ learned q01~q99 empirical domain
→ learned low/high interval
```

어느 단계든 실패하면 즉시 `EntryIntervalResult(passed=False)`다. 다섯 feature가 모두 통과할 때만 line 284~289에서 `passed=True`가 된다.

### Strict should_buy

`engine/strategies/evaluator.py:498~500`

```python
if strict_entry:
    should_buy = interval_result.passed
```

현재 strict schema에서 `quality_score`, `signal_threshold`, sizing strategy는 `should_buy` 원 판정에 들어가지 않는다.

## 0.5 Quality·legacy 계산 체인

`engine/strategies/evaluator.py:354~496`

```text
technical legacy components
  ma_align + macd + rsi + bb + volume
+ news global
+ news topic
= raw_score                         (line 453)
+ event adjustment                 (line 472)
+ optional crash bonus             (line 478)
× market_adjustment                (lines 482~494)
= quality_score                    (line 496)
```

Legacy schema에서만 다음 비교가 신규 진입 판정이다.

`engine/strategies/evaluator.py:501~502`

```python
should_buy = quality_score >= rb.signal_threshold
```

Strict schema에서는 `threshold`가 결과에 저장되지만 진입 boolean에는 사용되지 않는다.

## 0.6 score·quality·final_score 구분

| 이름 | 현재 의미 | should_buy 상위값 여부 |
|---|---|---|
| `raw_score` | market adjustment 전 quality component 합 | legacy에서 간접 상위값 |
| `quality_score` | `raw_score × market_adjustment` | legacy should_buy·sizing 상위값, strict should_buy에는 미사용 |
| `SignalResult.score` | `quality_score`와 동일 값으로 저장 | sizing 입력 |
| `signal_threshold` | legacy boolean threshold 및 signal-scaled 분모 | strict boolean에는 미사용 |
| `final_score` | candidate/report/dashboard 계층에서 쓰이는 별도 명칭 | **이 evaluator의 should_buy 계산 체인에는 없음** |

`final_score` 검색 결과는 live 후보 정렬·dashboard 표시 등에 존재하지만 `evaluate_signal()`의 `should_buy`를 만드는 값이 아니다.

## 0.7 Signal-scaled 암묵적 veto 체인

`engine/strategies/evaluator.py:542~571`

```python
elif strategy == "signal_scaled":
    ratio_signal = min(signal_score / max(rb.signal_threshold, 0.1), 2.0)
    ratio = rb.base_position_ratio * min(ratio_signal * rb.signal_multiplier, 1.0)
```

`quality_score=signal_score=0`이면 amount는 0이다.

대표 zero-size guards:

- `engine/learning/backtest.py:615~620`
- `engine/learning/execution_mode_backtest.py:854~859`
- `engine/learning/entry_fitness_threadsafe.py:112~117`

```text
should_buy=True
→ amount=0
→ shares=0
→ shares<=0 continue
→ 신규 주문 없음
```

이 암묵적 veto는 신규 주문에만 작동한다. daily tape 안의 `should_buy=True`는 그대로 남아 청산 strict-pass로 사용된다.

---

# STEP 1 — 사용처 전수 분류

검색 기준:

```text
should_buy
strict_interval_pass
_strict_interval_pass_from_tape
entry_interval_break
```

## (A) 신규 진입 판정·진입 후보 gate

| 파일·라인 | 사용처 | 설명 |
|---|---|---|
| `engine/learning/backtest.py:601~620` | `if not sig.should_buy` | legacy/general backtest 신규 진입 gate 후 sizing |
| `engine/learning/execution_mode_backtest.py:823~859` | `if not sig.should_buy` | execution-mode 신규 진입 gate 후 sizing·zero-size guard |
| `engine/learning/entry_fitness_threadsafe.py:88~117` | `if not signal.should_buy` | thread-safe entry fitness 신규 진입 gate |
| `engine/central/signal_collector.py:263~269` | `snap.should_buy` | 중앙 controller 후보 수집 |
| `engine/live/central_control.py:623~637` | `res.should_buy` | BUY 또는 HOLD 생성 |
| `engine/live/scheduled_open_buy_queue.py:424~438` | `res.should_buy` | next-open BUY 또는 HOLD 생성 |
| `engine/strategies/learned_rulebook.py:325~340` | `res.should_buy` | learned strategy BUY/HOLD 변환 |
| `engine/portfolio/noop_gate.py:459~471` | `sig.should_buy` | portfolio daily loop 진입 gate |
| `engine/portfolio/pit_executable_rulebook_probe.py:251~263` | `sig.should_buy` | PIT executable 신규 진입 gate |
| `engine/portfolio/pit_universe_bias_probe.py:582~599` | `sig.should_buy` | PIT universe 신규 진입 gate |
| `engine/live/elite_pullback_replay.py:388~400` | row `should_buy` | replay의 BUY 연속 구간·첫 BUY 후보 구성 |
| `engine/live/elite_shadow_trader.py:816~832` | evaluation `should_buy` | shadow 신규 포지션 후보 gate |
| `engine/live/elite_signal_history.py:170~196` | row `should_buy` | first BUY·연속 BUY 일수 산출, 후속 buy gate 입력 |
| `engine/live/elite_strategy_sim.py:169~178` | current·last `should_buy` | strategy simulator 신규 BUY gate |
| `engine/live/elite_strategy_sim.py:446~460` | evaluation `should_buy` | simulation entry 후보 skip 여부 |
| `engine/live/s2_auto_trader.py:315~329` | evaluation `should_buy` | 실행 직전 candidate validation |

### A 경로 공통 특징

대부분은 `should_buy=False`면 곧바로 신규 후보·주문을 버린다. 따라서 향후 `should_buy`의 의미를 바꾸면 이 모든 A 사용처가 함께 바뀐다.

## (B) 보유 중 청산·interval-break

| 파일·라인 | 사용처 | 설명 |
|---|---|---|
| `engine/learning/execution_mode_backtest.py:76~90` | `strict_interval_pass = sig.should_buy` | 청산용 tape flag 생성. **원 결합 지점** |
| `engine/strategies/exit_simulator.py:220~242` | `_strict_interval_pass_from_tape()` | holding day tape point를 public dict로 바꾸고 strict flag 읽음 |
| `engine/strategies/exit_simulator.py:238~242` | strict flag→should_buy fallback | strict flag가 없으면 should_buy를 청산 판정으로 다시 사용 |
| `engine/strategies/exit_simulator.py:434~449` | `interval_pass is False` | 다음 거래일 open으로 `entry_interval_break` 청산 |
| `engine/learning/entry_fitness_threadsafe.py:119~135` | `entry_phase_signal_tape=signal_tape` | daily tape를 entry-phase 청산 simulator에 전달 |
| `scripts/research/run_stage3_aggressive.py:389~408` | monkeypatch tape injection | 일반 execution backtest에도 동일 tape와 provisional exit를 주입 |

### v4 정적 호출 경로

```text
run_stage3_aap_overlap_entry_v4_host.py
→ run_stage3_aap_tradecount_factor_v3_host.py
→ run_stage3_aap_newfitness_v2_host.py
→ run_stage3_aap_newfitness_official.py
→ run_stage3_official_2sym.py
→ run_stage3_baseline_light.py
→ run_stage3_aggressive.py:run_entry_backtest_period()
→ execution_mode_backtest.run_backtest_execution_mode()
→ simulate_exit(entry_phase_signal_tape=tape)
```

`run_stage3_aggressive.py:396~408`은 `_build_daily_signal_tape()`가 만든 동일 객체를 저장한 뒤 모든 entry-phase `simulate_exit()` 호출에 전달한다.

## (C) fitness·진단·로깅·직렬화

| 파일·라인 | 사용처 | 설명 |
|---|---|---|
| `engine/strategies/evaluator.py:607` | demo print | 콘솔 진단 |
| `engine/learning/backtest.py:175~184` | signal snapshot | 거래 진단 JSON |
| `engine/learning/execution_mode_backtest.py:84` | public tape `should_buy` | daily tape 진단·artifact |
| `engine/learning/execution_mode_backtest.py:735~760` | tape slice/result attach | holding/cooldown/daily signal artifact 생성 |
| `engine/central/signal_collector.py:247~258` | snapshot copy | A 경로 전의 DTO 저장 |
| `engine/live/elite_pullback_replay.py:351,365~381` | replay row 저장 | 상태·예외 placeholder와 평가 결과 기록 |
| `engine/live/elite_shadow_trader.py:463~480` | evaluation payload | live/shadow 진단 payload |
| `engine/live/elite_signal_history.py:146~164` | history row 저장 | signal history artifact |
| `engine/live/elite_signal_history.py:237` | `last_should_buy` | summary 진단 및 후속 A gate 입력 |
| `engine/live/elite_strategy_sim.py:147~155` | ratio retention | BUY row만 골라 signal 유지율 계산 |
| `engine/live/event_policy.py:124~140` | `pass_on`, `pass_off` | event shadow 비교 로그 |
| `engine/live/s2_auto_trader.py:414` | evaluation summary | 주문 계획 로그 |
| `scripts/research/dry_run_stage3_runtime_aap.py:98~104` | high-quality interval fail count | runtime 진단 |
| `scripts/research/run_stage3_baseline_light.py:288~315` | strict/quality 통계 | strict count, quality blocked/override 진단 |
| `scripts/research/run_stage3_aap_detail.py:304~309` | trade row strict flag | 상세 산출물 |
| `scripts/research/run_stage3_aap_newfitness_official.py:464~468` | fold-best strict flag | fold-best 거래 로그 |
| `scripts/research/run_stage3_aap_newfitness_v2_host.py:334~338` | fold-best strict flag | host fold-best 로그 |
| `scripts/research/run_stage3_aap_tradecount_factor_v3_host.py:203~206` | joint pass 날짜 | EEC/support 계산 입력 |

## 1.1 전수 검색에서 확인된 구조적 사실

1. `should_buy`의 canonical 계산은 evaluator 한 곳이다.
2. 신규 진입을 직접 차단하는 A 사용처는 다수다.
3. 보유 중 청산을 직접 실행하는 B 소비자는 `exit_simulator.py` 한 곳이다.
4. 그러나 B 입력을 만드는 `strict_interval_pass = should_buy` alias 때문에 evaluator 변화가 청산에 전파된다.
5. C 사용처 중 EEC·support·history도 의미가 바뀌므로, 거래 손익이 같아도 진단 산출물은 달라질 수 있다.

---

# STEP 2 — 진입/청산 결합 지점과 C1-S 실패 체인

## 2.1 정확한 결합 라인

### 결합 1 — strict 원 판정과 신규 진입

`engine/strategies/evaluator.py:498~500`

```python
if strict_entry:
    should_buy = interval_result.passed
```

### 결합 2 — 신규 진입 값과 청산 flag

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

### 결합 3 — 청산 helper의 should_buy fallback

`engine/strategies/exit_simulator.py:232~242`

```python
value = point.get("strict_interval_pass")
if isinstance(value, bool):
    return value
should_buy = point.get("should_buy")
return should_buy if isinstance(should_buy, bool) else None
```

`strict_interval_pass`를 분리해도 fallback을 그대로 두면 구형·불완전 tape에서 다시 오염될 수 있다.

### 결합 4 — false를 실제 청산으로 변환

`engine/strategies/exit_simulator.py:434~449`

```python
interval_pass = _strict_interval_pass_from_tape(...)
if interval_pass is False:
    next_idx = i + 1
    ...
    return _build_trade(
        exit_date=next_row.name,
        exit_price=float(fill),
        exit_reason=ENTRY_PHASE_INTERVAL_BREAK_REASON,
```

## 2.2 train_3 2024-11-04 거래 오염 체인

이번 작업에서는 백테스트를 실행하지 않았다. 아래 수치는 기존 read-only 산출물 `stage3_c1s_explicit_refactor_20260715/readout.md`에 기록된 실패를 코드 라인과 연결한 것이다.

### 현행

```text
2024-11-04 signal
→ strict interval pass
→ should_buy=True
→ 2024-11-05 진입

2024-11-06 holding day
→ strict interval 자체 pass
→ quality_score=0
→ 현행 strict branch는 quality를 보지 않음
→ should_buy=True
→ public tape strict_interval_pass=True
→ 보유 유지
→ 2024-11-12 interval-break 청산, +7.330704697986577%
```

### 임시 C1-S

```text
2024-11-06 holding day
→ interval_result.passed=True
→ signal_scaled AND quality_score=0
→ C1-S가 should_buy=False로 변경
→ _build_daily_signal_tape()가 그 SignalResult 저장
→ run_stage3_aggressive.py가 동일 tape를 simulate_exit에 주입
→ _signal_tape_point()가 to_public_dict() 호출
→ strict_interval_pass = should_buy = False
→ _strict_interval_pass_from_tape()가 False 반환
→ exit_simulator가 다음 거래일 open 청산
→ 2024-11-07 청산, +4.646812080536913%
```

### 라인 단위 체인

| 순서 | 파일·라인 | 역할 |
|---:|---|---|
| 1 | `evaluator.py:496` | quality_score 계산 |
| 2 | `evaluator.py:498~500` | 현재 strict pass를 should_buy에 저장; C1-S 삽입 지점 |
| 3 | `execution_mode_backtest.py:698~724` | 매 거래일 evaluate_signal 결과를 `_DailySignalPoint.signal`에 저장 |
| 4 | `run_stage3_aggressive.py:396~408` | 동일 tape를 entry-phase `simulate_exit`에 주입 |
| 5 | `exit_simulator.py:220~221` | `_DailySignalPoint.to_public_dict(role="holding")` 호출 |
| 6 | `execution_mode_backtest.py:84~89` | `strict_interval_pass`를 `should_buy`에서 복제 |
| 7 | `exit_simulator.py:232~242` | 복제된 strict flag를 읽음 |
| 8 | `exit_simulator.py:435~449` | false면 다음 시가 interval-break 청산 |

## 2.3 결합 범위

진입 조건만 바꿨을 때 다음 값이 함께 변할 수 있다.

- 신규 주문 목록
- 보유 중 청산일·청산가·보유일
- 손익·MAE·일평균수익
- entry fitness
- holding/cooldown signal path
- strict pass count·joint support·EEC
- live BUY/HOLD·queue·shadow candidate

따라서 `should_buy` 의미 변경은 evaluator 한 줄 수정으로 끝나는 로컬 변경이 아니다.

---

# STEP 3 — 분리 가능성 진단

## 옵션 비교

| 옵션 | 설계 | 영향 범위 | legacy | fixed sizing | entry-scope 청산 불변 | 위험도 |
|---|---|---|---|---|---|---|
| O1 | `SignalResult.strict_interval_pass`를 별도 필드로 추가하고 tape·exit가 이것만 사용 | evaluator, execution tape, exit helper | `None` 유지 가능 | 진입 동작 불변 | 단계적 적용 시 보장 가능 | 낮음~중간 |
| O2 | `entry_order_eligible` 추가, `should_buy`는 strict pass로 유지; A 소비자를 새 필드로 이전 | evaluator + A 사용처 전부 | 초기 alias로 불변 가능 | quality=0 fixed 보존 가능 | 청산은 기존 should_buy 사용으로 불변 | 중간~높음 |
| O3 | `strict_interval_pass`와 `entry_order_eligible` 모두 도입하고 `should_buy`를 deprecated compatibility alias로 한시 유지 | evaluator, tape, exit, 모든 A/C consumer | 명시 분기 가능 | 명시 보존 가능 | 가장 강하게 보장 가능 | 중간 |
| O4 | tape serializer가 `interval_checks`의 5개 `interval_pass`를 재조합해 strict flag 생성 | execution tape 중심 | legacy는 None | 진입 불변 | should_buy 변경과 분리 가능 | 중간 |
| O5 | exit simulator가 OHLCV/rulebook으로 strict interval을 매일 재계산 | exit simulator + feature evaluator | 별도 처리 필요 | 진입과 무관 | 이론상 가능 | 높음, 비권고 |

## O1 — 명시적 strict_interval_pass

제안 형태:

```python
@dataclass
class SignalResult:
    ...
    strict_interval_pass: Optional[bool] = None
```

Evaluator:

```text
strict schema: strict_interval_pass = interval_result.passed
legacy schema: strict_interval_pass = None
```

Tape:

```text
strict_interval_pass = sig.strict_interval_pass
```

Exit:

```text
현재 tape schema에서는 strict_interval_pass만 허용
should_buy fallback은 구형 artifact compatibility에만 제한
```

장점:

- 현재 의미의 원천인 `interval_result.passed`를 직접 보존한다.
- quality·sizing·gate 변경이 청산에 들어오지 않는다.
- fixed sizing quality=0 strict pass도 청산 strict pass로 유지된다.

주의:

- 새 필드가 public JSON에 추가되면 전체 artifact SHA는 의도적으로 변한다.
- 거래·손익·fitness bitwise 불변과 전체 JSON bytewise 불변은 분리해 검증해야 한다.

## O2 — entry_order_eligible 분리

제안 형태:

```text
strict_interval_pass = interval_result.passed
entry_order_eligible = strict_interval_pass AND entry_gate
```

A 사용처는 `entry_order_eligible`를 읽고 B 사용처는 `strict_interval_pass`를 읽는다.

장점:

- 이름과 역할이 가장 명확하다.
- `should_buy`를 당장 바꾸지 않고 compatibility alias로 유지할 수 있다.

위험:

- A 사용처가 연구·portfolio·live에 넓게 퍼져 있어 일부만 이전하면 환경별 진입 의미가 달라진다.
- live queue, shadow trader, central controller, PIT probe를 포함한 전수 migration이 필요하다.

## O3 — 완전한 dual-predicate 모델

권고 장기 구조:

```text
interval_result.passed
      ↓
strict_interval_pass  ─────────────→ holding exit
      ↓
entry gate / quality / sizing eligibility
      ↓
entry_order_eligible ──────────────→ new order
```

`should_buy`는 다음 중 하나로만 정의해야 한다.

1. `entry_order_eligible`의 deprecated alias
2. 제거 예정 필드

두 의미를 동시에 유지하면 현재 문제가 반복된다.

## O4 — interval_checks 재조합

예시:

```text
strict_interval_pass =
    strict_entry
    AND exactly five checks exist
    AND every check.interval_pass is True
```

코드 줄 수는 적지만 fail-closed의 조기 반환으로 partial checks가 생기는 경우를 정확히 처리해야 한다. 원 결과인 `interval_result.passed`를 직접 저장하는 O1보다 간접적이다.

## O5 — exit 재계산 비권고

청산 simulator에서 feature를 다시 계산하면 다음 위험이 생긴다.

- D-5 인덱스 경계 중복 구현
- q01/q99·hard domain·interval semantics 중복
- signal context와 exit context 차이
- 향후 evaluator 변경과 재계산 구현의 drift

분리 목적에는 과도한 변경이다.

## 권고 순서

### Phase 1 — 청산 원 판정 고정

```text
strict_interval_pass를 interval_result.passed에서 명시 저장
should_buy는 현행 그대로 유지
exit가 명시 strict flag만 읽도록 변경
```

이 단계에서는 거래·손익·fitness가 bitwise 동일해야 한다.

### Phase 2 — 진입 eligibility 명시

```text
entry_order_eligible 필드 추가
초기값은 현행 should_buy와 동일
A 사용처를 전수 migration
```

이 단계도 결과 불변이어야 한다.

### Phase 3 — gate 변경

```text
C1-S 또는 다양성 gate는 entry_order_eligible에만 적용
strict_interval_pass는 절대 변경하지 않음
```

이 순서를 거쳐야 진입 gate 변경과 청산 불변을 동시에 보장할 수 있다.

## 결과 불변의 정의

| 대상 | Phase 1·2 기대 |
|---|---|
| 체결 날짜·수량 | bitwise 동일 |
| 청산 날짜·가격·사유 | bitwise 동일 |
| 손익·MAE·fitness | float bitwise 동일 |
| legacy signal 결과 | bitwise 동일 |
| fixed quality=0 거래 | 보존 |
| public JSON SHA | 새 필드 추가 시 변경 가능 |
| 기존 필드만 projection한 JSON SHA | 동일해야 함 |

전체 artifact byte SHA까지 유지해야 한다면 새 필드를 즉시 public serialization에 넣지 않고 내부 필드·versioned sidecar로 먼저 도입해야 한다.

---

# STEP 4 — 다양성 작업 대비 경고 체크리스트

## 이 값·함수를 바꾸면 청산 오염 가능

- [ ] **`evaluate_signal()` strict branch의 `should_buy`**를 바꾸면 daily tape strict flag가 바뀐다.
- [ ] **`interval_result.passed`에 quality·diversity gate를 직접 합치면** 신규 진입과 보유 strict 상태가 동시에 바뀐다.
- [ ] **`execution_mode_backtest._DailySignalPoint.to_public_dict()` line 86~89**의 `strict_interval_pass = should_buy` alias를 그대로 둔 채 gate를 바꾸면 청산이 오염된다.
- [ ] **`exit_simulator._strict_interval_pass_from_tape()`의 should_buy fallback**을 그대로 두면 누락 tape에서 오염이 재발한다.
- [ ] **`evaluate_entry_intervals()` 또는 `extract_entry_features()`를 바꾸면** strict entry와 strict interval-break가 모두 바뀐다.
- [ ] **entry interval low/high, q01/q99 domain, schema version을 바꾸면** 청산 strict 상태도 바뀐다.
- [ ] **`_build_daily_signal_tape()`가 저장하는 signal 객체를 entry 전용으로 변형하면** holding path도 같은 변형을 본다.
- [ ] **`run_stage3_aggressive._entry_phase_execution_context()`의 tape 주입을 바꾸면** v4 청산 의미가 바뀐다.
- [ ] **`entry_fitness_threadsafe.py`의 `entry_phase_signal_tape` 전달을 바꾸면** Dask/thread-safe fitness 청산이 달라진다.
- [ ] **`entry_phase_exit`, `entry_phase_max_holding_days`를 바꾸면** gate와 무관하게 손익·fitness가 달라진다.
- [ ] **quality_score·market adjustment를 strict should_buy에 섞으면** holding day의 뉴스·시장 context도 청산 trigger가 된다.
- [ ] **signal tape schema/version을 바꾸면서 cache token을 유지하면** 구·신 의미가 cache에서 혼합될 위험이 있다.

## 신규 진입 consumer migration 체크

`should_buy`를 entry alias에서 다른 의미로 바꿀 때 다음 A 경로를 모두 감사해야 한다.

- [ ] execution backtest
- [ ] thread-safe entry fitness
- [ ] general backtest
- [ ] central signal collector/controller
- [ ] scheduled-open queue
- [ ] learned rulebook BUY/HOLD adapter
- [ ] portfolio noop/PIT loops
- [ ] elite replay/history/shadow/strategy simulator
- [ ] S2 auto trader execution validation

한 곳이라도 구 필드를 계속 읽으면 연구·live·portfolio 사이의 진입 정의가 달라진다.

## Legacy·fixed 보존 체크

- [ ] legacy schema는 `quality_score >= signal_threshold` 의미를 유지한다.
- [ ] legacy에서는 `strict_interval_pass=None` 또는 미적용이어야 한다.
- [ ] fixed sizing strict pass의 quality=0을 C1-S로 제거하지 않는다.
- [ ] signal-scaled quality=0은 신규 주문 eligibility에서만 false가 되고 strict interval pass는 유지돼야 한다.
- [ ] 현재 zero-size guard는 방어적 이중 안전장치로 유지한다.

## 불변 검증 체크

- [ ] 변경 전후 세 fold 체결 list canonical SHA
- [ ] 청산일·청산가·사유·보유일 float/field projection SHA
- [ ] 손익·MAE·fitness float64 bit SHA
- [ ] train_2 quality=0 fixed 5건 보존
- [ ] train_3 13건 유지 및 2024-11-04 거래 청산 2024-11-12 유지
- [ ] legacy canonical JSON projection SHA
- [ ] holding/cooldown tape의 strict flag는 기존 interval 판정과 동일
- [ ] EEC는 어떤 필드 기준인지 명시: strict support와 executable entry support를 혼용하지 않음

## 다양성 작업 선행 조건

```text
DO NOT redefine strict-AND or add quality/diversity gate to should_buy
until strict_interval_pass and entry_order_eligible are independently represented.
```

한국어 결론:

> **청산용 strict interval 원 판정을 먼저 고정·분리한 뒤에만 진입 gate·다양성 정의를 변경해야 한다.**

---

# 소스·보호·Git 감사

## 핵심 소스 SHA

| 파일 | SHA256 |
|---|---|
| `engine/strategies/evaluator.py` | `435b87aa999884527062963ca00a5fece63acd47c92916966442a22830965d01` |
| `engine/learning/entry_fitness_threadsafe.py` | `6ec29acfeac41a37732927630b05f59eab251a76480b5f18e8c8c07d796455f0` |
| `engine/learning/execution_mode_backtest.py` | `ce2b6673375a121c02a443ad24b811e2b7c00ce3de2c723ec69e132e74caf0ca` |
| `engine/learning/backtest.py` | `734519f71fd6bbf0d6c07c27c2626a5a93b309c4c6cca1de87bad4c9854f812e` |
| `engine/strategies/exit_simulator.py` | `37ea9550bb8870c2dce85fc6f0a9ea14cf5ed5c881312b1b0da8bf9fa0959d86` |
| `scripts/research/run_stage3_aggressive.py` | `3fb837c3a575d98260e3bc71eb45678440797a8f61188cdff366c93a0f8ebe7d` |
| 이전 C1-S 실패 readout | `f1a05ed24aad482ab7d5b678ae566f8fa57ee8e38e258ede7204c086c754aaa2` |

## 보호파일 시작·종료 SHA

| 파일 | 시작 SHA256 | 종료 SHA256 | 상태 |
|---|---|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` | 동일 | 불변 |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` | 동일 | 불변 |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` | 동일 | 불변 |

보호파일은 SHA 계산 외 읽지 않았고 변경하지 않았다.

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
f38dbf5ab31af0081f4a3747211a7e1c8364eb69

정적 조사 전 백업 commit:
7663507

백업 메시지:
should_buy 진입·청산 이중 용도 정적 매핑 전 기준점 백업: 소스·보호 SHA·daemon·clean tree 상태를 고정

코드 수정 commit:
없음 — read-only

분석 산출물 commit:
PENDING_AFTER_FIRST_COMMIT
```

분석 산출물 commit SHA를 반영한 메타데이터 commit은 최종 제출 메시지에 기록한다. 동일 commit 내부에 자기 SHA를 넣는 것은 self-reference라 불가능하다.

## 산출물 SHA

최종 `readout.md` SHA256은 같은 폴더의 `SHA256SUMS.txt`를 정본으로 한다.
