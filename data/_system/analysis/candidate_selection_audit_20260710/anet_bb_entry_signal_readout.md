# ANET·BB 진입 시점 신호 구성 확인

- 조사 방식: 기존 로그·상태·snapshot read-only
- 최종 판정: **진입 시점 component 확인 불가 / 2026-07-10 point snapshot에서는 두 후보 모두 CE형 확인**
- 운영·라이브·원본 코드·설정 변경: 0건

## 1. 핵심 결론

실제 과거 진입 기록에서 확인 가능한 것은 `score·threshold·ratio`까지다. Technical component dict는 ANET·BB 모든 shadow 진입에서 저장되지 않았다.

실제 live 주문 intent와 pending order에도 score·threshold·ratio·component가 없다. 따라서 실제 주문 제출 순간의 Top2 집중도는 확인할 수 없다.

다만 2026-07-10 real-dashboard/live93 point snapshot에는 full component가 남아 있다. 이 시점에서는 두 후보 모두 RSI와 MA 두 지표만 양수이고 Top2가 전체 양수 기여의 100%를 차지하며 ratio도 1.25 미만이다.

## 2. 최초 shadow 신호

| 후보 | 최초 신호 KST | score | threshold | ratio | ratio<1.25 | component |
|---|---|---:|---:|---:|---|---|
| ANET | 2026-07-02T09:00:17.353337+09:00 | 3.025097 | 2.639090 | 1.146265 | YES | 저장 안 됨 |
| BB | 2026-07-01T22:41:02.197363+09:00 | 3.290903 | 2.791951 | 1.178711 | YES | 저장 안 됨 |

최초 두 신호 모두 임계를 넘겼지만 ratio는 1.25 미만이었다. 다만 component가 없으므로 이 진입들이 RSI+MA 몰빵이었는지, news/event 보너스를 포함했는지는 확인할 수 없다.

## 3. 전체 shadow 진입 ratio

| 후보 | shadow 진입 | ratio<1.25 | 비율 | ratio 범위 | component snapshot |
|---|---:|---:|---:|---:|---:|
| ANET | 6 | 5 | 83.33% | 1.146265~1.904102 | 0 |
| BB | 4 | 4 | 100.00% | 1.178711~1.178711 | 0 |

ANET은 6건 중 5건이 저ratio였고 한 건은 score가 5.025097로 올라 ratio 1.904102였다. 그 추가 2점의 component 출처는 로그에 없어 news/event/기타 보너스로 단정할 수 없다.

BB는 4건 모두 ratio 1.178711로 저ratio였다.

## 4. Full component가 있는 point snapshot

Snapshot 시각: `2026-07-10T10:01:09.313612+00:00` — 실제 진입 시각이 아니라 2026-07-10 재평가 시점이다.

| 항목 | ANET | BB |
|---|---:|---:|
| final score | 3.025097 | 3.290903 |
| raw score | 3.025097 | 3.290903 |
| threshold | 2.639090 | 2.791951 |
| ratio | 1.146265 | 1.178711 |
| 임계 초과율 | 14.63% | 17.87% |
| MA | 1.314850 | 1.540522 |
| MACD | 0.000000 | 0.000000 |
| RSI | 1.710248 | 1.750382 |
| BB | 0.000000 | 0.000000 |
| Volume | 0.000000 | 0.000000 |
| News | 0.000000 | 0.000000 |
| News topics | 0.000000 | 0.000000 |
| Events | 0.000000 | 0.000000 |
| 양수 component 수 | 2 | 2 |
| Top2 | rsi+ma_align | rsi+ma_align |
| Top2 집중도 | 100.00% | 100.00% |
| 진입 원천 | technical core only | technical core only |

이 point snapshot에서는 둘 다 news·event·폭락 보너스가 아니라 RSI+MA technical core만으로 진입 조건을 넘었다.

## 5. CE형 증상 판정

### 진입 시점

- ANET: ratio 저점유는 5/6건에서 확인되지만 component 집중도는 확인 불가 — `PARTIAL`
- BB: ratio 저점유는 4/4건에서 확인되지만 component 집중도는 확인 불가 — `PARTIAL`
- 실제 live 주문 제출 시점: ratio와 component 모두 없음 — `UNVERIFIABLE`

### 2026-07-10 point snapshot

- ANET: ratio 1.1463, Top2 100%, 양수 지표 2개 — `CE_LIKE_CONFIRMED_AT_POINT_SNAPSHOT`
- BB: ratio 1.1787, Top2 100%, 양수 지표 2개 — `CE_LIKE_CONFIRMED_AT_POINT_SNAPSHOT`

## 6. ANET과 BB 비교

두 후보의 full point snapshot 구성은 사실상 같다.

- 양수 technical 지표: 둘 다 RSI+MA 두 개
- Top2 집중도: 둘 다 100%
- news/event 보너스: 둘 다 0
- ratio: ANET 1.1463, BB 1.1787
- ANET이 임계에 약간 더 가까움: 임계 초과 14.63% 대 BB 17.87%

따라서 사용자가 제시한 ANET 상승 방향과 BB 하락 방향은 이 point snapshot의 몰빵·턱걸이 정도로 구분되지 않는다. 신호 구성은 매우 비슷한데 결과 방향이 갈린 사례다.

단, 이는 실제 각 진입 순간의 component 비교가 아니라 2026-07-10 동일 시점 snapshot 비교다.

## 7. 데이터 부재 항목

확인할 수 없는 항목:

- 모든 shadow entry의 component별 기여도
- 모든 shadow entry의 raw_score와 market adjustment 분해
- ANET ratio 1.9041 거래의 추가 2점 출처
- 2026-07-09 live 주문 제출 시점의 score·threshold·ratio
- 2026-07-09 live 주문 제출 시점의 Top2·news/event bonus

현재 point snapshot 값을 과거 진입 시점에 소급 적용하지 않았다.

## 8. 소스 요약

- `elite_shadow_trades.jsonl`: 역사 진입 ratio+outcome, component 없음
- `elite_strategy_sim_trades.jsonl`: ANET 1건 ratio+outcome, component 없음
- `elite_shadow_state.json`: OPEN/CLOSE 시각만
- `live_slots_events.jsonl`: live intent status·notional만
- `pending_orders.json`: market/sector/VIX context만
- `real_dashboard_buy_candidates.json`: 2026-07-10 full point component
- `live_slots_state.json`: point score·threshold·ratio, component 없음
- `live93_three_symptom_scan.csv`: full point component 파생 분석
- Stage3 canonical exit trades: outcome만 있고 entry signal 없음
- daily signal replay·live auto events·텍스트 로그: 해당 candidate 기록 없음

## 9. 산출물

- `anet_bb_signal_source_coverage.csv`
- `anet_bb_entry_signal_events.csv`
- `anet_bb_point_signal_components.csv`
- `anet_bb_signal_comparison.csv`
- `anet_bb_entry_signal_summary.json`
