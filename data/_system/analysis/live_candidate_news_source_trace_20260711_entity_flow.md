# 개체 관점 뉴스 취득 흐름

## A. intraday/live-slot central Stage3 개체

1. `scripts/run_live.py`가 시장 시간 job을 기본 60초 간격으로 실행한다.
2. `LiveCentralController`가 각 ticker의 현재가를 조회하고 각 entity rulebook을 평가한다.
3. `_evaluate_stage3_entity_signal()`이 `get_market_context()`를 호출한다.
4. `get_market_context()`는 60분 이내 `market_state.json`이 있으면 재사용하고, 없거나 만료되면 NewsAPI top-headlines를 새로 가져온다.
5. Event는 현재 NewsAPI 분류 결과와 이전 `market_state`의 TTL 잔존 Event를 합친 뒤 모든 entity에 공유된다.
6. News/NewsTopics는 해당 ticker의 로컬 Alpha Vantage 집계 CSV에서 D-1 이하, 최대 7일 이내 row를 찾는다. 평가 시 API 호출은 없다.
7. 두 값을 `evaluate_signal()`에 넣어 후보 score를 만든다.

## B. next-open D-1 개체

1. `prepare_if_due()`가 매분 실행된다.
2. 애프터마켓 이후에는 기본 60분마다 draft를 재선별하고, 개장 10분 전 final queue를 만든다.
3. 각 entity는 D-1 최종 OHLCV bar를 기준으로 평가된다.
4. 시장 값은 `market_history.csv`에서 D-1 lag lookup한다.
5. `use_llm_events=False`이므로 Event flag는 전부 0이다.
6. News/NewsTopics만 로컬 ticker sentiment CSV에서 D-1·최대 7일 조건으로 조회한다.

## CE 추적

확인된 CE 후보는 `stage3:CE:998b0b638c66`이며 최초 live candidate 시각은 `2026-07-07T22:22:21.577113+00:00`, 대시보드 직접 주문 snapshot은 `2026-07-08T14:27:15.330072+00:00`이다. snapshot의 Event 기여는 `+4.62260455`다.

현재 next-open 코드에서는 Event가 강제로 0이므로, 이 +4.62는 next-open D-1 point-in-time 평가 경로에서 생성된 값과 일치하지 않는다. 보존된 주문 source도 `live_slots_state_fallback_plus_broker_quote`와 대시보드 직접 주문으로 기록돼 있어, CE는 intraday/live-slot central Stage3 평가 결과를 사용한 것으로 코드·snapshot이 정합한다.

CE News/NewsTopics 소스는 `data/_system/ticker_sentiment/CE_daily.csv`다. 이 파일의 mtime은 `2026-06-02T19:44:06Z`, 마지막 데이터 row는 `2026-05-20`이다. CE signal date가 2026-07-07 부근이면 7일 max-age를 크게 초과하므로 lookup 결과는 News=0, NewsTopics={}가 된다. 주문 snapshot의 News=0, NewsTopics=0과 일치한다.

CE Event 소스는 당시 `market_state.json`의 전역 active events다. 다만 7월 7~8일 당시 파일이 보존되지 않아 어떤 기사와 어떤 fresh/decay 조합이 +4.62를 만들었는지는 확인 불가다.
