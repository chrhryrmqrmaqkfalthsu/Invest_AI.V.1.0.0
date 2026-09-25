# swap_guard_queue_v0 — 큐 + 자연청산 임박 가드 백테스트

## 제약 준수

- batch PID `1507266`, paper PID `2106002` 및 자식 프로세스 kill/restart 없음.
- 푸시 없음. 로컬 커밋만 수행 대상.
- 코드 변경 범위: `engine/central/backtester.py`.
- `decide_buys()` 점수 계산식·allocation 로직은 변경하지 않았다.
- `swap_guard_queue_enabled=false`일 때 기존 동작 동일성 회귀 테스트 통과.
- 신규 산출물은 `data/_system/central/stage2_b/swap_guard_queue_v0/` 안에만 작성.

---

## 구현 요약

새 인자:

```text
swap_guard_queue_enabled: bool = False
exit_imminence_threshold: float = 0.75
turnover_guard: float = 0.0
queue_signal_ttl: int = 5
```

이번 백테스트 설정:

```text
swap_guard_queue_enabled = true
exit_imminence_threshold = 0.75
turnover_guard = 8.0
queue_signal_ttl = 5
```

동작 위치:

```text
_process_exits()
SignalCollector.collect()
decide_buys()
_execute_score_swaps()
_execute_swap_guard_queue()
normal buy execution
```

`swap_guard_queue_enabled=false`면 guard/queue 상태 자체를 만들지 않는다. 결과 객체에도 `swap_guard_queue_stats`가 붙지 않는다.

---

## 자연청산 임박도 proxy — 누수 방지

proxy는 미래를 보지 않는다.

사용한 정보:

```text
- position entry_date
- position max_holding_days
- current-day close
- current-day ATR
- position stop_price
- position target_price
- position trailing_stop
```

명시적으로 쓰지 않은 정보:

```text
- 미래 종가
- 미래 고가
- 실제 미래 자연청산일
- 미래 B 수익
- segment end 이후 가격
```

proxy 구성:

```text
exit_imminence_score = max(
  time_imminence,
  take_profit_imminence,
  stop_imminence,
  trailing_imminence
)
```

점수가 높을수록 현재 시점 정보만으로 곧 청산될 가능성이 높다고 본다. `exit_imminence_score >= 0.75`인 A는 swap 대상에서 제외한다.

코드 통계에도 다음이 기록된다.

```text
proxy_uses_future_data = false
```

---

## 회귀 테스트

Smoke 구간:

```text
2025-07-01 ~ 2025-08-15
first 80 stage2 survivor entities
```

비교:

```text
default arguments
vs
swap_guard_queue_enabled=false explicit
```

결과:

```text
passed = true
final_equity 동일
return 동일
MDD 동일
trade sequence 동일
equity curve 동일
rejected orders 동일
swap_guard_queue_stats 없음
```

수치:

```text
final_equity = 10664.632844771546
return = +6.646328447715459%
MDD = -2.693648571664735%
trades = 70
rejected_orders = 1
```

---

## OOS 결과

기간:

```text
2025-07-01 ~ 2026-06-15
```

| version | return | MDD | trades | turnover | rejected | reconcile |
|---|---:|---:|---:|---:|---:|---:|
| baseline/off | +64.66% | -5.81% | 476 | 7609.01% | 3 | 0 |
| guard_queue_v0/on | +53.66% | -8.79% | 548 | 8600.69% | 2 | 0 |
| delta | -11.00pp | -2.98pp | +72 | +991.67pp | -1 | 0 |

OOS 판정:

```text
비열위 실패.
return -11.00pp, MDD도 -2.98pp 악화.
```

### OOS 큐/가드 작동

```text
swap_executed = 27
queue_registered = 64
queue_converted = 22
queue_conversion_rate = 34.38%
queue_signal_lost = 41
queue_expired = 1
queued_entry_trades = 22
```

큐는 실제로 작동했다. 다만 큐/스왑이 수익 개선으로 이어지지 않았다.

### OOS winner clipping

v0 swap exit 기준:

```text
swap_exit_count = 27
valid future paths = 27
future peak >= +10%: 21 / 27 = 77.78%
future peak >= +15%: 18 / 27 = 66.67%
```

기존 swap_combo OOS의 +15% clipping rate는 대략 76.9%~100%였다. 따라서 v0는 clipping을 일부 낮췄지만, 여전히 매우 높다.

### OOS premature exit

baseline 자연청산과 비교 가능한 v0 swap exit:

```text
valid = 20
within 5 trading days = 7 / 20 = 35.00%
within 10 trading days = 13 / 20 = 65.00%
median days to baseline exit = 7.0d
```

즉 자연청산 임박 A를 충분히 줄이지 못했다. 핵심 gate 실패다.

---

## Stress 결과

기간:

```text
2022-01-01 ~ 2022-06-30
```

| version | return | MDD | trades | turnover | rejected | reconcile |
|---|---:|---:|---:|---:|---:|---:|
| baseline/off | +0.49% | -12.97% | 264 | 3597.41% | 9 | 0 |
| guard_queue_v0/on | +0.27% | -12.80% | 292 | 3878.82% | 6 | 0 |
| delta | -0.22pp | +0.18pp | +28 | +281.41pp | -3 | 0 |

Stress 판정:

```text
부분 통과.
수익은 -0.22pp 소폭 열위지만, MDD는 +0.18pp 개선.
기존 swap_combo처럼 Stress에서 크게 무너지는 구조는 아니다.
```

### Stress 큐/가드 작동

```text
swap_executed = 8
queue_registered = 32
queue_converted = 12
queue_conversion_rate = 37.50%
queue_signal_lost = 19
queue_expired = 0
queued_entry_trades = 12
```

### Stress winner clipping

```text
swap_exit_count = 8
valid future paths = 8
future peak >= +10%: 3 / 8 = 37.50%
future peak >= +15%: 1 / 8 = 12.50%
```

Stress clipping은 낮은 편이지만 기존 swap_combo Stress의 +15% clipping rate 0~10%보다 아주 약간 높다.

### Stress premature exit

```text
valid = 6
within 5 trading days = 3 / 6 = 50.00%
within 10 trading days = 4 / 6 = 66.67%
median days to baseline exit = 5.5d
```

Stress에서도 자연청산 임박 A를 충분히 피하지 못했다.

---

## exit_imminence proxy 정확도

### OOS

```text
matched_samples = 131
actual within10 = 108
high_pred_count = 28
true_positive = 27
precision = 96.43%
recall = 25.00%
median_days_high_pred = 3.0d
median_days_low_pred = 6.0d
```

해석:

```text
proxy가 high라고 찍은 A는 실제로 곧 자연청산될 가능성이 매우 높았다.
하지만 recall이 낮아, 곧 자연청산될 A를 많이 놓쳤다.
```

### Stress

```text
matched_samples = 34
actual within10 = 27
high_pred_count = 8
true_positive = 7
precision = 87.50%
recall = 25.93%
median_days_high_pred = 5.0d
median_days_low_pred = 6.0d
```

Stress도 같은 패턴이다. high prediction은 꽤 정확하지만, threshold 0.75가 너무 보수적이라 많은 임박 A를 low로 놓쳤다.

---

## 사전 gate 판정

| gate | 결과 | 판정 |
|---|---|---|
| Stress baseline 비열위 | return -0.22pp, MDD +0.18pp | 부분 통과 |
| OOS baseline 비열위 | return -11.00pp, MDD -2.98pp | 실패 |
| winner clipping 감소 | OOS +15% 66.67%, 기존 76.9~100%보다 낮음 | 부분 통과, 아직 높음 |
| premature exit 감소 | OOS 65%, Stress 66.67% within10 | 실패 |
| turnover 대비 순효과 | OOS/Stress 모두 turnover 증가, return 개선 없음 | 실패 |
| 큐 작동 | OOS 64 등록/22 전환, Stress 32 등록/12 전환 | 통과 |
| proxy 누수 방지 | current bar only, future natural exit 미사용 | 통과 |

---

## 최종 판정

```text
swap_guard_queue_v0는 폐기.
```

이유:

1. Stress에서 크게 무너지지는 않았지만 수익은 소폭 열위다.
2. OOS에서 baseline 대비 -11.00pp로 크게 열화했다.
3. winner clipping은 줄었지만 여전히 66.67%로 높다.
4. premature exit within10이 65~66.7%로 높아, 핵심 실패 원인을 충분히 제거하지 못했다.
5. turnover가 증가했는데 순효과가 음수다.
6. 큐는 작동했지만 수익/경로 개선으로 연결되지 않았다.

따라서 이 v0 위에 학습을 얹는 것은 비추천이다.

---

## 다음 판단

이 결과는 “큐 + 가드 구조 전체가 무의미하다”라기보다는, 현재 proxy/threshold/action 조합이 부족하다는 뜻이다.

특히 proxy 정확도는 다음을 보여준다.

```text
high prediction precision은 높다.
recall이 낮다.
```

즉, 임박도를 잡는 방향은 맞지만 너무 많은 임박 A를 놓친다. 다만 threshold를 결과 보고 튜닝하면 과적합이므로, 이 v0는 사전 게이트 기준으로 폐기하는 것이 맞다.

다음으로 갈 수 있는 선택지는 둘 중 하나다.

```text
1. cutoff-aware 인프라를 먼저 만든다.
2. 별도 v1 설계 단계에서 proxy recall을 높이는 새 규칙을 사전 고정한 뒤 다시 검증한다.
```

현재 결과만으로는 학습형 교체로 승격할 근거가 부족하다.
