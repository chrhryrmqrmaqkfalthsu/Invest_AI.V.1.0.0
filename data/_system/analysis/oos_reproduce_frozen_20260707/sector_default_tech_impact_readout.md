# CE sector_name 기본값 tech 이슈 영향 범위 read-only 점검

점검 시각: 2026-07-09 KST
대상 후보 파일: `data/_system/analysis/oos_reproduce_frozen_20260707/candidate_universe.json`
대상 현재 슬롯 파일: `data/_system/live_slots_state.json`
소스 수정: 없음

## 결론 요약

1. `oos_reproduce_frozen_20260707/candidate_universe.json`의 93개 후보 룰북은 전부 `sector_name` 필드가 없다.
2. frozen 재현 경로는 `sector_name = rb.sector_name or ctx.sector_name or "tech"` 구조라서, 룰북에 sector_name이 없으면 종목명 기반 `_detect_sector_name(meta.name)` 결과를 사용한다.
3. CE의 종목명 `Celanese Corp - Series A`는 현재 `_detect_sector_name()` 규칙에서 materials/chemicals로 잡히지 않고 최종 fallback인 `tech`가 된다.
4. 따라서 CE의 frozen 백테스트/재현도 올바른 materials 섹터가 아니라 `tech` fallback을 쓴 것으로 판단된다. 즉 백테스트와 실전이 완전히 다른 섹터 로직을 썼다기보다는, 백테스트 자체도 같은 섹터 분류 결함을 공유한 상태로 검증된 것이다.
5. 현재 `engine/market/context.py`의 `SECTOR_ETFS`에도 `materials`/`XLB` 또는 `chemicals`가 없다. 따라서 CE를 materials로 분류하더라도 현재 시장 컨텍스트에는 materials 점수 자체가 없다.
6. 현재 활성 후보 26개에서 sector component를 제거해 재정렬하면 상위권 순서는 일부 바뀌지만, CE는 저장 순위 4위에서 sector 제거 추정 순위도 4위로 유지된다.
7. 그러므로 현재 자료만으로는 “CE가 오직 이 버그 때문에 잘못 뽑혔다”고 결론내릴 수 없다. 다만 후보 universe 전체의 섹터 입력 품질은 명백히 오염되어 있다.

## 1. frozen 백테스트/재현 경로와 실전 섹터 처리 대조

확인한 frozen 재현 코드:

- `data/_system/analysis/ohlc_freeze_rebuild_20260707_1653/run_ohlc_freeze_rebuild.py`
- `data/_system/analysis/perday_perstock_20260707/run_perday_perstock_frozen.py`

핵심 로직:

```text
sector_name = str(getattr(rb, "sector_name", "") or ctx.get("sector_name") or "tech")
```

`ctx.get("sector_name")`는 `engine/pipeline/context.py::prepare_ticker_context()`에서 만들어지며, 내부적으로 `engine.learning.learner._detect_sector_name(meta.name)`를 사용한다.

CE 확인:

```text
name: Celanese Corp - Series A
_detect_sector_name(name): tech
reason: default_fallback_to_tech
```

따라서 CE는 frozen 재현/백테스트 경로에서도 materials/chemicals가 아니라 tech fallback을 사용한 것으로 보는 게 맞다.

실전 후보 경로는 두 갈래가 확인된다.

```text
engine/live/scheduled_open_buy_queue.py:
  sector_name = str(getattr(rb, "sector_name", "") or "tech")

engine/live/central_control.py:
  sector_score = sector_strength.get(str(getattr(rb, "sector_name", "") or ""), 50.0)
```

현재 CE 슬롯에는 `sector_score=100.0`이 들어 있고, 현재 `market_state.json`에서 `tech=100.0`이다. 따라서 현재 표시/저장된 CE sector_score는 tech 점수로 판단된다.

다만 CE 현재 row는 다음과 같다.

```text
raw_score: 8.363246295633697
final_score: 8.363246295633697
implied_adjustment: 1.0
reasons: MACD크로스, RSI, BB근접, 이벤트반응
시장보정 문구: 없음
```

즉 CE에 `sector_score=100`이 표시되어 있지만, 현재 저장된 `final_score`에는 sector adjustment가 실제로 반영되지 않았거나, 다른 경로에서 `market_adjustment=1.0`으로 저장된 상태다.

## 2. 93개 후보 중 sector_name 누락 범위

후보 universe 총계:

```text
candidate_universe_count: 93
rulebook_sector_name_missing: 93
rulebook_sector_name_present: 0
```

종목명 기반 `_detect_sector_name()` 결과:

```text
tech: 86
healthcare: 2
energy: 4
industrials: 1
```

분류 사유:

```text
default_fallback_to_tech: 77
explicit_tech_keyword: 9
explicit_healthcare_keyword: 2
explicit_energy_keyword: 4
explicit_industrials_keyword: 1
```

즉 대부분의 후보는 명시 sector가 없어서 종목명 키워드 기반 추정 또는 최종 tech fallback에 의존한다. 특히 77개는 sector 키워드가 잡히지 않아 그냥 `tech`로 떨어진다.

## 3. sector_score 기여분 제거 시 현재 활성 후보 재정렬

현재 `live_slots_state.json` 기준 unique 활성 후보 수:

```text
26
```

저장된 final_score 상위 8개:

```text
1. BMI   16.5691
2. BMA   13.4703
3. BTBT  11.1366
4. CE     8.3632
5. ADMA   8.1339
6. ALGT(stage2) 6.7068
7. ALGT(stage3) 5.5973
8. CMC    5.0614
```

sector component 제거 추정 상위 8개:

```text
1. BMA   10.9303
2. BTBT   9.9921
3. BMI    8.5362
4. CE     8.3689
5. ADMA   6.2717
6. BCS    5.3295
7. ALGT(stage3) 5.0558
8. CMC    4.6942
```

변화:

```text
BMI: 1위 → 3위
BMA: 2위 → 1위
BTBT: 3위 → 2위
CE: 4위 → 4위
ADMA: 5위 → 5위
ALGT(stage2): 6위 → 11위
BCS: 14위 → 6위
```

CE 관련:

```text
stored final_score: 8.363246295633697
raw_score: 8.363246295633697
stored sector_score: 100.0
sector_strength_weight: -0.6208615991099308
score_without_sector_component 추정: 8.368902087910461
rank_without_sector 추정: 4
rank_delta: 0
```

CE의 경우 sector contribution 제거만으로는 순위가 바뀌지 않는다. CE 진입의 핵심은 sector 점수보다 `이벤트반응(+4.62)`과 기술적 신호 합산이다.

## 4. 해석

이 이슈는 두 층으로 나뉜다.

첫째, 백테스트-실전 정합성 측면:

```text
CE는 frozen 재현에서도 tech fallback을 사용한 것으로 판단된다.
따라서 “백테스트는 올바른 materials, 실전만 tech”인 형태의 정합성 붕괴는 현재 증거로는 아니다.
```

둘째, 모델 입력 품질 측면:

```text
CE는 실제 materials/chemicals 종목인데 tech fallback을 사용했다.
market context에도 materials/XLB가 없다.
후보 universe 93개 전부 sector_name이 없다.
77개 후보는 명시 sector 키워드 없이 tech fallback이다.
```

따라서 이건 백테스트-실전 불일치보다 더 근본적인 “섹터 feature 설계/저장 누락” 문제다. frozen 검증 성과도 이 결함을 포함한 상태의 성과로 봐야 한다.

## 5. CE 포지션 판단에 대한 직접 결론

현재 자료만으로는 다음 결론은 불가하다.

```text
CE는 섹터 버그 때문에 잘못 뽑힌 종목이다.
```

왜냐하면:

```text
1. CE는 sector component 제거 추정 후에도 현재 활성 후보 4위다.
2. CE final_score는 raw_score와 동일해, 현재 저장 점수에는 sector adjustment가 실질 반영되지 않은 것으로 보인다.
3. CE의 강한 점수 원인은 이벤트반응(+4.62), RSI, BB근접, MACD크로스다.
```

하지만 다음 결론은 가능하다.

```text
CE의 sector_score=100 표시는 소재 섹터 점수가 아니라 tech fallback 점수일 가능성이 높다.
따라서 대시보드의 CE sector_score는 신뢰하면 안 된다.
다음 후보 정렬 전에는 sector_name/materials/XLB 매핑 보정이 필요하다.
```

## 6. 산출 파일

- `sector_default_tech_candidate_audit.csv`: frozen 후보 93개 전체의 sector_name 누락 및 감지 결과
- `sector_default_tech_current_rank_impact.csv`: 현재 활성 후보 26개의 sector component 제거 추정 순위 영향
