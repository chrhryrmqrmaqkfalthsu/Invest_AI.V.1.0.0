# 프로세스·무결성 감사

## 학습·GA 미실행

분석 전·중·후 다음 프로세스 패턴을 검사했다.

```text
run_stage2
run_stage3
run_fitness
run_hybrid
genetic*.py
train_grouped
```

검출된 학습·GA 프로세스: **0개**

실행한 작업:

```text
frozen OHLCV 읽기
D-1 상대 feature 계산
L2 라벨 재계산
Pearson 상관 계산
mutual_info_classif 단변량 MI 계산
CSV 구조·수치 검증
결정적 재계산 SHA 대조
```

모델 fit, GA, threshold 탐색, survivor 선택, 거래 backtest는 수행하지 않았다.

## Daemon

검증 상태:

```text
PID: 494330
STAT: Sl
CMD: data/_system/ops/live_candidate_slots.py daemon --interval 60
```

재시작·중단·설정 변경 없음.

## 입력 SHA-256

```text
stage2_3_rediscovery_pilot_20260712/feature_set.csv
39b96b7af6b78278755064249f761907823d430ed5b7d28c8ee42901d65dc403

stage2_3_rediscovery_pilot_20260712/symbol_list.csv
c385a1514567dc71ca92c8bc9626d0a53e9662f0903d040ab4500a41cfe2e922

relative_target_label_feasibility_20260712/summary.json
3a5d9335278bbaff16a63289729ba39f7493913054b462c9ac80e921d741ecd1
```

50개 frozen OHLCV는 `symbol_list.csv`에 저장된 SHA-256과 모두 일치했다.

## 원본·라이브 SHA-256

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

작업 전 기준과 동일하다.

## 출력 검증

```text
relative_feature_set.csv: 74,750행 × 21열
relative_feature_information.csv: 14행 × 13열
three_way_information_comparison.csv: 3행 × 13열
source_checks.csv: 50행 × 8열
CSV 파싱 오류: 0
행 너비 오류: 0
```

추가 assertion:

- 50종목 모두 1,495행
- 종목·날짜 중복 0
- 신규 feature NaN·inf 0
- percentile feature 범위 `[0,1]`
- L2 라벨 값 `{0,1}`
- L0 기존 저장 라벨 불일치 0
- 기존 L2 동일 표본 Top-5 MI `0.1291213283` 재현
- 신규 상대 feature Top-5 MI `0.0301729424`
- `|corr|≥0.10` 신규 feature 0개

## 결정성 검증

분석 스크립트를 동일 입력으로 재실행해 다음 5개 파일의 실행 전·후 SHA-256이 동일함을 확인했다.

```text
relative_feature_set.csv
83d887d8f6e87e4a207b63d458eca8b9f00b33561c82401c6d6a24c4aee25c3c

relative_feature_information.csv
33001a82ce3ebe72da9624964f607d3886e2885598147aa30dc18a6c825ea6ee

three_way_information_comparison.csv
98a64a0bba1fdbd828d7b16b5d671fccfd9c485fb80eca0e01cf690dc7816416

source_checks.csv
98b35f0fbfbe8bfbfc792d044f9b9ad8d057e55f12dc9caa1c9b367f582f2edd

summary.json
f8e601b0c77b5d0577b0eca78f68718d036474cc6265c4db856adebfb7957a0c
```

결정적 재계산 결과: `OK`

## 사전 백업

신규 출력 디렉터리 생성 전에 생성했다.

```text
backup/pre_relative_feature_information_20260712.tar.gz
backup/pre_relative_feature_information_20260712.manifest.sha256
```

Manifest 검증 결과: `OK`
