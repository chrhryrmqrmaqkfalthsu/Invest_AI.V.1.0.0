# S1/S2/S3 실험 variant 및 “5일 lookback → 2일 내 +3% 타겟” 후보선정 로직 전수 조사

- 조사일: 2026-07-12
- 조사 대상: **실험 방식으로서의 S1/S2/S3**
- 명시적 오탐 제외: 학습 파이프라인 `stage1/stage2/stage3`, `S2AutoTrader`
- 조사 방법: 현재 코드·기존 분석 artifact·reachable Git history·2026-07-09~10 커밋·daemon 로그 대조
- 코드 및 운영 설정 변경: **0**

## 최종 판정

### **(B) S2는 반영됐으나 그 실체가 “5일 → 2일 내 +3% 후보 selector”가 아닌 다른 로직**

확정된 S1/S2/S3는 **진입 전 후보선정 variant가 아니라 진입 후 청산 전략 비교 variant**다.

- **S1**: 원본 take-profit 포함 청산
- **S2**: take-profit을 무시하고 stop·trailing·sell omen·timeout·breakeven으로 청산
- **S3**: 진입일 또는 다음 거래일에 +2%를 찍으면 +2%로 청산하고, 못 찍으면 S2로 복귀

후속 `S3_target_3pct`도 존재한다. 그러나 이것은:

1. 먼저 +2%를 진입일/다음 날 안에 찍은 뒤
2. S2 원래 청산일까지 +3% 도달을 기다리는
3. **사후 청산 variant**다.

즉 `+3%` 자체가 2일 이내로 제한되지 않으며, 진입 전 “상승 예상 개체”를 선별하지 않는다.

정확한 결합 로직:

```text
최근 최소 5일 상태
→ 앞으로 2거래일 안에 +3% 상승 예상
→ 예상 통과 개체만 candidate_pool에 편입
```

은 코드·설정·artifact·Git 이력에서 **NOT_FOUND**다.

## 1. 실제 S1/S2/S3 variant 정의

정의 파일:

`data/_system/analysis/perday_perstock_20260707/run_perday_perstock_frozen.py`

이 파일은 Git 추적 대상이 아니어서 최초 커밋은 `NOT_FOUND_UNTRACKED_ARTIFACT`다.

- mtime: `2026-07-07T17:17:25.244504Z`
- SHA-256: `feef9d9996d4bac4530827a51942f3fd7b307b2e1f927a62bde341961fb00d95`

### S1

`S1_original_take_profit_included`

원본 frozen 거래의 net·holding·exit를 그대로 사용한다.

- 정의: `run_perday_perstock_frozen.py:349-365`
- 성격: 청산 baseline
- 후보선정 사용: 없음

### S2

`S2_no_take_profit_solo`

shared ExitPolicy를 replay하되 `take_profit` 결정만 무시하고 계속 보유한다.

- 구현: `run_perday_perstock_frozen.py:214-308`
- variant 이름: `run_perday_perstock_frozen.py:351-354`
- 성격: no-TP 청산
- 후보선정 사용: 없음

### S3

`S3_tp2_2d_then_no_take_profit`

- 고정 target: `TP2_GROSS_PCT = 2.0`
- 검사 bar: `entry_idx`, `entry_idx+1`
- +2% hit: target 가격으로 청산
- 미달: S2 결과 사용

근거:

- `run_perday_perstock_frozen.py:22`
- `run_perday_perstock_frozen.py:311-346`
- `run_perday_perstock_frozen.py:351-354`

이것이 사용자 기억의 “2일 상승”과 가장 가까운 실제 S3다. 다만 차이는 명확하다.

- +3%가 아니라 +2%
- 예상·분류가 아니라 실현 여부를 사후 확인
- 후보선정이 아니라 청산
- 신호 시점부터가 아니라 entry bar와 그 다음 bar

## 2. +3% variant의 실체

후속 실험:

`data/_system/analysis/exit_variants_20260707/run_exit_variants.py`

- mtime: `2026-07-07T17:48:58.629104Z`
- SHA-256: `fc616f4f86c4ae9ac61f8488e8667e3b5fd895d32fea40fc5e4292ce59f23769`

`S3_target_3pct`는 다음과 같이 작동한다.

1. `locate_tp2()`가 진입일·다음 날의 +2% hit를 찾음
2. hit가 없으면 S2 fallback
3. hit가 있으면 `entry_price * 1.03`을 설정
4. hit 당일부터 S2 원래 exit date까지 +3% target을 탐색
5. +3% 미도달이면 S2 fallback

근거:

- +2% 2-bar trigger: `run_exit_variants.py:142-150`
- +3% 후속 target: `run_exit_variants.py:209-230`
- variant 등록: `run_exit_variants.py:269-286`

따라서 이것은 **“2일 내 +3% 예상”이 아니다.**

```text
2일 안에 +2% 실현
→ 이후 정상 S2 청산 전까지 +3% 실현 여부 관찰
```

이다.

## 3. 5일 lookback 계보

5일 lookback은 실제로 존재한다. 그러나 S1/S2/S3 exit variant와는 별도 연구 계보다.

### 5일 range predictor

commit:

`3048579f792f0d3d213034c6956f33973cc8130b`

시각:

`2026-07-04T12:05:21Z`

메시지:

`고저구간 예측 GA v3를 기존 Stage2 진입 컴포넌트와 최근 5일 패턴 결합 구조로 변경`

historical code:

- D-1~D-5 Stage2 컴포넌트: lines 5-8
- `LOOKBACK = 5`: line 50
- target: 다음 날 high/low 6-bin: lines 11-13, 70
- 연구 전용·run_live 없음: lines 15-18

### 다음 날 +2% event predictor

commit:

`33605eb80b6a1f25c09aacd2813b4da348f91b8b`

시각:

`2026-07-04T14:22:39Z`

이 버전은 최근 5일 feature를 유지하면서 다음 날 시가 대비 상방·하방 +2% 이벤트를 분리 학습했다.

- target: 다음 날 +2%/-2%: historical lines 3-10
- 이전 5일 feature 유지: lines 12-15
- `EVENT_BIN_THRESHOLD = 3`: line 40
- 연구 전용·run_live 없음: lines 17-20

이것은 사용자 기억과 가깝지만 여전히 다르다.

- 2일이 아니라 다음 날
- +3%가 아니라 +2%
- S1/S2/S3 exit variant가 아님
- 라이브 후보선정 미연결

### 후속 payoff 계보

2026-07-05~06에는 5일 lag payoff 실험이 추가됐지만 target은 next-day ATR 배수로 변형됐다.

- `cc58b5adeeccc09af366f68706bf207335aa424b`: 5일 lag wrapper
- `e22a445e94931838f98288f517ab3665ae1e8cd9`: D0 누수 제거 + stress/OOS 최종 감사

해당 predictor의 live loader·inference call site는 `NOT_FOUND`다.

## 4. 세 계보의 분리

| 기억 속 요소 | 실제 확인된 계보 | 라이브 후보선정 연결 |
|---|---|---|
| S1/S2/S3 실험 | 2026-07-07 청산 variant 비교 | 없음 |
| 최소 5일 상태 | 2026-07-04~06 range/payoff 연구 predictor | 없음 |
| 2일 상승 | S3가 entry day+next day의 +2% 실현을 사후 검사 | 없음 |
| +3% | S3 후속 exit target; +2% trigger 후 S2 exit 전까지 검사 | 없음 |
| 개체별 variant 선택 | IS mean net/day로 S1/S2/S3 best assignment 연구 | 없음 |
| 라이브 S2 반영 | no-TP exit·next-open·final_score priority AutoTrader 설계 | 후보선정은 기존 로직 유지 |

즉 사용자 기억은 실제로 존재한 세 연구 조각을 한 selector로 결합해 기억한 것으로 보인다. 이 해석은 **[추정]**이며, 각 조각의 존재와 미연결은 확정이다.

## 5. “S2 최종 반영”의 실제 의미

결정 문서:

`data/_system/analysis/s2_auto_design_20260708/readout.md`

commit:

`379c6b0195a0777fbacc08b2037f8d9378634945`

문서는 명시적으로:

- 기존 `live_candidate_slots.py`는 후보 생성·정렬 담당
- 별도 S2AutoTrader가 자동매수·청산 담당
- 검증 정본은 `S2 K=20 final_score priority`
- S2는 `s2_take_profit_enabled=false`
- 고정 +3% 익절은 현재 live가 아니며 별도 필드가 필요

라고 정의한다.

근거:

- 구조 분리: `s2_auto_design_20260708/readout.md:10-18`
- K20·final_score: lines 35-37
- 고정 3%가 아님: lines 225-239
- no-TP trigger: lines 256-267
- candidate slots 미수정 원칙: lines 409-420

구현 commit:

`75faf9a5871fe92fa43cb842f79481b0b6ac4825`

시각:

`2026-07-08T06:21:18Z`

변경 파일에는 다음이 포함됐다.

- `engine/core/exit_policy.py`
- `engine/live/s2_auto_config.py`
- `engine/live/s2_auto_trader.py`
- `scripts/run_s2_auto_live.py`

반면 다음은 변경되지 않았다.

- `data/_system/ops/live_candidate_slots.py`
- `engine/live/elite_shadow_report.py`
- `engine/live/elite_shadow_trader.py::evaluate_candidate`

구현 readout도 `live_candidate_slots.py`의 후보 선정·정렬을 건드리지 않았다고 명시한다.

- `s2_auto_implementation_20260708/readout.md:27`
- no-TP 구현: lines 184-233

따라서 **“S2 최종 반영”은 후보 selector 반영이 아니라 자동매매의 no-TP 청산 방식과 final_score 우선순위 반영**이다.

## 6. 2026-07-09~10 Git 정밀 조사

사용자가 추정한 목·금 구간을 별도로 확인했다.

### 2026-07-09

commit `e589e57434b9bef1503cca30a37bd7652acd6f96`

- 후보 슬롯 daemon guard와 cron 추가
- 변경 파일: guard shell, cron
- S1/S2/S3·5일 predictor·2일/+3% 연결 없음

### 2026-07-10

commit `cb7f48983645933002b11fa07f4c206fe1b1af4e`

- CE/CDE/BKSY/BOIL denylist 추가
- S1/S2/S3 selector 연결 없음

다음 live 후보 경로에 대해 Git 전체 이력을 검색했다.

- `engine/live/elite_shadow_report.py`
- `engine/live/elite_shadow_trader.py`
- `data/_system/ops/live_candidate_slots.py`

검색 토큰:

- `S1_original`
- `S2_no_take_profit`
- `S3_tp2`
- `tp2_hit_2d`
- `target_3pct`
- `lookback=5`
- `0.03`

연결 commit 결과: **0개**.

판정: `CONFIRMED_NOT_CONNECTED`.

## 7. 현재 daemon의 실동작

사용자가 지목한 PID 479037은 같은 daemon 계보의 이전 PID다.

- PID 479037 시작: `2026-07-11T17:34:33Z`
- guard 마지막 확인: `2026-07-11T20:15:11Z`
- 현재 PID 494330 시작: `2026-07-11T20:16:02Z`
- 명령: `live_candidate_slots.py daemon --interval 60`

이번 조사에서 daemon 재시작은 하지 않았다.

현재 후보선정 코드:

1. `build_elite_shadow_report(stage2_limit=60, stage3_limit=80)`
2. upstream gate/KEEP 확인
3. `evaluate_candidate()`
4. `should_buy=true`
5. `final_score` 내림차순

근거:

- 정책 선언: `live_candidate_slots.py:357-367`
- report 호출: lines 374-379
- gate·signal 판정: lines 386-415
- 정렬: lines 315-316, 448-449
- 저장 fields: lines 269-312

`evaluate_candidate()`는 `evaluate_signal()` 결과의:

- `should_buy`
- `score`
- `threshold`
- `ratio`

를 반환한다.

- `elite_shadow_trader.py:381-419`
- `elite_shadow_trader.py:448-484`

S1/S2/S3 variant, 5일 predictor score, 2일 +3% 확률 필드는 없다.

## 8. 현재 후보 10개 적용 여부

snapshot:

- state SHA-256: `199cdb06eac7e638cd30ae5fe01825a0134079a48117f03e2f8eef44ff947986`
- state `updated_at`: `2026-07-12T07:55:11.185892Z`
- 후보 수: 10
- stage 분포: stage3 10, stage2 0

후보:

- ADMA
- CRS
- ALGT
- AEIS
- ARKW
- CBRL
- BTU
- BB
- BN
- ACMR

10개 후보 row 전부에서 다음과 같은 variant 관련 field는 없었다.

- S1/S2/S3
- predictor
- lookback
- tp2
- target_3pct
- two-day probability

따라서 현재 10개는 **S1/S2/S3 variant selector를 거치지 않았다.**

다만 frozen offline artifact에서 현재 10개를 다시 조회하면 IS 평균 net/day 기준 best variant는 10개 모두 S3였다. 이것은 연구상 reference일 뿐 live에는 연결되지 않았다.

특히 `stage3:` candidate_id는 rulebook Stage3 lineage를 뜻하며 S3 exit variant 적용을 뜻하지 않는다.

## 9. CRS 대입

CRS:

`stage3:CRS:8695c9ce3320`

최초 신호:

- 시각: `2026-07-09T13:20:33.590054-04:00`
- 가격: `600.8599853515625`
- final score: `2.971797614887265`
- 개체 threshold: `2.5574757832651467`
- margin: `+0.41432183162211844`
- ratio: `1.1620042052140767`

공식 통과 이유:

```text
2.971797614887265 >= 2.5574757832651467
```

CRS의 5일 상태 복원값:

- MA5: `600.9259887695313`
- 가격-MA5: `-0.010983618482518498%`
- high5: `625.989990234375`
- low5: `576.1599731445312`
- range 위치: `49.56854050942229%`
- ret5: `-2.590629791871546%`
- MA 정배열 점수: `+1.073432530260209`
- Volume_MA5 점수: `0`

하지만 다음은 존재하지 않는다.

- CRS 2일 +3% predictor model
- CRS 2일 +3% probability
- CRS predictor threshold
- CRS predictor pass/fail
- live 후보 row의 variant assignment

판정:

- 5일 상태: 일부 복원
- 2일 +3% 예측값: `NOT_STORED`
- selector 적용 여부: `CONFIRMED_FALSE`
- CRS 통과 경위: `signal_threshold` 통과

참고로 offline IS 737개 거래에서 CRS의 평균 net/day는:

- S1: `0.7668573604164047%`
- S2: `0.7668573604163967%`
- S3: `0.9153286718749454%`
- offline best: S3

그러나 이 결과도 live 후보선정이나 live exit assignment로 연결되지 않았다.

## 10. 사용자 기억 대조표

| 사용자 기억 | 실제 근거 | 판정 |
|---|---|---|
| S1/S2/S3 실험이 있었다 | 청산 variant로 존재 | 일치 |
| 최소 지난 5일을 봤다 | 별도 range/payoff predictor가 5일 사용 | 부분 일치 |
| 2일 내 상승을 봤다 | S3가 entry 후 2 bars의 +2% 실현을 검사 | 부분 일치 |
| target이 +3%였다 | 후속 S3_target_3pct가 존재하나 2일 제한 아님 | 부분 일치 |
| 상승 예상 개체만 뽑았다 | 실제 variant는 사후 청산 replay | 불일치 |
| S2가 최종 반영됐다 | no-TP exit·K/final_score auto-trader 설계로 반영 | 실체가 다름 |
| 목·금에 selector가 live 연결됐다 | 해당 기간 연결 commit 없음 | 불일치 |
| CRS가 그 selector를 통과했다 | score≥individual threshold로 통과 | 불일치 |

## 11. 미해결·복원 불가 항목

- S1/S2/S3 artifact 최초 작성 커밋: `NOT_FOUND_UNTRACKED_ARTIFACT`
- 해당 artifact를 만든 사용자 대화 원문: `NOT_STORED`
- “2일 +3% selector”를 지시한 별도 외부 문서: `NOT_FOUND`
- 삭제된 predictor 상세 최종 검증 rows: `NOT_STORED`
- CRS exact predictor probability: `UNRECOVERABLE`

## 결론

이전 포렌식의 “predictor 라이브 미연결” 판정은 variant 관점에서도 유지된다.

새로 확정된 사실은 다음이다.

> S1/S2/S3는 실제로 존재했지만 후보선정 variant가 아니라 청산 variant였다.

그리고:

> 5일 predictor, S3의 2-bar +2% exit, 후속 +3% exit, S2 no-TP live 반영은 서로 다른 계보다.

따라서 최종 판정은 **(B)**다.

**S2는 반영됐지만, 반영된 실체는 no-TP 청산과 final_score 우선순위였으며 “5일 상태로 2일 내 +3% 예상 개체만 선별”하는 로직은 라이브에 반영되지 않았다.**

세부 산출물:

- `variant_definition_inventory.csv`
- `five_day_lookback_map.csv`
- `two_day_three_pct_target_map.csv`
- `git_connection_timeline.csv`
- `live_active_variant.csv`
- `crs_variant_application.csv`
- `immutability_check.csv`
