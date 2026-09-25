# CE 룰북 backtest vs live 정합성 diff — READ-ONLY

대상 candidate_id: `stage3:CE:998b0b638c66`

## 1. 로드 경로 추적

### backtest / oos_reproduce_frozen 계열

- 재현 스크립트: `data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py`
- 후보 파일: `data/_system/analysis/oos_reproduce_frozen_20260707/candidate_universe.json`
- CE candidate row의 `source_file`: `exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl`
- 코드 경로: `run_candidate()` → `engine.live.elite_shadow_trader._load_rulebook_for_candidate(candidate)` → `source_file`에서 `rulebook_hash`가 같은 row를 찾아 `row["rulebook"]` 반환 → `Rulebook.from_dict(rb_dict)`
- 형식: full 원본 `final_rulebooks.jsonl` row의 `rulebook` dict

### live_candidate_slots / live revalidation 계열

- 후보 생성/재검증 코드: `engine.live.elite_shadow_report.build_elite_shadow_report()` 및 `engine.live.elite_shadow_trader._load_rulebook_for_candidate()`
- S2 auto validation 코드: `engine/live/s2_auto_trader.py::_candidate_full_payload()` → `build_elite_shadow_report()` → `evaluate_candidate()` → `_load_rulebook_for_candidate()`
- CE full source: `exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl`
- 형식: full 원본 `final_rulebooks.jsonl` row의 `rulebook` dict

### real_dashboard 표시/수동매수 snapshot 계열

- 상태 파일: `data/_system/live_slots_state.json`
- CE 위치: `slots`
- 형식: full rulebook이 아니라 display/order snapshot. `threshold`, `stop_loss_atr`, `take_profit_atr`, `trailing_atr`, `max_holding_days`, `final_score`, `reasons` 등 일부 값만 저장.
- `rulebook` dict 자체는 저장되어 있지 않음.

### 파일 일치 여부

| 비교 | 판정 |
|---|---|
| backtest full rulebook vs live revalidation full rulebook | SAME_FILE |
| backtest full rulebook vs real_dashboard/live_slots_state persisted snapshot | DIFFERENT_FILE |

## 2. CE source 요약

```text
full rulebook source_file: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
full rulebook hash: 998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78
candidate_universe source_file: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
live_slots_state candidate location: slots
live_slots_state rulebook dict present: False
```

## 3. 판정

```text
FINAL_VERDICT: DRIFT_HARMFUL
```

정확한 의미:

```text
- backtest 재현 경로와 live revalidation 경로는 같은 full rulebook 파일을 읽으므로 CONSISTENT.
- 하지만 real_dashboard/live_slots_state에 보존된 CE payload는 full rulebook이 아니라 compact snapshot이다.
- 이 snapshot을 Rulebook으로 복원하거나 full rulebook 대체물로 사용하면 should_buy/final_score/exit trigger에 영향을 주는 필드가 누락 또는 기본값으로 drift한다.
```

## 4. full path 비교

full source 기준으로 backtest와 live revalidation은 필드 단위로 모두 일치한다.

```text
backtest_full_path: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
live_revalidation_full_path: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
full_path_field_mismatch_count: 0
```

## 5. real_dashboard/live_slots_state snapshot과 full rulebook diff

차이 필드 수: `30`

| field | backtest full | live snapshot / reconstructed | note | impact |
|---|---:|---:|---|---|
| use_market_entry_adjustment | False | True | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| vix_sensitivity | -1 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_war | 0.542323096041 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_rate_hike | 1.43315021261 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_rate_cut | -0.892581119569 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_geopolitical | -1.93124685135 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_tariff | -1.21400416525 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_export_ban | -0.308314371562 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_earnings_shock | 0.795132287842 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_oil_surge | 0.172981818556 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_banking_crisis | -0.0192687522117 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_inflation | 1.2754307508 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_response_fed_statement | 0.160533408412 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| event_strength_multiplier | 2.33874362477 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| weight_ma_align | 1.22724132374 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| weight_macd_golden | 1.16778788141 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| weight_rsi_zone | 1.72507752429 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| rsi_low | 36.938502712 | 30 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| rsi_high | 80 | 70 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| weight_bb_near_lower | 0.847776347082 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| bb_proximity | 1.13247867254 | 1.05 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| weight_volume_surge | 0.0887692459382 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| volume_surge_ratio | 1.55974961134 | 1.5 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_SHOULD_BUY_OR_FINAL_SCORE |
| stop_loss_atr_bear | 1 | 2 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |
| take_profit_atr_bull | 4.62468031876 | 3.5 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |
| trailing_atr_volatile | 3.97929124925 | 2 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |
| trailing_activation_profit_pct | 6.56883447707 | 3 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |
| breakeven_trigger_profit_pct | 4 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |
| breakeven_floor_profit_pct | 1.27673918688 | 0 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |
| sell_omen_threshold | 0.616855013161 | 1 | MISSING_IN_LIVE_SLOT_IF_RECONSTRUCTED_DEFAULT | AFFECTS_EXIT_TRIGGER_OR_POSITION_POLICY |

## 6. 핵심 drift 요약

should_buy/final_score 영향 가능 필드 예:

```text
sector_name: full=tech / live_snapshot_default=tech
use_market_entry_adjustment: full=False / live_snapshot_default=True
vix_sensitivity: full=-1.0 / live_snapshot_default=0.0
signal_threshold: full=2.6541866643896674 / live_snapshot threshold=2.6541866643896674 / Rulebook.from_dict(live_row).signal_threshold=2.0
RSI: full=[36.93850271203499, 80.0] / live_snapshot_default=[30.0, 70.0]
MACD weight: full=1.167787881408684 / live_snapshot_default=1.0
BB weight/proximity: full=0.8477763470822091/1.1324786725411682 / live_snapshot_default=1.0/1.05
```

청산 영향 가능 필드 예:

```text
stop_loss_atr: full=2.9488902763720244 / live_snapshot=2.9488902763720244 / default_reconstructed=2.9488902763720244
take_profit_atr: full=3.7074125087814065 / live_snapshot=3.7074125087814065 / default_reconstructed=3.7074125087814065
trailing_atr: full=2.53183095624166 / live_snapshot=2.53183095624166 / default_reconstructed=2.53183095624166
max_holding_days: full=10 / live_snapshot=10 / default_reconstructed=10
```

## 7. CSV

Diff CSV:

```text
data/_system/analysis/oos_reproduce_frozen_20260707/live_vs_backtest_rulebook_diff_table.csv
```
