# 지난 5일 기반 후보 필터·게이트 전수 조사와 CRS 통과 경위

- 대상: `stage3:CRS:8695c9ce3320`
- 최초 신호: `2026-07-09T17:20:33.590054+00:00` (`13:20:33 ET`)
- 최초 가격: `600.8599853515625`
- 최초 점수: `2.971797614887265`
- threshold: `2.5574757832651467`
- 코드 변경: **0**

## 최종 결론

실시간 후보 슬롯 선정에는 **“지난 5일 고점·저점·range를 보고 직접 거르는 차단 게이트”가 존재하지 않았다 (`NOT_FOUND`)**.

실제 후보 슬롯 통과 정책은 다음이었다.

1. 정적 `KEEP` 게이트
2. `evaluate_signal()` 실행 성공
3. `final_score >= threshold`
4. 통과 후보를 final score 중심으로 정렬

`data/_system/ops/live_candidate_slots.py:364-366`은 2026-07-08 이후 `entry_quality allow/block`을 슬롯 자격과 정렬에 사용하지 않는다고 명시한다. 따라서 Elite Shadow의 MA5·5일 고점·저점 기반 차단 조건은 CRS의 **후보 슬롯 최초 등재를 막을 수 없었다**.

CRS가 통과한 직접 이유는 `2.971797614887265 >= 2.5574757832651467`, 즉 `+0.41432183162211844`의 점수 여유가 있었기 때문이다.

## 1. 실제 live 후보 선정에 사용된 5일 요소

### MA5를 포함한 정배열 점수

- `engine/core/indicators.py:109`: `MA5 = Close.rolling(5).mean()`
- `engine/core/indicators.py:150-153`: `MA5 > MA20 > MA60`이면 `Aligned_bull=1`
- `engine/strategies/evaluator.py:64-78`: 정배열이면 `weight_ma_align` 전액 가산

이는 **점수 요소**이지 차단 게이트가 아니다.

CRS 복원값:

- MA5 `600.9259887695313`
- MA20 `581.2125`
- MA60 `488.610834757487`
- `MA5 > MA20 > MA60 = true`
- 정배열 기여 `+1.073432530260209`

신호가격은 MA5보다 `0.06600341796877274`달러, `0.010983618482518498%` 낮았다. 그러나 evaluator의 정배열은 **실시간 가격과 MA5의 비교가 아니라 완성 일봉의 MA5·MA20·MA60 배열**을 본다. 따라서 실시간 가격이 MA5보다 아주 조금 낮아도 정배열 점수는 유지됐다.

### 5일 평균 거래량 기반 점수

- `engine/core/indicators.py:135-137`: `Volume_MA5`, `Volume_ratio=Volume/Volume_MA5`
- `engine/strategies/evaluator.py:124-129`: ratio가 룰북 threshold 이상이면 거래량 점수 가산

CRS의 ratio는 약 `0.8259`, 룰북 기준은 `1.2`였다.

- threshold 대비 여유 `-0.3741`
- 거래량 점수 `0.0`

따라서 CRS는 5일 거래량 요소로 이득을 얻지 못했다.

## 2. 실제 live 후보 선정에 없던 5일 차단

슬롯 선정 루프 `data/_system/ops/live_candidate_slots.py:386-415`의 차단 사유는 다음뿐이다.

- gate missing
- `DROP_BAD_MAE_CAPTURE`
- 이미 보유 중
- evaluator 실패
- `should_buy=false`

이 루프에는 다음 조건이 없다.

- 최근 5일 고점 근접 차단
- 최근 5일 range 상단 차단
- 최근 5일 상승률 과열 차단
- 실시간 가격이 MA5보다 낮으면 차단
- 당일 고점 근접 차단

판정: `LIVE_SLOT_5D_HARD_BLOCK = NOT_FOUND`.

## 3. 별도 Shadow 경로에 존재하는 5일 차단

`engine/live/elite_shadow_entry_quality.py`에는 5일 기반 품질 점수와 차단이 실제 존재한다.

- 최근 5일 저점 회복: `81-82`, `127-135`
- MA5 위 여부: `79`, `100`, `150-152`
- MA5 아래 3% penalty: `178-180`
- 5일 고점에서 12% 이상 이격 penalty: `181-183`
- 5일 수익률 20% 이상 + MA20 이격 18% 이상 과열: `227`, `254-256`
- event-heavy + MA5 아래 + q<75 차단: `248-250`
- BB/RSI bottom-fishing + MA5 아래 + q<60 차단: `222`, `251-253`

그러나 이 경로는 Elite Shadow 가상 OPEN용이다. 후보 슬롯 코드가 명시적으로 EQ를 자격·정렬에서 제외했으므로 **CRS 슬롯 통과 경위와 분리해야 한다**.

신호시점 Shadow allow/block 최종 결과는 저장되지 않았다. 복원 가능한 사실은 다음이다.

- 신호가격은 MA5보다 `-0.01098%`
- 따라서 `above_ma5=false`
- CRS 이유에 BB와 RSI가 있었으므로 Shadow 코드상 `bottom_fishing=true`가 될 조건
- 최근 5일 저점 대비 `+4.0360%`로 품질 점수 `+10` 구간
- 최근 5일 고점 대비 `-4.0068%`로 고점 회복 실패 penalty 없음
- 5일 수익률 `-1.5274%`로 overheat 아님

하지만 q-score를 완전히 재현할 당시 원본 technical snapshot과 모든 입력이 저장되지 않았으므로 Shadow 최종 allow/block은 `PARTIALLY_RECOVERED`, 확정값은 `NOT_STORED`다.

## 4. CRS 신호 시점 5일 위치

기존 포렌식의 Alpaca IEX completed daily bars와 evaluator-input 재구성을 재사용했다.

- prior 5-session high: `625.94`
- prior 5-session low: `577.55`
- range width: `48.39`
- range position: `48.17107946179478%`
- high 대비: `-4.006776152416769%`
- low 대비: `+4.036011661598593%`
- 5-session window 첫 close 대비: `-1.527420539584623%`

즉 CRS는 **최근 5일 고점 추격 상태가 아니었고 range 중앙 부근**이었다.

반면 2026-07-09의 eventual session high는 `601.61`이었고 신호가격은 그보다 `-0.12466791583209957%` 낮았다. 이는 사후적으로 당일 고점 부근 진입이 맞다는 뜻이다. 그러나 eventual high는 13:20 ET 시점에 알 수 없는 미래 정보이며, 시스템에는 당시 running intraday high 기반 차단도 없었다.

따라서 “5일 고점 필터가 있었다면 막혔을 것”이라는 해석은 맞지 않는다. prior 5-session 기준으로 CRS는 고점에서 4% 아래였다.

## 5. 최초 점수 중 5일 기여

복원된 최초 점수:

| component | value |
|---|---:|
| ma_align | +1.073432530260209 |
| RSI | +1.6475902670733407 |
| BB | +1.9410509060649137 |
| volume | 0.0 |
| news | 0.0 |
| news topics | 0.0 |
| Event | -1.6902760885111983 |
| total | 2.971797614887265 |

5일과 연관된 component는 다음 두 개다.

1. `ma_align +1.073432530260209`: MA5가 포함된 joint binary condition
2. `volume 0.0`: 5일 평균 거래량 기반

주의: `ma_align`은 MA5·MA20·MA60을 하나의 boolean으로 평가한다. 코드에는 MA5만의 독립 weight가 없다.

- 엄격히 분리 가능한 “5일만의 점수”: `0.0`
- ma_align 전체를 5일 연관 점수로 세는 상한 attribution: `1.073432530260209`

즉 “최초 점수 2.9718 중 정확히 MA5가 얼마를 만들었나”는 구조적으로 분리 불가다. 확정 가능한 것은 MA5가 정배열 성립의 필수 조건 중 하나였고, 그 joint component가 전액 가산됐다는 점이다.

## 6. 통과 경위 최종 판정

### 확정

- 5일 hard block in live slot: `NOT_FOUND`
- 5일 요소는 live evaluator에서 점수로만 작용
- CRS는 정배열 점수 `+1.0734` 획득
- 5일 거래량 점수는 `0`
- prior 5-session range에서는 중앙권
- 최초 score가 threshold보다 `0.4143` 높아 통과

### 부분 복원

- Shadow 품질 필터를 실제 슬롯에 적용했다면 CRS가 차단됐는지: `PARTIALLY_RECOVERED`
- MA5 아래·BB/RSI bottom-fishing 조건은 확인되지만 전체 q-score 원본은 `NOT_STORED`

### NOT_STORED

- 2026-07-09 13:20 ET의 완전한 Shadow entry-quality payload
- 당시 q-score와 최종 `allow` 값
- 당시 yfinance evaluator-input의 원 응답 payload
- prior 5-session 개별 일봉 row를 그대로 보존한 별도 dump

세부 표:

- `five_day_element_inventory.csv`
- `crs_signal_5day_values.csv`
- `score_contribution_decomposition.csv`
- `pass_verdict.csv`
