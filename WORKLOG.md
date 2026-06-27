# WORKLOG

- 2026-06-28 `d2ac64c` — Group A: 중앙 매수 선택 entity rulebook이 신호평가·주문 집행·position snapshot까지 전달되도록 수정하고, live universe 기본 ID를 날짜 기반 Stage2 실전 ID로 분리하면서 legacy LR8D stage1 16개를 격리.
- 2026-06-28 `8b0c9cb` — feat/next-open-buy-timing: D-1 종가 선별 → D open 집행용 scheduled open buy queue, 거래일 helper, run_live next_open wiring, 큐/타이밍 테스트 추가.
- 2026-06-28 `af00c08` — feat/next-open-buy-timing: next_open 평가에서 fresh get_market_context 호출을 제거하고 get_market_history + 백테스트 _lookup_signal_context 기반 D-1 point-in-time market/news context로 고정.
- 2026-06-28 `d6f7b54` — feat/next-open-buy-timing: pending BUY 정산 경로에 선택 entity rulebook/ATR/context를 영속 저장·복원하고 broker open order 가드를 next_open flat guard에 추가.
