# 프로세스·무결성 감사

## 학습·GA 미실행

분석 전과 결과 검증 시 다음 프로세스 패턴을 점검했다.

```text
run_stage2
run_stage3
run_fitness
run_hybrid
genetic*.py
train_grouped
```

검출된 학습·GA 프로세스: **0개**

실행한 연구 작업은 다음뿐이다.

```text
기존 CSV/OHLCV 읽기
D-1 지표와 상대 라벨 계산
Pearson 상관 계산
mutual_info_classif 단변량 통계 계산
CSV 재계산 대조
```

모델 fitting, threshold 탐색, survivor 선택, 거래 backtest는 수행하지 않았다.

## Daemon

검증 시 상태:

```text
PID: 494330
STAT: Sl
CMD: data/_system/ops/live_candidate_slots.py daemon --interval 60
```

Daemon 재시작·중단·설정 변경은 없었다.

## 입력 무결성

```text
feature_set.csv
39b96b7af6b78278755064249f761907823d430ed5b7d28c8ee42901d65dc403

symbol_list.csv
c385a1514567dc71ca92c8bc9626d0a53e9662f0903d040ab4500a41cfe2e922
```

50개 frozen OHLCV 파일은 `symbol_list.csv`의 개별 SHA-256과 모두 일치했다.

## 원본·라이브 해시

```text
.env
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

data/_system/ops/live_candidate_slots.py
259d3bec12901591c84cd1ad9aec01612d914c9120c0976b54bb34adfe684dbb

engine/central/signal_collector.py
fc0768235189c5a6f95926d2c4f42aa78401e11b8fa2a8ab95992515a700f497

engine/learning/execution_mode_backtest.py
efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0

engine/strategies/evaluator.py
d7ce157564c3311d95ba73de79f41dfad3d7d1134727dd8a5fa776487cd83584

scripts/research/run_stage2.py
9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c

scripts/research/run_stage2_path_filter.py
52fda4ce2b047561f3b2eda5f6d5985e0b24232f2eade96a3a199734ad155a44

scripts/research/run_stage3_aggressive.py
8f275ca52745b6b9f92d56e0e24d8043ccef8644b5c5d996217b9c6226e701c0
```

작업 전 기준 해시와 동일하다.

## CSV 재계산 검증

`compute_readonly_metrics.py`를 다시 실행해 메모리 결과와 저장 CSV를 비교했다.

```text
label_positive_rates.csv: 50행 일치
label_fairness_comparison.csv: 3행 일치
feature_label_information.csv: 42행 일치
label_information_summary.csv: 3행 일치
CSV 파싱 오류: 0
행 너비 불일치: 0
```

부동소수 비교 허용오차는 절대·상대 `1e-12`였다.

## 백업

초기 계산 스크립트의 공통 날짜 수정 전 백업:

```text
backup/pre_relative_target_common_date_fix_20260712.tar.gz
backup/pre_relative_target_common_date_fix_20260712.manifest.sha256
```

입력·기존 결과·신규 결과를 묶은 최종 검증 백업:

```text
backup/relative_target_label_feasibility_inputs_outputs_20260712.tar.gz
backup/relative_target_label_feasibility_inputs_outputs_20260712.manifest.sha256
```

최초 신규 디렉터리 생성 직전의 별도 tar 백업은 만들지 못했다. 다만 신규 디렉터리는 기존 파일이 없던 경로였고, 원본·기존 산출물은 수정되지 않았다. 입력 원본은 Git·기존 manifest·최종 검증 백업으로 보존된다.
