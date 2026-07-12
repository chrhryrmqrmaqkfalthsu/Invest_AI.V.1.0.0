# Stage2/3 rolling 재발견 — 50종목 파일럿

- 판정: **PILOT_FAIL**
- 표본: 50종목 (현 라이브 후보 10 + seed `20260712` 결정적 무작위 40)
- frozen OHLC 실제 시작일: 2020-06-08
- `2020-01-01~2020-06-07`: `NOT_STORED`
- 매일 독립 평가 행: 75,900
- 병렬 실행: spawn `Pool(6)`, 실제 worker PID 6개
- worker 오류: 0
- survivor: 0 / 50

## 최종 판정

코드 실행, 출력 형식, 6병렬 처리, 양방향 gene, 최소폭 제약, strict AND, upper-bound fallback은 정상 동작했다. 그러나 train에서 학습된 개체가 stress와 OOS에서 동시에 정밀도·표본을 유지하지 못했고, 동일 임계선 rolling은 과도한 하루 단위 진입·청산 반복을 보였다. 따라서 전체 확대 조건을 충족하지 못해 `PILOT_FAIL`로 판정한다.

사후에 거래수·정밀도 임계값을 완화하지 않았다. 이번 결과만 보고 게이트를 낮추면 과적합 완충 취지가 훼손되므로, 현재 개체 구조를 그대로 전체 종목에 확대하지 않는다.

## Step 0 — 중단 잔여물 정리

직전 중단 작업의 대상 디렉터리는 비어 있었으며 부분 생성 일반 파일은 0개였다. 삭제한 파일은 없고 `_aborted/`로 이동할 파일도 없었다. 중단으로 인한 라이브 코드·설정·daemon 변경이 없음을 확인한 뒤 Step 1을 진행했다.

상세 기록: `step0_cleanup_report.md`

## Step 1 — 기존 stage2/3 무손실 복사

`run_stage2_path_filter.py`, `run_stage2.py`, stage2 학습·백테스트 의존 모듈, stage3 wrapper와 동적 원본 orchestration 백업, `exit_gene.py`, `stage3_gate.py`를 포함한 **39개 파일**을 복사했다.

- 복사 전 원본 SHA-256 기록: 39개
- 복사 직후 원본↔복사본 SHA-256 일치: **39/39**
- 해시 불일치: 0
- 실제 작업본: `scripts/research/rolling_rediscovery/upstream_snapshot/`

이 복사본은 보존용 박제가 아니라 실제 수정·실행 베이스다. 별도 독립 runner를 새로 만들어 우회하지 않았다.

`build_stage3_live_pool.py`는 학습 의존성이 아니라 결과를 라이브 후보 풀로 내보내는 배포 도구이므로, 라이브 변경 금지에 따라 복사·호출·수정하지 않았다.

상세 기록: `copy_inventory.csv`, `copy_integrity_check.csv`, `dependency_exclusion_log.csv`

## Step 2 — 복사본 직접 수정

복사 작업본 안에서 다음 구조를 구현했다.

- 모든 저장 거래일을 보유 상태와 무관한 독립 진입 후보로 평가
- D-5~D-1 path_filter 계열 12개 feature만 사용
- 모든 gene을 정규화 train 범위의 `[하한, 상한]`으로 정의
- gene 최소폭 10% 강제
- 폭 98% 이상인 사실상 무제한 gene이 2개를 초과하면 노이즈 개체로 탈락
- 12개 지표가 각각 자기 구간을 만족해야 통과하는 strict AND
- 가중합, 합산 상쇄, 호재 예외 경로 없음
- 상한 학습 실패 시 해당 feature의 train 성공 거래 최댓값 또는 최소폭 경계를 상한으로 대입
- train만 GA 학습에 사용하고 stress·OOS는 검증 전용
- 점수는 strict-AND 통과 train 표본의 2일 +3% 정밀도
- 동일 임계선으로 진입·유지·청산
- 인위적 보유일 상한 없음
- 50종목을 종목 단위 spawn `Pool(6)`로 독립 학습
- worker별 입력·결과 파일 분리 후 최종 병합

상세 기록: `modification_log.md`

## Feature·라벨·누수 점검

사용 feature는 다음 12개다.

`ret_d5_pct`, `ret_d4_pct`, `ret_d3_pct`, `ret_d2_pct`, `ret_d1_pct`, `cumulative_ret5_pct`, `up_days5`, `days_since_high5`, `close_pos5`, `pullback_from_high5_pct`, `single_up_day5_pct`, `fade_after_surge_score`

- feature cutoff: D-1
- `STK_gap_d0`: 미사용
- `ETF_gap_d0`: 미사용
- flow: 미사용
- order_book: 미사용
- D0 open 및 D+1~D+2 high는 라벨·체결 성과 계산 전용이며 GA feature 목록에는 없음
- 라벨: D0 open 대비 D+1~D+2 high 최대값이 +3% 이상이면 1

frozen snapshot 이전인 2020년 1월 1일~6월 7일 데이터는 합성하거나 추정하지 않고 `NOT_STORED`로 기록했다.

## GA·gene 건전성

- 최종 gene 수: 50종목 × 12개 = 600
- 양방향 `[하한, 상한]` 통과: **600/600**
- 최소폭 통과: **600/600**
- strict AND 표시: **600/600**
- upper-bound fallback 실제 적용: **2,063건**
- 열린 gene probe: 차단 (`open_or_nonfinite_bound`)
- 최소폭 미달 probe: 차단 (`min_width_violation`)
- 한 지표 과다값으로 다른 지표 미달을 상쇄하는 probe 통과: **0건**

[추정] 파일럿 게이트는 다음과 같이 사전 적용했다.

- train 최소 표본: `max(20, train 행의 2%)`
- stress/OOS 최소 표본: `max(8, 검증 행의 1.5%)`
- 진입·청산 임계선: `max(45%, train 양성률 + 8%p)`, 최대 80%
- 검증 정밀도 하한: `max(30%, regime 양성률 + 3%p, train 정밀도 - 15%p)`
- 왕복 거래비용: 10bp

## Stress·OOS 이중 게이트 결과

- train gate 통과: **49/50**
- stress gate 통과: **4/50**
- OOS gate 통과: **5/50**
- stress와 OOS 동시 통과: **0/50**
- 얇은 표본 관련 탈락 표시: 23/50
- stress/OOS에서 `passed_count < 8` 발생: 총 25건

평균 정밀도:

- train: 77.24%
- stress: 44.75%
- OOS: 47.86%

평균 통과 표본:

- train: 21.52건
- stress: 16.78건
- OOS: 8.96건

train에서 선택된 희소 구간 조합이 다른 regime에서 유지되지 않았다. stress 통과 집합과 OOS 통과 집합의 교집합이 0이므로 survivor HHI는 `INSUFFICIENT_DATA`다.

## Rolling vs 고정 2일

최종 비교 CSV는 50종목 × 3 regime × 2방식 = **300행**이다.

- rolling 동일 임계선: 150행
- 고정 2일 보유: 150행

survivor가 0개이므로 survivor 전용 성과 비교는 `INSUFFICIENT_DATA`다. 진단을 위해 전체 50개 최종 train 개체의 OOS 결과를 비교했다.

### 동일 임계선 rolling

- 평균 거래수: 7.96건
- 거래당 평균수익률: **+0.1475%**
- 평균 복리수익률: +2.2204%
- 평균 MDD: -6.5569%
- 최장 실제 보유: 3세션
- 252세션 초과 보유: 0건

### 고정 2일 보유

- 평균 거래수: 7.76건
- 거래당 평균수익률: **+0.6183%**
- 평균 복리수익률: +6.7798%
- 평균 MDD: -9.0476%

### 휩쏘

- OOS rolling 1세션 휩쏘율 평균: **90.01%**
- OOS 임계선 crossing 평균: 15.9회

rolling은 MDD가 낮았지만 거래당 평균수익률과 복리수익률이 고정 2일보다 낮았다. 이진형 strict-AND 점수가 통과일에는 고정 정밀도, 미통과일에는 0으로 급변해 하루 단위 청산이 과도하게 발생했다. 무한보유 위험은 없었지만 rolling 실효성도 확인되지 않았다.

## 코드·출력 검증

- CSV 전수 파싱 대상: 72개
- CSV 파싱 오류: **0**
- 필수 산출물 누락: 0
- 50종목 완료: 50/50
- worker 오류: 0
- 실제 worker PID: 6개
- 병렬 worker 상한 초과: 없음
- 원본 stage2/3 및 라이브 코드 SHA-256 불변
- daemon PID 494330 유지
- `.env` SHA-256 불변
- 사전 백업 manifest 검증 통과

## 전체 확대 영향

현재 상태로 전체 종목 확대를 진행하지 않는다. 코드와 산출 형식은 정상이나 다음 두 문제가 남았다.

1. stress·OOS 이중 게이트 survivor가 0개다.
2. 동일 임계선 rolling의 1세션 휩쏘율이 90.01%이고 고정 2일보다 평균수익이 낮다.

다음 재실행에서는 임계값 완화보다 먼저, strict AND 원칙을 유지하면서도 매일 변화하는 **연속 확률 점수**를 만드는 방법을 복사 작업본 안에서 보완해야 한다. 진입·청산 임계선을 다르게 두는 hysteresis는 사용자가 지정한 동일 임계선 원칙과 충돌하므로 이번 파일럿에는 적용하지 않았다.
