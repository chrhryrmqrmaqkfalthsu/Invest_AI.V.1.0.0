# LR8D PIT Universe Bias Probe Report

작성일: 2026-06-10 KST  
상태: v0 실측 완료  
실행 모드:

```bash
venv/bin/python scripts/research/run_central_portfolio_noop_gate.py --mode pit_universe_bias_probe
```

---

## 1. 결론

PIT universe bias probe 결과, 현재 fixed16 `realistic_research_baseline`에는 material한 universe survivorship premium이 섞여 있는 것으로 판정한다.

사전 고정 판정:

```text
label: bias_material
```

핵심 변화:

```text
win_rate: 71.83% → 60.56%  (-11.27pp)
profit_factor: 4.05 → 2.63  (-35.19%)
total_return_on_gross_entry_pct: 3.27% → 2.12%  (-1.15pp)
```

사전 기준상 다음 중 하나라도 충족하면 `bias_material`이다.

```text
win_rate 하락폭 > 5pp
profit_factor 하락폭 > 20%
total_return_on_gross_entry_pct 하락폭 > 1.0pp
```

이번 결과는 세 조건을 모두 초과한다.

---

## 2. 실행 조건

reference:

```text
fixed16 realistic_research_baseline
source: data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv
universe: 2026-06-09 exported lr8d_stage1_20260609 fixed16
entry: T+1 open
exit: conservative_core
```

candidate:

```text
PIT universe-only bias probe
2024 신규 진입 universe: universe_asof_2023-12-31.json
2025 신규 진입 universe: universe_asof_2024-12-31.json
entry: T+1 open
exit: conservative_core
rulebook: current data/symbols/{ticker}/parameters.json
```

중요 제한:

```text
이 probe는 universe-only probe다.
룰북 자체는 point-in-time으로 재학습하지 않았다.
selected executable rulebook에 2025H2 source 편향이 남아 있을 수 있다.
따라서 이 결과는 full PIT baseline이 아니라 survivorship bias의 1차 실측이다.
```

---

## 3. PIT manifest 무결성

selection rule:

```text
pit_all_available_labels_top16_v0
```

as_of=2023-12-31:

```text
allowed_labels: 2022, 2023
forbidden_labels: 2024, 2025H2
count: 16
forbidden_label_violation_count: 0
missing_parameters_count: 0
```

as_of=2024-12-31:

```text
allowed_labels: 2022, 2023, 2024
forbidden_labels: 2025H2
count: 16
forbidden_label_violation_count: 0
missing_parameters_count: 0
```

PIT 후보 17개 고유종목은 모두 실제 가격 로더로 로드 가능했다.

```text
ADSK, ALSN, AZO, CDNS, CINF, CRWD, EME, ITT, LASR,
LECO, MCK, MELI, MPC, PGR, SE, TDG, TTWO
```

---

## 4. fixed16 overlap

2024 거래용 PIT universe:

```text
MCK, CRWD, MELI, MPC, CDNS, TDG, ALSN, PGR,
ADSK, SE, LECO, AZO, TTWO, LASR, EME, CINF
```

fixed16 overlap:

```text
CRWD, EME, LASR, MPC
4 / 16
```

2025 거래용 PIT universe:

```text
MCK, CRWD, MELI, MPC, CDNS, ALSN, PGR, ADSK,
SE, LECO, TDG, AZO, LASR, EME, CINF, ITT
```

fixed16 overlap:

```text
CRWD, EME, ITT, LASR, MPC
5 / 16
```

해석:

```text
현재 fixed16 baseline이 거래한 universe의 약 70%는
해당 as_of 시점에 같은 selection rule을 정직하게 적용했다면 top16에 없었을 종목이다.
```

---

## 5. 성과 비교

### 5.1 fixed16 baseline

```text
trade_count: 71
ticker_count: 10
gross_entry_krw: 1438.8663
total_pnl_krw: 46.9930
total_return_on_gross_entry_pct: 3.2660%
avg_trade_pnl_pct: 2.9639%
win_rate_pct: 71.8310%
profit_factor: 4.0516
avg_holding_days: 17.1268
max_holding_days: 30
```

exit reason:

```text
breakeven_stop: 4
stop_loss: 1
take_profit: 1
time_out: 28
trailing: 37
```

### 5.2 PIT universe-only candidate

```text
trade_count: 71
ticker_count: 9
gross_entry_krw: 1198.4404
total_pnl_krw: 25.3876
total_return_on_gross_entry_pct: 2.1184%
avg_trade_pnl_pct: 1.5158%
win_rate_pct: 60.5634%
profit_factor: 2.6257
avg_holding_days: 13.9155
max_holding_days: 28
```

exit reason:

```text
stop_loss: 13
take_profit: 6
time_out: 20
trailing: 32
```

### 5.3 delta

```text
trade_count: 71 → 71
active_ticker_count: 10 → 9
win_rate_delta: -11.27pp
profit_factor_delta: -35.19%
return_delta: -1.15pp
avg_trade_pnl_delta: -1.45pp
```

---

## 6. 위험 축 변화

### 6.1 time_out drag

fixed16:

```text
time_out_total_pnl_krw: +8.1120
time_out_loss_count: 14
time_out_loss_pnl_krw: -12.9256
```

PIT:

```text
time_out_total_pnl_krw: +6.0395
time_out_loss_count: 6
time_out_loss_pnl_krw: -4.5234
```

해석:

```text
time_out drag는 오히려 개선됐다.
성과 하락의 주범은 time_out이 아니라 stop_loss 증가와 trailing 수익 감소다.
```

### 6.2 stop_loss

fixed16:

```text
stop_loss_count: 1
stop_loss_pnl_krw: -0.7052
```

PIT:

```text
stop_loss_count: 13
stop_loss_pnl_krw: -8.2007
```

해석:

```text
PIT universe로 바꾸면 stop_loss가 1건에서 13건으로 증가한다.
기존 fixed16 baseline의 낮은 stop_loss 수는 universe selection premium의 영향을 받았을 가능성이 크다.
```

### 6.3 trailing

fixed16:

```text
trailing_pnl_krw: +35.0389
trailing_count: 37
```

PIT:

```text
trailing_pnl_krw: +18.4711
trailing_count: 32
```

해석:

```text
PIT universe에서는 trailing winner의 이익이 약 절반으로 줄었다.
fixed16 baseline은 사후 survivor universe에서 trailing winner를 더 많이 담고 있었을 가능성이 있다.
```

---

## 7. ticker 변화

fixed16 active trade tickers:

```text
CAKE, EME, HSBC, ITT, LASR, MPLX, NBIX, WAB, WELL, WPM
```

PIT active trade tickers:

```text
AZO, CDNS, CINF, EME, ITT, LASR, MCK, MELI, TTWO
```

PIT ticker별 PnL:

```text
AZO:  +1.0870
CDNS: -1.1801
CINF: +2.2509
EME:  +8.3417
ITT:  +0.6719
LASR: +5.1933
MCK:  +4.0789
MELI: -1.7712
TTWO: +6.7151
```

해석:

```text
PIT universe도 완전히 나쁜 것은 아니다.
다만 fixed16의 HSBC/WAB/WELL 같은 사후 winner가 빠지고,
PIT에서는 stop_loss가 많은 신규 ticker가 들어오면서 성과가 하락했다.
```

---

## 8. cap binding

PIT candidate 기준:

```text
event_date_count: 125
avg_open_positions_on_event_dates: 2.816
max_open_positions_on_event_dates: 6
max_gross_exposure_on_event_dates: 125.4479
```

cap binding rough check:

```text
cap=120: 2 event days binding_or_over
cap=180: 0
cap=240: 0
cap=300: 0
cap=480: 0
cap=600: 0
```

해석:

```text
PIT universe에서도 480/600 cap은 전혀 binding되지 않는다.
capital allocation entry sizing은 여전히 주요 수익률 레버가 아니다.
```

---

## 9. 해석

이번 결과는 current fixed16 baseline을 폐기한다는 뜻이 아니다. 정확한 지위는 다음이다.

```text
current fixed16 realistic baseline:
  현재 live promoted 16종목의 감사/진단 기준으로 유효
  거래 레벨 look-ahead 제거 효과 측정에 유효
  capital allocation entry sizing 음성 probe에 유효

하지만:
  live 기대수익 또는 2024-2025 point-in-time 운용 가능 수익률로 해석하면 안 됨
  universe survivorship premium을 포함함
```

PIT universe-only baseline의 지위:

```text
universe selection bias의 1차 실측 기준
하지만 rulebook-level look-ahead가 남아 있으므로 최종 live 기대수익 기준은 아님
```

---

## 10. 다음 결정

사전 기준상 `bias_material`이 확인됐으므로 다음 단계는 full PIT rerun 설계가 정당화된다.

우선순위:

```text
1. full PIT rerun 설계 진행
2. executable rulebook도 as_of 이전 데이터로만 생성
3. run_backtest에 T+1 entry + conservative_core exit 배선
4. as_of별 rulebook artifact ref를 manifest에 저장
5. PIT central baseline을 다시 산출
```

현재 fixed16의 좋은 숫자는 보수적으로 해석한다.

```text
71.8% win rate와 PF 4.05는 live 기대값이 아니라,
사후 promoted universe 감사 기준의 성과다.
```
