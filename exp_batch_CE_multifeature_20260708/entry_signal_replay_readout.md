# CE entry signal replay at actual buy time

## Verdict

Stage3 기준으로는 모두 `should_buy=True`였다.

```text
old_tech_selected Stage3: BUY
old_tech_actual_live Stage3: BUY
xlb_single_selected Stage3: BUY
multifeature_selected Stage3: BUY
```

따라서 이번 CE 실제 진입은, 비교에 사용한 Stage3 룰북 기준으로는 tech를 materials로 바꾸거나 7개 멀티피처를 넣었어도 피하지 못했을 가능성이 높다.

```text
판정: STAGE3_ALL_BUY
Stage3 selected 룰북 중 should_buy=False: 없음
```

## Actual entry timestamp

```text
intent_created_at: 2026-07-08T14:27:15.330072+00:00
candidate_last_seen_at: 2026-07-08T14:27:14.509621+00:00
KST: 2026-07-08 23:27:15
candidate_price: 48.61000061035156
actual_live_rulebook_hash: 998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78
candidate_id: stage3:CE:998b0b638c66
```

Manual snapshot:

```text
final_score: 8.363246295633697
raw_score: 8.363246295633697
threshold: 2.6541866643896674
reasons:
  MACD크로스(+1.17)
  RSI 44∈[37,80](+1.73)
  BB근접(+0.85)
  이벤트반응(+4.62)
```

## Look-ahead guard

The loaded CE daily data includes 2026-07-08, but that final daily bar is after the 2026-07-08 14:27 UTC entry time. It was excluded.

```text
ce_loaded_full_last_date: 2026-07-08
ce_eval_last_completed_bar: 2026-07-07
excluded_2026_07_08_final_bar: true
```

CE evaluation bar:

```text
date: 2026-07-07
Close: 48.68000030517578
RSI: 43.68414670397032
MACD_golden: 1
BB_lower: 44.66607962023797
ATR: 2.2415417624847387
```

Market context used from the actual CE snapshot:

```text
market_score: 87.2
vix_level: 17.18
tech_sector_score: 100.0
```

Event flags were not persisted directly. I used the minimal binary flag set that reproduces the actual live CE snapshot event score `+4.62`:

```text
has_war: 1
has_rate_hike: 1
replayed actual old event_adj: +4.620125606520604
```

## Feature scores as of 2026-07-07 completed bar

Formula:

```text
score = clip(50 + ret_60d_pct * 5, 0, 100)
```

| Feature | Symbol | 60d return | Score |
|---|---:|---:|---:|
| sector_materials | XLB | -0.3097% | 48.4517 |
| peer_EMN | EMN | -4.2498% | 28.7512 |
| peer_DD | DD | -1.8628% | 40.6861 |
| peer_LYB | LYB | -24.4500% | 0.0000 |
| peer_WLK | WLK | -34.9288% | 0.0000 |
| macro_ind | XLI | +5.9179% | 79.5894 |
| cost_oil | USO | -14.2092% | 0.0000 |

## Signal replay summary

| Stage | Rulebook | Sector | should_buy | Final | Threshold | Margin | Market adj |
|---|---|---|---:|---:|---:|---:|---:|
| Stage2 | old_tech_selected | tech | False | -0.6824 | 2.5636 | -3.2460 | 1.0000 |
| Stage2 | xlb_single_selected | materials | True | 4.1409 | 3.1385 | +1.0024 | 1.0000 |
| Stage2 | multifeature_selected | materials | False | -1.8743 | 2.0350 | -3.9093 | 1.1014 |
| Stage3 | old_tech_selected | tech | True | 3.7268 | 3.4032 | +0.3236 | 1.0000 |
| Stage3 | old_tech_actual_live | tech | True | 8.3608 | 2.6542 | +5.7066 | 1.0000 |
| Stage3 | xlb_single_selected | materials | True | 4.4835 | 2.9803 | +1.5032 | 1.0000 |
| Stage3 | multifeature_selected | materials | True | 4.2318 | 2.4351 | +1.7967 | 1.0000 |

## Stage3 component breakdown

### old_tech_selected Stage3

```text
hash: 12fbd9799087bfe58a32393885cb0882cb29d72c2bac0f912b9782df4688eab1
should_buy: True
final_score: 3.726756
threshold: 3.403206
margin: +0.323550
MACD: +1.125590
RSI: +1.538835
BB: +1.062331
events: +0.000000
```

### old_tech_actual_live Stage3

```text
hash: 998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78
should_buy: True
final_score: 8.360767
threshold: 2.654187
margin: +5.706581
MACD: +1.167788
RSI: +1.725078
BB: +0.847776
events: +4.620126
```

Snapshot reproduction:

```text
manual snapshot final_score: 8.363246295633697
replay final_score: 8.360767
difference: about 0.0025
```

### xlb_single_selected Stage3

```text
hash: e524fb7e4f8ccf210f2170b4bc47f6d0dd6dfcd79c678690cbc1cf38ee2a338e
should_buy: True
final_score: 4.483524
threshold: 2.980339
margin: +1.503186
MACD: +2.000000
RSI: +2.000000
BB: +0.506297
events: -0.022772
```

### multifeature_selected Stage3

```text
hash: c383986ec2887d333ecb469e90ba34351cfcc3271a55e38c873e2af33a318766
should_buy: True
final_score: 4.231834
threshold: 2.435105
margin: +1.796728
MACD: +1.873256
RSI: +1.425808
BB: +0.932770
events: +0.000000
```

Multifeature weights:

```text
mf_weight_sector_materials: +0.617297
mf_weight_peer_EMN: -1.000000
mf_weight_peer_DD: +0.014664
mf_weight_peer_LYB: +1.000000
mf_weight_peer_WLK: +0.137977
mf_weight_macro_ind: -1.000000
mf_weight_cost_oil: +0.040535
```

Feature contributions on entry date:

```text
sector_materials/XLB: -0.019115
peer_EMN: +0.424975
peer_DD: -0.002732
peer_LYB: -1.000000
peer_WLK: -0.137977
macro_ind/XLI: -0.591788
cost_oil/USO: -0.040535
ce_multifeature_correlation_adj: -0.705171
```

Even though the feature block was net negative, the technical MACD/RSI/BB score still cleared the threshold.

## Final interpretation

```text
섹터를 materials로 고쳤으면 진입을 피했을 것이다: 현재 replay에서는 지지되지 않음
멀티피처를 넣었으면 진입을 피했을 것이다: 현재 replay에서는 지지되지 않음
```

Better phrasing:

```text
이번 CE 진입은 섹터 오분류 때문에 발생했다기보다,
진입 직전 CE의 MACD/RSI/BB 기술 신호가 여러 Stage3 룰북에서 공통적으로 threshold를 넘은 사건이다.
```

## Limitations

1. The exact 2026-07-08 14:27 UTC intraday OHLCV snapshot was not persisted locally. The replay therefore used the 2026-07-07 completed bar and excluded the 2026-07-08 final daily bar.
2. The original event flag vector was not persisted. The event flags were reconstructed only to match the live snapshot event score.
3. This is an event replay, not a statistical test.
