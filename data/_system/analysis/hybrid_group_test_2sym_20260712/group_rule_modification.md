# 4그룹 하이브리드 진입 규칙 구현 기록

## 범위와 커밋

- 기존 기준 커밋: `489eaeafeaafc029403babc972bd671ff58db5bc`
- grouped GA 추가 커밋: `33231bea7ee7862c06422b059c37ea2401a4fd02`
- 무상태 연구 진입점 추가 커밋: `0b6df1e1487dac5ff880cf703d8fad8d3c7674fd`
- 원본 Stage2/3·라이브 파일은 수정하지 않았다.
- 기존 복사본 strict-AND `genetic.py`와 rolling 청산 `execution_mode_backtest.py`도 수정하지 않았다.

추가 파일:

```text
scripts/research/rolling_rediscovery/upstream_snapshot/
├── engine/learning/grouped_genetic.py
└── scripts/research/
    ├── run_hybrid_group_test_2sym.py
    └── run_hybrid_group_test_2sym_entry.py
```

## 지표 수 해석

지시서에는 “15개 지표”라고 적혀 있으나 명시된 그룹 구성은 산술상 다음과 같이 **14개**다.

```text
G1 4개 + G2 4개 + G3 3개 + G4 3개 = 14개
```

그룹 구성을 우선해 임의의 15번째 지표를 추가하지 않았다. G3의 train 15-percentile member floor는 별도 고정 제약이며 feature interval gene으로 세지 않았다.

## 그룹 구성

`run_hybrid_group_test_2sym.py` 63~107행:

```text
G1_PULLBACK
  pullback_from_high5_pct
  fade_after_surge_score
  inv_close_pos5 = 1-close_pos5
  inv_ret_d1_pct = -ret_d1_pct

G2_VOLATILITY
  single_up_day5_pct
  atr14_pct
  realized_vol20_pct
  bb_width20_pct

G3_RANGE_EXPANSION
  true_range_d1_pct
  range_vs_atr14
  range_vs_range20

G4_VOLUME_CONFIRMATION
  volume_ratio5_prior
  volume_ratio20_prior
  volume_chg1_pct
```

삭제한 기존 약한 지표:

```text
ret_d5_pct, ret_d4_pct, ret_d3_pct, ret_d2_pct,
cumulative_ret5_pct, up_days5, days_since_high5
```

## 진입 판정부 diff

기존 strict-AND:

```python
inside = (x_norm >= low) & (x_norm <= high)
entry = all(inside for every feature)
```

신규 A 구조 (`grouped_genetic.py` 83~123행):

```python
feature_pass = finite & (x_norm >= low) & (x_norm <= high)
group_counts = [sum(feature_pass[group]) for group in groups]
group_pass = group_counts >= learned_integer_thresholds
G3_pass &= all(G3_member >= train_q15_floor)
entry = all(group_pass across G1, G2, G3, G4)
```

따라서:

- 지표 구간 안이면 1점, 밖이면 0점
- weight 없음
- 그룹별 threshold는 정수 gene
- 그룹 간에는 보상 없는 strict AND
- G3의 세 지표는 train 분포의 15-percentile보다 낮으면 그룹 count와 무관하게 실패

## Group threshold gene

`grouped_genetic.py` 31~80행과 214~282행:

- `GroupedIntervalIndividual`에 14개 low·high와 4개 정수 threshold를 저장한다.
- threshold 허용 범위는 각 그룹별 `1..group_size`다.
- crossover는 feature interval과 group threshold를 독립적으로 부모에게서 선택한다.
- mutation은 group threshold를 ±1 이동하며 허용 범위를 벗어나지 못하게 한다.
- validation 결과 6개 후보 × 3 regime × 4그룹, 총 72행에서 threshold 범위 오류는 0건이었다.

## GA 골격

`grouped_genetic.py` 290~428행:

- population 100
- generation 50
- patience 15
- elite 8
- tournament 4
- interval mutation rate 0.18
- bilateral 최소폭 0.10
- 성공 라벨 표본 최댓값 upper fallback 유지
- precision 중심 fitness와 train 최소표본 `max(20, 2%)` 유지

6개 학습 모두 실제 50 generation까지 실행됐다.

## Feature와 누수 방지

`run_hybrid_group_test_2sym.py` 218~355행:

- 각 D0 행에서 feature는 D-1 이전 완료봉만 사용한다.
- ATR14와 Bollinger width는 복사본 `engine/core/indicators.py`의 공식과 동일하다.
- realized volatility는 D-20~D-1 close return sample std다.
- range20·volume20 분모는 D-21~D-2로 D-1 현재값을 제외한다.
- D0 open/high/low/close는 label·체결·보유 중 +3% 확인에만 사용한다.
- STK/ETF gap_d0, flow, orderbook는 사용하지 않았다.

20일 지표 warm-up 때문에 종목별 feature 행은 기존 1,518행에서 1,503행으로 15행 줄었다. OOS는 두 방식 모두 종목별 252행으로 동일하다.

## Rolling 청산

기존 복사본 `execution_mode_backtest.py`를 그대로 사용했다.

```text
진입일 목표 = D+2
보유 중 active=True → 현재일+2로 연장
active=False → 즉시 청산하지 않고 기존 목표 유지
목표일 도달 → 종가 청산
TP OFF
```

거래 CSV의 독립 trace와 기존 함수 반환값의 진입일·청산일·보유세션·순수익률을 전부 대조했다.

## 무상태 연구 진입점

복사본 `engine/core/indicators.py`는 production logger를 통해 복사본 `config/policy.yaml`을 요구한다. 라이브 설정을 복사하거나 생성하지 않기 위해 `run_hybrid_group_test_2sym_entry.py`가 no-op logger만 메모리에 주입한 뒤 runner를 실행한다.

- `.env` 로드 없음
- policy 파일 생성 없음
- copied log 파일 생성 없음
- indicator 계산식 변경 없음
