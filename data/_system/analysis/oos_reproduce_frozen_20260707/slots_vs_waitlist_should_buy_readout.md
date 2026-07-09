# 슬롯 vs waitlist should_buy 상태 확인 — READ-ONLY

범위:

```text
정규 후보 파일: data/_system/real_dashboard_buy_candidates.json
live slots state: data/_system/live_slots_state.json
코드 정책 확인:
  data/_system/ops/live_candidate_slots.py
  engine/live/real_dashboard_api.py
  scripts/export_real_dashboard_buy_candidates.py
```

금지 준수:

```text
read-only 점검
--write 실행 없음
주문 제출 없음
코드/운영 데이터 수정 없음
```

최종 판정:

```text
REGULAR_8_ALL_SHOULD_BUY_TRUE
WAITLIST_19_ALL_SHOULD_BUY_TRUE
WAITLIST_IS_SCORE_RANK_OVERFLOW_NOT_SIGNAL_FALSE
K20_IS_REQUEST_LIMIT_OR_EXPORT_LIMIT_NOT_FORCE_FILL
LIVE_SLOT_ENGINE_IS_8_SLOT_DESIGN
```

---

## 1. 현재 상태 요약

정규 후보 파일:

```text
path: data/_system/real_dashboard_buy_candidates.json
updated_at: 2026-07-09T16:58:29.612877+00:00
candidates: 8
export_meta.source_section: slots
export_meta.limit: 20
```

live slots state:

```text
updated_at: 2026-07-09T17:02:33.329171+00:00
last_refresh: 2026-07-09T17:02:33.328751+00:00
slots: 8
candidate_pool: 27
waitlist: 19
```

재평가 방식:

```text
build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
get_market_context()
evaluate_candidate(candidate, ctx=ctx)
_load_rulebook_for_candidate(candidate)로 full rulebook 존재 확인
```

---

## 2. 정규 파일 8개 후보 — 현재 should_buy 재확인

요약:

```text
regular_total: 8
regular_should_buy_true: 8
regular_should_buy_false: 0
regular_unknown: 0
full_rulebook_len: 전 후보 88
```

| candidate_id | ticker | stage | should_buy | score | threshold | ratio | full_rulebook_len |
|---|---|---|---:|---:|---:|---:|---:|
| stage2:ALGT:402f72d48c3c | ALGT | stage2 | True | 6.706770 | 2.333673 | 2.873912 | 88 |
| stage3:ADMA:42437a3ee595 | ADMA | stage3 | True | 8.133905 | 2.179291 | 3.732364 | 88 |
| stage3:ALGT:aec5dd5b1dc1 | ALGT | stage3 | True | 5.597293 | 2.293728 | 2.440260 | 88 |
| stage3:BCS:5e7da5a74b01 | BCS | stage3 | True | 7.133476 | 3.301677 | 2.160562 | 88 |
| stage3:BMA:0c978464f9dd | BMA | stage3 | True | 13.470340 | 2.848392 | 4.729104 | 88 |
| stage3:BMI:07d4ee0f7841 | BMI | stage3 | True | 16.550465 | 2.429303 | 6.812846 | 88 |
| stage3:BTBT:363898884d44 | BTBT | stage3 | True | 11.633724 | 1.911258 | 6.086947 | 88 |
| stage3:CE:998b0b638c66 | CE | stage3 | True | 7.195458 | 2.654187 | 2.710984 | 88 |

판정:

```text
현재 정규 파일 8개 후보는 모두 full 룰북 재평가 기준 should_buy=True 상태를 유지 중이다.
```

---

## 3. 현재 live slots 8개 — should_buy 재확인

요약:

```text
slots_total: 8
slots_should_buy_true: 8
```

| slot | candidate_id | ticker | should_buy | score | threshold | ratio |
|---:|---|---|---:|---:|---:|---:|
| 1 | stage3:BMI:07d4ee0f7841 | BMI | True | 16.550465 | 2.429303 | 6.812846 |
| 2 | stage3:BMA:0c978464f9dd | BMA | True | 13.470340 | 2.848392 | 4.729104 |
| 3 | stage3:BTBT:363898884d44 | BTBT | True | 11.633724 | 1.911258 | 6.086947 |
| 4 | stage3:ADMA:42437a3ee595 | ADMA | True | 8.133905 | 2.179291 | 3.732364 |
| 5 | stage3:CE:998b0b638c66 | CE | True | 7.195458 | 2.654187 | 2.710984 |
| 6 | stage3:BCS:5e7da5a74b01 | BCS | True | 7.133476 | 3.301677 | 2.160562 |
| 7 | stage2:ALGT:402f72d48c3c | ALGT | True | 6.706770 | 2.333673 | 2.873912 |
| 8 | stage3:ALGT:aec5dd5b1dc1 | ALGT | True | 5.597293 | 2.293728 | 2.440260 |

정규 파일의 후보 8개와 live slot 후보 8개는 candidate_id set 기준 동일하다.

---

## 4. waitlist 19개 — should_buy vs 점수 밀림 확인

요약:

```text
waitlist_total: 19
waitlist_should_buy_true: 19
waitlist_should_buy_false: 0
waitlist_unknown: 0
full_rulebook_len: 전 후보 88
```

| wait_rank | candidate_id | ticker | stage | should_buy | score | threshold | ratio | 대기 사유 |
|---:|---|---|---|---:|---:|---:|---:|---|
| 1 | stage3:ADPT:78c31f1ca209 | ADPT | stage3 | True | 5.449958 | 2.389647 | 2.280654 | 8-slot 점수순 밀림 |
| 2 | stage2:CMC:4f6ee2739add | CMC | stage2 | True | 5.061368 | 2.390548 | 2.117242 | 8-slot 점수순 밀림 |
| 3 | stage3:ANET:fe220620802b | ANET | stage3 | True | 5.025097 | 2.639090 | 1.904102 | 8-slot 점수순 밀림 |
| 4 | stage3:ARKG:50b05b8de94f | ARKG | stage3 | True | 4.799111 | 3.505971 | 1.368839 | 8-slot 점수순 밀림 |
| 5 | stage3:BKSY:f1bcc8efea02 | BKSY | stage3 | True | 4.296854 | 2.232911 | 1.924329 | 8-slot 점수순 밀림 |
| 6 | stage2:FIX:cab7d458767d | FIX | stage2 | True | 3.674072 | 2.137243 | 1.719071 | 8-slot 점수순 밀림 |
| 7 | stage3:BB:f1bdfe7f8ad9 | BB | stage3 | True | 3.290903 | 2.791951 | 1.178711 | 8-slot 점수순 밀림 |
| 8 | stage3:CDE:ceb9fe0512dc | CDE | stage3 | True | 3.259063 | 2.707639 | 1.203655 | 8-slot 점수순 밀림 |
| 9 | stage3:CIEN:2ed675d30868 | CIEN | stage3 | True | 3.222903 | 3.096890 | 1.040690 | 8-slot 점수순 밀림 |
| 10 | stage3:BWXT:f195725cb792 | BWXT | stage3 | True | 3.196564 | 2.015809 | 1.585747 | 8-slot 점수순 밀림 |
| 11 | stage2:CEF:fe84c0ad85d8 | CEF | stage2 | True | 3.191415 | 2.301963 | 1.386389 | 8-slot 점수순 밀림 |
| 12 | stage3:ARKW:296c057b4ef7 | ARKW | stage3 | True | 3.156659 | 2.563383 | 1.231443 | 8-slot 점수순 밀림 |
| 13 | stage3:BOIL:9044dc2c67a3 | BOIL | stage3 | True | 2.893894 | 2.183173 | 1.325545 | 8-slot 점수순 밀림 |
| 14 | stage3:APH:c7885deba35c | APH | stage3 | True | 2.888220 | 1.500000 | 1.925480 | 8-slot 점수순 밀림 |
| 15 | stage3:CBRL:677767a0b6a9 | CBRL | stage3 | True | 2.822752 | 1.904704 | 1.481990 | 8-slot 점수순 밀림 |
| 16 | stage3:BN:d264957fe5f6 | BN | stage3 | True | 2.797891 | 2.648022 | 1.056597 | 8-slot 점수순 밀림 |
| 17 | stage3:CAPR:a51d615a0ff1 | CAPR | stage3 | True | 2.621203 | 2.159376 | 1.213871 | 8-slot 점수순 밀림 |
| 18 | stage3:BNTX:d667608bc166 | BNTX | stage3 | True | 2.536688 | 1.960699 | 1.293767 | 8-slot 점수순 밀림 |
| 19 | stage3:AEIS:6e26f08a7c6d | AEIS | stage3 | True | 2.445473 | 1.639118 | 1.491945 | 8-slot 점수순 밀림 |

판정:

```text
waitlist 19개는 모두 should_buy=True다.
즉 waitlist는 should_buy=False 후보가 아니라, 후보 pool 내에서 8개 live slot보다 final_score 순위가 낮아 대기 중인 후보들이다.
```

---

## 5. should_buy=True인데 슬롯에 못 든 후보 존재 여부

결론:

```text
YES: 19개
```

근거:

```text
candidate_pool=27
slots=8
waitlist=19
재평가 기준 waitlist_should_buy_true=19
```

코드 근거:

```text
data/_system/ops/live_candidate_slots.py:414-418
  if not ev.get('should_buy'): continue
  pool.append(public_candidate_row(...))
```

즉 `candidate_pool` 자체가 should_buy=True 통과 후보만 담는 구조다. 이후:

```text
data/_system/ops/live_candidate_slots.py:318-340
  sort_candidate_pool(rows)
  for idx in range(SLOT_COUNT): top 8 -> slots
  state['waitlist'] = pool[SLOT_COUNT:]
```

waitlist는 should_buy=False 탈락 목록이 아니라, should_buy=True pool의 slot overflow다.

---

## 6. K=20 슬롯 정책 확인

### 6.1 live_candidate_slots 운영 state는 8-slot 설계

코드:

```text
data/_system/ops/live_candidate_slots.py:1
  "Live 8-slot candidate display tool."

data/_system/ops/live_candidate_slots.py:43
  SLOT_COUNT = 8

data/_system/ops/live_candidate_slots.py:327
  for idx in range(SLOT_COUNT):

data/_system/ops/live_candidate_slots.py:340
  state['waitlist'] = pool[SLOT_COUNT:]
```

판정:

```text
live_slots_state의 slots/current_slots는 8개 상한으로 설계되어 있다.
20종목을 강제로 slots에 채우는 구조가 아니다.
```

### 6.2 real dashboard API는 max_slots query로 표시 개수 요청 가능

코드:

```text
engine/live/real_dashboard_api.py:923
  def _real_candidate_slots_payload(max_slots: int = 8)

engine/live/real_dashboard_api.py:931
  limit = max(1, int(max_slots or 8))

engine/live/real_dashboard_api.py:937-956
  pool = state['candidate_pool'] if present
  ordered = _sort_live_slot_pool(pool)
  for idx in range(limit): out.append(...)
```

엔드포인트:

```text
engine/live/real_dashboard_api.py:2826-2828
  /api/real/candidate_slots?max_slots=N
```

실제 read-only GET 확인:

```text
/api/real/candidate_slots              -> count 8
/api/real/candidate_slots?max_slots=20 -> count 20
```

`max_slots=20` 응답의 candidate_id 상위 20개:

```text
stage3:BMI:07d4ee0f7841
stage3:BMA:0c978464f9dd
stage3:BTBT:363898884d44
stage3:ADMA:42437a3ee595
stage3:CE:998b0b638c66
stage3:BCS:5e7da5a74b01
stage2:ALGT:402f72d48c3c
stage3:ALGT:aec5dd5b1dc1
stage3:ADPT:78c31f1ca209
stage2:CMC:4f6ee2739add
stage3:ANET:fe220620802b
stage3:ARKG:50b05b8de94f
stage3:BKSY:f1bcc8efea02
stage2:FIX:cab7d458767d
stage3:BB:f1bdfe7f8ad9
stage3:CDE:ceb9fe0512dc
stage3:CIEN:2ed675d30868
stage3:BWXT:f195725cb792
stage2:CEF:fe84c0ad85d8
stage3:ARKW:296c057b4ef7
```

판정:

```text
real dashboard API의 max_slots=20은 candidate_pool에서 최대 20개를 표시 요청하는 기능이다.
20종목을 강제 충족하는 로직은 아니다. pool이 20개 미만이면 빈 row가 섞일 수 있고, pool이 충분하면 상위 20개를 보여준다.
```

### 6.3 export script의 --limit 20 의미

코드:

```text
scripts/export_real_dashboard_buy_candidates.py:616-617
  --source-section choices=(slots, candidate_pool, waitlist)
  --limit default=8
```

현재 write 실행 상태:

```text
source_section: slots
limit: 20
live_slot_count: 8
exported_count: 8
```

판정:

```text
--source-section slots --limit 20은 slots section에서 최대 20개를 가져오라는 뜻이다.
하지만 live_slots_state.slots 자체가 8개라 export는 8개만 된다.
20개 정규 후보를 export하려면 별도 지시로 --source-section candidate_pool --limit 20을 사용해야 한다.
```

---

## 7. 최종 결론

```text
1. 현재 정규 파일 8개 후보는 모두 should_buy=True 상태 유지 중이다.
2. waitlist 19개도 모두 should_buy=True다.
3. waitlist는 신호 탈락이 아니라 8-slot 상한에서 final_score 순으로 밀린 대기열이다.
4. live_candidate_slots 운영 state는 명시적 8-slot 설계다.
5. /api/real/candidate_slots?max_slots=20은 candidate_pool에서 최대 20개를 보여줄 수 있으나, 20종목 강제 충족 정책은 아니다.
6. 현재 정규 파일이 8개인 이유는 export를 --source-section slots로 실행했기 때문이며, slots section 자체가 8개라 limit=20이어도 8개만 export된다.
```
