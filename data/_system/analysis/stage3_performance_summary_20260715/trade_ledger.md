# Stage3 performance trade ledger

## 범위와 산식

이 파일은 기존 full row-level 원장을 투자자 관점으로 해석하기 위한 ledger index다. full 거래별 원장 canonical source는 아래 5개 JSONL 파일이다. 각 파일은 모든 거래에 대해 `entry_signal_date`, `entry_date`, `entry_price`, `exit_date`, `exit_price`, `exit_reason`, `holding_days`, `mae_pct`, `eec_event_clusters`를 포함한다.

- AAP: `data/_system/analysis/stage3_aap_eec_penalty_v5_20260715/AAP/fold_best_trade_level.jsonl` — 55 trades
- LASR: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/LASR/fold_best_trade_level.jsonl` — 60 trades
- ADPT: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/ADPT/fold_best_trade_level.jsonl` — 55 trades
- BTBT: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/BTBT/fold_best_trade_level.jsonl` — 47 trades
- FIX: `data/_system/analysis/stage3_multiticker_v5_probe_20260715/FIX/fold_best_trade_level.jsonl` — 58 trades

투자자 관점 파생값:

- `ret_% = (exit_price / entry_price - 1) * 100`
- `pnl_$ = ret_% * 10000 / 100`, 즉 1회 거래당 10,000 USD 동일 명목 기준
- `win_+0.5 = ret_% >= 0.5`
- `cluster = entry_signal_date가 eec_event_clusters[start,end]에 들어가는 cluster_index`
- 수수료·슬리피지·세금은 미반영이다.

## Fold-level compact ledger

| ticker | fold | trades | avg ret % | median ret % | win % | avg win % | avg loss % | payoff | max loss % | max gain % | total pct-pts | pnl @10k/trade | MDD % | max cluster removed pct-pts | remain pct-pts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AAP | train_1 | 21 | 3.21 | 2.51 | 85.71 | 3.69 | N/A | N/A | +0.20 | 6.90 | 67.34 | $6,733.89 | 0.00 | 19.70 | 47.64 |
| AAP | train_2 | 21 | 4.24 | 4.28 | 80.95 | 5.43 | -4.22 | 1.29 | -4.22 | 11.27 | 89.00 | $8,899.64 | -4.22 | 21.74 | 67.26 |
| AAP | train_3 | 13 | 4.20 | 3.90 | 100.00 | 4.20 | N/A | N/A | +0.97 | 8.89 | 54.58 | $5,458.07 | 0.00 | 9.69 | 44.89 |
| LASR | train_1 | 20 | 4.98 | 5.57 | 80.00 | 6.67 | -1.76 | 3.79 | -4.98 | 15.10 | 99.69 | $9,968.54 | -5.28 | 24.98 | 74.70 |
| LASR | train_2 | 20 | 9.50 | 7.96 | 100.00 | 9.50 | N/A | N/A | +1.11 | 21.64 | 189.99 | $18,999.02 | 0.00 | 122.12 | 67.87 |
| LASR | train_3 | 20 | 14.10 | 3.66 | 80.00 | 17.95 | -1.30 | 13.84 | -1.87 | 50.06 | 282.09 | $28,209.16 | -5.09 | 256.63 | 25.47 |
| ADPT | train_1 | 16 | 5.46 | 4.89 | 100.00 | 5.46 | N/A | N/A | +1.62 | 12.78 | 87.33 | $8,733.29 | 0.00 | 30.00 | 57.33 |
| ADPT | train_2 | 19 | 5.07 | 4.60 | 73.68 | 7.43 | -1.53 | 4.86 | -3.78 | 17.30 | 96.41 | $9,641.48 | -3.78 | 6.26 | 90.16 |
| ADPT | train_3 | 20 | 6.93 | 5.09 | 90.00 | 7.73 | -0.84 | 9.18 | -0.84 | 21.17 | 138.65 | $13,864.66 | -0.84 | 33.73 | 104.92 |
| BTBT | train_1 | 17 | 17.95 | 12.62 | 100.00 | 17.95 | N/A | N/A | +4.76 | 45.00 | 305.17 | $30,517.03 | 0.00 | 99.63 | 205.54 |
| BTBT | train_2 | 18 | 10.45 | 9.46 | 88.89 | 11.94 | -3.48 | 3.43 | -3.48 | 37.45 | 188.11 | $18,811.21 | -3.48 | 36.36 | 151.75 |
| BTBT | train_3 | 12 | 6.84 | 6.71 | 91.67 | 7.46 | N/A | N/A | +0.00 | 15.19 | 82.09 | $8,208.64 | 0.00 | 28.38 | 53.70 |
| FIX | train_1 | 19 | 3.98 | 3.14 | 94.74 | 4.21 | -0.13 | 32.95 | -0.13 | 11.99 | 75.69 | $7,569.45 | -0.13 | 9.39 | 66.30 |
| FIX | train_2 | 19 | 4.00 | 3.92 | 89.47 | 4.53 | -0.44 | 10.33 | -0.71 | 15.27 | 76.08 | $7,607.75 | -0.71 | 14.97 | 61.11 |
| FIX | train_3 | 20 | 4.97 | 4.58 | 95.00 | 5.29 | -1.06 | 4.97 | -1.06 | 15.68 | 99.46 | $9,946.38 | -1.06 | 32.20 | 67.26 |

## Row-level 확인 방법

거래별 row는 기존 JSONL을 그대로 canonical ledger로 사용한다. 예를 들어 AAP 첫 거래는 다음 필드를 가진다.

```json
{
  "ticker": "AAP",
  "period_label": "train_1",
  "entry_signal_date": "2022-07-01",
  "entry_date": "2022-07-05",
  "entry_price": 172.350006104,
  "exit_date": "2022-07-08",
  "exit_price": 184.240005493,
  "exit_reason": "entry_interval_break",
  "holding_days": 3,
  "mae_pct": 0.0
}
```

투자자 관점 gross return은 `(184.240005493 / 172.350006104 - 1) * 100 = 6.90%`, 10,000 USD 명목 PnL은 `+$689.88`이다.

## 비고

이 파일은 read-only 산출물이다. 원본 JSONL·OHLCV·보호파일은 수정하지 않았다. 전체 275개 row-level ledger를 중복 저장하면 원본 JSONL과 동일 정보를 반복하게 되므로, canonical source path와 산식 및 fold-level compact ledger를 함께 기록했다.
