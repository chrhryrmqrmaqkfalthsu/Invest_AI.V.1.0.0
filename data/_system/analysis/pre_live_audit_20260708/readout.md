# Pre-Live Audit — 실거래 전 최종 점검

- 생성일: 2026-07-08
- 범위: `live_candidate_slots.py`, `/dashboard-real` 실거래 후보 슬롯, frozen/gate 산출물, 상태파일/안전장치
- 제약: 코드·데이터·상태파일 수정 없음. 산출물만 생성.
- 종합 판정: **NOT_READY**

## 종합 판정

자동 실거래 투입은 아직 **NOT_READY**다.

단, 현재처럼 직접 주문이 꺼진 상태에서 **정보성 후보판/수동 참고용**으로만 쓰는 것은 **GO_WITH_CAUTION** 수준이다. 자동 주문 또는 실청산까지 붙이려면 아래 high severity mismatch를 먼저 정리해야 한다.

핵심 이유:

```text
1. 후보 선정·정렬 로직 자체는 확정 로직과 대체로 일치한다.
2. EQ는 판단 경로에서 빠져 있다.
3. 그러나 live candidate report와 20260707 gate list 사이에 candidate_id drift가 있다.
4. 백테스트 T+1 시가 진입과 현재 슬롯의 실시간 후보 진열/즉시 선택 가정이 다르다.
5. 슬롯 도구는 청산을 담당하지 않아 S2 실청산 일치 여부가 미확정이다.
6. K=8 슬롯 운용은 K=20/10/5 검증과 직접 일치하지 않는다.
```

## 1. 로직 정합성 확인

### 판정: PASS_WITH_RISK

`data/_system/ops/live_candidate_slots.py`의 후보 선정·정렬 경로는 다음이다.

```text
candidate source:
  build_elite_shadow_report(stage2_limit=60, stage3_limit=80)[:93]

gate:
  live_candidate_list_20260707.json / entry_filter_candidates.csv
  DROP_BAD_MAE_CAPTURE 13개 제외, KEEP만 통과

signal:
  evaluate_candidate(candidate, ctx)
  ev.should_buy=True만 pool 진입
  evaluate_signal 기준 final_score >= threshold

sort:
  priority_group 오름차순
  final_score 내림차순
  ticker 오름차순
  candidate_id 오름차순

priority_group:
  SPY regime DOWN && vol_group == HIGH_VOL → 1, 후순위
  그 외 → 0
```

확정 로직과의 대조:

| 항목 | 판정 | 근거 |
|---|---|---|
| bad-MAE 13개 제외 / KEEP 80 자격 | PASS | `gate_keep` false면 `DROP_BAD_MAE_CAPTURE`로 제외 |
| should_buy(final_score >= threshold) | PASS | `if not ev.get("should_buy")`면 제외 |
| final_score 내림차순 | PASS | `sort_candidate_pool`: `-final_score` |
| SPY DOWN 시 HIGH_VOL 후순위 | PASS | `priority_group = 1 if spy.is_down and vol_group == HIGH_VOL else 0` |
| EQ 판단 경로 배제 | PASS | `entry_quality_allow=None`, `entry_quality_label=EQ_UNVERIFIED_REFERENCE_ONLY` |
| 숨은 보조 정렬 | RISK | final_score 동점 시 ticker/candidate_id 보조 정렬 존재 |

EQ 관련 확인:

```text
entry_quality_policy = EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE
entry_quality_verdict = EQ_FILTER_UNVERIFIED
entry_quality_allow = null
entry_quality_score = null
entry_quality_label = EQ_UNVERIFIED_REFERENCE_ONLY
```

따라서 EQ allow/block은 후보 자격이나 순서에 쓰이지 않는다.

## 2. 검증-실전 가정 불일치 점검

### 2-a. 진입 시점 정의

판정: **MISMATCH**

백테스트/per_trade 데이터는 신호일 다음 거래일 시가 진입이다.

```text
per_trade_entry_quality_regime.csv:
entry_date - signal_date 최소 1일
same-day entry 0건
```

분포:

```text
1 calendar day: 34,201건
2 calendar days: 426건
3 calendar days: 8,034건
4 calendar days: 1,311건
```

반면 라이브 후보 슬롯은 정규장 중 `evaluate_candidate()` 결과를 즉시 후보 슬롯에 올린다. 현재 슬롯에는 `first_signal_at`이 표시되지만, 이 값은 운영 도입/refresh 이후부터 기록된 시각이다. 과거 최초 신호일을 완전 복원한 값은 아니다.

현재 슬롯 신호 나이 예시:

```text
first_signal_at: 2026-07-07T22:22:21Z
now_utc 확인 시 약 6.66시간 경과
```

위험:

```text
실전에서 같은 날 즉시 매수하면 T+1 open 진입 검증과 다르다.
며칠 묵은 stale signal인지 완전 판별하지 못한다.
```

권장 조치:

```text
자동 실거래 전 다음 중 하나를 정책으로 고정해야 한다.
1. 신호 발생일 D → 다음 정규장 시가 주문만 허용
2. 정규장 즉시 매수 전략으로 별도 forward/OOS 검증
```

### 2-b. 청산

판정: **MISMATCH / UNVERIFIED**

`live_candidate_slots.py`는 진입 후보 진열/상태 기록만 한다. 청산을 담당하지 않는다.

`/dashboard-real` 후보 슬롯의 `매수 후보 선택`도 현재는 다음만 수행한다.

```text
/api/real/live_slot_buy
→ live_slots_state.json held_exclusions에 기록
→ 후보 슬롯 재생성
→ 실제 브로커 주문 없음
```

따라서 S2 청산, 즉 “개체 원 규칙/no-TP” 청산이 실제 계좌에서 어느 시스템으로 실행되는지 현재 audit 범위에서는 확인되지 않았다.

위험:

```text
진입 후보만 따라 하고 청산이 S2와 다르면 검증 성과와 달라진다.
```

권장 조치:

```text
실전 청산 담당 시스템을 별도로 audit하고 S2 원 규칙과 일치하는지 확인한다.
```

### 2-c. 데이터 lag

판정: **MISMATCH**

라이브 `evaluate_candidate()`는 다음 입력을 쓴다.

```text
_load_ohlcv(ticker): adapter.load_history(years=1) 또는 yfinance 1y/1d
_latest_price(ticker): yfinance 1d/1m prepost=True 최신가
get_market_context(): 현재 market_state/news/event cache
_news_context(): signal_date 기준 lagged sentiment/topic
```

백테스트/frozen replay는 고정 OHLC와 과거 신호일 기준으로 평가했다. 라이브는 최신 1분봉 가격과 현재 market context가 섞이므로, D-1 lag 가정과 완전히 동일하지 않다.

권장 조치:

```text
D-1 close 기준 후보 생성인지, 정규장 live price 기준 후보 생성인지 명확히 고정하고 그 방식으로 별도 검증한다.
```

### 2-d. 정규장 게이트·시간대

판정: **RISK**

`regular_hours_gate.py`는 America/New_York 기준 평일 09:30~16:00만 본다.

코드 주석에 명시된 한계:

```text
거래소 휴장일/조기폐장은 별도 캘린더가 없으면 100% 반영하지 못한다.
```

권장 조치:

```text
실전 주문 연결 전 broker clock 또는 NYSE calendar 기반으로 보강한다.
```

## 3. 게이트 목록·데이터 최신성 확인

### 3-a. frozen gate 내부 일관성

판정: **PASS**

```text
live_candidate_list_20260707.json:
- total 93
- KEEP 80
- DROP 13
- unique candidate_id 93

OHLC snapshot:
- *_ohlcv.csv 93개

frozen OOS trades:
- oos_trades_frozen.csv 존재
```

### 3-b. current live report와 gate list drift

판정: **MISMATCH**

`build_elite_shadow_report(stage2_limit=60, stage3_limit=80)[:93]`의 현재 후보 93개와 `live_candidate_list_20260707.json`의 93개가 완전히 같지 않다.

현재 report에는 있으나 gate list에 없는 candidate_id:

```text
stage3:CVNA:8d8594e95b89
stage3:CW:81ce9154b422
stage3:CWK:2970595abcd4
stage3:DB:3fddd15661db
```

gate list에는 있으나 현재 report에는 없는 candidate_id:

```text
stage3:AMP:7652abdd1325
stage3:AZZ:abfc9c937b7e
stage3:CAMT:bd5f11c548d5
stage3:CARR:91ed85bfb4a5
```

최근 상태파일의 `last_refresh.blocked_summary`에도 `gate_missing`이 있었다.

```text
gate_missing: 3
DROP_BAD_MAE_CAPTURE: 13
not_buy_signal: 49
eligible_pool_count: 28
```

위험:

```text
현재 report 후보가 frozen 검증 universe와 drift 중이다.
Gate list에 없는 live candidate는 gate_missing으로 제외된다.
Gate list에만 있는 candidate는 현재 report에서 나오지 않는다.
```

권장 조치:

```text
실전 전 둘 중 하나를 선택해야 한다.
1. 운용 universe를 frozen 93으로 고정
2. 현재 report universe로 gate/OOS/portfolio 검증 재생성
```

## 4. 실거래 안전장치 점검

### 4-a. 슬롯 도구 자체 주문 여부

판정: **PASS**

`live_candidate_slots.py`는 브로커 주문을 내지 않는다. `buy`도 실제 주문이 아니라 다음만 한다.

```text
held_exclusions[candidate_id]에 open 기록
manual_buy_events append
slots rebuild
state 저장
```

### 4-b. `/dashboard-real` 후보 슬롯 버튼

판정: **PASS_WITH_RISK**

후보 슬롯의 `매수 후보 선택`은 `/api/real/live_slot_buy`로 가며 실제 주문이 아니다.

```text
_mark_real_slot_manual_buy()
→ held_exclusions 기록
→ state rebuild
→ event jsonl append
```

확인 문구도 다음 취지다.

```text
이 버튼은 후보 상태 기록/제외용입니다.
실제 주문은 사용하는 매매 화면/브로커에서 별도로 확인하세요.
```

### 4-c. 별도 manual_buy_intent endpoint

판정: **RISK**

`/api/real/manual_buy_intent`는 별도 경로다. 환경변수 `KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1`이면 실제 Alpaca live market order를 낼 수 있다.

```text
_create_real_buy_intent()
if _direct_orders_enabled():
    broker.place_buy(... OrderType.MARKET ...)
```

현재 공개 연결 상태:

```text
account_check: passed
broker_mode: alpaca_live
total_value: 650.37
cash: 650.37
holdings_count: 0
direct_orders_enabled: false
```

즉 지금은 직접 주문이 꺼져 있지만, 환경변수 하나로 실제 주문 경계가 바뀐다.

권장 조치:

```text
실전 전 direct order env 관리 절차, 주문금액 cap, 이중확인, dry-run policy를 문서화한다.
```

### 4-d. 중복 매수 방지

판정: **RISK**

`held_exclusions`는 candidate_id 기준 중복 후보 제거에는 작동한다. 하지만 브로커 실제 보유와 자동 동기화되는 것은 아니다.

```text
active_held_ids(state)
→ held_exclusions status open/held/active만 확인
```

위험:

```text
다른 화면/브로커에서 직접 매수했는데 held_exclusions가 갱신되지 않으면 같은 ticker/candidate가 다시 뜰 수 있다.
```

권장 조치:

```text
브로커 포지션과 held_exclusions를 ticker/candidate_id 기준으로 reconcile하는 별도 검증 필요.
```

### 4-e. 상태파일 복구성

판정: **PASS_WITH_RISK**

상태파일은 `/tmp`가 아니라 영구 경로에 있다.

```text
STATE_PATH = data/_system/live_slots_state.json
EVENTS_PATH = data/_system/live_slots_events.jsonl
```

현재 존재와 갱신 확인:

```text
live_slots_state.json mtime: 2026-07-08 05:00 UTC
live_slots_events.jsonl mtime: 2026-07-08 04:59 UTC
```

하지만 JSON 손상 시 `load_state()`는 신규 기본 state를 반환할 수 있다. held_exclusions 복구는 별도 보장되지 않는다.

## 5. 미검증·리스크 항목

| 항목 | 판정 | 내용 |
|---|---|---|
| K=8 슬롯 | UNVERIFIED | 포트폴리오 검증은 K=20/10/5/무제한. 현재 live 슬롯은 8칸. |
| 수동 선택 | UNVERIFIED | 사람이 8개 중 고르는 방식은 final_score 자동 우선순위와 다름. |
| 신호 나이 | RISK | first_signal_at은 운영 도입 후 기록. stale signal 완전 차단 없음. |
| 청산 정책 | UNVERIFIED | 슬롯 도구는 진입 후보만 표시. S2 청산 실행 경로 미확정. |
| TIMEOUT_15 | UNVERIFIED | 포트폴리오상 후보지만 라이브 청산에 확정 반영되지 않음. |
| 하락장 일반화 | RISK | OOS 2025-2026 중심. 장기 하락장/급락장 일반화는 제한. |
| 데이터 지연 | RISK | yfinance/adapter/market_state cache와 종목별 최신봉 차이 가능. |
| direct order boundary | RISK | 현재 off지만 env 하나로 Alpaca live market order 가능. |

## 현재 슬롯 상태

현재 `/api/real/candidate_slots` 기준:

| slot | ticker | candidate_id | final_score | EQ |
|---:|---|---|---:|---|
| 1 | BMI | stage3:BMI:07d4ee0f7841 | 15.9707 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 2 | BMA | stage3:BMA:0c978464f9dd | 13.4703 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 3 | BTBT | stage3:BTBT:363898884d44 | 11.4328 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 4 | CE | stage3:CE:998b0b638c66 | 8.3632 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 5 | ADMA | stage3:ADMA:42437a3ee595 | 8.1339 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 6 | ALGT | stage2:ALGT:402f72d48c3c | 6.7068 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 7 | CAMT | stage3:CAMT:bd5f11c548d5 | 6.4889 | EQ_UNVERIFIED_REFERENCE_ONLY |
| 8 | ALGT | stage3:ALGT:aec5dd5b1dc1 | 5.5973 | EQ_UNVERIFIED_REFERENCE_ONLY |

```text
slots_filled: 8
waitlist_count: 20
held_count: 0
manual_buy_events: 0
```

## 권장 조치 목록

자동 실거래 전 필수:

```text
1. current live report와 gate list drift 해결
   - frozen 93 고정 또는 current 93 재검증

2. 진입 시점 정책 고정
   - T+1 open queue 방식인지, intraday 즉시 매수 방식인지 확정

3. 청산 시스템 audit
   - 실제 S2 청산이 어떻게 구현/실행되는지 확인

4. K=8 포트폴리오 재검증
   - S2 + K=8 + final_score priority

5. direct order 경계 강화
   - env 관리, 주문 cap, 이중확인, dry-run/real switch 명시

6. broker holdings ↔ held_exclusions reconcile 검증
```

정보성 후보판으로만 쓸 때 권장:

```text
- “실제 주문 아님 / 후보 제외용” 문구 유지
- 후보 선택 전 first_signal_at과 최신봉 시간 확인
- 1순위 final_score 우선 선택 원칙 유지
- 수동 선택 사유 기록
```

## 최종 판정

```text
자동 실거래 투입: NOT_READY
정보성 후보판/수동 참고: GO_WITH_CAUTION
```

이유는 후보 선정 로직 자체는 정리됐지만, execution 가정(T+1 vs 즉시), universe/gate drift, 청산 경로, K=8 미검증이 남아 있기 때문이다.

## 산출물

```text
data/_system/analysis/pre_live_audit_20260708/readout.md
data/_system/analysis/pre_live_audit_20260708/findings.csv
```
