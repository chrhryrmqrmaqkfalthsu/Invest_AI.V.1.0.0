# Stage 3 runtime dry-run — AAP train_1

## Verdict

`PASS`

실제 로컬 AAP OHLCV와 `train_1` 한 fold에서 population 10, generation 3으로 Stage 3 strict-entry 배선을 최소 실행했다. 성과 판정이 아니라 runtime wiring 검증만 수행했다.

## Fold domain

분포 리포트와 sample count·q01·q99가 모두 일치했다.

| Feature | sample_count | q01 | q99 | Match |
|---|---:|---:|---:|---|
| ma_trend | 251 | -23.2794308986 | 4.8904158435 | PASS |
| macd_hist | 251 | -8.3319011497 | 2.6279197861 | PASS |
| rsi | 251 | 11.7521743270 | 70.2607673746 | PASS |
| bb_position | 251 | -0.2389724704 | 1.0924193967 | PASS |
| volume_ratio | 251 | 0.5244281054 | 2.2934032984 | PASS |

## Entry-scope GA

```text
population: 10
generations: 3
evaluations: 34
invalid candidates: 0
best joint support: 24
```

Best candidate feature support:

```text
ma_trend: 157
macd_hist: 183
rsi: 124
bb_position: 130
volume_ratio: 140
```

편측·NaN·domain 밖·도달 불가능 후보는 최종 population과 best에서 0건이었다.

## Strict-AND runtime

Daily tape에서 strict schema v2 평가가 실제로 확인됐다.

```text
quality_score >= threshold 이지만 strict interval 실패로 no-buy 유지한 날짜: 256
```

따라서 뉴스·시장·기술 quality 합산값이 높아도 strict interval 실패를 뒤집지 못했다.

## Daily tape와 entry-phase exit

```text
daily signal tape rows: 1005
holding signal points: 40
cooldown signal points: 12
entry_interval_break exits: 12
```

Best entry candidate의 12개 거래는 모두 `entry_interval_break`로 다음 거래일 open 청산 경로를 실제 통과했다.

## 경로 분리

Entry wrapper 실행 결과:

```text
trade_count: 12
exit reasons: entry_interval_break=12
```

같은 candidate를 원본 `run_backtest_period()`로 실행한 결과:

```text
trade_count: 8
exit reasons:
stop_loss=2
time_out=2
trailing=4
```

원본 경로에서는 `entry_interval_break`, `entry_provisional_atr_stop`, `entry_provisional_max_holding`이 0건이었다. Qualify/entry provisional 경로와 기존 exit/validate 14-field 경로가 runtime에서도 분리됐다.

## 검증 항목

```text
domain_loaded: PASS
domain_matches_report: PASS
population_size_within_limit: PASS
generations_within_limit: PASS
all_candidates_valid: PASS
entry_scope_schema_v2: PASS
strict_and_runtime_seen: PASS
high_quality_cannot_override_interval: PASS
daily_tape_populated: PASS
holding_path_populated: PASS
cooldown_path_measured: PASS
interval_break_triggered: PASS
entry_wrapper_semantics: PASS
original_path_not_entry_exit: PASS
```

## 초기 중단 기록

첫 실행은 workspace 상대경로로 AAP snapshot을 찾지 못해 domain 로드 전에 즉시 중단됐다. GA는 시작되지 않았다. 경로를 저장소 루트 기준으로 수정한 뒤 동일 제한으로 재실행해 PASS했다.
