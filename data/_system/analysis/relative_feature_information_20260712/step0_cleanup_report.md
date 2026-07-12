# Step 0 — 상대 정규화 feature 조사 사전 상태

## 신규 디렉터리 생성 전 백업

이번에는 신규 출력 디렉터리를 만들기 전에 다음 백업을 먼저 생성·검증했다.

```text
backup/pre_relative_feature_information_20260712.tar.gz
backup/pre_relative_feature_information_20260712.manifest.sha256
```

백업 포함 범위:

- 기존 50종목 `feature_set.csv`
- 기존 50종목 `symbol_list.csv`
- 직전 상대 라벨 타당성 분석 전체
- 지표 확장·그룹핑 분석 전체
- 50종목 frozen OHLCV snapshot 전체

백업 manifest 검증 결과: `OK`

## 기준 상태

```text
Git HEAD: e05f56ee4a18b4d6e5d310191a863e39d7bfeb0f
작업 트리: clean
daemon PID: 494330, STAT Sl
신규 출력 경로: 작업 시작 전 NOT_EXISTS
```

## 분석 제한

- GA 실행 금지
- 모델 학습 금지
- threshold 탐색 금지
- 원본 stage2/3 수정 금지
- 라이브 후보 풀·스위치 수정 금지
- `.env` 수정 금지
- D0 이후 데이터의 feature 사용 금지

분석 스크립트는 frozen OHLCV와 기존 feature set을 읽어 상대 feature·L2 라벨·상관·MI를 계산하고 신규 분석 경로에만 CSV를 생성한다.

## 시장·섹터 상대강도

Frozen OHLCV snapshot에서 다음 시장 proxy를 찾았으나 저장 파일이 없었다.

```text
SPY
QQQ
IWM
VTI
```

따라서 시장·섹터 대비 5일 상대강도는 `NOT_AVAILABLE`로 기록하고 계산 대상에서 제외한다. 임의로 50종목 평균을 시장 proxy로 만들지 않았다.
