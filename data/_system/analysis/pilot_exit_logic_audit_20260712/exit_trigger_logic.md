# 파일럿 rolling 청산 트리거 코드 감사

## 범위와 근거

- 조사 대상: `scripts/research/rolling_rediscovery/upstream_snapshot/` 복사본과 `data/_system/analysis/stage2_3_rediscovery_pilot_20260712/` 산출물만 사용했다.
- 복사본 기준 커밋: `96ee50edfefcb9d06dab13ba67689a8d5c6ff477`
- `engine/learning/execution_mode_backtest.py` SHA-256: `fd3014798f647038ec12ab678fbde55154a4dec1e579a6ff885b44cbba876be7`
- `scripts/research/run_stage2.py` SHA-256: `3004ea6593230d8b20ecbc1a71b4a8153bfa9aff7573939a36c15b6fcf2ca5f2`
- 재학습은 하지 않았다. 저장된 worker 입력 CSV, 저장된 최종 gene·threshold, 복사본 백테스트 함수를 사용해 거래 이벤트만 재구성했다.
- 재구성 검증: 50종목 × 3 regime = 150개 조합의 거래 수와 평균수익률이 기존 `rolling_vs_fixed_backtest.csv`의 rolling 행과 전부 일치했다.

## 점수 생성

복사본 `engine/learning/genetic.py` 63~69행은 각 feature가 자기 `[low, high]` 구간 안에 들어오는지를 계산한 뒤 모든 feature를 `np.all`로 AND 처리한다.

복사본 `engine/learning/execution_mode_backtest.py` 38~40행은 이 strict-AND mask를 다음과 같이 점수로 바꾼다.

```python
return np.where(np.asarray(active, dtype=bool), float(pass_probability), 0.0)
```

따라서 일별 점수는 연속적인 별도 exit 예측값이 아니라 다음 두 값 중 하나다.

- strict AND 통과: 학습 표본의 `pass_probability`
- strict AND 미통과: `0.0`

복사본 `scripts/research/run_stage2.py` 439~447행에서 regime별 mask와 score를 만들고, 517~524행에서 동일 score와 `best.decision_threshold`를 rolling 백테스트에 전달한다.

## 정확한 진입·유지·청산 조건

복사본 `engine/learning/execution_mode_backtest.py` 95~107행:

```python
score_arr = np.asarray(scores, dtype=float)
active = score_arr >= float(threshold)

for i in range(len(frame)):
    if active[i] and entry_idx is None:
        ... 진입 ...
    elif not active[i] and entry_idx is not None:
        ... 청산 ...
```

정확한 일반 청산 트리거는 다음과 같다.

```text
position_is_open AND score_today < decision_threshold
```

일반 청산 가격은 같은 파일 108~126행에 따라 해당 평가일의 `entry_open_d0`, 즉 D0 시가다. 시가가 비정상이면 D0 종가로 대체한다.

```python
exit_price = float(frame.iloc[i]["entry_open_d0"])
if not math.isfinite(exit_price) or exit_price <= 0:
    exit_price = float(frame.iloc[i]["entry_close_d0"])
```

구간이 끝날 때 아직 active 상태인 포지션은 128~145행에서 마지막 평가일 종가로 강제 mark-to-market 청산한다. 이것이 일반 점수 하락 외 유일한 청산 경로다.

## 2일 +3% 라벨과 청산의 관계

`label_2d3pct`는 GA 학습과 precision 평가에 사용되는 목표변수다. `rolling_score_backtest()`에는 라벨 배열이 인자로 전달되지 않으며, 함수 안에서도 `label_2d3pct`, 미래 고가, +3% 달성 여부를 참조하지 않는다.

따라서 다음 항목은 청산 트리거가 아니다.

- 진입 후 실제로 +3%를 달성했는지
- D+1 또는 D+2가 지난 뒤 +3% 미달이 확정됐는지
- +3% 달성 시 익절
- 2거래일 경과 시 시간 청산
- 별도 stop-loss, take-profit, max-holding-day

## 세 시나리오별 실제 동작

### (a) “+3%를 못 찍었다”는 결론이 난 경우

**라벨 미달 확정 자체로는 아무 청산도 발동하지 않는다.**

2일 +3% 라벨은 사후 학습·평가용이며 rolling 포지션 상태기계에 전달되지 않는다. 포지션은 마지막 +3% 예측일까지 기계적으로 보유하는 것도 아니고, D+2 종료 시 자동 청산하는 것도 아니다. 다음 평가일의 rolling score가 임계선 이상이면 계속 보유하고, 임계선 미만이면 그 평가일 D0 시가에 청산한다.

실제 거래 2,112건 중 진입일 라벨이 0이었던 거래는 789건이었지만, `LABEL_MISS`를 직접 청산 사유로 사용한 거래는 0건이었다.

### (b) 매일 재평가 중 다음날 +3% 미달이 예상되는 경우

이 파일럿에는 “다음날 +3% 미달 확률”을 연속값으로 산출하는 별도 exit 모델이 없다. 매일 D-5~D-1 feature가 학습된 12개 interval을 모두 통과하는지만 다시 평가한다.

- 다음 평가일 strict AND 통과: `score = pass_probability`, 임계선 이상이면 보유 유지
- 다음 평가일 strict AND 미통과: `score = 0.0`, 열린 포지션이면 그날 D0 시가에 즉시 청산

따라서 실질적으로는 “다음날 미달 예상”이 아니라 **다음 평가일의 진입용 strict-AND 조건 재통과 여부**가 청산을 결정한다.

### (c) 실제 청산 발동 트리거의 정확한 조건식

```text
일반 청산:
entry_idx is not None
AND NOT (score_arr[i] >= decision_threshold)

동치:
position_is_open AND score_arr[i] < decision_threshold
```

파일럿 점수 생성 방식까지 풀어 쓰면 다음과 같다.

```text
position_is_open
AND NOT all_12_features_inside_their_trained_intervals
```

정상적인 학습 결과에서 `pass_probability >= decision_threshold`였으므로 strict AND 미통과일의 `score=0.0`이 일반 청산을 발동했다. 재구성된 일반 청산 2,105건의 exit score는 전부 정확히 `0.0`이었다.

```text
강제 청산:
루프 종료 후 entry_idx is not None
→ 마지막 평가일 D0 종가로 period-end mark-to-market
```

## 결론

rolling 청산은 **2일 +3% 라벨 미달 청산이 아니라, 진입과 같은 일별 strict-AND 점수가 같은 임계선 아래로 내려가는 순간의 점수 하락 청산**이다. 별도 보유기간 상한은 없으며, 구간말 강제평가만 추가로 존재한다.
