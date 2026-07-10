# CE형 개체의 정적 예측 특징 탐색

- 최종 판정: **WEAK**
- 네 번째 정적 게이트 후보: **0개**
- 동적 realized component·현재 시장 상태: 사용하지 않음
- 설계·운영 구현 변경: 없음

## 1. 타깃 정의

기존 과거 평균 PnL 음수 대상은 제외했다. discovery에서 stage별 경계를 고정하고 frozen OOS에는 재튜닝 없이 적용했다.

- IS→OOS 붕괴: PnL 격차 상위 10%이면서 OOS/IS PnL 비율 <=0.5
- 양의 평균 tail risk: 거래 수로 기대되는 worst MAE를 회귀 보정한 residual 하위 10%
- 고승률 소수 대형손실: 승률 상위 25%, worst/median-win 상위 25%, top3 loss share 중앙값 이상

- discovery: 15,363개, bad 3,248개
- frozen validation: 82개, bad 42개
- 기존 v3·BOIL·history 정적 게이트 통과 frozen: 18개, bad 10개

Stage별 경계:

- Stage2 collapse gap 1.6071%p, MAE residual -4.3801% 이하, high-win 76.47% 이상
- Stage3 collapse gap 4.2805%p, MAE residual -8.5182% 이하, high-win 68.18% 이상

## 2. CE7 타깃 타당성

- frozen 결과 존재: 6/7
- 결과 타깃 bad: 3/6 — BOIL, BTE, CDE
- 결과 타깃 good: ANET, BB, CE
- frozen 없음: CWK

CE 동적 FAIL 7개는 단일한 결과 붕괴 집합이 아니다. 따라서 CE7 라벨 자체를 최적화하지 않고 독립 결과 타깃을 사용했다.

## 3. 정적 특징 검증

- 허용된 단일 정적 특징: 57개
- 명목 bootstrap 재현 단일 특징: 1개
- frozen family FDR 통과 단일 특징: 0개
- 2개 이하 조합: 20개
- 명목 bootstrap 재현 조합: 2개
- frozen family FDR 통과 조합: 0개
- 기존 게이트 순증군·stage 일관성까지 통과: 0개

결과 파생 `eval_*`·`target_*` 열은 predictor에서 완전히 제외했다.

## 4. 남은 약한 신호

### 단일 `trailing_atr`

- 경계: Stage2 >= 2.3110, Stage3 >= 2.3635
- broad frozen 위험차 0.2486, 95% CI [0.0244, 0.4531]
- frozen FDR q=0.4755
- 기존 게이트 통과 frozen 위험차 0.3500, 95% CI [-0.1003, 0.7662]

### 조합 `stored_validation_expectancy_pct` AND `stored_validation_fitness`

- Stage2 경계: stored_validation_expectancy_pct >= 2.3690, stored_validation_fitness >= 58.2942
- Stage3 경계: stored_validation_expectancy_pct >= 3.1862, stored_validation_fitness >= 82.6185
- broad frozen: 35/82 flag, 위험차 0.2529, 95% CI [0.0329, 0.4787], FDR q=0.2015
- 기존 게이트 통과 frozen: 11/18 flag, 위험차 -0.0260, 95% CI [-0.5000, 0.4667]
- Stage2 위험차: None
- Stage3 위험차: 0.3230174081237911

broad frozen에서는 명목상 분리되지만 FDR와 순증군에서 깨지고, Stage2에서는 분리 불가 또는 반대 방향이다.

## 5. CE7 포섭

- 최상위 약한 조합 포섭: 1/7
- 결과 bad 포섭: 1/3
- 포섭 ID: stage3:BOIL:9044dc2c67a3

포섭된 BOIL은 이미 v3·BOIL 정적 게이트 영역이다. BTE·CDE를 놓쳐 네 번째 게이트의 순증 가치가 없다.

## 6. 최종 판정

**WEAK**

일부 단일·조합 특징은 보정 전 frozen 95% CI에서 방향을 보였지만 family FDR, 기존 게이트 통과 순증군 bootstrap, stage 일관성을 동시에 통과하지 못했다.

따라서 네 번째 STATIC BLOCK 후보는 없다. 연구 MONITOR 수준으로만 남기며 CE형 실패 검증의 주 경로는 진입 시점 동적 observation logging이다.

## 7. 커브피팅 점검

- 경계는 discovery에서만 선택
- frozen 경계 재튜닝 없음
- ticker-cluster bootstrap
- frozen 단일/조합 family별 BH FDR
- 결과 파생 predictor 완전 제외
- 기존 게이트 통과 순증군과 Stage2/Stage3 방향 확인

## 8. 산출물

- `ce_static_target_labels.csv.gz`
- `ce_static_feature_matrix.csv.gz`
- `ce_static_feature_predictive_power.csv`
- `ce_static_pair_predictive_power.csv`
- `ce_static_nominal_pair_robustness.csv`
- `ce_static_ce7_capture.csv`
- `ce_static_curve_fit_notes.csv`
- `ce_static_predictor_summary.json`
