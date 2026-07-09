# SAFETY: dashboard-real fallback 직접 주문 차단 guard 변경 readout

목적:

```text
_create_real_buy_intent()에서 broker.place_buy 호출 직전에 safety guard를 추가한다.
fallback compact candidate payload(real_candidate_fallback=True 또는 candidate_source=live_slots_state_fallback)는 full rulebook 검증이 없으므로 live 주문을 거부한다.
정규 후보 row 경로는 기존 동작을 유지한다.
```

변경 범위:

```text
수정 파일: engine/live/real_dashboard_api.py
수정 함수: _create_real_buy_intent()
수정 위치: if _direct_orders_enabled(): 직후, broker = _get_real_broker() 호출 전
수정 외 범위: 후보 산출/룰북 로딩/청산/정규 후보 로더/자동매매 경로 변경 없음
```

---

## 1. 변경 내용

추가된 guard 조건:

```text
candidate.get('real_candidate_fallback') is truthy
OR
candidate.get('candidate_source') == 'live_slots_state_fallback'
```

발동 시 동작:

```text
- broker = _get_real_broker() 호출 전 종료
- broker.get_current_price() 호출 없음
- broker.place_buy() 호출 없음
- intent row를 status='rejected'로 저장
- execution_mode='blocked_fallback_candidate_no_verified_full_rulebook'
- rejection_reason='REJECTED: fallback candidate has no verified full rulebook — order blocked for safety'
- live_slots_events에 DASHBOARD_REAL_BUY_REJECTED 이벤트 기록
- 예외를 raise하지 않고 row를 정상 반환
```

정규 후보 row 동작:

```text
real_candidate_fallback이 없고 candidate_source가 live_slots_state_fallback이 아니면 기존 direct order branch를 그대로 탄다.
```

---

## 2. 변경 diff

```diff
diff --git a/engine/live/real_dashboard_api.py b/engine/live/real_dashboard_api.py
index 7f863ea..e4ab11e 100644
--- a/engine/live/real_dashboard_api.py
+++ b/engine/live/real_dashboard_api.py
@@ -732,6 +732,36 @@ def _create_real_buy_intent(req: RealBuyIntentRequest) -> dict[str, Any]:
         }
     )
     if _direct_orders_enabled():
+        is_fallback_candidate = bool(candidate.get("real_candidate_fallback")) or str(candidate.get("candidate_source") or "") == "live_slots_state_fallback"
+        if is_fallback_candidate:
+            reason = "REJECTED: fallback candidate has no verified full rulebook — order blocked for safety"
+            row.update(
+                {
+                    "status": "rejected",
+                    "rejected_at": utc_now_iso(),
+                    "rejection_reason": reason,
+                    "execution_mode": "blocked_fallback_candidate_no_verified_full_rulebook",
+                    "order_blocked": True,
+                    "broker_order": None,
+                }
+            )
+            _append_live_slot_event(
+                {
+                    "event": "DASHBOARD_REAL_BUY_REJECTED",
+                    "time": row.get("rejected_at"),
+                    "candidate_id": candidate_id,
+                    "ticker": ticker,
+                    "source": row.get("source"),
+                    "execution_mode": row.get("execution_mode"),
+                    "status": row.get("status"),
+                    "reason": reason,
+                    "candidate_source": candidate.get("candidate_source"),
+                    "real_candidate_fallback": bool(candidate.get("real_candidate_fallback")),
+                }
+            )
+            intents[intent_id] = row
+            _write_intent_state(REAL_BUY_INTENT_PATH, data)
+            return row
         broker = _get_real_broker()
         if broker is None:
             raise ValueError(f"real broker unavailable: {_real_broker_error}")
```

---

## 3. 검증 결과

검증 방식:

```text
실제 Alpaca 호출 없음.
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1을 테스트 프로세스에만 부여.
REAL_BUY_INTENT_PATH와 LIVE_SLOTS_EVENTS_PATH는 /tmp 임시 파일로 monkeypatch.
_candidate_for_real()은 테스트 후보 dict를 반환하도록 monkeypatch.
_get_real_broker()는 FakeBroker 또는 호출 카운터로 monkeypatch.
```

명령:

```text
venv/bin/python -m py_compile engine/live/real_dashboard_api.py
KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1 PYTHONPATH=. venv/bin/python <inline guard test>
```

결과:

```text
PY_COMPILE_OK
FALLBACK_GUARD_TEST_OK
```

### 3.1 fallback candidate 요청 검증

테스트 후보:

```text
candidate_id: stage3:FBK:test
ticker: FBK
candidate_source: live_slots_state_fallback
real_candidate_fallback: True
notional: 100.0
price: 10.0
```

검증 결과:

| 항목 | 결과 |
|---|---:|
| _create_real_buy_intent() 예외 발생 | NO |
| returned status | rejected |
| returned execution_mode | blocked_fallback_candidate_no_verified_full_rulebook |
| returned order_blocked | True |
| rejection_reason contains verified full rulebook | True |
| _get_real_broker() 호출 | 0 |
| broker.get_current_price() 호출 | 0 |
| broker.place_buy() 호출 | 0 |
| 임시 intent file status | rejected |
| 임시 live_slots_events event | DASHBOARD_REAL_BUY_REJECTED |

판정:

```text
fallback candidate는 broker.place_buy 이전에 정상 거부된다.
```

### 3.2 정규 candidate 요청 검증

테스트 후보:

```text
candidate_id: stage3:REG:test
ticker: REG
status: pending
manual_buy_enabled: True
candidate_source: absent
real_candidate_fallback: absent
notional: 100.0
price: 10.0
```

검증 결과:

| 항목 | 결과 |
|---|---:|
| _create_real_buy_intent() 예외 발생 | NO |
| returned status | submitted |
| returned execution_mode | direct_alpaca_live_market_order |
| broker.get_current_price() 호출 | 1 |
| broker.place_buy() 호출 | 1 |
| fake broker_order.order_id | fake-order-id |

판정:

```text
정규 candidate는 기존 direct order branch를 정상 통과한다.
```

---

## 4. 최종 판정

```text
SAFETY_GUARD_ADDED
FALLBACK_CANDIDATE_BLOCKED_BEFORE_BROKER
REGULAR_CANDIDATE_PATH_UNCHANGED
```

남은 범위:

```text
fallback 시 full 룰북 재조회 후 통과시키는 방식은 이번 작업 범위가 아니며 구현하지 않았다.
후보 산출, 룰북 로딩, 청산 로직, 정규 후보 로더는 수정하지 않았다.
```
