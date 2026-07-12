# CRS 전수 흔적 발굴 readout

- 대상: `stage3:CRS:8695c9ce3320`
- full rulebook hash: `8695c9ce33203c60489bdd4a2671bf68221da19a6896816778e738f10fcfa0c3`
- 조사 시작 기준 Git HEAD: `1188f761cf9e4520814cb2c5193750a6fdf24ab5`
- 원본 코드 변경: **0**
- 판정: `RECOVERED`=저장 증거 또는 결정적 코드 경로로 복원, `PARTIALLY_RECOVERED`=일부만 복원·직접 원본 부재, `NOT_STORED`=조사한 저장 계층에 구조적으로 보존되지 않음.

## 핵심 결론

기존 조사에서 미해결이던 최초 점수의 이유 목록과 Event 성분은 2026-07-09 tar 백업에서 복원됐다. 최초 신호 점수 `2.971797614887265`는 아래 성분 합과 정확히 일치한다.

| component | value | 판정 |
|---|---:|---|
| ma_align | 1.073432530260209 | RECOVERED |
| rsi | 1.6475902670733407 | RECOVERED |
| bb | 1.9410509060649137 | RECOVERED |
| macd | 0.0 | RECOVERED |
| volume | 0.0 | RECOVERED |
| news | 0.0 | RECOVERED |
| news_topics | 0.0 | RECOVERED |
| events | -1.6902760885111983 | RECOVERED |
| market multiplier | 1.0 | RECOVERED |
| final score | 2.971797614887265 | RECOVERED |

최초 신호 시각 자체의 byte-for-byte evaluator payload는 남지 않았다. 그러나 최초 시각에서 18분 44초 뒤와 34분 38초 뒤의 백업이 동일 점수·동일 이유를 보존하고, 17:56 dashboard export가 component 전부를 보존한다. 따라서 점수 분해는 `RECOVERED`, “17:20:33 당시 직렬화된 원본 payload”는 `NOT_STORED`, 최초 시각 이유 스냅샷은 엄격히 `PARTIALLY_RECOVERED`다.

## 최초 신호와 초기 refresh

- 최초 신호: `2026-07-09T17:20:33.590054+00:00`
- 최초 가격: `600.8599853515625`
- 최초 점수: `2.971797614887265`
- threshold: `2.5574757832651467`
- margin: `0.41432183162211844`
- pass: `true`

초기 이유:
- `정배열(+1.07)`
- `RSI 60∈[39,67](+1.65)`
- `BB근접(+1.94)`
- `이벤트반응(-1.69)`

근거 A:
- archive `backup/pre_rulebook_completeness_audit_20260709_174011.tar.gz`
- archive mtime UTC `2026-07-09T17:40:12.154623+00:00`
- archive SHA-256 `48e1d76c7fc5954042f3f4984e91cc70e2e5ea8982a840b4afcff30b3cc3cb3a`
- member `data/_system/live_slots_state.json`
- member mtime UTC `2026-07-09T17:39:17+00:00`
- member SHA-256 `288bb174baba69ae0f9b4bf923a13720d197d830d45af466677766d9450c3f3f`
- 저장값: price `599.760009765625`, score `2.971797614887265`, market `77.6`, sector `100.0`, VIX `15.97`, 위 이유 4개.

근거 B:
- archive `backup/pre_candidate_pool_export_write_20260709_175553.tar.gz`
- archive mtime UTC `2026-07-09T17:55:53.716939+00:00`
- archive SHA-256 `8d22077d08f6692e2632c3e51dc083cb64f7e713de68b961c11fa6aa9c69beae`
- member mtime UTC `2026-07-09T17:55:11+00:00`
- member SHA-256 `afce99ec52ee838e5b3afa20218489d8843365289bc0ef22499a9bbd5fe4765a`
- 저장값: price `601.1849975585938`, 같은 score/context/reasons.

근거 C:
- archive `backup/pre_exit_wiring_universe_audit_20260709_182021.tar.gz`
- archive SHA-256 `74978d27766f3cde11fdf725cc7ab0fb480b95f3d0bc5e1c462c23f0ae7d47ac`
- member `data/_system/real_dashboard_buy_candidates.json`
- member SHA-256 `7a223b01b2ddabf318cdaddbb48a53c384ae3066c0d7d1e0fd47196539ec42c2`
- 위 component와 이유 목록을 완전 보존.

## -0.4376680992 차이 해소

기존 진단 재구성은 Event를 0으로 놓고 뉴스 입력을 `global news=+0.061116159768250376`, `topic news=-1.3137241491157623`, 합계 `-1.252607989347512`로 사용했다.

실제 초기 live snapshot은 `news=0`, `news_topics=0`, `events=-1.6902760885111983`이었다.

`-1.6902760885111983 - (-1.252607989347512) = -0.4376680991636863`

이는 기존 미해결 차이 `-0.43766809916368654`와 부동소수점 오차 범위에서 동일하다. 차이는 시장보정이 아니라 **진단에서 Event를 누락하고 실제 live에서 0이던 뉴스 block을 사후 데이터로 대체한 데서 발생**했다. 룰북의 `use_market_entry_adjustment=false`이므로 시장 multiplier는 `1.0`이다.

## Event 원본과 역산

저장된 Event contribution은 `-1.6902760885111983`이다. 룰북 11개 binary Event의 `2^11` 조합을 전수 계산하면 아래 조합 하나만 정확히 일치한다.

`(rate_hike + rate_cut + geopolitical + inflation + fed_statement) × 2.8016756833476597 = -1.6902760885111983`

이 조합은 수학적으로 유일한 역산값이다. 그러나 2026-07-09 17:20의 `market_state.json`, `active_events`, 기사 payload, fresh/decay 상태는 tar·Git·로그 어느 계층에도 보존되지 않았다.

- Event 수치: `RECOVERED`
- 유일 역산 flag 조합: `PARTIALLY_RECOVERED / INFERRED_UNIQUE`
- 실제 active-event 원본과 기사별 근거: `NOT_STORED`

현재 `data/_system/market_state.json`은 2026-07-11 state이므로 최초 신호의 직접 근거로 사용하지 않았다.

## 최초 가격 provider

최초 신호 당시 코드의 `engine/live/elite_shadow_trader.py::_latest_price()`는 먼저 yfinance `period="1d", interval="1m", prepost=True`의 마지막 Close를 사용하고, 실패 시 evaluator daily frame의 마지막 Close로만 fallback한다. Alpaca 호출 경로는 없다.

- 최초 저장가격 `600.8599853515625`
- 당시 daily fallback 후보(2026-07-08 close) `587.03`

두 값이 다르므로 daily fallback은 배제된다. provider는 **yfinance 1분봉/prepost 경로로 RECOVERED**한다. 개별 yfinance 응답 payload와 quote timestamp/provider 필드는 저장되지 않아 `NOT_STORED`다. 이후 Alpaca IEX 데이터는 사후 검증이며 최초 가격 source가 아니다.

## bb_proximity 출처

`bb_proximity=1.1402337701369225`는 Stage3 entry GA에서 생성됐다.

- train period `train_3`, 2024-07-01~2025-06-30
- seed `2026061880`
- population 100, generations 50
- entry pool rank 65, diversity selection rank 9
- entry hash `85ae94be7cc680723147abed944a715a51f4312b65adcb6ba1b94d64ad7f86a3`
- final hash `8695c9ce33203c60489bdd4a2671bf68221da19a6896816778e738f10fcfa0c3`

Exit GA는 exit gene만 수정하므로 bb 값은 entry 개체에서 최종 룰북으로 상속됐다. Stage2 전체 300개 대비 약 93.33 percentile이며 허용범위 `[1.0, 1.15]` 상단에 가깝다. 반면 Stage3 선택 entry 20개 중 11개, final 60개 중 33개가 같은 값이므로 **Stage2 대비 높은 값이지만 CRS Stage3 선택군 안에서는 이상치가 아니라 지배적인 값**이다.

핵심 artifact:
- `.../CRS/stage3/entry_rulebooks.jsonl`, mtime `2026-07-07T04:22:07.313968Z`, SHA-256 `53e58bd063f20a1b7d9ce0afe92b7dcf1093aed4b941f79bd9c031e0cfa5aa39`
- `.../CRS/stage3/final_rulebooks.jsonl`, mtime `2026-07-07T07:11:37.439751Z`, SHA-256 `ee4e6964212fc69927000fef1037db201bfb7d6f271b9cdfd0df3d3119a963ff`
- `.../CRS/stage3/entry_result.json`, SHA-256 `c7cf7a5e299696166988d290c6168aa76a6accdbc46db3bd81df105a7f440f2e`
- `.../CRS/stage3/manifest.json`, SHA-256 `f253fbc07b377794218167ab19d2c53a339d906b5a60d069d878194f336fadd2`

## 원본 캔들 가용성

가용한 최신 장기 일봉 CSV:
- `data/_system/analysis/ohlc_snapshot_20260707/CRS_ohlcv.csv`
- 1,526 rows, `2020-06-08`~`2026-07-06`
- mtime `2026-07-07T16:56:26.331753Z`
- SHA-256 `ed2a0a6cabd0100c99d9fe5714c59f31de174878434f4dacb9efca29729cd0bf`

추가 연구 cache:
- `data/_system/research/honest_full_6174_20260610/stage0/ohlcv_cache/CRS.pkl`, 1,527 rows, 2020-05-11~2026-06-08, SHA-256 `e9750a6f8d61546ed3501cb7029294f688003832aa8bb6dcffbaa7ea34064063`
- `data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/CRS.pkl`, 1,526 rows, 2020-05-18~2026-06-12, SHA-256 `b04a21480695380694c587640291938e93b85c0785af3c62b0584851cba8636f`

신호 전후 전체 1분봉 raw cache는 발견되지 않았다. daemon 로그는 frame을 메모리에 로드했다는 사실만 남기며 row payload를 보존하지 않는다. 사후 Alpaca 분석에서 선택된 분봉 point만 남았다.

- 장기 일봉 `RECOVERED`
- 신호 당일 전체 1분봉 `NOT_STORED`
- 저장된 선택 분봉 point `PARTIALLY_RECOVERED`

## CRS raw news

`data/_system/ticker_news_cache/CRS/av_CRS_202607.json.gz`에 7건이 저장돼 있다.

- gzip mtime `2026-07-11T15:12:28.419073Z`
- gzip SHA-256 `13698997d5ab9791c54306ccd12f37a2186497a2a568816e230469c568652d34`
- decompressed SHA-256 `a232caabd39e69cd14afde7743043f00b1f3aa845c67fe38acc319baf9528fcf`
- 기사 범위 2026-07-06~2026-07-08
- 7/7 기사 2건, 7/8 Russell 지수 제외 기사 1건 포함

`data/_system/ticker_sentiment/CRS_daily.csv`는 7/6 count 4, 7/7 count 2, 7/8 count 1을 보존하고 마지막 날짜가 7/8이다. 월별 raw cache가 7/11에 갱신됐는데도 7/9 이후 CRS 기사 행은 없으므로 해당 ticker-news row는 `NOT_STORED`다. 글로벌 market Event 기사와 CRS ticker raw news는 별도 경로다.

## “안 본 것”과 “없는 것”

확인 계층:
- Git 추적 파일과 reachable history
- `data/_system` state·analysis·research·news·sentiment
- `data/logs`, 루트 `logs`, `live`
- `backup/*.tar.gz` 내부 member
- Stage2/Stage3 CRS artifact와 실행 로그
- 현재 및 과거 코드 경로

대형 cache는 candidate/hash exact scan, CRS 명명 경로 scan, 저장 계층별 검색으로 나눠 조사했다. `.env` 및 민감 설정은 열지 않았다.

`NOT_STORED`는 다음 구조적 부재에만 사용했다.
- 17:20:33의 직렬화된 evaluator payload
- 당시 `active_events`/기사/fresh-decay 원본 state
- yfinance 개별 응답 payload·provider metadata·quote timestamp
- 신호 전후 전체 1분봉 raw cache
- 2026-07-09 이후 CRS ticker-news row

세부 근거는 `crs_timeline.csv`, `recovery_ledger.csv`, `bb_proximity_distribution.csv`, 뉴스·캔들 덤프, 분할 파일 인벤토리를 참조한다.
