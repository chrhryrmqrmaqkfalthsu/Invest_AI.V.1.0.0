# stage2/3 재발견 설계 사전조사 — 호재 예외 필드 검증

- 감사일: 2026-07-12
- 기준 HEAD: `c6f035bd078799ae0bead57de4713d414a17202b`
- 범위: 코드·설정·라이브 저장 상태 read-only 조사. 산출물 및 사전 백업 외 코드·설정·daemon 변경 없음.

## 최종 판정

**EXCEPTION_DEFERRED**

현재 코드에는 호재 방향을 나타내는 단일 의미축 후보 `sentiment_avg`와 `sent_earnings`가 존재한다. 그러나 두 필드 모두 라이브 후보 스냅샷에 원값이 보존되지 않고, daemon PID 494330은 이 필드를 직접 읽지 않는다. daemon 입력에는 이미 가격·거래량·시장·뉴스·Event 등이 합산된 `raw_score`/`final_score`와 텍스트 `reasons`만 남는다(`data/_system/ops/live_candidate_slots.py:269-316`). 따라서 B작업의 5번 예외를 실행 시점에 신뢰·재현 가능하게 적용할 수 없다.

또한 `sentiment_avg`는 저장 과거값 1,440건 기준 2거래일 수익률 상관이 0.0611, +3% 라벨 lift가 +0.67%p에 불과했고, ADMA와 ACMR에서는 상관과 lift가 모두 음수였다. `sent_earnings`는 pooled lift +2.92%p였으나, 0 값이 결측과 중립을 구분하지 못하고 후보 스냅샷 미보존·기사 희소성 때문에 `INSUFFICIENT_DATA`로 판정했다. 이는 STORED + RELIABLE 조건을 충족하지 못한다.

따라서 데이터 보존·재현 경로가 별도 설계되고 신뢰도가 재검증될 때까지 **예외 없이 모든 지표를 자기 하한·상한의 개별 AND로 통과시키는 A안**을 적용한다. BOIL/CE류가 다른 지표 과다값이나 합산점수로 미달 지표를 상쇄하여 재통과하는 경로를 허용하지 않는다.

## 핵심 근거

### 1. 적격 후보 인벤토리

- `sentiment_avg`: `engine/market/ticker_sentiment.py:39-64, 67-145`. Alpha Vantage 기사별 `ticker_sentiment_score`를 `relevance_score`로 가중한 일별 평균. 최초 관련 커밋 `f304f9c9d3a2f9508a065ba7b900612d1ae62b84`(2026-05-27T06:00:27Z). 단일 의미축이지만 다기사 합산이며 예외 적격성은 저장·신뢰 보장에 조건부다.
- `sent_earnings`: `engine/market/ticker_sentiment.py:15-29, 119-143`. earnings 토픽 기사 점수의 ticker relevance × topic relevance 가중평균. 최초 파일 커밋 `a6dcd08def0c732255cd1fcdbc7265a41ddcf806`(2026-05-29T08:43:39Z). 단일 토픽 축이나 기사 합산이고 0이 결측/중립을 혼합한다.
- `event_adjustment`: `engine/market/context.py:403-538, 581-627`. 복수 뉴스 Event impact의 합산 및 TTL/decay 병합 결과. Event 관련이며 합산 결과라 부적격.
- `topic_news`: `engine/strategies/evaluator.py:144-171`. 15개 토픽 정규화 피처에 학습 가중치를 곱해 합산·cap한 값. 합산 결과라 부적격.
- `score_with_events`: `engine/market/context.py:753-774`. `score + event_adjustment`. 합산 결과라 부적격.
- `final_score`: `engine/strategies/evaluator.py:223-232`. `raw_score * market_adjustment`; `raw_score` 자체에 가격·거래량·뉴스·Event 등이 포함된다. 최초 커밋 `59b8a47b4023106070f3afb8d12ae8128b435004`(2026-05-25T00:33:29Z). 5번 규칙의 금지 대상 그 자체다.
- `flow`, `order_book`, `catalyst`, `surprise`, `guidance`, `upgrade`: 라이브 후보 단일 점수 필드 정의를 확인하지 못했다. 텍스트 키워드나 이벤트 분류 외 점수는 `NOT_STORED`다.

### 2. 라이브 저장 및 10개 후보

10개 후보 모두 `data/_system/ticker_sentiment/{ticker}_daily.csv` 원천 파일은 존재하며 2026-07-12 10:15 UTC에 갱신됐다. 그러나 최신 데이터 날짜는 ADMA 07-11, CRS 07-08, ALGT 07-11, AEIS 07-10, ARKW 07-07, CBRL 07-10, BTU 07-10, BB 07-10, BN 07-10, ACMR 07-09로 불균일하다. CRS와 ARKW는 2026-07-09 이후 값이 없어 손실/공백이 확인된다.

`engine/strategies/learned_rulebook.py:196-232, 275-293`는 D-1 lag와 max-age를 적용해 `sentiment_avg` 및 topic features를 평가기에 전달한다. 하지만 `data/_system/ops/live_candidate_slots.py:269-312`는 `final_score`, `raw_score`, `threshold`, `ratio`, 시장 점수와 reasons만 저장한다. 즉 원 단일 뉴스 점수는 후보 스냅샷에서 `NOT_STORED`이며 daemon이 직접 읽는 경로도 없다. 원천 CSV 관점에서는 STORED, 예외 적용 관점에서는 NOT_STORED이므로 전체 liveness는 `PARTIAL`이다.

### 3. 신뢰도

저장된 감성 날짜와 `data/_system/analysis/ohlc_snapshot_20260707/{ticker}_ohlcv.csv`를 교집합으로 결합했다. 라벨은 신호일 종가 대비 다음 2거래일 종가 중 최대값이 +3% 이상인지로 정의했고, 상관은 2거래일 후 종가수익률과 계산했다.

- `sentiment_avg`: n=1,440, pooled corr=0.0611, 기준 +3% 비율=27.71%, 양수 감성 시=28.38%, lift=+0.67%p. 10개 중 양의 상관 8개, 양의 lift 7개이나 ADMA/ACMR은 음수. 역사 범위 -0.9482~+0.9228 및 잦은 부호 전환. `UNRELIABLE`.
- `sent_earnings`: n=1,440, pooled corr=0.0426, 양수 earnings 감성 시 +3% 비율=30.63%, lift=+2.92%p. 다만 0이 결측/중립을 혼합하고 후보별 기사 수가 희소하며 라이브 원값 미보존. `INSUFFICIENT_DATA`.
- Event 계열은 사용자 기억의 -1.69↔+4.38 부호 반전 및 현재 OFF 상태와 일치하며, 구조적으로도 복수 이벤트 합산이라 예외 후보가 아니다.

[추정] OHLC 스냅샷 종료일 이후 데이터와 후보 생성 시점별 정확한 lagged 원값이 보존되지 않아, 라이브 의사결정 당시의 뉴스 점수 재구성은 완전 보장할 수 없다. 해당 부분은 `UNRECOVERABLE` 또는 `NOT_STORED`로 취급했다.

## 사용자 기억과의 대조

- “Event 축은 이미 OFF이고 신뢰도가 크게 반전”: 코드상 Event는 별도 policy/shadow 경로이며 `event_adjustment` 자체도 복수 이벤트 합산·감쇠 결과다. 기억과 일치한다.
- “뉴스 점수는 2026-07-09 이후 손실 가능성”: 원천 CSV 자체는 일부 후보에서 07-10~11까지 있으나 CRS는 07-08, ARKW는 07-07에서 끝난다. 더 결정적으로 후보 스냅샷에는 모든 후보의 원 뉴스 점수가 저장되지 않는다. 따라서 기억의 `NOT_STORED` 우려는 예외 적용 관점에서 확인됐다.
- “stage2 final_score는 금지 대상”: `final_score = raw_score * market_adjustment`이며 raw_score가 다축 합산이므로 정확히 일치한다.

## B작업 5번 규칙 영향

B작업에서는 호재 예외를 활성화하지 않는다. 모든 지표는 독립적인 AND 조건으로 평가하고, 한 지표 미달을 뉴스·거래량·최근 봉·Event·final_score 등 다른 값으로 상쇄하지 않는다. 향후 예외 재검토 조건은 후보 생성 시점의 원 단일 필드 영구 저장, 결측/중립 구분, D-1 lag provenance, 2026-07-09 이후 연속성, 독립 OOS lift 기준 통과를 모두 만족하는 것이다.
