# Step 0 — 사전 상태와 분석 범위

## 보존 대상

기존 50종목 산출물과 이전 λ 실험 결과는 수정하지 않았다.

```text
data/_system/analysis/stage2_3_rediscovery_pilot_20260712/
data/_system/analysis/hybrid_group_test_2sym_floored_20260712/
data/_system/analysis/fitness_consistency_penalty_2sym_20260712/
data/_system/analysis/fitness_lambda_sweep_2sym_20260712/
```

신규 출력만 다음 경로에 생성했다.

```text
data/_system/analysis/relative_target_label_feasibility_20260712/
```

## 사전 백업

```text
backup/pre_relative_target_label_feasibility_20260712.tar.gz
backup/pre_relative_target_label_feasibility_20260712.manifest.sha256
```

공통 날짜 교집합 처리 전 계산 스크립트도 별도로 보존했다.

```text
backup/pre_relative_target_common_date_fix_20260712.tar.gz
backup/pre_relative_target_common_date_fix_20260712.manifest.sha256
```

## 입력 데이터

- 기존 feature set: `stage2_3_rediscovery_pilot_20260712/feature_set.csv`
- 입력 행: 75,900
- 종목: 50
- frozen OHLCV: `ohlc_snapshot_20260707/*_ohlcv.csv`
- 모든 OHLCV SHA-256은 기존 `symbol_list.csv`와 일치
- 기존 L0 라벨 재계산 불일치: 0건

기존 50종목 feature set에는 가격 path 12개만 저장돼 있었다. 요청한 14개 그룹 feature를 맞추기 위해 frozen OHLCV에서 D-1 기준 ATR·실현변동성·Bollinger 폭·range·volume 지표를 결정적으로 다시 계산해 기존 행에 결합했다.

## 공통 표본

20일 지표 warm-up과 AEVA의 비유한 값 3개 때문에 최초 유효 행은 종목별 1,500~1,503개였다. 종목 간 공평성 비교에서 표본 수 차이를 제거하기 위해 50종목 모두 유효한 날짜의 교집합만 사용했다.

```text
공통 날짜: 1,500개
종목당 행: 1,500
총 행: 75,000
기간: 2020-07-08 ~ 2026-07-01
```

## 금지 작업 확인

- GA 실행 없음
- 모델 학습 없음
- threshold 탐색 없음
- 라이브 후보 풀 변경 없음
- daemon 변경 없음
- `.env` 변경 없음
- 원본 stage2/3 수정 없음

`compute_readonly_metrics.py`는 입력을 읽고 통계를 stdout으로 계산하는 분석 전용 스크립트이며, 자체적으로 파일을 쓰거나 학습 프로세스를 실행하지 않는다.
