# 실전 거래 결과의 후보 선별 되먹임 확인 readout

범위: 코드·데이터·설정 변경 없이 로컬 파일과 코드 경로를 읽어 추적했다. Alpaca 원격 API는 호출하지 않았고, 주문도 실행하지 않았다. 원격 Alpaca 주문 이력에만 존재할 수 있는 값은 `UNKNOWN`으로 표기한다.

## 판정 요약

- 실전 결과 되먹임 판정: **ABSENT**
- 이유: 현재 후보 선별 경로(`build_elite_shadow_report` → `live_candidate_slots.refresh_slots` → `real_dashboard_buy_candidates export`)는 백테스트/학습 산출물과 현재 신호 평가를 사용하지만, `alpaca_live` 실전 체결·청산 결과를 후보 제외/감점/정렬에 사용하지 않는다.
- CE 재등장 원인: CE `stage3:CE:998b0b638c66`는 현재도 `gate_keep=True`이고 `evaluate_signal.should_buy=True`라 26개 후보에 남았다. 실전 손실/손절 기록이 로컬 selector 입력으로 들어가지 않기 때문에 후보 재등장에 영향을 주지 못한다.

## 1. 실전 체결/청산 결과 저장 위치

| 구분 | 경로/소스 | 성격 | 백테스트와 구분 여부 | 후보 선별 입력 여부 |
| --- | --- | --- | --- | --- |
| 대시보드 실전 매수 intent | `data/_system/real_dashboard_manual_buy_intent.json` | 실전 후보 매수 요청/주문 제출 스냅샷 | 구분됨. `execution_mode=direct_alpaca_live_market_order`, `candidate_snapshot` 포함 | 사용 안 함 |
| Alpaca 예약 청산 주문 상태 | `data/_system/real_dashboard_alpaca_exit_orders.json` | 실전 OCO/stop/limit 예약 주문 상태 | 구분됨. `mode=alpaca_reserved_exit_order`, `account_source=alpaca_live` | 사용 안 함 |
| 로컬 실전 대시보드 거래내역 | `data/_system/real_dashboard_trades_history.json` | 로컬 실전 거래내역 파일 | 구분됨. `account_source=alpaca_live` | 사용 안 함 |
| Alpaca 원격 주문 이력 API | `engine/live/real_dashboard_alpaca_history_patch.py:51-95`, `98-188`, `242-272` | `/api/real/trades_history` 호출 시 Alpaca filled orders를 조회해 FIFO로 거래내역 생성 | 구분됨. `source=alpaca_live_orders...` | 사용 안 함. 런타임 API 응답이며 selector 파일 입력이 아님 |
| 중앙/레거시 실전 포지션 청산 로그 | `data/_system/trade_log.csv`; 작성 코드 `engine/live/position_manager.py:774-792`, `833-852`, `883-891` | PositionManager가 체결 확인 후 append | 실전 청산 로그. 백테스트와 별도 | 후보 선별에는 사용 안 함 |
| 종목별 레거시 실전 거래 CSV | `data/symbols/<ticker>/trades_live.csv`; 코드 `engine/storage/repository.py:43-44`, `272-289` | `append_live_trade/read_live_trades` 레거시 저장 | 백테스트와 별도 | 후보 선별에는 사용 안 함 |
| 백테스트/학습 exit trades | `exp_batch_stage123_2009_20260616_full/tickers/<TICKER>/stage3/exit_trades.jsonl` 등 | 학습/백테스트 산출물 | 실전과 구분됨. 예: stage3는 `final_rulebook_hash` 기준 | 과거 성능/학습 산출물로 간접 사용. 실전 결과는 아님 |

### 실전과 백테스트가 섞이는가?

저장 위치 기준으로는 섞이지 않는다. `exit_trades.jsonl`은 학습/백테스트 산출물이고, 실전 대시보드/Alpaca 기록은 `real_dashboard_*`, `trade_log.csv`, Alpaca API 응답 쪽이다. 다만 대시보드 화면용 enrichment에서 실전 보유 종목에 `live_slots_state`/buy intent의 후보 스냅샷을 붙여 보여주는 경로는 있다(`engine/live/real_dashboard_api.py:373-420`). 이것은 화면 context 결합이지 후보 선별 되먹임은 아니다.

## 2. 후보 선별 경로가 실전 결과를 읽는지

| 단계 | 코드 위치 | 읽는 입력 | 실전 결과 참조 여부 |
| --- | --- | --- | --- |
| 전체 룰 풀 → elite report | `engine/live/elite_shadow_report.py:456-460` | `collect_stage2_elite`, `collect_stage3_elite` 결과 | 없음 |
| Stage2 선별 | `engine/live/elite_shadow_report.py:221-279` | `central_index.jsonl`, survivor source row, 학습 metrics | 없음 |
| Stage3 선별 | `engine/live/elite_shadow_report.py:282-368` | `final_rulebooks.jsonl`의 `bull_metrics/stress_metrics`, rulebook | 없음 |
| 93개 컷 | `data/_system/ops/live_candidate_slots.py:45`, `346`, `381-382` | `build_elite_shadow_report(... include_trades=False)` 결과 상위 93개 | 없음 |
| 26개 후보 pool | `data/_system/ops/live_candidate_slots.py:392-418`, `451-452` | gate map, held exclusions, 현재 `evaluate_candidate`/`should_buy` | 없음. `trade_log.csv`, `real_dashboard_trades_history.json`, Alpaca orders 미참조 |
| 8개 슬롯 | `data/_system/ops/live_candidate_slots.py:322-340` | candidate_pool 정렬 결과 | 없음 |
| orderable export | `scripts/export_real_dashboard_buy_candidates.py:434-505`, `510-538` | live state source row, full rulebook, 현재 should_buy 재검증 | 없음 |

관련 검색에서도 `build_elite_shadow_report`, `live_candidate_slots.py`, `export_real_dashboard_buy_candidates.py`는 `real_dashboard_trades_history`, `trade_log.csv`, `real_dashboard_manual_buy_intent`, Alpaca order history를 후보 제외/정렬 입력으로 읽지 않는다.

## 3. CE 구체 추적

대상: `stage3:CE:998b0b638c66`, full rule hash `998b0b638c6649fe10d3dff0fc74f890d9891ef9caa7ed61a2bae1e340288b78`

### 현재 후보 상태

- `live_slots_state.candidate_pool` 내 순위: 25위
- ticker: `CE`
- final_score: `2.67876715278174`
- threshold: `2.6541866643896674`
- ratio: `1.0092610247507685`
- gate_status/gate_keep: `KEEP / true`
- `real_dashboard_buy_candidates.json`에도 존재하며 status는 `pending`, `candidate_source=real_dashboard_buy_candidates_export`, `full_rulebook_verified=true`, `should_buy_verified=true`

### 7/8 매수 관련 로컬 기록

`data/_system/real_dashboard_manual_buy_intent.json`에 CE intent가 있다.

- intent_id: `real-buy:stage3:CE:998b0b638c66`
- created_at: `2026-07-08T14:27:15.330072+00:00`
- submitted_at: `2026-07-08T14:27:15.592260+00:00`
- execution_mode: `direct_alpaca_live_market_order`
- broker_order.order_id: `422afeab-0fdb-41ce-8e85-83df4f5a0a60`
- broker_order.status/raw_status: `pending / pending_new`
- broker_order.filled_shares: `0.0`
- broker_order.filled_avg_price: `0.0`

주의: 이 로컬 intent 파일만 보면 “주문 제출”은 확인되지만 “체결 완료”는 확인되지 않는다. 실제 Alpaca 원격에서 나중에 체결되었을 가능성은 로컬 파일만으로는 `UNKNOWN`이다.

### 7/8~7/9 청산/손절 관련 로컬 기록

`data/_system/real_dashboard_alpaca_exit_orders.json`에 CE OCO 예약 상태가 있다.

- ticker: `CE`
- mode: `alpaca_reserved_exit_order`
- order_kind: `oco`
- broker_order.status: `pending_new`
- limit_price: `50.03`
- stop leg stop_price: `46.14`
- stop leg status: `held`
- filled_qty: `0`
- fractional_exit_watch.last_price: `46.29`
- fractional_exit_watch.status: `active`

로컬에서 CE 손절 완료를 확인할 수 있는 파일은 발견되지 않았다.

- `data/_system/trade_log.csv`: CE 없음
- `data/_system/real_dashboard_trades_history.json`: CE 없음
- `data/_system/positions.json`: CE 없음
- `data/_system/manual_sell_intent.json`: CE 없음

따라서 “7/9 실전 손절 완료”는 로컬 저장 파일 기준으로는 확인 불가다. Alpaca 원격 주문 이력에서만 확인 가능한 상태라면 `/api/real/trades_history`의 런타임 조회 대상이지만, 그 결과는 현재 후보 선별 입력으로 저장/사용되지 않는다.

### CE가 다시 26개에 뜬 이유

1. CE의 후보 생성은 실전 결과가 아니라 `exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl`의 룰/metrics와 현재 신호 평가에서 온다.
2. `live_candidate_slots.py`는 93개 후보에서 CE를 평가할 때 gate map과 현재 `should_buy`만 본다. 실전 CE 손실/손절 이력 파일을 읽지 않는다.
3. CE는 현재 `gate_keep=True`이고 `final_score >= threshold`라서 26개 candidate_pool에 남았다.
4. 실전 손실이 있었더라도 그 결과가 deny-list, penalty, gate, score adjustment로 연결된 코드 경로가 없다.

## 4. 되먹임 존재 판정

**ABSENT**

판정 근거:

- 실전 주문/체결 관련 로컬 파일은 `real_dashboard_manual_buy_intent.json`, `real_dashboard_alpaca_exit_orders.json`, `real_dashboard_trades_history.json`, `trade_log.csv` 등에 분산되어 있다.
- 후보 선별 실행 경로는 이 파일들을 읽지 않는다.
- Alpaca 원격 filled orders 조회 코드는 대시보드 API 응답용이며(`real_dashboard_alpaca_history_patch.py:242-272`), selector의 input artifact가 아니다.
- `exit_trades.jsonl`은 백테스트/학습 산출물이며 실전 결과 저장소가 아니다.

## 5. 실전 결과 기반 deny-list 부착 지점 후보

구현하지 않았다. 위치 후보만 제시한다.

### 1순위: `data/_system/ops/live_candidate_slots.py`의 93 → 26 후보 pool 게이트

추천 위치: `refresh_slots()` 안의 후보 loop, `cid = position_key(candidate)` 직후 또는 `gate_keep` 확인 직후.

현재 위치:

- `data/_system/ops/live_candidate_slots.py:389-418`

붙일 이유:

- 여기가 실제 26개 후보로 좁히는 핵심 지점이다.
- 여기에서 실전 손절/손실 기반 deny-list를 확인하면 slots, waitlist, export 후보 모두 자연스럽게 영향을 받는다.
- deny key는 `candidate_id`와 full `rulebook_hash` 둘 다 지원해야 한다. 같은 룰이 candidate_id 포맷 변경으로 우회되는 것을 막기 위해서다.

### 2순위: `scripts/export_real_dashboard_buy_candidates.py`의 export 재검증 loop

추천 위치: `for live_row in source_rows:` 이후 full_candidate 매칭 직후 또는 `evaluate_candidate` 전.

현재 위치:

- `scripts/export_real_dashboard_buy_candidates.py:468-505`

붙일 이유:

- live slots에는 보이더라도 orderable JSON에는 못 들어가게 하는 마지막 안전망이다.
- 단독으로만 붙이면 UI 후보에는 남을 수 있으므로 1순위 위치보다 후순위다.

### 3순위: `engine/live/real_dashboard_api.py`의 `_real_candidate_state()` 또는 `_real_candidate_slots_payload()`

현재 위치:

- `_real_candidate_state`: `engine/live/real_dashboard_api.py:633-673`
- `_real_candidate_slots_payload`: `engine/live/real_dashboard_api.py:923-957`

붙일 이유:

- 대시보드 표시/수동 매수 버튼 차단용 보조 안전장치다.
- 단, 근본 선별 pool 자체는 그대로라서 주 게이트로는 부적합하다.

### 붙이지 않는 쪽이 나은 위치

`engine/live/elite_shadow_report.py`는 학습/백테스트 산출물 기반 elite report 생성기이므로 실전 결과 deny-list를 직접 섞기보다는 live gate 단계에서 분리하는 편이 낫다. 실전 손실 기반 deny-list는 백테스트 곡선 필터가 아니라 운영 리스크 필터이기 때문이다.

## 6. deny-list 입력 파일 후보

구현하지 않았지만, 위치 후보는 다음과 같다.

- `data/_system/live_real_denylist.json`
- 키 예시: `candidate_id`, `rulebook_hash`, `ticker`, `blocked_until`, `reason`, `source_trade_id`, `pnl_pct`, `exit_reason`, `created_at`
- source는 반드시 실전 결과 기반이어야 한다. 예: `alpaca_live_orders_matched_fifo`, `trade_log.csv`, `real_dashboard_manual_sell_intent`, `real_dashboard_trades_history.json`
- 백테스트 `exit_trades.jsonl` 기반 deny-list와 혼합하면 안 된다.

## 최종 결론

CE가 7/8 실전 매수·7/9 손절되었다는 사실이 Alpaca 원격에는 존재할 수 있으나, 로컬 selector 기준으로는 그 청산 결과가 후보 선별 입력에 없다. 현재 로컬에는 CE 매수 intent와 OCO 예약 상태만 있고, CE 손절 완료 거래 기록은 `trade_log.csv`/`real_dashboard_trades_history.json`에서 확인되지 않는다. 따라서 CE가 다시 후보 26개에 뜬 직접 원인은 **실전 손실 되먹임 경로 부재 + 현재 신호가 threshold를 넘음 + gate_keep 유지**다.
