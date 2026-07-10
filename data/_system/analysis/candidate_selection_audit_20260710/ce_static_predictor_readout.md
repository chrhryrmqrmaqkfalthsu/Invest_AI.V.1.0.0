# CE형 개체의 정적 예측 특징 탐색

- 최종 판정: **WEAK**
- 룰북·저장 정적 파라미터만 predictor로 사용
- 결과·동적 realized component·현재 시장 상태는 predictor에서 제외
- 설계·구현 변경: 없음

## 1. 타깃과 검증

- IS discovery: 15,363개, bad 3,248개 (21.14%)
- frozen OOS: 82개, bad 42개 (51.22%)
- 결과 타깃: split·stage 상대 collapse, 거래수 보정 worst-MAE tail, high-win large-loss
- predictor 경계: discovery에서만 선택, frozen 재튜닝 없음

## 2. 누수 감사

예비 분석에서 타깃 계산용 worst-MAE residual이 특징 후보에 포함된 누수를 발견했다. 해당 결과는 폐기했고 다음을 predictor에서 강제 제외했다.

- 모든 `eval_*` 결과 필드
- 모든 `target_*` 라벨
- `is_oos_gap_pp`, `oos_to_is_pnl_ratio`
- raw base/holdout 성과 컬럼

최종 검증 특징은 순수 정적 57개, 2-feature 조합 20개다.

## 3. CE 7개 타깃 타당성

- frozen 존재: 6/7
- 결과상 bad: 3/6
- frozen 없음: stage3:CWK:2970595abcd4

CE 동적 FAIL과 실제 frozen bad는 동일하지 않으므로 CE7 포섭률에 맞춰 경계를 조정하지 않았다.

## 4. 단일 특징 검증

| 특징 | IS AUC | frozen AUC | frozen RD | RD 95% CI | frozen FDR q | 재현 |
|---|---:|---:|---:|---:|---:|---|
| exit_is_trailing | 0.501 | 0.695 | 0.390 | [0.181, 0.581] | 0.023 | NO |
| stored_validation_expectancy_pct | 0.629 | 0.642 | 0.194 | [-0.012, 0.392] | 0.475 | NO |
| core_weight_entropy | 0.534 | 0.636 | 0.221 | [-0.011, 0.427] | 0.475 | NO |
| trailing_atr | 0.539 | 0.631 | 0.249 | [0.024, 0.453] | 0.475 | NO |
| stored_validation_trade_count | 0.589 | 0.617 | 0.157 | [-0.062, 0.382] | 0.475 | NO |
| min_core_conditions_to_threshold | 0.507 | 0.617 | 0.182 | [-0.044, 0.429] | 0.475 | NO |
| stop_loss_atr | 0.598 | 0.595 | 0.195 | [-0.029, 0.397] | 0.475 | NO |
| stored_is_validation_fitness_ratio | 0.593 | 0.585 | 0.194 | [-0.017, 0.384] | 0.475 | NO |
| dominant_is_bb | 0.498 | 0.579 | 0.208 | [-0.021, 0.431] | 0.475 | NO |
| use_market_entry_adjustment | 0.528 | 0.575 | 0.153 | [-0.065, 0.360] | 0.475 | NO |
| stop_to_take_ratio | 0.547 | 0.575 | 0.157 | [-0.067, 0.378] | 0.475 | NO |
| stored_validation_fitness | 0.581 | 0.562 | 0.171 | [-0.032, 0.364] | 0.475 | NO |

## 5. 2개 특징 조합

- 시도: 20
- 미보정 frozen hit: 2
- bootstrap+FDR 재현: 0
- 특징 2개 초과와 복잡 모델은 시도하지 않았다.

## 6. 최종 판정

**WEAK**

일부 방향성 또는 미보정 hit는 남지만 bootstrap CI와 frozen FDR을 동시에 통과하지 못했다.
네 번째 정적 BLOCK 근거로는 부족하며 MONITOR 탐색 수준만 허용된다.

## 7. 커브피팅 점검

- IS 전용 또는 미보정 전용 특징·조합: 37개
- frozen family별 FDR 적용
- v3·BOIL 통과 frozen 18개는 순증 참고치로만 사용
- 누수 예비 결과는 최종 판정에서 완전히 제외

## 8. 산출물

- `ce_static_target_labels.csv.gz`
- `ce_static_feature_matrix.csv.gz`
- `ce_static_feature_predictive_power.csv`
- `ce_static_pair_predictive_power.csv`
- `ce_static_ce7_capture.csv`
- `ce_static_curve_fit_notes.csv`
- `ce_static_predictor_summary.json`
