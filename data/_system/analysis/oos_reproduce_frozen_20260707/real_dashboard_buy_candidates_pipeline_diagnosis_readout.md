# real_dashboard_buy_candidates 파이프라인 진단 readout

범위:

```text
대상 파일: data/_system/real_dashboard_buy_candidates.json
관련 표시 후보: data/_system/live_slots_state.json
관련 코드: engine/live/real_dashboard_api.py, engine/live/real_dashboard_holding_days_patch.py, data/_system/ops/live_candidate_slots.py
수정/재생성 원칙: 후보값·룰북 수동 조작 금지. 정식 생성 경로 확인 후에만 재생성.
```

최종 판정:

```text
PATH_CONFIG_ISSUE
```

재생성 여부:

```text
NOT_REGENERATED
```

재생성하지 않은 이유:

```text
real_dashboard_buy_candidates.json을 full rulebook 포함 정규 후보 파일로 생성/갱신하는 정식 생성 함수 또는 스케줄이 현재 코드에서 발견되지 않았다.
따라서 live_slots_state 값을 변환해 채우는 것은 새 로직/수동 조작에 해당한다.
요청 조건상 정식 생성 경로가 없으면 재생성하면 안 되므로 중단했다.
```

---

## 1. 현재 real_dashboard_buy_candidates.json 상태

파일 상태:

```text
path: data/_system/real_dashboard_buy_candidates.json
exists: YES
mtime_utc: 2026-07-07 17:17:30.511494461 +0000
mtime_kst: 2026-07-08 02:17:30.511494 KST
size: 433 bytes
```

JSON 내용:

```text
schema_version: 1
source: real_dashboard_buy_candidates
buy_mode: real_isolated
trade_date: ''
updated_at: ''
manual_buy_enabled: True
candidates_type: dict
candidates_count: 0
```

동반 real-dashboard 전용 파일:

```text
data/_system/real_dashboard_rulebooks.json: []
data/_system/real_dashboard_universe.json: count=0, updated_at='', items=[]
```

판정:

```text
파일은 존재하지만 내부적으로 갱신된 후보 state가 아니다.
현재 후보 0개는 계산 결과로 0이 나온 상태가 아니라, 실거래 전용 후보 파일이 비어 있는 placeholder 상태다.
```

---

## 2. 누가/언제/어떤 트리거로 갱신하도록 되어 있는지

### 2.1 코드 검색 결과

검색 대상:

```text
engine/
scripts/
api_server*.py
config/
```

확인된 참조:

```text
engine/live/real_dashboard_api.py
  REAL_BUY_CANDIDATES_PATH = Path('data/_system/real_dashboard_buy_candidates.json')
  _real_candidate_state()에서 read_json(REAL_BUY_CANDIDATES_PATH, {})
  _candidate_for_real()에서 candidates[cid] 조회
  /api/real/central_candidates에서 _real_candidate_state() 반환
  _create_real_buy_intent()에서 candidate_state_path metadata로 사용

engine/live/real_dashboard_holding_days_patch.py
  _patch_candidate_lookup_for_real_buy()에서 정규 조회 실패 시 live_slots_state fallback 사용
```

확인되지 않은 것:

```text
real_dashboard_buy_candidates.json에 write하는 생성 함수: NOT_FOUND
atomic_write_json(REAL_BUY_CANDIDATES_PATH, ...): NOT_FOUND
write_json(REAL_BUY_CANDIDATES_PATH, ...): NOT_FOUND
정규 후보 파일 생성 스크립트: NOT_FOUND
정규 후보 파일 갱신 cron/systemd/timer: NOT_FOUND
마지막 성공 생성 로그: NOT_FOUND
```

### 2.2 현재 스케줄

crontab/config 확인:

```text
config/cron/kingmaker_crontab
  dashboard_guard.sh: 1분 감시
  live_candidate_slots_guard.sh: 1분 감시
```

실제 crontab:

```text
* * * * * /usr/bin/flock -n /tmp/kingmaker_dashboard_guard.lock /home/g3000kkw/kingmaker/scripts/dashboard_guard.sh
@reboot /usr/bin/flock -n /tmp/kingmaker_dashboard_guard.lock /home/g3000kkw/kingmaker/scripts/dashboard_guard.sh
* * * * * /usr/bin/flock -n /tmp/kingmaker_live_candidate_slots_guard.lock /home/g3000kkw/kingmaker/scripts/live_candidate_slots_guard.sh
@reboot /usr/bin/flock -n /tmp/kingmaker_live_candidate_slots_guard.lock /home/g3000kkw/kingmaker/scripts/live_candidate_slots_guard.sh
```

확인 결과:

```text
live_candidate_slots는 스케줄/감시가 있다.
real_dashboard_buy_candidates.json 전용 생성/갱신 스케줄은 없다.
```

### 2.3 API 경로에서의 real 후보 파일 용도

`engine/live/real_dashboard_api.py`:

```text
615 def _default_real_candidate_state() -> dict[str, Any]:
616     return {
...
628         'candidates': {},
629         'note': '실거래 대시보드 전용 매수 후보 파일이 아직 없거나 비어 있습니다.',
630     }

633 def _real_candidate_state(*, include_blocked: bool = False) -> dict[str, Any]:
634     data = read_json(REAL_BUY_CANDIDATES_PATH, {})
...
652     candidates = {
653         str(cid): dict(row)
654         for cid, row in (state.get('candidates') or {}).items()
...
672     state['candidates'] = candidates
673     return state
```

단건 조회:

```text
676 def _candidate_for_real(candidate_id: str) -> dict[str, Any]:
680     state = _real_candidate_state(include_blocked=True)
681     row = (state.get('candidates') or {}).get(cid)
682     if not isinstance(row, dict):
683         raise ValueError(f'real candidate not found or stale: {cid}')
```

판정:

```text
real_dashboard_buy_candidates.json은 현재 API에서 읽기 전용 source로만 쓰인다.
이 파일을 live 후보에서 자동 생성하는 경로는 없다.
```

---

## 3. 왜 candidates=0인가

가능성별 판정:

| 가설 | 판정 | 근거 |
|---|---|---|
| 후보 생성 로직이 살아 있고 실제 후보가 0개로 판정됨 | NO | 생성 함수/스케줄/성공 로그가 없다. live_candidate_slots는 현재 27개 eligible 후보를 계산 중이다. |
| 생성 로직이 죽음 | PARTIAL_NO | 별도 생성 로직 자체가 발견되지 않는다. 죽은 데몬이라기보다 경로 미구현/미연결에 가깝다. |
| 경로·설정 문제 | YES | dashboard-real 표시 후보는 live_slots_state에서 오지만 정규 매수 후보는 real_dashboard_buy_candidates.json에서 읽는다. 두 파일 사이 sync가 없다. |
| live 후보가 실제로 0개 | NO | live_slots_state의 candidate_pool=27, slots=8, waitlist=19. |

현재 live 슬롯 상태:

```text
live_slots_state.updated_at: 2026-07-09T15:58:05.791535+00:00
last_refresh.time: 2026-07-09T15:58:05.791162+00:00
last_refresh.eligible_pool_count: 27
last_refresh.buy_signal_count: 27
candidate_pool: 27
slots: 8
waitlist: 19
```

결론:

```text
candidates=0은 LEGIT_ZERO_CANDIDATES가 아니다.
정규 real 후보 파일을 채우는 연결이 없기 때문에 0으로 남아 있다.
```

---

## 4. live_candidate_slots와 정규 후보 파일 사이 단절 지점

### 4.1 live_candidate_slots 산출 경로

`data/_system/ops/live_candidate_slots.py`:

```text
381 report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
382 candidates = (report.get('candidates') or [])[:max_candidates]
...
403 ev = evaluate_candidate(candidate, ctx=ctx)
414 if not bool(ev.get('should_buy')): continue
418 pool.append(public_candidate_row(candidate, ev, gate, spy))
...
451 pool = sort_candidate_pool(pool)
452 state['candidate_pool'] = pool
464 rebuild_slots_from_pool(state, reason='fresh_evaluation')
466 save_state(state)
```

저장 대상:

```text
data/_system/live_slots_state.json
```

### 4.2 public_candidate_row는 compact 표시 row를 만든다

`data/_system/ops/live_candidate_slots.py`:

```text
269 def public_candidate_row(candidate, ev, gate, spy):
273     rb = candidate.get('rulebook') if isinstance(candidate.get('rulebook'), dict) else {}
277     return {
278         'candidate_id': position_key(candidate),
279         'ticker': ...,
282         'rulebook_hash_short': ...,
284         'final_score': ...,
...
310         'max_holding_days': ...,
311         'exit_strategy': ...,
312         'stop_loss_atr': ...,
313         'take_profit_atr': ...,
314         'trailing_atr': ...,
315     }
```

확인 결과:

```text
public_candidate_row 출력에는 full rulebook / selected_rulebook / source_file이 없다.
현재 live_slots_state의 candidate_pool/slots/waitlist도 full rulebook 0개, selected_rulebook 0개다.
```

현재 live_slots_state full 룰북 존재 확인:

| section | rows | full_rulebook>=50 | selected_rulebook>=50 | source_file present |
|---|---:|---:|---:|---:|
| candidate_pool | 27 | 0 | 0 | 0 |
| slots | 8 | 0 | 0 | 0 |
| waitlist | 19 | 0 | 0 | 0 |

예시:

```text
candidate_pool top3:
- stage3:BMI:07d4ee0f7841 / BMI / rulebook_hash_short=07d4ee0f7841 / full rulebook absent / selected_rulebook absent / source_file absent
- stage3:BMA:0c978464f9dd / BMA / rulebook_hash_short=0c978464f9dd / full rulebook absent / selected_rulebook absent / source_file absent
- stage3:BTBT:363898884d44 / BTBT / rulebook_hash_short=363898884d44 / full rulebook absent / selected_rulebook absent / source_file absent
```

### 4.3 full 룰북은 평가 중에는 존재하지만 저장되지 않는다

`engine/live/elite_shadow_trader.py`:

```text
397 rb_dict = _load_rulebook_for_candidate(candidate)
402 rb = Rulebook.from_dict(rb_dict)
...
468 'rulebook': rb,
469 'rulebook_dict': rb_dict,
```

하지만 `public_candidate_row()`는 `ev['rulebook_dict']` 또는 full `ev['rulebook']`를 output에 저장하지 않는다.

결론:

```text
live_candidate_slots는 full rulebook을 평가 시점에 로드하지만, live_slots_state에는 compact 표시 payload만 저장한다.
정규 real 후보 파일은 이 compact payload를 자동으로 받아 full 후보로 승격하는 생성 경로가 없다.
```

---

## 5. dashboard-real 매수 경로와 SAFETY guard 영향

정규 경로:

```text
_create_real_buy_intent()
  -> _candidate_for_real(candidate_id)
  -> _real_candidate_state(include_blocked=True)
  -> real_dashboard_buy_candidates.json candidates[cid]
```

현재 정규 파일:

```text
candidates_count=0
```

따라서 정규 조회 결과:

```text
real candidate not found or stale: {cid}
```

fallback patch:

```text
engine/live/real_dashboard_holding_days_patch.py:137-169
  원본 _candidate_for_real()이 'not found or stale'이면 live_slots_state에서 같은 candidate_id를 찾아 fallback row 반환
  out.setdefault('candidate_source', 'live_slots_state_fallback')
  out.setdefault('real_candidate_fallback', True)
```

방금 추가된 SAFETY guard:

```text
engine/live/real_dashboard_api.py:734-764
  if direct_orders_enabled and fallback candidate이면
  status='rejected'
  execution_mode='blocked_fallback_candidate_no_verified_full_rulebook'
  broker 호출 전 return
```

현재 결과:

```text
정규 real 후보 파일이 비어 있어 정규 경로를 탈 수 없다.
fallback은 compact live_slots_state row라 SAFETY guard에서 거부된다.
```

---

## 6. 재생성 판단

요청 조건:

```text
진단 결과 후보를 정상 생성할 수 있는 상태면, 정규 후보 파일을 정식 생성 경로로 재생성한다.
수동으로 값을 꾸며 넣지 말 것.
반드시 원래 생성 로직을 통해서만.
full rulebook / selected_rulebook이 포함되는지 검증.
```

확인 결과:

```text
정식 생성 경로: NOT_FOUND
full rulebook 포함 정규 후보 파일 생성 함수: NOT_FOUND
real_dashboard_buy_candidates.json 전용 스케줄: NOT_FOUND
live_slots_state → real_dashboard_buy_candidates.json sync 경로: NOT_FOUND
```

재생성 여부:

```text
NOT_REGENERATED
```

이유:

```text
현재 가능한 재생성은 live_slots_state compact row를 변환하거나 build_elite_shadow_report/evaluate_candidate로 새 생성기를 만드는 방식뿐이다.
이는 원래 생성 로직 실행이 아니라 새 로직/수동 가공에 해당한다.
따라서 이번 지시 조건상 실행하지 않았다.
```

---

## 7. 최종 판정

```text
PATH_CONFIG_ISSUE
```

판정 근거:

```text
1. live_candidate_slots는 정상 동작한다.
   latest live_slots_state: candidate_pool=27, slots=8, waitlist=19.

2. real_dashboard_buy_candidates.json은 비어 있고 내부 updated_at도 없다.

3. real_dashboard_buy_candidates.json을 갱신하는 생성 함수/스케줄/성공 로그가 없다.

4. dashboard-real 표시 후보는 live_slots_state를 사용한다.
   반면 정규 매수 경로는 real_dashboard_buy_candidates.json을 사용한다.

5. live_slots_state는 compact 표시 payload이며 full rulebook/selected_rulebook/source_file을 저장하지 않는다.

6. 따라서 정규 매수 후보 파일이 비어 있는 것은 후보가 없어서가 아니라, 표시 후보 파이프라인과 정규 주문 후보 파이프라인이 연결되어 있지 않기 때문이다.
```

---

## 8. 다음 단계 후보 — 이번 작업에서는 미실행

이번 readout은 진단만 수행했다. 가능한 후속 작업은 별도 지시가 필요하다.

```text
A. live_candidate_slots 평가 결과에서 full rulebook_dict를 보존해 real_dashboard_buy_candidates.json 정규 후보 파일을 생성하는 공식 exporter/daemon 추가.
B. dashboard-real 정규 매수 경로가 real_dashboard_buy_candidates.json 대신 full-rulebook 재검증된 candidate provider를 사용하도록 변경.
C. s2_auto_trader의 _candidate_full_payload / selected_rulebook guard 패턴을 dashboard-real 정규 후보 생성에도 적용.
```

주의:

```text
이번 요청에서는 후보 산출, SAFETY guard, 청산 로직, live_candidate_slots 계산 로직을 수정하지 않았다.
정규 후보 파일도 재생성하지 않았다.
```
