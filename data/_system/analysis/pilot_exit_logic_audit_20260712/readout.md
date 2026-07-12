# 파일럿 청산 로직 감사 판정

## 최종 판정

# **CLEAN**

이번 rolling 파일럿의 청산은 의도한 **rolling 점수 단독 청산**으로 작동했다. 과거 Stage3 exit GA는 복사본 안에 보존돼 있지만 현재 실행 경로에서 학습·호출·결합되지 않았다.

## 핵심 수치

저장된 최종 gene·threshold와 worker 입력을 사용해 재학습 없이 50종목 × 3 regime의 rolling 거래를 재구성했다.

- 재구성 거래: 2,112건
- 기존 rolling 집계와 일치: 150 / 150 종목×regime 조합
- 점수 하락 청산: 2,105건, 99.6686%
- 라벨 미달 직접 청산: 0건
- 구간말 강제 mark-to-market: 7건, 0.3314%
- 기타 청산: 0건
- 일반 청산 2,105건의 exit score: 전부 `0.0`
- 진입일 2일 +3% 라벨이 0인 거래: 789건, 그러나 이 값이 청산을 직접 발동한 사례는 0건

보유 세션 분포:

| 보유 세션 | 거래 수 | 청산 구성 |
|---:|---:|---|
| 0 | 6 | 구간말 강제평가 6 |
| 1 | 1,871 | 점수 하락 1,870 + 구간말 강제평가 1 |
| 2 | 220 | 점수 하락 220 |
| 3 | 15 | 점수 하락 15 |

1세션 이하 거래는 1,877건으로 전체의 88.87%다. 이는 exit GA 오염이 아니라, binary strict-AND 점수가 다음 거래일에 0으로 바뀌는 빈도가 높아서 발생한 rolling 휩쏘다.

## 세 시나리오 판정

### (a) “+3% 못 찍음” 결론 시

라벨 미달 확정으로 청산하지 않는다. D+2까지 의무 보유하거나 D+2에 자동 청산하는 규칙도 없다. 이후 일별 rolling score가 임계선 아래로 내려가면 그 평가일 시가에 청산한다.

### (b) 다음날 +3% 미달 예상 시

별도 연속형 exit 예측모델은 없다. 다음 평가일의 동일 12-feature strict-AND 조건이 깨지면 score가 0이 되고, 열린 포지션은 그날 시가에 청산된다.

### (c) 실제 트리거

```text
일반 청산 = position_is_open AND score_today < decision_threshold
강제 청산 = regime 종료 후에도 position_is_open
```

## Stage3 exit GA 판정

- 과거 설정: population 60, generation 25
- 이번 파일럿 실제 학습: **아니오**
- 현재 Stage3 진입점: `run_stage2.main()`으로 위임
- 현재 `exit_gene.py` import: 0건
- 현재 `run_exit_ga()` 호출: 0건
- exit-GA 산출물: 0건
- rolling과 exit GA 동시 청산: 없음
- 충돌·중복: 없음

복사본 기준 커밋 `96ee50edfefcb9d06dab13ba67689a8d5c6ff477`에서:

- `engine/learning/execution_mode_backtest.py` 95~107행: 동일 임계선 active 판정과 점수 하락 청산
- 같은 파일 108~126행: D0 시가 청산
- 같은 파일 128~145행: 구간말 종가 강제평가
- `scripts/research/run_stage2.py` 439~447행: strict-AND mask와 binary score 생성
- 같은 파일 517~524행: rolling과 fixed 비교군을 별도 method로 실행
- `scripts/research/run_stage3_aggressive.py` 21~25행: Stage2 rolling orchestration으로 위임

과거 `.bak` 파일에는 exit GA가 있으나 현재 실행 파일이 아니다.

## 파일럿 결과 영향 평가

**exit GA 혼입에 따른 수익·휩쏘·survivor 오염은 0으로 판정한다.**

따라서 이번 파일럿을 다시 돌려야 할 근거가 있다면 그것은 다음 문제에서 찾아야 한다.

- GA population·generation 축소
- strict-AND interval의 지나친 일별 불연속성
- binary score 구조로 인한 1일 휩쏘
- stress/OOS 게이트와 표본 크기
- survivor 0건이라는 일반화 실패

Stage3 exit GA가 섞였다는 이유로 현재 수익·휩쏘·survivor 결과를 폐기할 필요는 없다. 다만 rolling 자체가 1세션 청산에 매우 치우쳤다는 결과는 그대로 유효하며, 다음 재실행에서는 점수 안정화나 진입·유지 임계선 분리 여부를 별도 실험해야 한다. 이 문장은 개선 방향에 대한 [추정]이며 이번 read-only 감사에서 코드를 변경하지 않았다.

## 검증 상태

- 조사 범위: 복사본 코드와 기존 파일럿 산출물만 사용
- 재학습: 없음
- 원본·라이브 코드 변경: 없음
- 설정 변경: 없음
- daemon 변경: 없음
- 감사 전 백업: `backup/pre_pilot_exit_logic_audit_20260712.tar.gz`
- 백업 manifest: `backup/pre_pilot_exit_logic_audit_20260712.manifest.sha256`
