# Event decay experiment validation
## TTL table
- 금리정책_인상: 10 trading days
- 금리정책_인하: 10 trading days
- 인플레이션: 10 trading days
- 연준발언: 3 trading days
- default: 5 trading days

Decay: weight = max(0, (TTL - elapsed_trading_days) / TTL).

## 6/11~6/13 replay
| timestamp | old events | old adj | decay events | decay adj | decay meta summary | note |
|---|---:|---:|---:|---:|---|---|
| 2026-06-11T19:58:47 | 인플레이션;금리정책_인상 | -9.9 | 인플레이션;금리정책_인상 | -9.9 | 금리정책_인상:impact=-2.94,elapsed=0,w=1.0,ttl=10; 인플레이션:impact=-7.0,elapsed=0,w=1.0,ttl=10 | observed: 후보 2건, event_adj=-9.90 |
| 2026-06-12T00:58:44 | 인플레이션;금리정책_인상 | -9.9 | 인플레이션;금리정책_인상 | -9.9 | 금리정책_인상:impact=-2.94,elapsed=0,w=1.0,ttl=10; 인플레이션:impact=-7.0,elapsed=0,w=1.0,ttl=10 | observed: 캐시 히트 2건, event_adj=-9.90 |
| 2026-06-12T01:58:44 | 지정학_긴장 | -2.5 | 인플레이션;금리정책_인상;지정학_긴장 | -12.4 | 금리정책_인상:impact=-2.94,elapsed=0,w=1.0,ttl=10; 인플레이션:impact=-7.0,elapsed=0,w=1.0,ttl=10; 지정학_긴장:impact=-2.5,elapsed=0,w=1.0,ttl=5 | observed: 후보 1건, event_adj=-2.50 |
| 2026-06-12T13:58:44 | {} | 0.0 | 인플레이션;금리정책_인상;지정학_긴장 | -12.4 | 금리정책_인상:impact=-2.94,elapsed=0,w=1.0,ttl=10; 인플레이션:impact=-7.0,elapsed=0,w=1.0,ttl=10; 지정학_긴장:impact=-2.5,elapsed=0,w=1.0,ttl=5 | observed: 키워드 매칭 후보 없음 |
| 2026-06-12T15:58:41 | {} | 0.0 | 인플레이션;금리정책_인상;지정학_긴장 | -12.4 | 금리정책_인상:impact=-2.94,elapsed=0,w=1.0,ttl=10; 인플레이션:impact=-7.0,elapsed=0,w=1.0,ttl=10; 지정학_긴장:impact=-2.5,elapsed=0,w=1.0,ttl=5 | observed: market_state active_events={} |
| 2026-06-13T15:58:41 | {} | 0.0 | 인플레이션;금리정책_인상;지정학_긴장 | -12.4 | 금리정책_인상:impact=-2.94,elapsed=0,w=1.0,ttl=10; 인플레이션:impact=-7.0,elapsed=0,w=1.0,ttl=10; 지정학_긴장:impact=-2.5,elapsed=0,w=1.0,ttl=5 | what-if: 신규 후보 없음, 토요일 |
| 2026-06-25T15:58:41 | {} | 0.0 | 인플레이션;금리정책_인상 | -1.0 | 금리정책_인상:impact=-0.29,elapsed=9,w=0.1,ttl=10; 인플레이션:impact=-0.7,elapsed=9,w=0.1,ttl=10 | what-if: TTL 직전 0.1 가중 확인 |
| 2026-06-26T15:58:41 | {} | 0.0 | {} | 0 | {} | what-if: TTL 도달 정상 소멸 확인 |

## Over-preservation samples
| scenario | calendar day | active | elapsed trading days | weight | event_adj |
|---|---:|---:|---:|---:|---:|
| 약한_금리_인상_-2.5 | 0 | True | 0 | 1.0 | -2.5 |
| 약한_금리_인상_-2.5 | 1 | True | 1 | 0.9 | -2.2 |
| 약한_금리_인상_-2.5 | 2 | True | 1 | 0.9 | -2.2 |
| 약한_금리_인상_-2.5 | 3 | True | 1 | 0.9 | -2.2 |
| 약한_금리_인상_-2.5 | 5 | True | 3 | 0.7 | -1.8 |
| 약한_금리_인상_-2.5 | 7 | True | 5 | 0.5 | -1.2 |
| 약한_금리_인상_-2.5 | 10 | True | 6 | 0.4 | -1.0 |
| 약한_금리_인상_-2.5 | 14 | False | expired | 0.0 | 0 |
| 약한_금리_인상_-2.5 | 15 | False | expired | 0.0 | 0 |
| 중간_금리_인상_-5.0 | 0 | True | 0 | 1.0 | -5.0 |
| 중간_금리_인상_-5.0 | 1 | True | 1 | 0.9 | -4.5 |
| 중간_금리_인상_-5.0 | 2 | True | 1 | 0.9 | -4.5 |
| 중간_금리_인상_-5.0 | 3 | True | 1 | 0.9 | -4.5 |
| 중간_금리_인상_-5.0 | 5 | True | 3 | 0.7 | -3.5 |
| 중간_금리_인상_-5.0 | 7 | True | 5 | 0.5 | -2.5 |
| 중간_금리_인상_-5.0 | 10 | True | 6 | 0.4 | -2.0 |
| 중간_금리_인상_-5.0 | 14 | False | expired | 0.0 | 0 |
| 중간_금리_인상_-5.0 | 15 | False | expired | 0.0 | 0 |
| 강한_금리_인상_-7.0 | 0 | True | 0 | 1.0 | -7.0 |
| 강한_금리_인상_-7.0 | 1 | True | 1 | 0.9 | -6.3 |
| 강한_금리_인상_-7.0 | 2 | True | 1 | 0.9 | -6.3 |
| 강한_금리_인상_-7.0 | 3 | True | 1 | 0.9 | -6.3 |
| 강한_금리_인상_-7.0 | 5 | True | 3 | 0.7 | -4.9 |
| 강한_금리_인상_-7.0 | 7 | True | 5 | 0.5 | -3.5 |
| 강한_금리_인상_-7.0 | 10 | True | 6 | 0.4 | -2.8 |
| 강한_금리_인상_-7.0 | 14 | False | expired | 0.0 | 0 |
| 강한_금리_인상_-7.0 | 15 | False | expired | 0.0 | 0 |
| 연준발언_-5.0 | 0 | True | 0 | 1.0 | -5.0 |
| 연준발언_-5.0 | 1 | True | 1 | 0.6667 | -3.3 |
| 연준발언_-5.0 | 2 | True | 1 | 0.6667 | -3.3 |
| 연준발언_-5.0 | 3 | True | 1 | 0.6667 | -3.3 |
| 연준발언_-5.0 | 5 | False | expired | 0.0 | 0 |
| 연준발언_-5.0 | 7 | False | expired | 0.0 | 0 |
| 연준발언_-5.0 | 10 | False | expired | 0.0 | 0 |
| 연준발언_-5.0 | 14 | False | expired | 0.0 | 0 |
| 연준발언_-5.0 | 15 | False | expired | 0.0 | 0 |
| 지정학_기본_-5.0 | 0 | True | 0 | 1.0 | -5.0 |
| 지정학_기본_-5.0 | 1 | True | 1 | 0.8 | -4.0 |
| 지정학_기본_-5.0 | 2 | True | 1 | 0.8 | -4.0 |
| 지정학_기본_-5.0 | 3 | True | 1 | 0.8 | -4.0 |
| 지정학_기본_-5.0 | 5 | True | 3 | 0.4 | -2.0 |
| 지정학_기본_-5.0 | 7 | False | expired | 0.0 | 0 |
| 지정학_기본_-5.0 | 10 | False | expired | 0.0 | 0 |
| 지정학_기본_-5.0 | 14 | False | expired | 0.0 | 0 |
| 지정학_기본_-5.0 | 15 | False | expired | 0.0 | 0 |

## Reader compatibility
- ok: True
- flags: `{"has_fed_statement": 0, "has_geopolitical": 1, "has_inflation": 1, "has_rate_cut": 0, "has_rate_hike": 1, "has_regulation_risk": 0, "has_supply_chain": 0, "has_trade_conflict": 0, "has_war": 0}`

## State consistency
- 2026-06-11T19:58:47: active=True, event_adj=-9.9, ok=True
- 2026-06-12T00:58:44: active=True, event_adj=-9.9, ok=True
- 2026-06-12T01:58:44: active=True, event_adj=-12.4, ok=True
- 2026-06-12T13:58:44: active=True, event_adj=-12.4, ok=True
- 2026-06-12T15:58:41: active=True, event_adj=-12.4, ok=True
- 2026-06-13T15:58:41: active=True, event_adj=-12.4, ok=True
- 2026-06-25T15:58:41: active=True, event_adj=-1.0, ok=True
- 2026-06-26T15:58:41: active=False, event_adj=0.0, ok=True

## Calendar note
- 2026-06-11 19:58 -> 2026-06-12 15:58 elapsed=1, method=weekday_fallback. market_history.csv ends at 2026-06-11, so 6/12+ replay uses weekday fallback.
