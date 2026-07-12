# payoff/range predictor 라이브 미연결 경위 + 검증상태 조사

- 조사 대상: 사용자 설계의 “2일 상승 selector”와 가장 가까운 range/payoff predictor 연구군
- 조사 기준 Git HEAD: `da7bee3f3db8d3c36ead224e1a84103ed8412c73`
- 현재 후보 풀 표본: 10개
- 코드 변경: **0**
- 최종 판정: **재작업필요 — 검증우선**

## 1. 결론

range/payoff predictor는 단순 아이디어 메모 수준은 아니었다. 다음 기능까지 연구 코드로 구현됐다.

- 최근 5일 또는 10일 lag feature 생성
- Stage2 성분과 가격·기술 상태 결합
- 개체별 feature weight, quantile band, cut 진화
- rolling train과 stress/OOS 생존 평가
- 누수 feature 제거
- 상방·하방 유전자 분리
- 최종 `true_validation_pass.jsonl`을 생성하도록 한 감사 wrapper

그러나 라이브 후보선정에 필요한 다음 계약은 만들어지지 않았다.

- 확정된 target과 horizon
- 전 종목 또는 현재 후보 종목의 frozen model artifact
- artifact version registry
- live inference loader
- feature parity 검사
- 기존 `final_score >= signal_threshold` 대비 동일 표본 성능 비교
- prospective shadow 검증

Git 이력과 운영 감사에서 라이브에 붙였다가 제거한 흔적은 `NOT_FOUND`다. 확인된 구조는 처음부터 `scripts/research` 아래의 one-off experiment였고, 2026-07-10 운영 비도달 감사에서 관련 산출물은 `TERMINATED_EXPERIMENT_DATA`, active reference 0으로 분류됐다.

명시적으로 “검증에 실패했기 때문에 승격하지 않는다”라고 적은 결정문은 `NOT_FOUND`다. 다만 다음 정황은 강하다.

1. target이 짧은 시간에 반복 변경됐다.
2. 주요 +2%/TP/SL/TP-only 실험은 final survivor가 0이었다.
3. 생존한 변형도 성능이 혼재하거나 gate가 지나치게 느슨했다.
4. cross-ticker·portfolio 비교가 없었다.
5. 마지막 검증 산출물은 운영에 연결되지 않은 채 종료·정리됐다.

따라서 중단 사유를 확정적으로 “검증 실패”라고 단정할 수는 없지만, **target 미고정과 검증 불안정 때문에 연구 단계를 벗어나지 못했다는 해석은 강한 INFERRED 판정**이다.

## 2. 미연결 경위

### 최초 생성과 일시 폐기

- `2026-07-03 14:32 UTC`, commit `b8138f3`: 다음날 고저 구간 예측 GA 신규 추가
- `2026-07-03 15:05 UTC`, `59ac6f8`: Stage2 흐름을 이식한 v3 추가
- `2026-07-03 15:13 UTC`, `ed211b1`: v1/v2 정리, v3 유지
- `2026-07-03 15:52 UTC`, `3c369e5`: “실패 예측 실험 러너 정리, 스윙 진입품질 작업으로 전환”하면서 v3도 삭제

이 구간은 predictor가 초기 실패 실험으로 취급됐음을 직접 보여준다.

### 재시작과 급격한 target 변경

`2026-07-04 11:16 UTC`, commit `70719cc`에서 v3가 Stage2 연속 생존 구조로 재추가됐다. 이후 하루 동안 target이 연속 변경됐다.

1. HIGH/LOW 실제 퍼센트 및 bin
2. 다음날 상·하방 2% binary event
3. 상방·하방 분리
4. 상방 +2% precision 우선
5. TP +2% / SL -1% 실전형 LONG
6. TP-only +2%
7. 다음날 large-range volatility

각 변경은 commit docstring과 메시지에 목적이 적혀 있어 의도적인 실험 전환으로 확정된다.

### payoff/ATR 방향으로 전환

2026-07-05~06에는 다음 구조가 추가됐다.

- probability classifier
- target-derived feature 누수 차단
- 2-gene payoff GA
- 3일·5일 lag wrapper
- HIGH/LOW 별도 유전자
- stress를 검증 전용으로 분리
- D0 gap 제거
- stress/OOS 진짜검증 감사

마지막 관련 개발 commit은 `2026-07-06 04:35 UTC`, `ec73c17`이다. 이후 라이브 연결 commit은 발견되지 않았다.

### 운영 종료 확인

`data/_system/analysis/candidate_selection_audit_20260710/operational_unused_second_pass.csv`는 range/payoff 관련 308개 row를 포함한다. 대표 상세 산출물을 다음처럼 판정했다.

- `second_pass_type=TERMINATED_EXPERIMENT_DATA`
- `operational_reachable=X`
- `active_reference_count=0`
- `safety_verdict=DELETE_OK`
- 근거: 운영 비도달·프로세스 없음·summary 존재

원본 감사 파일:

- mtime: `2026-07-10T14:26:48.803670796Z`
- SHA-256: `54308dfdc9e0d72a6f98af11d1628202b165070c32b68d157137ed6bf5da231c`

대용량 `predictors_all.jsonl`, `period_metrics`, `summary`, payoff `all_candidates` 등은 이 정리 과정에서 삭제됐고, 일부 보호 survivor pool만 남았다.

## 3. 검증 상태

### 검증 구조는 존재했다

Range predictor의 historical 구조는 다음을 사용했다.

- 252거래일 rolling train
- 21거래일 step
- rolling survivor 전달
- stress period
- OOS 2025H2
- train 분포로 만든 quantile spec을 final period에 재사용

Payoff classifier는:

- train1~3 fit
- stress/train에서 threshold 선택
- OOS 2025-07-01~2026-06-30 보고
- target-derived column 누수 차단

최종 payoff 감사 wrapper는:

- 5일 lag
- D0 gap 제거
- train1~3 soft pass
- stress validation
- OOS final check
- stress/OOS 각각 최소 신호 10개
- 학습 통과와 진짜 검증 통과 분리

따라서 “검증 코드 자체가 없었다”는 설명은 틀리다.

### 주요 실험 결과

#### 다음날 상방 2% HIGH

`exp_fix_range_predictor_stage2_v3_rolling_event2pct_high_20260704_001`

- stage survivor: 480
- final survivor: 17
- final survivor file mtime: `2026-07-04T14:56:01.558024Z`
- SHA-256: `238ff50136f39598b10ee5bcc376c1a5369586046f90d396ff303c1bf7b8f625`

17개는 당시 sequential final gate를 통과했다. 그러나 상세 `final_period_metrics.jsonl`과 summary가 삭제되어 각 survivor의 stress/OOS 정확 성능은 현재 `NOT_STORED`다.

#### precision 강화 상방 2%

- stage survivor 387
- final survivor 0

즉 일반 gate에서 생존한 모델은 있었지만, precision과 신호 빈도를 강하게 요구한 버전은 최종 생존하지 못했다.

#### TP/SL 및 TP-only

- FIX TP +2% / SL -1%: final 0
- MPC TP +2% / SL -1%: final 0
- MPC TP-only +2%: final 0

실거래 의미에 가까운 label로 바꾸자 final survivor가 사라진 것이 확인된다.

#### 다음날 large range

- stage survivor 475
- final survivor 0

#### 이후 coarse/pattern 변형

`coarse3_stage2gate_loose`, final 22:

- median OOS HIGH lift: `-8.506pp`
- median OOS LOW lift: `-1.245pp`
- median OOS both lift: `-8.299pp`

완화된 gate를 통과했지만 baseline 대비 중앙 성능은 불리했다.

`high_only`, final 4:

- median OOS signal coverage: `8.714%`
- median OOS HIGH lift: `+16.228pp`
- median stress HIGH lift: `+1.630pp`

긍정적 흔적은 있으나 FIX 단일 종목, 모델 4개, 첫 survivor OOS signal count 19 수준의 작은 표본이다.

`true3_stage2gate_fixed`, final 51:

- median OOS HIGH lift: `-6.639pp`
- median OOS LOW lift: `+7.884pp`
- median OOS both lift: `+1.660pp`
- median stress HIGH lift: `-2.335pp`
- median stress LOW lift: `+2.140pp`
- median stress both lift: `-8.366pp`

상방과 하방 head의 성능 방향이 일관되지 않았다.

일부 no-danger/open-gate 변형은 300개 중 299~300개가 final survivor가 됐다. 이는 강한 예측력보다는 gate가 거의 선택성을 갖지 못한 가능성을 시사한다. 이는 `INFERRED`다.

### 마지막 payoff 검증 결과

최종 감사 wrapper는 `true_validation_pass.jsonl`과 summary를 만들도록 구현돼 있다. 그러나 해당 experiment 결과는 현재 삭제돼 다음이 `NOT_STORED`다.

- soft pass count
- validation pool count
- true validation pass count
- stress/OOS 개별 성능
- 선택된 predictor 및 cut

백업 tar 전수 확인에서도 experiment result는 발견되지 않았고 스크립트만 보존돼 있었다.

### 기존 signal_threshold 방식과 비교

직접 비교는 `NOT_FOUND`다.

Predictor 연구 코드에는 다음 비교가 없다.

- 동일 후보 universe
- 기존 `final_score >= signal_threshold`
- predictor 추가 전후 후보 수
- 동일 exit rule 적용 성과
- portfolio CAGR/MDD/Sharpe

일부 feature에 Stage2 lag raw score가 들어가지만, 이는 baseline A/B 비교가 아니다.

### q<45처럼 검증 실패로 제외된 것인가

q<45에는 후보선정에서 사용하지 않는다는 운영 문구와 별도 검증 readout이 남아 있었다. Predictor에는 이에 해당하는 명시적 exclusion 문서가 없다.

판정:

- 명시적 검증 탈락 결정: `NOT_FOUND`
- 검증 불안정 정황: `CONFIRMED`
- 그것이 미승격의 직접 원인이라는 판단: `INFERRED`

## 4. “2일·2~3% → 1일·ATR” 변형 경위

### 2일 horizon

중요하게, 저장소에 처음 등장한 predictor부터 target은 `next_day`였다. exact 2-trading-day label은 코드·artifact·reachable Git history에서 `NOT_FOUND`다.

따라서 코드상으로는 “2일 구현을 1일로 변경했다”가 아니라:

> 사용자 원 설계의 2일 horizon이 구현에 들어오지 않았고, 첫 구현부터 1일 horizon으로 시작했다.

왜 2일을 버렸는지 적은 기록은 `NOT_FOUND`다.

### +2% target

+2% next-day target은 실제로 구현됐다.

- 다음날 high가 open 대비 +2% 이상
- 상·하방 분리
- precision 우선
- TP +2% / SL -1%
- TP-only +2%

하지만 여러 변형에서 final survivor가 0이 되거나 성능이 불안정했다.

### ATR 변형

ATR 전환은 명시적인 설계 변경이다.

- 종목별 변동성 차이를 normalize
- 상방 목표와 하방 위험을 분리
- GOOD_LONG_DAY와 BAD_RISK_DAY를 정의
- 개체별 cut으로 선별
- 고정 +2%보다 종목 변동성에 맞춘 target을 시험

해당 의도는 payoff classifier와 two-gene code에 직접 적혀 있다.

따라서 다음처럼 판정한다.

- 2일 → 1일: 원인 `NOT_FOUND`, 최초 구현부터 1일
- +2% → TP/SL/TP-only: 의도적 실험 변경 `CONFIRMED`
- +2% → range/ATR: 의도적 target reformulation `CONFIRMED`
- +3% exact target의 정식 계보: `NOT_FOUND`

## 5. 현재 상태로 재연결 가능한가

### 코드 실행 가능성과 라이브 연결 가능성은 다르다

연구 runner는 실행 가능한 수준이다. 하지만 라이브 선정에 바로 붙일 수 있는 수준은 아니다.

현재 `run_range_predictor_stage2_v3.py`는:

- historical commit `3b24382`의 자기 파일을 `git show`로 동적 로드
- 5일이 아니라 10일 lookback 강제
- HIGH/LOW head objective 실험 구조

Payoff final audit는 별도 wrapper에서 다시 5일 lookback을 강제한다. 즉 현재 연구 코드 자체도 하나의 frozen production specification으로 통일돼 있지 않다.

또한 live에는 다음이 없다.

- predictor artifact loader
- predictor version 선택
- inference API
- feature freshness/parity guard
- missing artifact fallback policy
- candidate row의 predictor score/reason schema

### 현재 후보 풀 10개 소급 적용

현재 후보:

- ADMA
- CRS
- ALGT
- AEIS
- ARKW
- CBRL
- BTU
- BB
- BN
- ACMR

10개 모두 대응하는 range/payoff trained artifact가 없다.

따라서 소급 결과는:

- 유효 predictor score: 0개
- pass/fail 계산 가능: 0개
- pool 감소량: `NOT_CALCULABLE`

이는 단순히 시간이 부족해 계산하지 않은 것이 아니다. 개체별 predictor 설계인데 해당 개체 artifact가 구조적으로 없기 때문이다. FIX 모델을 CRS나 ADMA에 적용하면 사용자 설계의 “개체별 기준선”을 위반하므로 수행하지 않았다.

CRS 역시:

- predictor artifact `NOT_FOUND`
- +2%/ATR 예측값 `NOT_STORED`
- predictor pass/fail `NOT_APPLICABLE`

### 재연결에 필요한 재작업

1. target을 먼저 고정해야 한다.
   - 정확히 2거래일 내 high +2% 또는 +3%
   - 종가 기준인지 시가 기준인지
   - 중간에 -1% 하락 후 +2% 도달도 성공인지
   - 2일 중 어느 시점까지인지

2. feature 시점을 고정해야 한다.
   - 신호 직전 완료 일봉 5개
   - D0 intraday 사용 여부
   - gap feature 허용 여부

3. 전 종목 artifact 전략을 정해야 한다.
   - 종목별 모델
   - sector pooled model
   - global model + 개체별 threshold

4. frozen OOS 비교가 필요하다.
   - 기존 signal_threshold only
   - predictor hard block
   - predictor rank penalty
   - predictor sizing

5. prospective shadow가 필요하다.
   - 판단 시점 feature snapshot
   - predictor version/hash
   - score/cut/pass reason
   - 2일 후 hit/miss 자동 label

## 6. 최종 판정

### **재작업필요 — 검증우선**

`재연결가능`으로 판정할 수 없는 이유:

- exact 2일 target이 구현돼 있지 않다.
- target이 여러 형태로 분기돼 있다.
- 현재 후보용 artifact가 없다.
- live inference 경로가 없다.
- 상세 payoff 최종 검증 결과가 삭제됐다.
- 기존 후보선정 대비 포트폴리오 비교가 없다.

연구 자산은 재사용 가치가 있다.

- dataset/lag feature builder
- leakage audit
- rolling survival
- 개체별 cut/weight
- stress/OOS final audit

하지만 “예전 predictor를 다시 연결”하는 작업이 아니라, **사용자 원 설계를 명시적으로 재고정하고 연구 자산을 이용해 다시 학습·검증하는 작업**이어야 한다.

세부 산출물:

- `disconnection_history.csv`
- `experiment_status_inventory.csv`
- `validation_status.csv`
- `target_transformation_timeline.csv`
- `reconnection_impact.csv`

백업:

- `backup/pre_payoff_predictor_status_20260712.tar.gz`
- SHA-256 `56c2ecf2a9177aadf16a7589ea61666121613421f4cc92d18545409248040d11`
