# 위험구조 3단계 — 임계 도달가능성 진단

- 기준 원본: 17,071개
- 방식: 저장 룰 임계와 각 종목 학습기간 일봉 지표 분포만 대조
- 운영/원본/라이브 변경: 없음

## 1. NEVER/RARELY 거래량 임계 원인

- 엄격 NEVER_FIRED: 84개
  - THRESHOLD_TOO_HIGH: 84개
  - NATURALLY_QUIET: 0개
- 완화 추가 RARELY_ACTIVE: 717개
  - THRESHOLD_TOO_HIGH: 446개
  - NATURALLY_QUIET: 271개

## 2. BOIL 원형

- 후보: stage3:BOIL:9044dc2c67a3
- 임계: 2.5
- 학습기간 max / p99: 2.17972 / 1.91466
- 도달가능성: **UNREACHABLE**
- 원인: **THRESHOLD_TOO_HIGH**

## 3. 원본 전체 활성 지표의 UNREACHABLE

- ma: 0 / 15,393 (0.00%)
- macd: 0 / 15,578 (0.00%)
- rsi: 0 / 16,243 (0.00%)
- bb: 0 / 16,094 (0.00%)
- volume: 1,094 / 15,226 (7.19%)
- 하나 이상 죽은 활성 core 조건 보유: 1,094 / 17,071 (6.41%)

## 4. 룰 조건 형태

- MA와 MACD는 임계 밴드가 아니라 불리언/교차 이벤트다.
- RSI는 유일한 양방향 밴드 조건이다.
- BB와 거래량은 단방향 임계이며, 최종 진입도 `final_score >= signal_threshold` 단방향이다.
- 따라서 ‘임계만 넘으면 점수 부여/진입’ 구조는 거래량·BB·최종 점수의 시스템 기본형이며, 모든 기술지표가 단방향인 것은 아니다.

## 5. 판정 기준과 한계

- 거래량 UNREACHABLE은 임계가 학습기간 관측 max보다 큰 경우, NEAR_UNREACHABLE은 p99보다 큰 경우다.
- MA/MACD/RSI/BB 전수 집계의 UNREACHABLE은 해당 학습기간에 조건 충족 관측이 0회인 활성 가중치 조건이다.
- 관측 학습기간 밖 미래 도달 가능성을 물리 법칙처럼 부정하는 뜻은 아니며, 저장 룰이 학습 데이터에서 검증되지 않았다는 진단이다.
- Stage3의 3개 exit 변형은 동일 entry 임계·활동도를 공유하므로 후보 수와 고유 entry rule 수를 함께 해석해야 한다.
