# CE형 개체의 정적 예측 특징 탐색

- 최종 판정: **WEAK**
- canonical 결과 버전: `v6-atomic-relative-target-no-leakage-final`
- 데이터: 룰북·저장된 학습/검증 통계·내부 holdout·frozen OOS read-only
- 진입 시점 realized component·현재 시장 상태: predictor에서 사용하지 않음
- 원본·라이브·운영 코드·재학습·주문·삭제: 0건
- 설계·구현 변경: 없음

## 1. 최종 결론

룰북에서 사전에 읽을 수 있는 순수 정적 특징 57개와 2-feature 조합 20개를 검증했다.

- frozen bootstrap과 다중검정을 모두 통과한 단일 특징: **0개**
- frozen bootstrap과 다중검정을 모두 통과한 2-feature 조합: **0개**
- 미보정 단일 특징 hit: 1개
- 미보정 2-feature 조합 hit: 0개

따라서 네 번째 정적 BLOCK을 만들 근거는 없다. 일부 방향성은 남지만 **MONITOR 연구 후보 이상으로 승격하면 안 된다.**

## 2. 타깃 정의

기존 history 평균 PnL 음수 후보는 제외했다. 기간 길이와 변동성 차이를 통제하기 위해 각 split·stage에서 같은 상대 결과 정의를 적용했다.

### A. IS→OOS 붕괴

```text
PnL gap이 해당 split·stage 상위 10%
AND OOS 평균 PnL / IS 평균 PnL <= 0.5
```

### B. 양의 평균이지만 극단 tail

```text
평균 PnL > 0
AND worst MAE가 log1p(거래수) 기대값 대비 residual 하위 10%
```

worst MAE 절대값은 거래 건수가 많을수록 기계적으로 악화되므로, `worst MAE ~ log1p(거래수)` 기대값을 제거한 residual로 비교했다.

### C. 고승률·대형손실

```text
평균 PnL > 0
AND 승률이 해당 split·stage 상위 25%
AND |worst loss| / median win이 상위 25%
```

타깃 규모:

| 구분 | 전체 | bad | bad 비율 |
|---|---:|---:|---:|
| IS discovery | 15,363 | 3,401 | 22.14% |
| frozen OOS | 82 | 22 | 26.83% |
| frozen 중 기존 v3·BOIL·history 정적 게이트 통과 | 18 | 7 | 38.89% |

결과 라벨의 frozen 상대경계는 outcome 정의이며 predictor 경계 튜닝이 아니다. Predictor 경계는 discovery에서만 결정하고 frozen에는 재튜닝 없이 적용했다.

## 3. CE 7개 타깃 정의 타당성

- frozen 결과 존재: **6/7**
- 위 결과 타깃에 해당: **1/6**
- 해당 후보: `stage3:BOIL:9044dc2c67a3`
- frozen 결과 없음: `stage3:CWK:2970595abcd4`

| 후보 | frozen 타깃 | 이유 |
|---|---|---|
| ANET | 정상 | — |
| BB | 정상 | — |
| BOIL | bad | sample-adjusted extreme tail |
| BTE | 정상 | — |
| CDE | 정상 | — |
| CE | 정상 | — |
| CWK | 판정 불가 | frozen 결과 없음 |

즉 기존 CE FAIL 7개는 frozen 결과상 붕괴 집합과 동일하지 않다. CE7 포섭률을 맞추도록 정적 경계를 조정하면 outcome 예측이 아니라 기존 동적 라벨 복제가 되므로 그렇게 하지 않았다.

## 4. 누수 감사

예비 과정에서 타깃 계산용 `eval_worst_mae_sample_adjusted_residual_pct`가 특징 후보에 포함된 누수를 발견했다. 해당 예비 결과는 폐기했다.

최종 predictor에서 다음을 강제 제외했다.

- 모든 `eval_*` 결과 필드
- 모든 `target_*` 라벨
- `is_oos_gap_pp`, `oos_to_is_pnl_ratio`
- raw base/holdout PnL·승률·표본 컬럼
- 진입 시점 realized component와 현재 시장 상태

최종 특징 목록에는 룰북 파라미터, 저장된 IS/validation fitness·expectancy 통계, core weight 구조, 학습분포 임계 percentile·발생률, exit 파라미터만 남았다.

## 5. 가장 강한 약한 신호

### `trailing_atr` 상위 35%

IS에서 선택된 raw 경계:

- Stage2: `trailing_atr >= 2.3110`
- Stage3: `trailing_atr >= 2.3635`

| 지표 | IS | frozen OOS |
|---|---:|---:|
| AUC | 0.536 | 0.658 |
| risk difference | 0.0458 | 0.2991 |
| risk difference 95% CI | [0.0131, 0.0795] | [0.1004, 0.4953] |
| family FDR q | 유의 | **0.1427** |

frozen 개별 CI는 0을 배제하지만 57개 단일 특징 탐색에 대한 FDR을 통과하지 못했다.

기존 정적 게이트 통과 18개 순증 참고군에서는:

- AUC: 0.727
- risk difference: 0.425
- 95% CI: **[-0.025, 0.833]**

순증 참고군에서도 CI가 0을 포함한다.

### CE7 포섭

`trailing_atr` 약한 경계가 flag하는 CE7:

- BOIL
- CDE
- CE
- CWK

frozen 결과가 있는 6개 중 실제 bad는 BOIL 하나뿐이다. 따라서 true positive 1개와 frozen 정상 false positive CDE·CE 2개가 함께 발생한다. CWK는 결과가 없어 검증할 수 없다.

## 6. 요청 특징별 검증 결과

| 특징군 | 대표 결과 | 판정 |
|---|---|---|
| 활성 core 지표 수 | frozen AUC 0.527, RD CI가 0 포함 | 기각 |
| core Top1/Top2 정적 집중도 | frozen AUC 0.529/0.564, RD 방향 불안정 | 기각 |
| 단방향 임계 percentile 극단성 | frozen AUC 0.458 | 기각 |
| IS/validation fitness 비율 | IS AUC 0.585, frozen AUC 0.547, RD CI가 0 포함 | IS 전용 |
| IS/validation fitness gap | IS AUC 0.573, frozen RD CI가 0 포함 | IS 전용 |
| 최소 core 조건 수 | frozen AUC 0.601, RD CI가 0 포함 | 약한 방향성 |
| core weight/threshold tightness | frozen RD가 0 또는 음수 | 기각 |
| IS 거래 수 | frozen AUC 0.499 | 기각 |
| expectancy / sqrt(IS 거래 수) | IS AUC 0.669, frozen AUC 0.537 | 커브피팅 |
| trailing ATR | frozen 방향성 존재, FDR q=0.143 | **WEAK/MONITOR만** |

IS에서 강해 보인 `is_expectancy_per_sqrt_trade`, stored fitness/expectancy 계열은 frozen에서 깨졌다. 이는 정적 과적합 지표가 다시 과적합되는 전형적인 사례다.

## 7. 2-feature 조합

- discovery 상위 5개 특징만 사용
- 각 조합은 AND 또는 OR
- 특징 수 최대 2개
- 총 20개 검증

결과:

- 미보정 frozen hit: 0개
- bootstrap+FDR 재현: 0개

복잡 모델이나 3개 이상 조합은 시도하지 않았다. 현재 표본에서 이를 시도하면 커브피팅 위험만 증가한다.

## 8. 최종 판정

### WEAK

정적 BLOCK 후보는 발견되지 않았다.

- `trailing_atr`에 부분적 방향성은 존재한다.
- 그러나 frozen family FDR을 통과하지 못했다.
- 기존 v3·BOIL 통과 순증군에서도 CI가 0을 포함한다.
- CE7 중 frozen 결과상 실제 bad가 1개뿐이어서 CE 동적 라벨 자체를 결과 붕괴 proxy로 볼 수도 없다.

따라서 권고 상태는 다음과 같다.

```text
STATIC BLOCK: 금지
STATIC MONITOR 연구 후보: trailing_atr 상위 35%
CE 검증의 주 경로: 동적 observation logging
```

동적 스냅샷을 축적한 뒤 ratio·realized Top2와 실제 진입 성과를 직접 연결하는 것이 유일하게 방어 가능한 다음 단계다.

## 9. 커브피팅 점검

- IS 전용 또는 미보정 전용 특징·조합: 39개
- predictor 경계는 frozen에서 재튜닝하지 않음
- frozen 단일 특징 family와 pair family에 각각 FDR 적용
- ticker-cluster bootstrap 1,000회
- v3·BOIL 통과 frozen 18개는 주 판정이 아닌 순증 참고치로만 사용
- 누수 예비 결과는 최종 판정에서 완전히 제외

## 10. 산출물

- `ce_static_target_labels.csv.gz` — IS/frozen 결과 타깃 목록
- `ce_static_feature_matrix.csv.gz` — 최종 무누수 분석 행렬
- `ce_static_feature_predictive_power.csv` — 57개 특징 IS/frozen 성능·CI·FDR
- `ce_static_pair_predictive_power.csv` — 20개 2-feature 조합 검증
- `ce_static_ce7_capture.csv` — CE7 타깃·상위 약한 특징 flag
- `ce_static_curve_fit_notes.csv` — IS 전용·미보정 전용 특징 목록
- `ce_static_predictor_summary.json` — 기계 판독 최종 판정
- `run_ce_static_predictor_search.py`
- `run_ce_static_predictor_search_v2.py`
- `finalize_ce_static_predictor_relative_outcome.py`
- `finalize_ce_static_predictor_no_leakage.py`
- `finalize_ce_static_predictor_atomic.py` — canonical 최종 실행
