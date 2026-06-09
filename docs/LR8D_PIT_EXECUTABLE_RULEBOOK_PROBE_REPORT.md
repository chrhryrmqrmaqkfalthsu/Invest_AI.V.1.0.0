# LR8D PIT Executable Rulebook Probe Report

작성일: 2026-06-10 KST  
상태: v0 실측 완료  
실행 모드:

```bash
venv/bin/python scripts/research/run_central_portfolio_noop_gate.py --mode pit_executable_rulebook_probe
```

---

## 1. 결론

`rulebook_hash`로 실행 가능한 full Rulebook artifact를 복원할 수 있었다. 따라서 full GA rerun 없이 중간 probe를 실행했다.

결과는 다음과 같다.

```text
fixed16 PF:              4.05
PIT universe-only PF:    2.63
PIT executable PF:       1.45
```

판정:

```text
rulebook-source look-ahead / selection premium도 material하다.
full PIT rerun 필요성이 더 강해졌다.
```

다만 이 probe는 trade count가 크게 변했다.

```text
PIT universe-only trade_count: 71
PIT executable trade_count:    175
```

따라서 이 결과는 “같은 거래를 룰북만 바꾼 apples-to-apples 비교”가 아니라, `as_of` 허용 label rulebook을 실제 실행했을 때 signal frequency와 exit profile까지 같이 바뀐 저비용 probe다.

---

## 2. artifact 복원 가능성

`topn.jsonl` 자체에는 실행 가능한 rulebook 파라미터가 없다.

```text
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn.jsonl
  → candidates의 hash / OOS summary 중심
```

하지만 별도 artifact 파일이 존재한다.

```text
data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn_rulebooks.jsonl
```

확인 결과:

```text
rulebook_artifact_rows: 10573
artifact_hashes: 10573
needed_hashes_unique: 10573
missing: 0
```

따라서 `topn.jsonl candidate rulebook_hash → full rulebook artifact` 복원이 100% 가능했다.

---

## 3. 현재 promoted rulebook source 확인

예시 종목들은 `parameters.json`의 `promotion.selection.selected_rulebook_source_label` 기준 모두 2025H2 source다.

```text
EME:  selected_rulebook_source_label = 2025H2
CRWD: selected_rulebook_source_label = 2025H2
MPC:  selected_rulebook_source_label = 2025H2
LASR: selected_rulebook_source_label = 2025H2
```

예: EME top candidates

```text
2022: 130b5566cfa06282, expectancy 2.5290, member_score 94.8718
2023: d9dc3661b98900d8, expectancy 3.8373, member_score 98.2051
2024: 6dc9a0f44716fb23, expectancy 4.1416, member_score 86.1538
2025H2: 04e513d011de510c, expectancy 5.8555, member_score 76.0256
```

즉 현재 promoted executable rulebook은 PIT 거래 시점에 허용되지 않는 future/stress label에서 온 경우가 있다.

---

## 4. 사전 고정 rulebook selection rule

rule id:

```text
pit_allowed_label_best_expectancy_v0
```

원칙:

```text
1. as_of manifest의 allowed label evidence만 사용한다.
2. forbidden label evidence는 절대 사용하지 않는다.
3. 각 ticker/as_of에 대해 allowed label 대표 후보 중 하나를 executable rulebook으로 선택한다.
```

정렬 기준:

```text
1. expectancy_pct 높은 순
2. profit_factor 높은 순
3. oos_member_score 높은 순
4. rank_is 낮은 순
```

as_of별 허용 label:

```text
as_of=2023-12-31:
  allowed_labels = 2022, 2023
  forbidden_labels = 2024, 2025H2

as_of=2024-12-31:
  allowed_labels = 2022, 2023, 2024
  forbidden_labels = 2025H2
```

선택 결과:

```text
selected_count: 32
missing_count: 0
selected_label_counts:
  2022: 12
  2023: 10
  2024: 10
```

---

## 5. 실행 조건

reference:

```text
fixed16 realistic_research_baseline
source: data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv
universe: 2026-06-09 exported lr8d_stage1_20260609 fixed16
rulebook: current promoted data/symbols/{ticker}/parameters.json
entry: T+1 open
exit: conservative_core
```

intermediate reference:

```text
PIT universe-only probe
universe: as_of별 PIT top16
rulebook: current promoted data/symbols/{ticker}/parameters.json
entry: T+1 open
exit: conservative_core
```

candidate:

```text
PIT executable rulebook probe
universe: as_of별 PIT top16
rulebook: as_of allowed label best candidate artifact
entry: T+1 open
exit: conservative_core
```

중요 제한:

```text
이 probe는 기존 topn rulebook artifact를 재사용한다.
GA를 재실행하지 않았다.
T+1/conservative_core 조건으로 룰북을 재학습하지 않았다.
따라서 full PIT baseline이 아니라 rulebook-source 편향의 저비용 측정이다.
```

---

## 6. 성과 비교

### 6.1 fixed16 baseline

```text
trade_count: 71
active_ticker_count: 10
win_rate_pct: 71.8310%
profit_factor: 4.0516
total_return_on_gross_entry_pct: 3.2660%
avg_trade_pnl_pct: 2.9639%
```

### 6.2 PIT universe-only

```text
trade_count: 71
active_ticker_count: 9
win_rate_pct: 60.5634%
profit_factor: 2.6257
total_return_on_gross_entry_pct: 2.1184%
avg_trade_pnl_pct: 1.5158%
```

### 6.3 PIT executable rulebook

```text
trade_count: 175
active_ticker_count: 17
win_rate_pct: 64.0000%
profit_factor: 1.4486
total_return_on_gross_entry_pct: 1.0754%
avg_trade_pnl_pct: 1.4613%
```

### 6.4 delta vs PIT universe-only

```text
trade_count_delta: +104
win_rate_delta: +3.44pp
profit_factor_delta: -1.1771 (-44.83%)
return_delta: -1.0430pp
avg_trade_pnl_delta: -0.0545pp
```

해석:

```text
평균 거래 수익률은 거의 비슷하지만, 거래 수가 크게 늘면서 손실 노출도 커졌다.
PF와 total return on gross entry가 크게 낮아졌다.
```

---

## 7. exit profile 변화

PIT executable exit reason:

```text
breakeven_stop: 27
stop_loss: 15
take_profit: 12
time_out: 74
trailing: 47
```

PIT executable PnL by exit reason:

```text
breakeven_stop: +4.6493
stop_loss:      -11.7107
take_profit:    +25.8113
time_out:       -14.4535
trailing:       +20.7148
```

핵심 변화:

```text
time_out_total_pnl: +6.0395(universe-only) → -14.4535(executable)
time_out_loss_count: 6 → 39
stop_loss_count: 13 → 15
```

해석:

```text
as_of 허용 label rulebook을 실행하면 신호가 훨씬 많아지고,
time_out 손실이 다시 주요 drag가 된다.
```

---

## 8. ticker별 결과

PIT executable active tickers:

```text
ADSK, ALSN, AZO, CDNS, CINF, CRWD, EME, ITT, LASR,
LECO, MCK, MELI, MPC, PGR, SE, TDG, TTWO
```

Ticker PnL:

```text
ADSK: -0.8710
ALSN: +4.0125
AZO:  +0.3230
CDNS: -1.3429
CINF: -1.4481
CRWD: -0.9108
EME:  +3.0374
ITT:  +0.6170
LASR: +1.4554
LECO: +0.1278
MCK:  +5.6894
MELI: +0.5431
MPC:  +0.0345
PGR:  -0.6933
SE:   +12.9818
TDG:  +0.1266
TTWO: +1.3287
```

최악 거래는 모두 time_out이다.

```text
MELI 2024-11-06~2024-12-19 -21.50% time_out
CDNS 2025-01-22~2025-02-24 -19.95% time_out
MPC  2024-04-12~2024-05-24 -16.88% time_out
SE   2025-10-27~2025-11-21 -16.01% time_out
CDNS 2025-10-27~2025-11-26 -13.65% time_out
```

---

## 9. cap binding

PIT executable:

```text
event_date_count: 248
avg_open_positions_on_event_dates: 7.17
max_open_positions_on_event_dates: 12
max_gross_exposure_on_event_dates: 161.43
```

cap binding rough check:

```text
cap=120: 36 event days
cap=180: 0
cap=240: 0
cap=300: 0
cap=480: 0
cap=600: 0
```

해석:

```text
거래 수가 늘면서 cap=120에서는 binding이 발생한다.
하지만 현재 480/600 cap에서는 여전히 binding되지 않는다.
```

---

## 10. 최종 해석

이번 probe로 세 단계 숫자가 정리됐다.

```text
1. fixed16 realistic baseline
   PF 4.05, win_rate 71.83%, return 3.27%
   → 사후 promoted universe + 2025H2 executable rulebook premium 포함

2. PIT universe-only
   PF 2.63, win_rate 60.56%, return 2.12%
   → universe selection은 시점 정직, executable rulebook은 current promoted

3. PIT executable rulebook
   PF 1.45, win_rate 64.00%, return 1.08%
   → universe와 executable source는 as_of 허용 label로 제한
   → 단, GA 재학습/보수적 조건 학습은 아직 아님
```

따라서 PF 2.63은 최종 바닥이 아니었다. executable rulebook source 편향을 제거하면 PF는 1.45까지 내려갔다.

판정:

```text
full PIT rerun is justified.
```

---

## 11. 다음 결정

full PIT rerun의 필요성은 더 강해졌다. 다만 full rerun 전에 반드시 설계해야 할 것이 있다.

```text
1. run_backtest에 T+1 entry / conservative_core exit 배선
2. as_of별 train 종료 기준으로 executable rulebook 생성
3. candidate selection rule 사전 고정
4. PIT manifest가 rulebook artifact ref를 직접 가리키도록 변경
5. central baseline이 manifest의 rulebook ref를 로드하도록 구현
```

현재 수치의 live 해석:

```text
PF 4.05: live 기대값으로 해석 금지
PF 2.63: universe-only 중간 baseline
PF 1.45: executable source까지 제한한 저비용 probe
```

보수적 live 기대값 논의는 PF 1.45 근처를 기준으로 시작하는 것이 더 안전하다.
