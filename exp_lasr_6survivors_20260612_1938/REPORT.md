# LASR 25H2 역방향 min_trades 제거 4구간 통과 6개 정체 확인

기존 산출물만 읽었다.

- `exp_lasr_reverse_20260612_1856/period_metrics.csv`
- `exp_lasr_reverse_20260612_1856/rulebooks_topn.jsonl`
- `exp_lasr_reverse_20260612_1856/trades.jsonl`
- `data/symbols/LASR/parameters.json`

GA·백테스트 신규 실행 없음. 라이브 파일 수정 없음.

## Step 1 — 6개 hash

min_trades 제거 기준에서 2022·2023·2024·2025H2를 모두 통과한 6개:

| hash | train rank |
|---|---:|
| 0707c5f2 | 36 |
| 2820575b | 29 |
| 28291859 | 48 |
| 89908043 | 23 |
| cd2d26c4 | 38 |
| de9eb672 | 40 |

## Step 2 — 구간별 성적

표기: Exp% / DD% / trades / member_score. `*`는 trades < 5 구간.

| hash | 2022 | 2023 | 2024 | 2025H2 |
|---|---|---|---|---|
| 0707c5f2 | 4.863 / -10.485 / 15 / 86.4 | 4.017 / 0.000 / 1 / 86.2 * | 10.208 / 0.000 / 2 / 84.8 * | 4.433 / -18.669 / 19 / 59.6 |
| 2820575b | 2.111 / -7.033 / 16 / 48.7 | 3.247 / 0.000 / 4 / 84.8 * | 7.846 / 0.000 / 3 / 80.6 * | 2.948 / -13.968 / 23 / 42.8 |
| 28291859 | 1.135 / -27.385 / 14 / 18.3 | 2.203 / 0.000 / 1 / 81.3 * | 6.792 / 0.000 / 2 / 77.7 * | 1.890 / -21.694 / 19 / 17.2 |
| 89908043 | 1.871 / -7.033 / 13 / 48.5 | 4.017 / 0.000 / 1 / 86.9 * | 10.894 / 0.000 / 2 / 90.5 * | 1.681 / -24.785 / 18 / 14.0 |
| cd2d26c4 | 3.190 / -7.033 / 17 / 84.2 | 9.669 / 0.000 / 1 / 92.6 * | 3.273 / -8.392 / 3 / 65.2 * | 2.078 / -21.219 / 16 / 23.9 |
| de9eb672 | 1.759 / -7.179 / 17 / 34.6 | 6.892 / 0.000 / 2 / 89.7 * | 3.965 / -3.629 / 8 / 69.5 | 1.996 / -13.968 / 20 / 22.8 |

### 청산 사유 분포

| hash | 2022 exits | 2023 exits | 2024 exits | 2025H2 exits |
|---|---|---|---|---|
| 0707c5f2 | time_out 15 | time_out 1 | take_profit 2 | stop_loss 4, take_profit 4, time_out 11 |
| 2820575b | breakeven_stop 9, time_out 7 | breakeven_stop 2, time_out 2 | take_profit 2, time_out 1 | breakeven_stop 9, stop_loss 4, take_profit 3, time_out 7 |
| 28291859 | time_out 14 | time_out 1 | take_profit 1, time_out 1 | stop_loss 3, take_profit 2, time_out 14 |
| 89908043 | breakeven_stop 8, time_out 5 | time_out 1 | take_profit 2 | breakeven_stop 10, stop_loss 1, take_profit 2, time_out 5 |
| cd2d26c4 | breakeven_stop 8, time_out 9 | time_out 1 | stop_loss 1, take_profit 2 | breakeven_stop 7, stop_loss 1, take_profit 2, time_out 6 |
| de9eb672 | breakeven_stop 8, time_out 9 | breakeven_stop 1, time_out 1 | breakeven_stop 1, take_profit 2, time_out 5 | breakeven_stop 8, stop_loss 3, take_profit 3, time_out 6 |

## Step 3 — 6개 vs 현재 라이브 42088d4e 청산폭 비교

| param | live 42088d4e | 0707c5f2 | 2820575b | 28291859 | 89908043 | cd2d26c4 | de9eb672 |
|---|---:|---:|---:|---:|---:|---:|---:|
| exit_strategy | hybrid | fixed | fixed | fixed | hybrid | fixed | fixed |
| stop_loss_atr | 1.739 | 1.771 | 1.771 | 1.771 | 2.722 | 2.697 | 1.771 |
| stop_loss_atr_bear | 2.593 | 4.976 | 3.223 | 4.976 | 5.000 | 3.223 | 5.000 |
| take_profit_atr | 4.716 | 4.675 | 4.641 | 4.641 | 4.641 | 4.641 | 4.641 |
| take_profit_atr_bull | 4.974 | 2.113 | 2.113 | 2.113 | 2.253 | 1.853 | 1.914 |
| trailing_atr | 2.184 | 3.000 | 1.113 | 1.113 | 2.558 | 3.000 | 2.891 |
| trailing_atr_volatile | 4.000 | 1.149 | 1.149 | 1.149 | 3.119 | 1.149 | 1.149 |
| trailing_activation_profit_pct | 4.004 | 1.624 | 5.126 | 1.890 | 5.913 | 6.595 | 1.000 |
| breakeven_enabled | False | False | True | False | True | True | True |
| breakeven_trigger_profit_pct | 0.000 | 0.000 | 5.176 | 0.000 | 4.603 | 6.762 | 4.000 |
| breakeven_floor_profit_pct | 0.000 | 0.000 | 1.000 | 0.000 | 1.000 | 1.408 | 1.000 |
| max_holding_days | 30 | 7 | 7 | 5 | 7 | 7 | 7 |
| sell_omen_enabled | False | True | True | True | True | True | True |
| sell_omen_threshold | 1.000 | 0.585 | 0.667 | 0.600 | 0.673 | 0.595 | 0.505 |

청산폭 관찰:

- 6개 모두 `max_holding_days`가 5~7일로, 라이브 30일보다 훨씬 짧다.
- 6개 모두 `take_profit_atr_bull`이 1.85~2.25로, 라이브 4.97보다 훨씬 좁다.
- 5개는 `fixed`, 1개는 `hybrid`다. 라이브는 `hybrid`다.
- 5개는 `sell_omen_enabled=True`, 라이브는 False다.
- 4개는 `breakeven_enabled=True`, 라이브는 False다.
- 기본 `take_profit_atr`은 4.64~4.68로 라이브 4.72와 비슷하다. 보수성 차이는 주로 `take_profit_atr_bull`, `max_holding_days`, `sell_omen`, `breakeven`에서 나온다.

## Step 4 — 신뢰도 메모

| hash | total trades | 최소 구간 trades | 최소 구간 | trades<5 구간 | 모든 구간 ≥3? | 모든 구간 ≥5? | 평균 Exp% | 최악 DD% |
|---|---:|---:|---|---|---:|---:|---:|---:|
| 0707c5f2 | 37 | 1 | 2023 | 2023, 2024 | False | False | 5.880 | -18.669 |
| 2820575b | 46 | 3 | 2024 | 2023, 2024 | True | False | 4.038 | -13.968 |
| 28291859 | 36 | 1 | 2023 | 2023, 2024 | False | False | 3.005 | -27.385 |
| 89908043 | 34 | 1 | 2023 | 2023, 2024 | False | False | 4.616 | -24.785 |
| cd2d26c4 | 37 | 1 | 2023 | 2023, 2024 | False | False | 4.552 | -21.219 |
| de9eb672 | 47 | 2 | 2023 | 2023 | False | False | 3.653 | -13.968 |

신뢰도 관찰:

- 6개 모두 `모든 구간 trades>=5`를 만족하지 못한다.
- 6개 중 `모든 구간 trades>=3`을 만족하는 것은 2820575b 1개뿐이다.
- 0707c5f2, 28291859, 89908043, cd2d26c4는 2023 거래 수가 1건이다.
- de9eb672는 2023 거래 수가 2건이다.
- 2820575b는 최저 거래 수가 3건이라 6개 중 표본 신뢰도는 가장 낫지만, 그래도 2023 4건·2024 3건으로 원본 strict_k3의 5건 기준에는 못 미친다.

## 한 줄 결론

이 6개는 청산폭만 보면 라이브 42088d4e보다 명확히 보수적이다. 특히 max_holding은 5~7일로 라이브 30일보다 짧고, bull take-profit도 훨씬 좁다. 하지만 6개 모두 한 개 이상 구간에서 trades<5이며, 5개는 최소 거래 구간이 1~2건이라 다년 일관성을 신뢰하기 어렵다. 6개 중 상대적으로 볼 만한 것은 2820575b 하나뿐이지만, 이 역시 2023 4건·2024 3건이라 표본 부족 판정은 유지된다.
