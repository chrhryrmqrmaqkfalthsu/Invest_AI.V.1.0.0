# Stage 3 strict-entry 기술 feature D-5 lag 적용

## 최종 판정

`D5_LAG_APPLIED`

기술 feature는 신호일 D 기준 D-5 거래일 값을 사용하고, 시장 context는 기존 D-1 lookup을 그대로 유지한다. 진입 체결은 D+1 open이다.

```text
기술 feature: D-5
시장·섹터·VIX·뉴스 context: D-1
진입 fill: D+1 open
```

## 무효 실행 정리

기존 결과 디렉터리:

```text
data/_system/analysis/stage3_strict_entry_official_2sym_20260713/
```

위 실행은 기술 feature lag가 D였으므로 `INVALID_LAG_MISSING.md`를 추가했다. AAP·POWI 프로세스는 확인 시점에 이미 qualify 단계에서 `qualified=false`로 종료된 상태였고, 잔존 Stage 3/GA worker는 0개였다.

무효 실행이 workspace 내부에 만든 다음 cache/log 잔여물은 별도 백업 후 제거했다.

```text
scripts/research/stage23_rework_20260713/data/
```

## 수정 파일

```text
scripts/research/stage23_rework_20260713/engine/strategies/evaluator.py
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_aggressive.py
```

## Evaluator D-5

상수:

```text
TECHNICAL_FEATURE_LAG_TRADING_DAYS = 5
```

`extract_entry_features()`는 신호일 D가 마지막 행일 때 다음 행을 사용한다.

```text
df.iloc[-1 - 5]
= df.iloc[-6]
= D-5 거래일
```

적용 feature:

```text
ma_trend
macd_hist
rsi
bb_position
volume_ratio
```

앞쪽 5거래일처럼 D-5 행이 없으면 5개 feature 모두 NaN을 반환한다. Strict validator는 NaN/Inf를 거부하며, `evaluate_signal()`의 insufficient-data 경로도 no-buy이므로 fail-closed다.

## Fold domain·GA support D-5

Stage 3 `build_entry_feature_domain()`은 먼저 전체 시계열에서 5개 raw feature를 계산하고 다음을 적용한다.

```text
feature_series.shift(5)
```

그 후 train fold 날짜를 자른다. 따라서 신호일 D의 domain row와 raw support value는 evaluator와 동일하게 D-5 값을 사용한다.

이 shifted raw values가 그대로 다음에 전달된다.

```text
run_ga(
    gene_scope="entry",
    entry_feature_domain=entry_feature_domain,
)
```

따라서 random 생성, mutation, crossover, current-fold seed 재검증, feature support 25행, joint support 12행이 모두 D-5 기준이다.

## Daily tape·interval-break D-5

Daily tape는 신호일 D마다:

```text
evaluate_signal(rb, df.iloc[:D+1], ...)
```

을 호출한다. Evaluator가 마지막 행 D가 아니라 D-5 행을 선택하므로 신규 진입 판정은 D-5다.

Entry-phase interval-break는 동일 daily tape의 `strict_interval_pass`를 읽는다. 따라서 보유 중 interval-break도 D-5 기준이다.

## 네 경로 일관성

| 경로 | 실제 기준 | 판정 |
|---|---:|---|
| Stage 3 fold q01/q99/IQR | D-5 shifted series | PASS |
| GA 생성·support 검사 | D-5 raw values | PASS |
| Daily tape 신규 진입 | D-5 evaluator feature | PASS |
| 보유 중 interval-break | D-5 daily tape | PASS |

## 시장 context

다음은 수정하지 않았다.

```text
engine/core/feature_lag.py
engine/learning/backtest.py
```

확인값:

```text
DEFAULT_LAG_DAYS = 1
FEATURE_LAG_DAYS = 1
```

시장·섹터·VIX·뉴스·이벤트 context는 D-1 이하 최신 데이터를 계속 사용한다.

## Look-ahead 관계

```text
D-5 기술 feature
+ D-1 시장 context
→ D일 신호 확정
→ D+1 open 체결
```

신호일 이후의 가격이나 context를 참조하지 않는다. 기술 feature는 D보다 5거래일 이전, 시장 context는 D보다 1일 이전이며 체결은 그 다음 거래일 open이므로 미래 참조가 없다.

## 로직 검증

GA·학습·백테스트는 실행하지 않았다.

```text
py_compile: PASS
D 행 입력에서 D-5 row 값 추출: PASS
앞쪽 5행 NaN: PASS
domain shift(5) 후 fold slice: PASS
domain raw values와 evaluator D-5 정렬: PASS
daily tape → evaluator D-5 추적: PASS
interval-break → 동일 tape 사용: PASS
market lag D-1 불변: PASS
잔존 GA worker: 0
```

## 사전 백업

```text
backup/pre_d5_lag_apply_20260713T092619Z.tar.gz
backup/pre_d5_lag_apply_20260713T092619Z.manifest.sha256
backup/invalid_stage3_workspace_cache_20260713T093046Z.tar.gz
backup/invalid_stage3_workspace_cache_20260713T093046Z.manifest.sha256
```
