# Stage3 진입 시점 데이터 소재 확정

- 조사 기준일: 2026-07-11 KST
- 조사 방식: 코드·로그·상태 파일 read-only
- 최종 판정: **PARTIAL**
- 운영·라이브·원본 코드·설정·설계 변경: 0건

## 1. 결론

Stage3 진입 점수와 component는 실제로 계산된다.

계산 함수는 `engine/strategies/evaluator.py`의 `evaluate_signal()`이며 다음을 반환한다.

- `should_buy`
- `score`
- `raw_score`
- `threshold`
- `reasons`
- `market_adjustment`
- `components`

Live Stage3 경로의 `evaluate_candidate()`도 이를 버리지 않고 일단 모두 반환한다. 여기에 `ratio`, 현재 가격, ATR, 시장·섹터·VIX, 뉴스 감성 등이 추가된다.

하지만 이후 저장 계층에서 필드를 축약한다.

- `score/raw_score/threshold/ratio`는 현재 candidate state에 일부 남음
- Shadow는 `entry_score/threshold/ratio/reasons`만 남음
- 실제 주문 intent는 신호 필드를 남기지 않음
- `components`와 `market_adjustment`는 durable entry history에 남지 않음
- Stage3 canonical `exit_trades.jsonl`에는 entry signal 필드가 0개

따라서 판정은 `NO_ENTRY_SNAPSHOT_DISCARDED`가 아니라 **`PARTIAL`**이다.

점수 일부는 남지만, CE 검증에 핵심인 component별 기여도와 Top2 집중도는 과거 진입별로 재구성할 수 없다. 신규 append-only 관측 로깅이 필요하다.

## 2. Stage3 진입 평가 함수와 반환값 흐름

### 2.1 계산 함수

파일:

```text
engine/strategies/evaluator.py
```

함수:

```text
evaluate_signal()
```

주요 라인:

- `SignalResult` 정의: 19~27행
- MA component: 64~78행
- MACD component: 80~94행
- RSI component: 96~107행
- BB component: 109~122행
- Volume component: 124~129행
- News component: 131~140행
- Topic news component: 142~171행
- Raw score 합산: 173~175행
- Event component: 176~197행
- Crash bonus: 199~204행
- Market adjustment: 206~223행
- `should_buy`: 225행
- Full `SignalResult` 반환: 230~238행

Component dict에는 다음 키가 생성된다.

```text
ma_align
macd
rsi
bb
volume
news
news_topics
events
```

Crash bonus는 `raw_score`에는 더해지지만 별도 `components["crash_bonus"]` 키로 저장되지는 않는다. 따라서 full component dict가 남아도 crash bonus는 `raw_score - sum(components)`로만 식별 가능하다.

### 2.2 Live wrapper

파일:

```text
engine/live/elite_shadow_trader.py
```

함수:

```text
evaluate_candidate()
```

라인 423~432에서 `evaluate_signal()`을 호출한다.

라인 435~439에서:

- score
- threshold
- ratio
- reasons
- components

를 꺼낸다.

라인 440~449에서는 component를 `assess_shadow_entry_quality()`에도 전달한다.

라인 450~470에서 반환하는 evaluation dict에는 다음이 있다.

```text
should_buy
score
raw_score
threshold
ratio
reasons
components
market_score
sector_score
vix_level
news_sentiment
```

단, `market_adjustment`는 `SignalResult`에 존재하지만 `evaluate_candidate()` 반환 dict에는 복사되지 않는다. 이 지점에서 live wrapper 기준으로 먼저 소실된다.

## 3. Live candidate state에서 남는 것

### 3.1 `live_candidate_slots`

`data/_system/ops/live_candidate_slots.py`의 `refresh_slots()`는 402~418행에서 `evaluate_candidate()`를 호출한다.

`should_buy=true`이면 `public_candidate_row()`로 넘긴다.

`public_candidate_row()` 277~315행은 다음 필드만 선택한다.

```text
final_score
raw_score
threshold
ratio
price
atr
market_score
sector_score
vix_level
reasons
```

여기서 제외되는 필드:

```text
components
market_adjustment
news_sentiment
topic features
event flags
```

라인 451~466에서 축약된 candidate pool을 `live_slots_state.json`에 저장한다.

즉 현재 candidate의 score·threshold·ratio는 남지만 component는 이 projection에서 버려진다.

또한 `live_slots_state.json`은 매 refresh 때 갱신되는 현재 상태다. 진입 건별 append-only history가 아니다.

### 3.2 First-seen 기록

`refresh_slots()` 428~439행은 최초 신호에 대해 다음만 남긴다.

```text
first_signal_at
first_signal_price
first_final_score
last_final_score
```

Threshold, ratio, raw score, components는 first-seen history에 없다.

## 4. 실제 주문 경로

### 4.1 실행 직전 재평가

`engine/live/s2_auto_trader.py`의 `_validate_candidate_signal()` 313~329행은 현재 후보를 다시 찾아 `evaluate_candidate()`를 재호출한다.

따라서 실행 직전 메모리에는 다시 full component가 존재한다.

그러나 `compute_order_plan()` 406~415행에서 저장하는 `evaluation_summary`는 다음만 선택한다.

```text
score
threshold
ratio
price
atr
should_buy
```

다음은 버려진다.

```text
components
raw_score
market_adjustment
reasons
news_sentiment
```

### 4.2 주문 intent

`submit_plan()` 445~458행의 intent에는 다음이 저장된다.

```text
candidate_id
ticker
shares
price
notional
entry_timing
execution_session
selected_rulebook
preflight_atr
```

신호 score·threshold·ratio·component는 저장하지 않는다.

`pending_orders.json`에도 rulebook의 설정 임계인 `signal_threshold`는 있을 수 있지만, 이는 그 진입 시점 realized score snapshot이 아니다.

따라서 실제 주문과 연결 가능한 entry component history는 없다.

## 5. Shadow 경로

`elite_shadow_trader.run_shadow_tick()` 803행에서 `evaluate_candidate()`를 호출하고, 808행에서 `should_buy`를 확인한다.

진입 시 `_open_position()`은 다음만 저장한다.

```text
entry_score
entry_threshold
entry_ratio
entry_reasons
```

위치는 500~505행이다.

저장하지 않는 필드:

```text
components
raw_score
market_adjustment
news_sentiment
```

따라서 `elite_shadow_trades.jsonl`에서는 ratio와 outcome을 연결할 수 있지만 Top2 집중도는 계산할 수 없다.

## 6. Stage3 `exit_trades.jsonl` 전수 스키마 재검증

269개 파일, **975,118행**을 전수 스캔했다.

실제 unique field는 22개다.

```text
breakeven_enabled
breakeven_trigger_profit_pct
entry_date
entry_price
entry_rulebook_hash
exit_date
exit_price
exit_rank
exit_reason
final_rulebook_hash
holding_days
max_holding_days
max_loss_during_hold
max_profit_during_hold
period_label
pnl_pct
sell_omen_enabled
stop_loss_atr
stop_loss_atr_bear
stop_price_at_entry
target_price_at_entry
trailing_stop_at_entry
```

다음 필드는 모두 0건이다.

```text
entry_signal_score
entry_signal_raw_score
entry_signal_threshold
entry_market_adjustment
entry_signal_components
entry_news_sentiment
entry_topic_features
entry_market_score
entry_sector_score
entry_vix_level
entry_event_flags
ratio
```

즉 앞선 커밋 `9162227`의 “Stage3 canonical entry snapshot 0건” 판정은 재확인됐다.

## 7. 왜 Stage2는 남고 Stage3는 사라지는가

Stage2와 Stage3는 upstream 평가 함수가 다르지 않다.

`engine/learning/backtest.py`는 둘 모두에 대해 진입 시점 full snapshot을 trade dict에 추가한다.

- `_signal_full_snapshot()`: 175~184행
- `_signal_snapshot()`: 405~417행
- trade update: 636~661행

이 단계의 trade dict에는 다음이 있다.

```text
entry_signal_score
entry_signal_raw_score
entry_signal_threshold
entry_market_adjustment
entry_signal_components
entry_news_sentiment
entry_topic_features
entry_market_score
entry_sector_score
entry_vix_level
entry_event_flags
```

Stage2 `trades.jsonl`은 이 enriched trade dict를 유지한다.

전수 결과:

- Stage2 files: 325
- Stage2 rows: 2,439,619
- 위 entry signal 필드 존재 확인

반면 Stage3 writer는 full trade dict를 `_compact_exit_trade()`로 축약한다.

실제 실행 파일 `run_stage3_aggressive.py`는 wrapper이며, 원본 모듈:

```text
run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001
```

을 로드한다.

원본의 `EXIT_TRADE_OUTPUT_FIELDS` 919~942행에는 22개 outcome·exit 필드만 있고 entry signal 필드는 없다.

`_compact_exit_trade()` 1038~1063행은 이 목록만 복사한다.

`exit_trades.jsonl` write는 1344행이다.

따라서 Stage3도 메모리상 full entry snapshot은 생성됐지만, 최종 compact projection에서 의도적으로 누락된다.

차이는 평가가 아니라 **마지막 persistence projection**이다.

## 8. Stage3 별도 진입 로그 탐색 결과

확인한 주요 소스:

- `live_slots_state.json`
- `live_slots_events.jsonl`
- `pending_orders.json`
- `live_auto_events.jsonl`
- `elite_shadow_state.json`
- `elite_shadow_trades.jsonl`
- `real_dashboard_buy_candidates.json`
- Stage3 `exit_trades.jsonl`
- Stage2 `trades.jsonl`
- S2 auto intent/state 경로

결과:

- 현재 point full component: real dashboard snapshot에 존재
- 현재 candidate score/ratio: live slots state에 존재
- Shadow historical ratio/outcome: 존재
- 실제 entry별 component history: 없음
- Stage3 canonical component history: 없음
- Stage3 전용 append-only entry log: 발견되지 않음

## 9. 최종 판정

### `PARTIAL`

남는 것:

- Live current state: score, raw score, threshold, ratio
- Execution plan: score, threshold, ratio
- Shadow history: entry score, threshold, ratio, reasons
- Stage3 exits: outcome, MAE, MFE, entry/exit price/date

버려지는 것:

- Entry-time component dict
- Entry-time market adjustment
- Entry-time news/topic/event decomposition
- 실제 주문과 연결되는 full signal snapshot
- Stage3 canonical entry score·component 전체

기존 데이터만으로 ratio 검증은 일부 가능하지만, historical Top2 집중도와 CE형 component 검증은 불가능하다.

## 10. 로깅이 필요한 정확한 지점

### Primary live observation 지점

```text
data/_system/ops/live_candidate_slots.py
refresh_slots()
```

`evaluate_candidate()`가 성공한 직후, `public_candidate_row()`로 축약하기 전인 408~418행 구간이 가장 이르며 손실 없는 지점이다.

여기서는 buy·non-buy를 모두 관측할 수 있다.

### 실제 주문 결정 지점

```text
engine/live/s2_auto_trader.py
_validate_candidate_signal()
```

322행의 `evaluate_candidate()` 직후가 실제 주문 직전 snapshot 지점이다.

`should_buy=true`가 확인된 decision에 append하고 order/client ID와 연결해야 한다.

### Shadow 진입 지점

```text
engine/live/elite_shadow_trader.py
_open_position()
```

`ev`가 full component를 가진 상태에서 position dict를 만들기 전이 적합하다.

### Historical Stage3 backtest 지점

Canonical Stage3 이력에도 보존하려면:

```text
EXIT_TRADE_OUTPUT_FIELDS
_compact_exit_trade()
```

에서 existing `entry_*` 필드를 projection에 포함해야 한다.

이번 조사는 구현하지 않았다.

## 11. 최소 관측 필드

```text
timestamp
candidate_id
stage
ticker
rulebook_hash
should_buy
score
raw_score
threshold
ratio
market_adjustment
components
market_score
sector_score
vix_level
news_sentiment
event_flags
decision_id_or_position_id
later_outcome_join_key
```

Crash bonus를 명확히 분석하려면 별도 component 필드로 저장하거나 `raw_score - sum(components)`를 함께 기록하는 것이 필요하다.

## 12. 구현 필요 여부

**신규 관측 로깅 필요**다.

기존 data는 `ENTRY_SNAPSHOT_EXISTS_ELSEWHERE` 수준이 아니다.

현재 point snapshot은 존재하지만 반복 진입 이력이 아니며, Shadow와 주문 history는 component를 버린다. 따라서 별도 로깅 없이 Stage3 CE의 Top2·threshold 조합을 historical outcome과 연결할 수 없다.

## 13. 산출물

- `stage3_entry_snapshot_flow.csv`
- `stage3_entry_snapshot_schema_fields.csv`
- `stage3_entry_snapshot_state_log_fields.csv`
- `stage3_entry_snapshot_persistence_matrix.csv`
- `stage2_stage3_entry_snapshot_comparison.csv`
- `stage3_entry_snapshot_storage_summary.json`
- `stage3_entry_snapshot_storage_readout.md`
- `run_stage3_entry_snapshot_storage_audit.py`
