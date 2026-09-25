# should_buy 진입/청산 이중 용도 정적 매핑

> **전 STEP read-only. 코드·원천 데이터 수정, GA·재학습·파라미터 탐색·백테스트 실행 없음.**

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 조사 방법: `grep -RIn`, Python AST 정적 검색, 라인 인용, 기존 C1-S 실패 readout 대조
- 검색 범위: 작업 위치 아래 모든 `*.py`
- 핵심 판정: **`SHOULD_BUY_IS_COUPLED_TO_EXIT`**
- 결론: 다양성·quality gate를 바꾸기 전에 **청산용 strict interval 판정과 신규 주문용 진입 eligibility를 별도 값으로 분리해야 한다.**

## 핵심 요약

현재 strict schema의 `should_buy`는 세 의미를 동시에 가진다.

1. 다섯 strict interval의 원 통과 판정
2. 신규 주문 생성 gate
3. daily signal tape를 통한 보유 중 interval-break 판정

직접 결합 라인은 `engine/learning/execution_mode_backtest.py:86~89`이다.

```python
"strict_interval_pass": (
    bool(getattr(sig, "should_buy", False))
    if bool(getattr(sig, "strict_entry", False))
    else None
),
```

`engine/strategies/exit_simulator.py:435~449`는 이 값이 false면 다음 거래일 시가에 청산한다. 따라서 진입 gate에 quality 조건을 섞어 `should_buy`를 바꾸면 청산도 함께 바뀐다.

필요한 선행 분리는 다음과 같다.

```text
strict_interval_pass = interval_result.passed          # 청산용, quality 독립
entry_order_eligible = strict_interval_pass AND gate   # 신규 진입용
```

`should_buy`는 호환 alias로 한시 유지할 수 있지만 어느 값의 alias인지 하나로 고정해야 한다.

---

# STEP 0 — 정의 위치·상위 계산 체인

## 0.1 경로 정정

프로젝트에 `engine/learning/evaluator.py`는 없다. 실제 평가기는 다음 파일이다.

```text
engine/strategies/evaluator.py
```

`entry_fitness_threadsafe.py`는 `should_buy`를 설정하지 않고 line 93에서 읽기만 한다.

## 0.2 Canonical 정의·설정 지점

| 파일·라인 | 설정 | 의미 |
|---|---|---|
| `engine/strategies/evaluator.py:39~51` | `SignalResult.should_buy: bool` | canonical 필드 정의 |
| `engine/strategies/evaluator.py:307~320` | `should_buy=False` | 데이터 부족 fail-closed |
| `engine/strategies/evaluator.py:498~500` | `should_buy = interval_result.passed` | strict schema 원 설정 |
| `engine/strategies/evaluator.py:501~502` | `should_buy = quality_score >= rb.signal_threshold` | legacy schema 원 설정 |
| `engine/strategies/evaluator.py:507~515` | `SignalResult(should_buy=should_buy, ...)` | 결과 객체 저장 |

Canonical boolean을 새로 계산하는 곳은 `evaluate_signal()` 한 곳이다. 아래 지점들은 복사·별칭·직렬화다.

## 0.3 파생·별칭 설정 지점 전수 목록

| 파일·라인 | 파생값 | 설명 |
|---|---|---|
| `engine/central/signal_collector.py:20~30` | `SignalSnapshot.should_buy` | 중앙 수집 DTO 필드 |
| `engine/central/signal_collector.py:247~258` | `sig.should_buy → snapshot.should_buy` | 평가 결과 복사 |
| `engine/learning/backtest.py:175~184` | JSON `should_buy` | signal snapshot |
| `engine/learning/execution_mode_backtest.py:76~100` | JSON `should_buy` | daily tape 직렬화 |
| `engine/learning/execution_mode_backtest.py:86~89` | `strict_interval_pass = sig.should_buy` | **청산 오염의 의미 결합** |
| `engine/live/elite_pullback_replay.py:351` | `should_buy=False` | replay 범위 밖 placeholder |
| `engine/live/elite_pullback_replay.py:365~378` | `res.should_buy → row.should_buy` | replay row 복사 |
| `engine/live/elite_pullback_replay.py:381` | `should_buy=False` | 평가 예외 placeholder |
| `engine/live/elite_shadow_trader.py:463~475` | evaluation dict | shadow 평가 payload |
| `engine/live/elite_signal_history.py:146~164` | local·row `should_buy` | signal history 복사 |
| `engine/live/elite_signal_history.py:237` | `last_should_buy` | history summary 별칭 |
| `engine/live/event_policy.py:124~140` | `pass_on`, `pass_off` | event 비교 별칭 |
| `engine/live/s2_auto_trader.py:414` | evaluation summary | 주문 계획 로그 |
| `engine/strategies/exit_simulator.py:241~242` | local `should_buy` | strict flag 누락 시 fallback |
| `scripts/research/run_stage3_baseline_light.py:299~306` | local strict·buy | signal 통계 별칭 |

## 0.4 Strict interval 계산 체인

### Schema 선택과 평가 호출

`engine/strategies/evaluator.py:325~342`

```python
schema_version = int(getattr(rb, "entry_interval_schema_version", 1))
strict_entry = schema_version >= STRICT_ENTRY_INTERVAL_SCHEMA_VERSION
entry_features = extract_entry_features(df)
interval_result = evaluate_entry_intervals(rb, entry_features)
```

### D-5 feature

`extract_entry_features()`는 신호일 D 기준 D-5 거래일에서 다음 값을 만든다.

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

### Fail-closed strict 판정

`engine/strategies/evaluator.py:121~289`

```text
finite
→ hard mathematical domain
→ learned q01~q99 empirical domain
→ learned low/high interval
```

어느 단계든 실패하면 `EntryIntervalResult(passed=False)`다. 다섯 feature가 모두 통과할 때만 line 284~289에서 `passed=True`가 된다.

### Strict boolean

`engine/strategies/evaluator.py:498~500`

```python
if strict_entry:
    should_buy = interval_result.passed
```

현재 strict schema의 boolean에는 `quality_score`, `signal_threshold`, sizing strategy가 들어가지 않는다.

## 0.5 Quality·legacy 계산 체인

`engine/strategies/evaluator.py:354~496`

```text
ma_align + macd + rsi + bb + volume
+ news global + news topics
= raw_score                         line 453
+ event adjustment                 line 472
+ optional crash bonus             line 478
× market_adjustment                lines 482~494
= quality_score                    line 496
```

Legacy schema에서만 다음 비교가 boolean을 만든다.

```python
should_buy = quality_score >= rb.signal_threshold
```

Strict schema에서 threshold는 진단·sizing용이며 strict boolean에는 미사용이다.

## 0.6 score·quality·final_score 구분

| 이름 | 의미 | should_buy 상위값 여부 |
|---|---|---|
| `raw_score` | market adjustment 전 component 합 | legacy에서 간접 상위값 |
| `quality_score` | `raw_score × market_adjustment` | legacy boolean·sizing 상위값 |
| `SignalResult.score` | quality_score와 동일하게 저장 | sizing 입력 |
| `signal_threshold` | legacy threshold·signal-scaled 분모 | strict boolean에는 미사용 |
| `final_score` | 후보/report/dashboard 계층의 별도 명칭 | **이 evaluator boolean 체인에는 없음** |

## 0.7 Signal-scaled 암묵적 veto

`engine/strategies/evaluator.py:542~571`

```python
elif strategy == "signal_scaled":
    ratio_signal = min(signal_score / max(rb.signal_threshold, 0.1), 2.0)
    ratio = rb.base_position_ratio * min(ratio_signal * rb.signal_multiplier, 1.0)
```

`quality_score=signal_score=0`이면 amount는 0이다.

Zero-size guards:

- `engine/learning/backtest.py:615~620`
- `engine/learning/execution_mode_backtest.py:854~859`
- `engine/learning/entry_fitness_threadsafe.py:112~117`

```text
strict should_buy=True
→ signal-scaled amount=0
→ shares=0
→ shares<=0 continue
→ 신규 주문 없음
```

이 veto는 신규 주문에만 작동한다. daily tape의 `should_buy=True`는 남아 청산 strict-pass로 사용된다.

---

# STEP 1 — 사용처 전수 분류

검색어:

```text
should_buy
strict_interval_pass
_strict_interval_pass_from_tape
entry_interval_break
```

## (A) 신규 진입 판정·후보 gate

| 파일·라인 | 사용처 | 설명 |
|---|---|---|
| `engine/learning/backtest.py:601~620` | `if not sig.should_buy` | general backtest 신규 진입 후 sizing |
| `engine/learning/execution_mode_backtest.py:823~859` | `if not sig.should_buy` | execution-mode 신규 진입 후 zero-size guard |
| `engine/learning/entry_fitness_threadsafe.py:88~117` | `if not signal.should_buy` | thread-safe entry fitness 진입 gate |
| `engine/central/signal_collector.py:263~269` | `snap.should_buy` | 중앙 후보 수집 |
| `engine/live/central_control.py:623~637` | `res.should_buy` | BUY/HOLD 생성 |
| `engine/live/scheduled_open_buy_queue.py:424~438` | `res.should_buy` | next-open BUY/HOLD 생성 |
| `engine/strategies/learned_rulebook.py:325~340` | `res.should_buy` | learned strategy BUY/HOLD adapter |
| `engine/portfolio/noop_gate.py:459~471` | `sig.should_buy` | portfolio daily 진입 gate |
| `engine/portfolio/pit_executable_rulebook_probe.py:251~263` | `sig.should_buy` | PIT executable 진입 gate |
| `engine/portfolio/pit_universe_bias_probe.py:582~599` | `sig.should_buy` | PIT universe 진입 gate |
| `engine/live/elite_pullback_replay.py:388~400` | row `should_buy` | BUY 연속구간·첫 후보 구성 |
| `engine/live/elite_shadow_trader.py:816~832` | evaluation `should_buy` | shadow 포지션 후보 gate |
| `engine/live/elite_signal_history.py:170~196` | row `should_buy` | first BUY·연속 BUY 산출 |
| `engine/live/elite_strategy_sim.py:169~178` | current·last flag | strategy simulator BUY gate |
| `engine/live/elite_strategy_sim.py:446~460` | evaluation flag | simulation entry skip |
| `engine/live/s2_auto_trader.py:315~329` | evaluation flag | 실행 직전 validation |

향후 `should_buy` 의미를 바꾸면 모든 A 사용처가 함께 변한다.

## (B) 보유 중 청산·interval-break

| 파일·라인 | 사용처 | 설명 |
|---|---|---|
| `engine/learning/execution_mode_backtest.py:76~90` | strict alias 생성 | **원 결합 지점** |
| `engine/strategies/exit_simulator.py:220~242` | `_strict_interval_pass_from_tape()` | holding tape point 읽기 |
| `engine/strategies/exit_simulator.py:238~242` | should_buy fallback | strict flag 누락 시 재결합 |
| `engine/strategies/exit_simulator.py:434~449` | `interval_pass is False` | 다음 거래일 open 청산 |
| `engine/learning/entry_fitness_threadsafe.py:119~135` | tape 전달 | thread-safe entry-phase 청산 입력 |
| `scripts/research/run_stage3_aggressive.py:389~408` | tape monkeypatch 주입 | execution backtest 청산 입력 |

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

## (C) fitness·진단·로깅·직렬화

| 파일·라인 | 사용처 | 설명 |
|---|---|---|
| `engine/strategies/evaluator.py:607` | demo print | 콘솔 진단 |
| `engine/learning/backtest.py:175~184` | signal snapshot | 거래 JSON |
| `engine/learning/execution_mode_backtest.py:84` | tape `should_buy` | daily artifact |
| `engine/learning/execution_mode_backtest.py:735~760` | tape slice/result | holding·cooldown·daily artifact |
| `engine/central/signal_collector.py:247~258` | snapshot copy | DTO 저장 |
| `engine/live/elite_pullback_replay.py:351,365~381` | replay row | placeholder·평가 기록 |
| `engine/live/elite_shadow_trader.py:463~480` | evaluation payload | shadow 진단 |
| `engine/live/elite_signal_history.py:146~164` | history row | signal 기록 |
| `engine/live/elite_signal_history.py:237` | last flag | summary·후속 gate 입력 |
| `engine/live/elite_strategy_sim.py:147~155` | ratio retention | BUY row 유지율 |
| `engine/live/event_policy.py:124~140` | `pass_on/off` | event 비교 로그 |
| `engine/live/s2_auto_trader.py:414` | evaluation summary | 주문 계획 로그 |
| `scripts/research/dry_run_stage3_runtime_aap.py:98~104` | diagnostic count | high-quality interval fail |
| `scripts/research/run_stage3_baseline_light.py:288~315` | signal stats | strict·quality 통계 |
| `scripts/research/run_stage3_aap_detail.py:304~309` | trade row | strict flag 기록 |
| `scripts/research/run_stage3_aap_newfitness_official.py:464~468` | fold-best row | strict flag 기록 |
| `scripts/research/run_stage3_aap_newfitness_v2_host.py:334~338` | host row | strict flag 기록 |
| `scripts/research/run_stage3_aap_tradecount_factor_v3_host.py:203~206` | joint dates | EEC/support 입력 |

## 구조적 사실

1. canonical 계산은 evaluator 한 곳이다.
2. 신규 진입 A 소비자는 연구·portfolio·live에 넓게 분산돼 있다.
3. 실제 청산 B 소비자는 exit simulator 한 곳이다.
4. B 입력을 만드는 alias 때문에 evaluator 변화가 청산으로 전파된다.
5. C 사용처 중 EEC·history도 의미가 바뀌므로 손익이 같아도 진단 산출물은 달라질 수 있다.

---

# STEP 2 — 결합 지점과 C1-S 실패 체인

## 2.1 정확한 결합 라인

### Strict 원 판정과 신규 진입

`engine/strategies/evaluator.py:498~500`

```python
if strict_entry:
    should_buy = interval_result.passed
```

### 신규 진입 값과 청산 flag

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

### 청산 helper fallback

`engine/strategies/exit_simulator.py:232~242`

```python
value = point.get("strict_interval_pass")
if isinstance(value, bool):
    return value
should_buy = point.get("should_buy")
return should_buy if isinstance(should_buy, bool) else None
```

### False를 실제 청산으로 변환

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

이번 작업에서는 백테스트를 실행하지 않았다. 아래 수치는 기존 read-only 산출물 `stage3_c1s_explicit_refactor_20260715/readout.md`에 기록된 실패를 정적 코드 라인과 연결한 것이다.

### 현행

```text
2024-11-04 strict pass
→ should_buy=True
→ 2024-11-05 진입

2024-11-06 holding day
→ strict interval 자체 pass
→ quality_score=0
→ 현행 strict branch는 quality 미사용
→ should_buy=True
→ strict_interval_pass=True
→ 보유 유지
→ 2024-11-12 청산, +7.330704697986577%
```

### 임시 C1-S

```text
2024-11-06 interval_result.passed=True
→ signal_scaled AND quality_score=0
→ C1-S가 should_buy=False로 변경
→ daily tape가 SignalResult 저장
→ 동일 tape가 simulate_exit에 주입
→ to_public_dict가 strict_interval_pass=False 생성
→ exit helper가 False 반환
→ 다음 거래일 시가 청산
→ 2024-11-07 청산, +4.646812080536913%
```

### 라인 단위

| 순서 | 파일·라인 | 역할 |
|---:|---|---|
| 1 | `evaluator.py:496` | quality_score 계산 |
| 2 | `evaluator.py:498~500` | strict pass를 should_buy에 저장; C1-S 삽입 지점 |
| 3 | `execution_mode_backtest.py:698~724` | 매일 SignalResult를 tape에 저장 |
| 4 | `run_stage3_aggressive.py:396~408` | 동일 tape를 entry-phase simulator에 주입 |
| 5 | `exit_simulator.py:220~221` | holding point를 public dict로 변환 |
| 6 | `execution_mode_backtest.py:84~89` | strict flag를 should_buy에서 복제 |
| 7 | `exit_simulator.py:232~242` | strict flag 읽기 |
| 8 | `exit_simulator.py:435~449` | false면 다음 시가 청산 |

## 2.3 함께 변할 수 있는 결과

- 신규 주문 목록
- 청산일·청산가·보유일
- 손익·MAE·일평균수익·fitness
- holding/cooldown signal path
- strict support·joint count·EEC
- live BUY/HOLD·queue·shadow 후보

`should_buy` 의미 변경은 evaluator 한 줄의 로컬 변경이 아니다.

---

# STEP 3 — 분리 가능성 진단

## 옵션 비교

| 옵션 | 설계 | 영향 범위 | legacy | fixed sizing | 청산 불변 | 위험도 |
|---|---|---|---|---|---|---|
| O1 | `SignalResult.strict_interval_pass` 별도 필드, tape·exit가 이것만 사용 | evaluator, tape, exit | `None` 가능 | 진입 불변 | 단계 적용 시 보장 가능 | 낮음~중간 |
| O2 | `entry_order_eligible` 추가, should_buy는 strict pass 유지, A 소비자 이전 | evaluator + A 전부 | 초기 alias로 불변 | q=0 보존 가능 | 기존 strict 값 유지 | 중간~높음 |
| O3 | 두 필드 모두 도입, should_buy deprecated alias | evaluator, tape, exit, A/C 전부 | 명시 분기 | 명시 보존 | 가장 강함 | 중간 |
| O4 | tape가 `interval_checks` 5개를 재조합 | tape 중심 | legacy None | 진입 불변 | 분리 가능 | 중간 |
| O5 | exit에서 strict interval 재계산 | exit+feature evaluator | 별도 처리 | 진입 독립 | 이론상 가능 | 높음, 비권고 |

## O1 — 명시적 strict_interval_pass

```python
@dataclass
class SignalResult:
    strict_interval_pass: Optional[bool] = None
```

```text
strict schema: interval_result.passed
legacy schema: None
public tape: sig.strict_interval_pass
exit: 현재 schema에서는 명시 flag만 사용
```

장점:

- 원천 판정을 직접 보존한다.
- quality·sizing·gate가 청산에 들어오지 않는다.
- fixed sizing quality=0 strict pass도 보존한다.

주의:

- public JSON에 필드를 추가하면 artifact 전체 SHA는 바뀐다.
- 거래·손익·fitness bitwise 불변과 전체 JSON bytewise 불변을 분리해 검증해야 한다.

## O2 — entry_order_eligible

```text
strict_interval_pass = interval_result.passed
entry_order_eligible = strict_interval_pass AND entry_gate
```

A 사용처는 entry flag, B 사용처는 strict flag를 읽는다. 의미는 가장 명확하지만 live queue·shadow·central·PIT를 포함한 전수 migration이 필요하다.

## O3 — 권고 장기 구조

```text
interval_result.passed
      ↓
strict_interval_pass  ─────────────→ holding exit
      ↓
quality/diversity/sizing gate
      ↓
entry_order_eligible ──────────────→ new order
```

`should_buy`는 `entry_order_eligible`의 deprecated alias 또는 제거 예정 필드 중 하나로만 정의해야 한다.

## O4 — interval_checks 재조합

```text
strict_entry
AND exactly five checks
AND every check.interval_pass is True
```

변경 줄 수는 적지만 fail-closed 조기 반환으로 partial checks가 생기는 경우를 정확히 다뤄야 한다. 원 결과를 직접 저장하는 O1보다 간접적이다.

## O5 — exit 재계산 비권고

- D-5 경계 중복 구현
- domain·interval semantics 중복
- signal/exit context 차이
- evaluator와 exit 구현 drift

분리 목적에 비해 위험이 크다.

## 권고 적용 순서 — 이번 작업에서는 미적용

### Phase 1

```text
strict_interval_pass를 interval_result.passed에서 명시 저장
should_buy 현행 유지
exit가 명시 strict flag만 읽도록 변경
```

거래·청산·손익·fitness가 bitwise 동일해야 한다.

### Phase 2

```text
entry_order_eligible 추가
초기값은 현행 should_buy와 동일
A 사용처 전수 migration
```

이 단계도 결과 불변이어야 한다.

### Phase 3

```text
C1-S·다양성 gate는 entry_order_eligible에만 적용
strict_interval_pass는 변경 금지
```

## 결과 불변 정의

| 대상 | 기대 |
|---|---|
| 체결 날짜·수량 | bitwise 동일 |
| 청산 날짜·가격·사유 | bitwise 동일 |
| 손익·MAE·fitness | float64 bitwise 동일 |
| legacy signal projection | bitwise 동일 |
| fixed quality=0 거래 | 보존 |
| 기존 필드 projection JSON | 동일 |
| 전체 public JSON SHA | 새 필드 추가 시 변경 가능 |

전체 artifact byte SHA까지 유지해야 하면 새 필드를 즉시 public serialization에 넣지 않고 내부 필드·versioned sidecar로 먼저 도입해야 한다.

---

# STEP 4 — 다양성 작업 대비 경고 체크리스트

## 이 값·함수를 바꾸면 청산 오염 가능

- [ ] `evaluate_signal()` strict branch의 `should_buy`
- [ ] `interval_result.passed`에 quality·diversity gate를 직접 합치는 변경
- [ ] `_DailySignalPoint.to_public_dict()` line 86~89의 alias
- [ ] `_strict_interval_pass_from_tape()`의 should_buy fallback
- [ ] `evaluate_entry_intervals()` 또는 `extract_entry_features()`
- [ ] interval low/high, q01/q99 domain, schema version
- [ ] `_build_daily_signal_tape()`에서 signal 객체를 entry 전용으로 변형하는 변경
- [ ] `run_stage3_aggressive._entry_phase_execution_context()`의 tape 주입
- [ ] `entry_fitness_threadsafe.py`의 `entry_phase_signal_tape` 전달
- [ ] `entry_phase_exit`, `entry_phase_max_holding_days`
- [ ] quality_score·market adjustment를 strict should_buy에 섞는 변경
- [ ] tape schema를 바꾸면서 cache token을 유지하는 변경

## 신규 진입 consumer migration

- [ ] execution backtest
- [ ] thread-safe entry fitness
- [ ] general backtest
- [ ] central signal collector/controller
- [ ] scheduled-open queue
- [ ] learned rulebook adapter
- [ ] portfolio noop/PIT loops
- [ ] elite replay/history/shadow/strategy simulator
- [ ] S2 auto trader validation

한 곳이라도 구 필드를 계속 읽으면 research·live·portfolio의 진입 정의가 달라진다.

## Legacy·fixed 보존

- [ ] legacy는 `quality_score >= signal_threshold` 의미 유지
- [ ] legacy `strict_interval_pass=None` 또는 미적용
- [ ] fixed strict pass quality=0을 C1-S로 제거하지 않음
- [ ] signal-scaled quality=0은 entry eligibility만 false, strict interval은 유지
- [ ] zero-size guard는 방어적 이중 안전장치로 유지

## 불변 검증

- [ ] 세 fold 체결 list canonical SHA
- [ ] 청산일·청산가·사유·보유일 projection SHA
- [ ] 손익·MAE·fitness float64 bit SHA
- [ ] train_2 fixed quality=0 5건 보존
- [ ] train_3 13건 유지
- [ ] 2024-11-04 거래의 2024-11-12 청산 유지
- [ ] legacy canonical projection SHA
- [ ] holding/cooldown strict flag가 기존 interval 판정과 동일
- [ ] EEC의 strict support와 executable support를 분리 표기

## 다양성 작업 선행 조건

```text
DO NOT redefine strict-AND or add quality/diversity gate to should_buy
until strict_interval_pass and entry_order_eligible are independently represented.
```

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

전체 검색 대상 source SHA는 `SHA256SUMS.txt`에 기록했다.

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
f38dbf5ab31af0081f4a3747211a7e1c8364eb69

정적 조사 전 백업 commit:
7663507

분석 산출물 commit:
d21b227b51b15d4b4e46a4e7bb4f679483196d44

코드 수정 commit:
없음 — read-only
```

산출물 commit SHA를 반영한 메타데이터 commit은 최종 제출 메시지에 기록한다. 동일 commit 내부에 자기 SHA를 넣는 것은 self-reference라 불가능하다.

## 산출물 SHA

최종 `readout.md` SHA256은 같은 폴더의 `SHA256SUMS.txt`를 정본으로 한다.
