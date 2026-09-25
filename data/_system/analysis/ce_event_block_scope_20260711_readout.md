# CE Event 블록 구성 확인

## 판정

- 구조: `MIXED`
- CE 7/7~7/8 실제 source: `INSUFFICIENT`

Event key는 11개다: 전쟁, 금리정책_인상, 금리정책_인하, 지정학_긴장, 관세, 수출규제, 실적쇼크, 유가급등, 은행위기, 인플레이션, 연준발언.

`실적쇼크`라는 기업 사건 유형이 Event에 존재한다. 그러나 입력은 ticker별 feed가 아니라 시장 공통 뉴스 100건을 S&P500 전체 영향 기준으로 분류해 만든 단일 `MarketContext.active_events`다. `learned_rulebook.py`는 active key 존재 여부만 모든 ticker에 공통 0/1 flag로 전달하며 기사 대상 기업과 CE 일치 여부를 검사하지 않는다. 따라서 순수 매크로 전용도, 정상적인 ticker-specific Event도 아닌 혼합 구조다.

News는 ticker별 `sentiment_avg`, NewsTopics는 ticker별 earnings·M&A 등 15개 topic z-score다. CE 룰북은 `use_news_global=False`다. 같은 기사가 ticker sentiment와 market news 양쪽에 들어오면 양쪽 반영 가능성이 있으나 CE 당시 중복 여부는 payload 부재로 확인 불가다.

CE 룰북 `stage3:CE:998b0b638c66`은 `use_event_block=True`, multiplier `2.3387436247691396`이다. 가장 큰 절대 계수는 매크로 key `geopolitical=-1.93124685`, 가장 큰 양수 계수는 매크로 key `rate_hike=+1.43315021`이다. 기업 사건 key `earnings_shock=+0.79513229`도 존재하며 multiplier 적용 단독 기여는 약 +1.86이다. 따라서 관찰된 +4.62260455는 단일 earnings_shock만으로 만들 수 없다.

확정된 CE 값은 최초 후보 `2026-07-07T22:22:21.577113+00:00`, 주문 snapshot `2026-07-08T14:27:15.330072+00:00`, Event `+4.62260455`, News `0`, NewsTopics `0`이다. 당시 event_flags, active_events, 기사 제목·URL·publishedAt, earnings_shock 활성 여부는 저장되지 않았다. 7/7~7/8 historical market_state/event feed snapshot도 없어 실제 source는 확인 불가다. 이전 매크로 조합 역산은 미확정 추측이다.

결론적으로 decay는 순수 매크로, 섹터/시스템, 기업 사건을 분리해야 한다. 기업 사건에는 ticker 일치 검증과 별도 짧은 TTL 또는 기사 발생시각 기반 decay가 필요하다.

산출물:
- `data/_system/analysis/ce_event_block_scope_20260711_event_key_classification.csv`
- `data/_system/analysis/ce_event_block_scope_20260711_ce_rulebook_event_coefficients.csv`
- `data/_system/analysis/ce_event_block_scope_20260711_active_event_source.csv`
- `data/_system/analysis/ce_event_block_scope_20260711_event_news_boundary.md`
- `data/_system/analysis/ce_event_block_scope_20260711_readout.md`

운영 코드·설정 변경: 0건
