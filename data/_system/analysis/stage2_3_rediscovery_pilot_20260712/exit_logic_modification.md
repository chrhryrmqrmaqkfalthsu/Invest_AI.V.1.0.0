# Step 1 — rolling 목표일 청산 로직 수정

## 수정 대상과 커밋

- 파일: `scripts/research/rolling_rediscovery/upstream_snapshot/engine/learning/execution_mode_backtest.py`
- 수정 전 기준 커밋: `ac16d8c94bace68f375632ed12da6bceb8a38e53`
- 수정 코드 커밋: `4b29e6bd64b9f98a663e1dd88ed8811571c53827`
- 원본·라이브 파일은 수정하지 않았다.

## 원본 대비 핵심 diff

삭제된 즉시 청산 개념:

```diff
- elif not active[i] and entry_idx is not None:
-     exit_price = frame.iloc[i]["entry_open_d0"]
-     ...
-     holding_sessions = i - entry_idx
```

추가된 목표일 방식:

```diff
+ target_idx = entry_idx + target_horizon_sessions
+
+ if active[i]:
+     proposed_target = i + target_horizon_sessions
+     if proposed_target > target_idx:
+         target_idx = proposed_target
+
+ if i >= target_idx:
+     exit at target-day D0 close
```

수정 코드 커밋 `4b29e6bd64b9f98a663e1dd88ed8811571c53827`의 파일 위치:

- 103~125행: `rolling_target_backtest()` 계약과 파라미터
- 141행: 진입 점수 판정 `active = score >= threshold`
- 182~199행: 진입 및 최초 목표일 `D+2` 설정
- 203~208행: `early_take_profit=ON`일 때 D0 high가 진입가+3%에 도달하면 즉시 익절
- 210~216행: 보유일 점수가 유효하면 목표일을 `현재일+2`로 연장
- 218~224행: 연장되지 않은 목표일 도달 시 청산
- 226~233행: 평가 구간 말 강제 mark-to-market

`score < threshold` 또는 `not active`를 직접 청산 조건으로 사용하는 코드는 0건이다. 점수 미달은 오직 목표일 연장을 중단한다.

## 체결 가격

- 진입: 유효 점수가 나온 날 D0 시가
- 목표일 청산: 목표일 D0 종가
- early take profit ON: D0 고가가 진입가+3% 이상이면 정확한 +3% 목표가격
- 구간말 강제평가: 마지막 D0 종가

목표일 매도 가격이 지시서에 시가/종가로 명시되지 않아, 2거래일 동안 장중 +3% 도달 가능성을 끝까지 관찰하고 고정 2일 기준선과 일관되게 비교하기 위해 목표일 종가를 사용했다. 이 선택은 **[추정]**이다.

## 예시 1 코드 트레이스

입력:

```text
2/2 active=True  → 진입, 최초 목표 2/4
2/3 active=True  → 목표를 2/5로 연장
2/4 active=False → 즉시 청산하지 않음, 목표 2/5 유지
2/5 active=False → 목표일 도달, 2/5 종가 청산
```

실제 테스트 결과:

```text
entry_date=2026-02-02
exit_date=2026-02-05
holding_sessions=3
target_extension_count=1
exit_reason=TARGET_DATE_REACHED
```

## 예시 2 코드 트레이스

입력:

```text
2/2 active=True  → 진입, 최초 목표 2/4
2/3 active=False → 즉시 청산하지 않음, 목표 2/4 유지
2/4 active=False → 목표일 도달, 2/4 종가 청산
```

실제 테스트 결과:

```text
entry_date=2026-02-02
exit_date=2026-02-04
holding_sessions=2
target_extension_count=0
exit_reason=TARGET_DATE_REACHED
```

## 잔재 검사

다음 패턴을 수정된 청산 파일에서 검색했다.

```text
elif not active
score < threshold
not active[i] and entry_idx
same-threshold daily rolling entry/maintain/exit
```

검색 결과: **0건**.

TP OFF의 정상 목표일 청산은 최소 2세션이므로, 0~1세션 정상 청산은 구조적으로 발생하지 않는다. 단, 평가 구간 마지막 1~2일에 진입한 포지션은 구간말 강제평가로 0~1세션에 닫힐 수 있다.
