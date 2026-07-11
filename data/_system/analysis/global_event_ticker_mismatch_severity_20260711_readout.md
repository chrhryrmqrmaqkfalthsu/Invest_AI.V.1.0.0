# 전역 이벤트 오적용(ticker 미스매치) 심각도 스캔

## 최종 판정

`INSUFFICIENT`

코드 구조상 기업 사건 key인 `실적쇼크`가 전역 `active_events`에 올라오면 ticker 일치 검증 없이 Event 블록을 사용하는 모든 평가 대상에 동일 flag가 전달된다. 따라서 결함의 **잠재 노출 범위는 Event 축을 쓰는 전 종목**이다.

그러나 현재 및 최근 보존 데이터에는 기업 사건이 실제 활성화된 시점의 key별 `event_flags`, 기사 대상 ticker, 평가 종목 전체 목록이 함께 남아 있지 않다. 따라서 무관 종목 몇 개가 실제로 flag를 받았는지 관찰값으로 확정할 수 없다.

## 1. 전역 기업 사건 영향 종목 수

현재 보존된 `data/_system/market_state.json`의 시점은 `2026-07-10T19:34:03.640405`이며 활성 Event는 금리정책_인상, 금리정책_인하, 인플레이션, 유가급등, 지정학_긴장, 관세다. 기업 사건 key `실적쇼크`는 활성 상태가 아니다.

따라서 현재 snapshot에서 실적쇼크 flag를 받은 종목 수는 0으로 판정할 수 없다. 이는 단지 해당 관찰 시점에 기업 사건이 비활성이었다는 뜻이며, 결함 노출 규모를 측정할 표본이 아니다.

7월 7~10일 후보 snapshot에는 Event 과반 9종목이 확인되지만 Event 합계만 보존돼 있다. `event_flags`, 당시 `active_events`, 기사 대상 ticker가 없으므로 기업 사건 flag 수신 종목 수와 무관 종목 수는 모두 확인 불가다.

## 2. Event 과반 9종목 분해

대상은 BMA, BNTX, BTBT, CMC, BWXT, BMI, BGC, CE, ACMR이다.

각 snapshot에는 Event 총기여만 있고 key별 기여가 없다. 따라서 9종목 모두 다음 항목이 확인 불가다.

- 매크로 key 기여 합계
- 기업 사건 key 기여 합계
- `earnings_shock` 활성 여부
- 기업 사건이 해당 종목 자신의 사건인지 여부
- 무관한 전역 사건인지 여부

관찰 가능한 사실은 9종목 모두 Event가 총점의 50%를 초과했다는 점뿐이다.

## 3. ticker 일치 검증 부재

코드로 재확인했다.

- Event: 단일 `MarketContext.active_events` key 존재 여부를 모든 ticker 평가에 공통 flag로 전달
- Event 적용 시 기사 대상 ticker와 평가 대상 ticker 비교 없음
- News/NewsTopics: `_load_ticker_sentiment(ticker)`를 통해 ticker별 자료 사용

따라서 Event는 ticker-unaware, News/NewsTopics는 ticker-aware다.

## 4. Event↔NewsTopics 중복 실측

확인 불가다.

보존 데이터에는 Event 기사와 ticker sentiment 기사 사이를 연결할 공통 article ID 또는 정규화 URL join key가 완전하게 남아 있지 않다. 후보 snapshot에도 Event 원문 기사와 NewsTopics 원문 기사 목록이 없다. 따라서 동일 기사 중복 사례 수를 관찰값으로 집계할 수 없다.

## 5. 결함 노출 규모 판정

실제 노출 종목 수 기준 판정은 `INSUFFICIENT`다.

다만 코드 구조상 `실적쇼크`가 활성화되면 해당 시점 Event 블록 평가 대상 전체가 동일 flag를 받으므로 잠재 범위는 소수 종목으로 제한되지 않는다. 이것은 코드 경로에 대한 구조적 사실이며, 실제 최근 라이브에서 무관 종목 다수가 영향을 받았다는 통계적 관찰과는 구분해야 한다.

따라서 이번 read-only 스캔만으로 `WIDESPREAD` 또는 `LIMITED`를 확정하지 않는다.

## 우선순위 해석

실측 규모는 확인 불가지만, ticker 검증 부재는 확정됐다. 기업 사건 활성 시 전역 확산이 가능한 구조이므로 안전성 관점에서는 ticker 일치 검증이 decay보다 선행 검토 대상이다. 다만 이는 실제 최근 발생 건수 통계가 아니라 구조적 위험에 따른 우선순위 판단이다.

## 산출물

- `data/_system/analysis/global_event_ticker_mismatch_severity_20260711_global_corporate_event_impact.csv`
- `data/_system/analysis/global_event_ticker_mismatch_severity_20260711_event_majority_9_decomposition.csv`
- `data/_system/analysis/global_event_ticker_mismatch_severity_20260711_ticker_validation_recheck.md`
- `data/_system/analysis/global_event_ticker_mismatch_severity_20260711_event_news_topics_duplicates.csv`
- `data/_system/analysis/global_event_ticker_mismatch_severity_20260711_readout.md`

운영 코드 및 설정 변경: 0건
