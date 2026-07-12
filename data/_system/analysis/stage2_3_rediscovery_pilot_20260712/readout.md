# Stage2/3 rolling 재발견 재실행 — 목표일 청산 + 원본 GA 크기

## 판정

# **PILOT_PASS**

요청한 두 핵심 결함은 수정됐다.

1. strict-AND 점수 붕괴 시 다음날 즉시 청산하던 경로가 제거됐고, rolling 목표일 방식으로 동작했다.
2. GA는 종목별 `train_1/train_2/train_3` 세 분할을 각각 `population=100`, `generations=50`, `patience=15`로 학습했다.

이 판정은 연구 파일럿의 구조·실행 게이트 통과를 뜻하며 라이브 반영 가능 판정은 아니다. survivor가 2종목뿐이고 HHI가 높아 추가 확대 검증이 필요하다.

## 실행 개요

- 실행 시각(UTC): 2026-07-12T15:43:10.182689+00:00
- 총 소요시간: 77.85초
- 종목: 50개
- 종목 목록: 직전 `_prev_run_gaShrunk_exitBug/symbol_list.csv`와 순서·source SHA까지 동일
- 일별 평가 행: 75,900
- 학습 후보: 150개, 즉 50종목 × 3 train split
- worker: 6개 spawn PID, 오류 0
- gene bounds: 1,800 / 1,800 양방향·최소폭 통과
- 실제 generation: 150개 학습 중 149개는 50세대, 1개는 patience로 41세대 종료

## 청산 로직 검증

- 진입일 목표일은 2거래일 뒤다.
- 보유 중 유효 점수가 나오면 목표일을 `그날 + 2거래일`로 연장한다.
- 점수가 끊겨도 즉시 청산하지 않고 마지막 유효 목표일까지 보유한다.
- 목표일에 새 유효 점수가 없으면 그날 종가에 청산한다.
- TP OFF는 +3%에 도달해도 목표일까지 보유한다.
- TP ON은 보유일 D0 고가가 진입가 대비 +3%에 도달하면 정확한 +3% 목표가격으로 즉시 익절한다.
- 평가 구간 말까지 목표일이 남은 포지션만 마지막 종가로 강제 mark-to-market한다.

수정된 `execution_mode_backtest.py`에서 다음 즉시 청산 패턴을 검색한 결과는 0건이다.

```text
elif not active
score < threshold
not active[i] and entry_idx
same-threshold daily rolling entry/maintain/exit
```

두 지시서 예시도 코드 테스트에서 각각 2/5와 2/4에 정확히 청산됐다. 상세 diff와 trace는 `exit_logic_modification.md`에 기록했다.

## 휩쏘 변화

| 항목 | 직전 파일럿 | 이번 목표일 TP OFF |
|---|---:|---:|
| OOS 종목별 평균 1세션 이하 비율 | 90.0100% | **0.1538%** |
| OOS 정상 1세션 청산 | 397건 수준의 즉시 점수청산 구조 | **0건** |
| OOS 1세션 이하 전체 거래 | 다수 | **1 / 659건** |
| OOS 1세션 이하 잔존 원인 | 점수 붕괴 즉시 청산 | **구간말 강제평가 1건** |

평균 휩쏘 감소폭은 89.8562%p다. `signal_whipsaw_rate`는 50종목 모두 0이었다. 따라서 하루 점수 붕괴로 다음날 튕기는 결함은 구조적으로 제거됐다.

## OOS 청산 방식 비교 — train-only champion 50종목 평균

비교용 champion은 stress/OOS를 보지 않고 각 종목의 세 split 후보 중 origin-train fitness가 가장 높은 후보로 선정했다.

| 방식 | 거래당 평균수익률 | 평균 복리수익률 | 평균 MDD | 평균 보유 | 최장 보유 |
|---|---:|---:|---:|---:|---:|
| rolling 목표일, TP OFF | **+0.5356%** | **+7.1997%** | -16.1335% | 2.274세션 | 6세션 |
| rolling 목표일, TP ON | +0.1057% | +2.0353% | **-13.2016%** | 1.486세션 | 6세션 |
| 고정 2세션 | +0.4130% | +5.7029% | -15.7224% | 1.960세션 | 2세션 |

TP OFF는 고정 2세션보다 거래당 평균수익률이 0.1226%p 높았고 평균 복리수익률도 높았다. 다만 평균 MDD는 약 0.41%p 더 나빴다. TP ON은 MDD를 줄였지만 같은 날 조기 익절이 많아 수익률이 크게 낮았다.

직전 고정 2일 결과 약 +0.62%와 이번 +0.4130%가 다른 이유는 청산 기준선 구현이 바뀐 것이 아니라, GA 확대·3-split 복원으로 종목별 비교 champion gene이 달라졌기 때문이다.

## Survivor 결과

150개 split 후보 중 다음 2개가 train·stress·OOS 게이트를 모두 통과했다.

| 종목 | origin split | Train 정밀도 / 표본 | Stress 정밀도 / 표본 | OOS 정밀도 / 표본 |
|---|---|---:|---:|---:|
| AAP | train_1 | 55.00% / 20 | 41.38% / 29 | 66.67% / 12 |
| POWI | train_2 | 60.00% / 20 | 48.39% / 31 | 53.85% / 13 |

- survivor 후보: 2 / 150
- survivor 종목: 2 / 50
- survivor 신호 HHI: **0.5008**
- train gate 통과 후보: 145
- stress gate 통과 후보: 6
- OOS gate 통과 후보: 18
- 얇은 표본 관련 탈락 표시: 12

HHI 0.5008은 survivor가 사실상 두 종목에 집중됐다는 뜻이다. 따라서 탐색 복원으로 survivor 0 문제는 해소됐지만, 종목 분산과 일반화 폭은 아직 좁다.

## 직전 survivor 0의 원인 판별

원본 크기와 세 train split을 함께 복원하자 survivor가 0개에서 2개로 늘었다. 따라서 직전 0개는 순수한 일반화 실패만으로 설명되지 않으며, **축소된 탐색 구조의 영향이 있었다**고 판정한다.

다만 이번 변경은 population·generation 확대와 3-split 복원을 동시에 적용했다. 어느 한 요소가 survivor 생성에 얼마나 기여했는지는 이번 한 번의 실행만으로 분리할 수 없다. “population 확대 단독 효과”라고 단정하는 것은 **[추정]**을 넘어선다.

## 과적합 점검

150개 후보 평균:

- Train 정밀도: 81.75%
- Stress 정밀도: 43.83%
- OOS 정밀도: 49.86%
- Train→Stress 평균 격차: 37.92%p
- Train→OOS 평균 격차: 31.89%p

전체 후보 평균의 train 대비 검증 정밀도 하락은 여전히 크다. survivor 2개는 이중 게이트를 통과했지만, 파일럿 전체는 강한 train 적합 경향을 보인다.

## 데이터·누수 확인

- 직전과 새 실행의 75,900개 행에서 ticker·date·regime·라벨·12개 feature가 전부 동일했다.
- 새로 추가한 `entry_high_d0`, `entry_low_d0`는 TP 체결 계산 전용이다.
- GA feature는 D-5~D-1의 12개만 사용했다.
- `STK_gap_d0`, `ETF_gap_d0`, flow, order_book는 사용하지 않았다.
- 거래수 게이트는 **[추정]** train `max(20, 2%)`, stress/OOS `max(8, 1.5%)`를 유지했다.

## 최종 무결성

- 재귀 CSV 133개 파싱 오류: 0
- 원본 Stage2/3 및 라이브 관련 코드 SHA-256: 실행 전후 동일
- `.env` SHA-256: 실행 전후 동일
- daemon PID 494330: 유지
- 라이브 후보 풀·정렬·설정 변경: 없음
- 사전 백업과 manifest 검증: OK
