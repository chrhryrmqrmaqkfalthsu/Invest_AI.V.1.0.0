# direct Event 전수 grep 확인

## 검색 범위

Git 추적 Python 파일 전체에 대해 다음 문자열을 전수 검색했다.

```text
event_flags
active_events
use_event_block
evaluate_signal(
has_war / has_rate_hike / has_rate_cut / has_geopolitical / has_tariff /
has_export_ban / has_earnings_shock / has_oil_surge /
has_banking_crisis / has_inflation / has_fed_statement
```

검색 건수:

| 검색어 | 참조 수 |
|---|---:|
| `event_flags` | 90 |
| `active_events` | 110 |
| `use_event_block` | 13 |
| `evaluate_signal(` | 32 |

## `active_events → has_*` exact 변환 파일

전체 저장소에서 현재 Event key를 `has_*` flag로 직접 만드는 파일은 7개다.

### 라이브·가상 런타임

1. `engine/live/central_control.py:582-597`
2. `engine/strategies/learned_rulebook.py:281-296`
3. `engine/live/elite_shadow_trader.py:375-390`

### 역사 데이터 생성·연구 전용

4. `scripts/build_market_history_v2.py:138-149`
5. `scripts/news_downloader/dry_run_append_market_history_v2.py:194-205`
6. `scripts/news_downloader/run_append_market_history_v2.py:286-297`
7. `scripts/research/_exp_event_decay_context.py:217-229`

역사 데이터 생성·연구 전용 4개는 실전·페이퍼·가상 후보의 현재 `MarketContext`를 평가하지 않는다. 따라서 라이브 direct Event 일괄 OFF 대상은 앞의 3개 런타임 변환 지점이다.

## 라이브 디렉터리의 직접 `evaluate_signal()` 호출 파일

- `engine/live/central_control.py`
- `engine/live/elite_pullback_replay.py`
- `engine/live/elite_shadow_trader.py`
- `engine/live/elite_signal_history.py`
- `engine/live/scheduled_open_buy_queue.py`
- `engine/strategies/learned_rulebook.py`

`elite_strategy_sim`, `live_candidate_slots`, dashboard exporter, S2 auto trader는 `evaluate_signal()`을 직접 호출하지 않고 `elite_shadow_trader.evaluate_candidate()`를 재사용한다.

## 추가 확인된 평가 경로

앞 조사에서 명시된 central, 일반 runner, 추가매수 재평가, Telegram, elite shadow 외에 다음 경로가 추가 확인됐다.

- `data/_system/ops/live_candidate_slots.py` — 운영 후보 슬롯 생성
- `scripts/export_real_dashboard_buy_candidates.py` — 실거래 대시보드 후보 재검증
- `engine/live/s2_auto_trader.py` — dry-run/실주문 계획 직전 재검증
- `engine/live/elite_strategy_sim.py` — 가상 전략 시뮬레이션
- `engine/live/elite_pullback_replay.py` — 가상 pullback replay
- `engine/live/elite_signal_history.py` — 현재 MarketContext를 쓰는 신호 히스토리
- `engine/central/signal_collector.py` — 중앙 백테스트 수집기. 기본 Event OFF이며 라이브 current-context 경로는 아님

## 누락 방지 결론

현재 `active_events`에서 direct Event flag를 생성하는 라이브 코드는 3곳으로 폐쇄적으로 수렴한다.

```text
central_control.py
learned_rulebook.py
elite_shadow_trader.py
```

이 3곳을 하나의 공통 helper로 치환하면, 이 helper를 재사용하는 실전·페이퍼·가상 호출 경로 전체를 한 스위치로 제어할 수 있다.
