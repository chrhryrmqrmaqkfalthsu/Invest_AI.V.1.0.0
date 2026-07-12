# 지난 5일 기반 후보 필터/게이트 전수 조사 + CRS 통과 경위

- 대상: `stage3:CRS:8695c9ce3320`
- 최초 신호: `2026-07-09 13:20:33.590054 ET`
- 최초 가격: `600.8599853515625`
- 최초 점수/threshold: `2.971797614887265 / 2.5574757832651467`
- 기존 근거 재사용: `data/_system/analysis/crs_full_forensics_20260711/`
- 원본 코드 변경: **0**

## 최종 결론

### 1. 후보 풀 선정 단계의 “지난 5일 차단 게이트”

**NOT_FOUND.**

CRS가 최초 신호로 `live_slots_state.json::first_seen_signals`에 들어간 경로는 `data/_system/ops/live_candidate_slots.py::refresh_slots()`다. 이 경로는 다음만 사용한다.

1. 사전 KEEP gate
2. `evaluate_candidate()`의 `should_buy=True`, 즉 `final_score >= threshold`
3. final score 중심 정렬

코드는 entry-quality allow/block을 slot eligibility나 ordering에 사용하지 않는다고 명시한다.

- `data/_system/ops/live_candidate_slots.py:351-366`
- `data/_system/ops/live_candidate_slots.py:386-415`

따라서 CRS 후보 풀 통과 시에는 5일 고점·저점·range·ret_5d 기반 차단 판정 자체가 실행되지 않았다.

### 2. 5일 기반 로직은 어디에 있었나

실제 live 계열에는 두 부류가 있었다.

- **신호 점수 요소**
  - MA5가 포함된 `MA5 > MA20 > MA60` 정배열
  - 5일 평균 거래량 기반 `Volume_ratio`
- **별도 elite shadow 가상 OPEN 품질 필터/순위 요소**
  - MA5 대비 위치
  - 최근 5일 고점/저점
  - 5일 수익률
  - 5일 range 파생값

후자는 `engine/live/elite_shadow_entry_quality.py`와 `engine/live/elite_entry_concentration.py`에 존재하지만 후보 풀 선정을 차단하지 않는다. `elite_shadow_trader.py`에서 가상 포지션 OPEN 직전에만 적용된다.

연구용 `scripts/research/run_stage2_path_filter.py`에는 5일 고점 이후 경과일, range 위치, 고점 대비 pullback을 직접 차단하는 유전자 로직이 있지만 live candidate 경로에 연결된 흔적은 찾지 못했다. 판정은 `RESEARCH_ONLY_NOT_LIVE`다.

## CRS 최초 신호 시점 5일 값

정확히 저장된 값:

- 신호 시각: `2026-07-09T17:20:33.590054+00:00`
- 신호 가격: `600.8599853515625`
- 정배열 component: `+1.073432530260209`
- volume component: `0.0`
- 최초 최종점수: `2.971797614887265`

5일 OHLCV 원본 frame은 신호 시점 payload로 저장되지 않았다. 따라서 MA5/high5/low5 등은 동일한 `auto_adjust=False` historical path를 2026-07-12에 재조회해 복원했으며 `PARTIALLY_RECOVERED`로 표시한다.

사용한 완료 일봉 5개:

- 2026-07-01 close `610.1599731445312`
- 2026-07-02 close `597.239990234375`
- 2026-07-06 close `619.25`
- 2026-07-07 close `590.3499755859375`
- 2026-07-08 close `587.6300048828125`

재구성 결과:

| 항목 | 값 | 판정 |
|---|---:|---|
| MA5 | 600.9259887695313 | PARTIALLY_RECOVERED |
| 가격-MA5 거리 | -0.0109836185% | MA5 바로 아래 |
| MA20 | 581.2125 | PARTIALLY_RECOVERED |
| MA60 | 488.610834757487 | PARTIALLY_RECOVERED |
| 정배열 | MA5 > MA20 > MA60 | RECOVERED component와 정합 |
| 5일 고점 | 625.989990234375 | PARTIALLY_RECOVERED |
| 5일 저점 | 576.1599731445312 | PARTIALLY_RECOVERED |
| 고점 대비 | -4.0144419679% | 5일 고점 근접 0.125%가 아님 |
| 저점 대비 | +4.2870059286% | 5일 저점 회복 4% tier |
| 5일 range 위치 | 49.5685405094% | range 중앙 부근 |
| 5일 수익률 | -2.5906297919% | 과열 조건 아님 |
| Volume_MA5 | 673,840 | PARTIALLY_RECOVERED |
| Volume_ratio | 0.8258637065 | threshold 1.2 미달 |

## 점수 기여 분해

최초 점수의 저장 component는 다음과 같다.

- MA 정배열: `+1.073432530260209`
- RSI: `+1.6475902670733407`
- BB: `+1.9410509060649137`
- MACD: `0`
- 5일 평균 거래량 기반 volume: `0`
- news: `0`
- news topics: `0`
- Event: `-1.6902760885111983`
- 시장 multiplier: `1.0`

합계는 `2.971797614887265`와 정확히 일치한다.

5일 기반 점수 기여는 다음과 같이 판정한다.

- 정배열 전체 component: `+1.073432530260209`
- 거래량 component: `0`
- 5일 기반 score total: `+1.073432530260209`

주의: 정배열 weight는 MA5만 독립 가산하는 구조가 아니라 `MA5 > MA20 > MA60` 전체 binary 조건에 한 번에 부여된다. 따라서 `+1.0734325`를 “MA5 단독 기여”로 더 세분할 수는 없다.

## Shadow OPEN 품질 게이트 replay

동일 코드와 재구성 일봉으로 replay하면:

- 5일 저점 대비 +4.287%: `+10`
- 1일 follow-through +1.780%: `+8`
- MA20 위: `+8`
- 기타 조건 합산 후 quality score: `31`
- `q_score < 45`: block

즉 별도 elite shadow OPEN 경로에서는 차단됐을 가능성이 높다. 하지만 exact historical `entry_quality` payload는 저장되지 않아 이 판정은 `PARTIALLY_RECOVERED / INFERRED_REPLAY`다. 또한 이것은 후보 풀 최초 신호 통과와 모순되지 않는다. 두 경로가 다르기 때문이다.

## 고점대비 -0.125% 해석

기존 포렌식의 `-0.125%`는 신호 이후 관측된 당일 session high `601.61`과 최초 가격 `600.859985...`의 차이다. 이는 5일 daily high가 아니다.

5일 daily high는 재구성상 `625.989990...`이며 최초 가격은 그보다 약 `4.014%` 낮았다.

따라서 다음을 확정한다.

- “5일 고점 바로 아래라서 기존 5일 게이트가 통과했다”는 설명은 틀림.
- 후보 pool에는 5일 고점 차단 게이트 자체가 없었음.
- shadow concentration 로직은 오히려 `dist_high5`가 -6%~0%이면 가점을 준다.
- 고점 근접 진입을 막으려면 research path filter의 `min_pullback_from_high5` 같은 별도 live gate가 필요하지만 당시 live 선정 경로에는 없었다.

## NOT_FOUND / NOT_STORED

- 후보 풀의 5일 high/low/range 차단 게이트: `NOT_FOUND`
- 신호 시점 exact OHLCV frame: `NOT_STORED`
- 신호 시점 exact entry_quality payload: `NOT_STORED`
- research 5-day path filter의 live wiring: `NOT_FOUND`

세부 표:

- `five_day_logic_inventory.csv`
- `crs_signal_five_day_values.csv`
- `score_contribution_breakdown.csv`
- `pass_verdict.csv`
