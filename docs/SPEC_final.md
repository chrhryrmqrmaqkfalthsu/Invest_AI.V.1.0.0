# Kingmaker 최종 통합 학습·검증·운영 사양서

작성일: 2026-06-03  
목적: 이 문서는 이후 코드 정리, 재학습, 앙상블, CapitalAllocator, ExitPolicy 구현의 단일 기준이다.

---

## 1. 설계 철학: 불변 원칙

1. **미래 데이터 금지**: 학습·검증·신호 생성에 미래 데이터를 절대 섞지 않는다. 테스트 연도는 학습에서 배제하고, 뉴스·이벤트는 최소 1일 lag를 둔다.
2. **검증=라이브**: 백테스트에서 검증한 룰, 피처, 체결, 청산, 자본 배분은 라이브와 동일해야 한다.
3. **산출물 추적 가능성**: 모든 산출물은 `run_id`, 데이터 구간, GA 설정, hash, feature lag를 저장해야 한다.
4. **역할 분리**: 종목 점수는 rolling OOS 검증이 담당하고, 종목 내 줄세우기는 6년 통학습 개체 점수가 담당한다.

---

## 2. 전체 파이프라인

```text
거름망(빠른 diagnostic + ADV 필터)
→ 종목 점수(rolling true-WF/OOS)
→ 통과 종목만 6년 통학습으로 실전 개체 생성
→ 실전 개체 BUY/HOLD 신호 수집
→ 중앙 시스템 2단계 자본 배분
→ 공통 ExitPolicy 청산
```

| 단계 | 역할 | 운영 확정 여부 |
|---|---|---|
| diagnostic | 명백한 탈락 종목 제거 | 확정 아님 |
| ADV 필터 | 비유동 종목 제거 | 하드 필터 |
| rolling true-WF | 종목 점수 산정 | 종목 검증 기준 |
| 6년 통학습 | 실전 개체 생성 | 종목 내 상대 줄세우기 |
| CapitalAllocator | 자본 배분 | 라이브 기준 |
| ExitPolicy | 청산 | 백테스트=라이브 공통 기준 |

---

## 3. 데이터 경계

### 3.1 등급 평가용 rolling 검증

```text
2023 평가: train 2020-2022 → test 2023
2024 평가: train 2020-2023 → test 2024
2025 평가: train 2020-2024 → test 2025
```

원칙:

- 테스트 연도는 GA 학습에 절대 포함하지 않는다.
- 각 테스트 연도는 독립 OOS로 본다.
- 연도별 GA는 새로 돌기 때문에 `rank 1`을 같은 개체로 추적하지 않는다.

### 3.2 실전 개체 생성용 통학습

```text
train = 2020-2025 전체 6년
purpose = 운영 후보 개체 생성 및 종목 내 상대 줄세우기
```

주의: 6년 통학습 성과는 OOS가 아니다. 종목 간 비교에는 쓰지 않고, 같은 종목 안의 개체 상대 강도 비교에만 쓴다.

### 3.3 뉴스·이벤트 lag

```text
D일 신호에는 D-1 거래일까지 확정된 뉴스·이벤트만 사용한다.
```

원본 AV 뉴스에는 `YYYYMMDDTHHMMSS` 시각 정보가 있으므로 향후 장중/장후 분리도 가능하다. 초기 구현은 안전하게 1일 lag를 적용한다.

---

## 4. 거름망: 1차 필터

### 4.1 빠른 diagnostic

빠른 diagnostic은 종목 확정 도구가 아니라 후보 선별기다. POS도 확정이 아니고, NEG도 강한 train 신호가 있으면 rescue 후보가 될 수 있다. 최종 종목 등급은 rolling true-WF로만 정한다.

### 4.2 ADV 필터

```text
최근 252거래일 평균 일일 거래대금 ADV < $25M → 제외
```

이 값은 config로 분리한다.

```yaml
liquidity:
  min_adv_usd: 25000000
  adv_lookback_days: 252
```

근거: 현재 diagnostic 완료 종목 표본에서 ADV $25M은 약 하위 10% 지점이다.

### 4.3 주문금액 대비 ADV 제한

고정 ADV 하한과 별도로, 실제 주문금액이 해당 종목 ADV의 일정 비율을 넘지 않도록 제한한다.

초기 기준:

```text
주문금액 <= ADV × 1%
```

config 예:

```yaml
liquidity:
  max_order_adv_ratio: 0.01
```

이 제한은 자본 규모가 커질 때 고정 ADV 하한만으로는 부족한 문제를 막는다.

---

## 5. 종목 점수: 0-100

종목 점수는 rolling OOS 결과로만 계산한다.

### 5.1 연도별 컷 기준

네 조건을 모두 만족해야 해당 연도 PASS다.

```text
oos_trades >= 5
oos_win_rate > 50%
oos_expectancy_pct > 1.0%
oos_profit_factor > 1.2
```

### 5.2 일관성 점수: 0-60

| PASS 수 | 점수 |
|---:|---:|
| 3/3 | 60 |
| 2/3 | 40 |
| 1/3 | 20 |
| 0/3 | 0, 제외 |

### 5.3 수익 품질 점수: 0-40

통과 해들의 평균 OOS expectancy와 profit factor를 점수화한다. 구체 scaling은 rolling 결과 전체 분포를 보고 확정한다.

```text
raw_stock_score = consistency_score + quality_score
stock_score = raw_stock_score × liquidity_weight
```

### 5.4 거래량 계수

| 최근 252일 평균 ADV | liquidity_weight |
|---:|---:|
| $100M 이상 | 1.00 |
| $25M 이상 ~ $100M 미만 | 0.90 |
| $25M 미만 | 제외 |

종목 점수 컷오프는 추후 rolling 분포를 보고 결정한다.

---

## 6. 개체 점수

통과 종목만 6년 전체 데이터로 통학습한다. 최소 자격을 통과한 개체는 개수 제한 없이 거래 후보가 된다.

초기 최소 자격 후보:

```text
trade_count 최소치 이상
expectancy_pct > 0
profit_factor > 1.0
```

구체 수치는 통학습 분포를 보고 조정한다.

중요: 통학습 성과는 OOS가 아니다. 종목 내 개체 상대 강도와 종목 내부 배분에만 사용한다.

### 6.1 개체 점수 정규화

개체 점수는 중앙 시스템 곱셈에 들어가기 전에 정규화한다.

허용 범위:

```text
0-1 또는 0-100
```

정확한 정규화 방식은 11번 미정 항목으로 남긴다. 단, CapitalAllocator에 들어가는 `member_score`는 비교 가능한 동일 스케일이어야 한다.

---

## 7. 중앙 시스템 자본 배분: 2단계

### 7.1 신호 수집

BUY 신호 개체만 자본 배분 대상이다. 각 개체는 다음 값을 가진다.

```text
stock_score
member_score
signal_strength
```

초기 신호 세기 정의:

```text
signal_strength = current_score / threshold
```

정확한 scale과 clamp는 `evaluate_signal()` 출력 분포를 보고 확정한다.

### 7.2 종목 매수 강도

1차 구현은 합 방식으로 시작한다.

```text
stock_buy_strength = Σ(member_score × signal_strength)
```

합 방식은 앙상블 동의도를 직접 반영한다. 다만 종목별 후보 개체 수가 다르면 자본이 과도하게 집중될 수 있으므로, 모의투자에서 다음 방식을 비교한다.

```text
합
평균
sqrt(N) 보정
```

비교 결과에 따라 보정 방식을 조정한다.

### 7.3 종목 배분 점수

```text
allocation_score = stock_score × stock_buy_strength
```

초기에는 단순 곱으로 사용한다. 제곱·로그·컷 강화는 모의투자 후 검토한다.

### 7.4 1단계: 종목 간 배분

```text
allocation_score 비율로 종목 간 자본 배분
종목당 최대 25%
종목당 최소 배분 $75 미만 제외
신호가 약하면 현금 보유
```

### 7.5 2단계: 종목 내부 개체 배분

```text
member_allocation_score = member_score × signal_strength
member_allocation = stock_allocation × member_allocation_score / Σ(member_allocation_score)
```

### 7.6 SafetyLayer 필수 통과

자동 추가매수를 포함한 모든 주문은 SafetyLayer를 반드시 통과해야 한다.

필수 게이트:

```text
종목당 25% 상한
주문당 max_notional
총 노출 한도
일일 주문 횟수 제한
ADV 대비 주문금액 제한
```

CapitalAllocator는 목표 금액을 계산할 뿐이고, 실제 주문 가능 여부의 최종 판단은 SafetyLayer가 담당한다.

---

## 8. 매도 / 청산: ExitPolicy

백테스트와 라이브는 동일한 ExitPolicy 모듈을 호출해야 한다.

Task Y에서 확인된 기존 불일치:

1. 백테스트와 라이브 청산 로직이 다른 파일에 중복 구현됨.
2. 동적 exit가 백테스트에는 반영되지만 라이브에는 일부 미반영.
3. short/inverse 방향 처리가 다름.
4. hybrid 우선순위가 다름.
5. 체결 가격 모델이 다름.
6. max_holding_days가 백테스트는 거래일, 라이브는 calendar day.
7. 추가매수가 백테스트는 자동, 라이브는 승인 기반.

### 8.1 1차 청산 규칙

ATR 기반 청산으로 통일한다.

```text
stop_loss
take_profit
trailing_stop
max_holding_days
```

동적 exit도 라이브에 반영한다.

```text
bear: stop_loss_atr_bear
bull: take_profit_atr_bull
volatile: trailing_atr_volatile
```

### 8.2 기준 통일

```text
max_holding_days = 거래일 기준
진입 = 신호 당일 종가가 아니라 다음날 시가 또는 다음 거래 가능 가격
청산 = 슬리피지 반영
추가매수 = 자동으로 통일
초기 운영 = long-only
short/inverse = 추후
```

### 8.3 long-only 강제

초기 운영은 long-only다. 문서상 원칙에 그치지 않고, universe 필터 또는 rulebook loading 단계에서 코드로 강제한다.

금지:

```text
direction=short
inverse ETF 전용 direction
short/inverse rulebook 자동 로드
```

short/inverse 지원은 별도 검증이 끝난 뒤 별도 단계로만 추가한다.

### 8.4 base/stress 체결 모델 저장

체결 모델은 보수적으로 만들되, 과도한 과소평가를 피하기 위해 두 가지 결과를 모두 저장한다.

```text
base case: next open + 기본 슬리피지
stress case: next open + 높은 슬리피지
```

종목 검증과 개체 검증 결과에는 base/stress 성과를 함께 저장해 민감도를 확인한다.

### 8.5 2차 개선

매수 패턴 유효성 기반 청산은 추후 학습·rolling 검증 후 ATR과 성과 비교하여 채택 여부를 결정한다.

---

## 9. 표준 메타데이터

모든 산출물에 아래 필드를 저장한다.

대상:

```text
parameters.json
ga_population_dump_*.json
backtest.json
true_wf_grade_*.json
bulk diagnostic results
ensemble member files
capital allocation logs
```

필수 필드:

```json
{
  "run_id": "uuid-or-timestamp",
  "created_at": "ISO-8601",
  "source": "diagnostic|true_wf|learn_full|ensemble|live",
  "ticker": "NVDA",
  "fitness_mode": "swing",
  "data_start": "YYYY-MM-DD",
  "data_end": "YYYY-MM-DD",
  "train_period": ["YYYY-MM-DD", "YYYY-MM-DD"],
  "test_period": ["YYYY-MM-DD", "YYYY-MM-DD"],
  "oos_periods": [["YYYY-MM-DD", "YYYY-MM-DD"]],
  "ga": {"population": 40, "generations": 50, "seed": 42},
  "rulebook_hash": "sha256...",
  "member_hash": "sha256...",
  "validation": {"validated": true, "method": "rolling_true_wf"},
  "feature_lag": {"ticker_sentiment_days": 1, "market_events_days": 1}
}
```

metadata 없는 산출물은 새 표준에서 운영 입력으로 쓰지 않는다.

---

## 10. 코드 정리 방향

### 10.1 학습 경로 재정의

| 경로 | 최종 역할 |
|---|---|
| diagnostic | 빠른 후보 선별기 |
| true-WF | 통합 검증 엔진 |
| learn_full | 실전 개체 생성기 |
| 기존 parameters/dump | 백업 유지 후 새 표준으로 교체 |

### 10.2 구현 우선순위

```text
1. backup/ .gitignore 추가
2. Metadata schema / hash utility
3. Feature lag 적용
4. ExitPolicy 공통화
5. True-WF 통합 검증 엔진 정리
6. Full training 실전 개체 생성기
7. CapitalAllocator
8. Live ensemble runner
```

---

## 11. 추후 결정 / 미정 항목

- 재학습 주기.
- 종목 점수 컷오프.
- 개체 최소 자격 구체 수치.
- 신호 세기의 정확한 정의와 clamp.
- 보유 종목 리밸런싱 처리.
- 동적 GA 컷 기준.
- 개체 점수 정규화 방식.
- 전체 6174 스크리닝 완료 후 최종 재검증 범위.

---

## 12. 검토 의견

### 12.1 핵심 위험은 기능 부족이 아니라 데이터 경계 불명확성이다

기존 산출물은 어떤 개체가 어떤 기간을 봤는지 추적할 수 없다. 따라서 새 기능보다 metadata 표준화가 우선이다.

### 12.2 6년 통학습 성과는 OOS가 아니다

통학습 성과를 종목 간 비교나 검증 점수로 쓰면 look-ahead 오염이다. 종목 검증은 rolling OOS만 사용해야 한다.

### 12.3 개체 점수 스케일이 필요하다

CapitalAllocator에서 `stock_score × member_score × signal_strength`를 쓰려면 member_score도 0-100 또는 0-1로 정규화해야 한다.

### 12.4 BUY 개체 수가 많은 종목에 자본이 집중될 수 있다

`합` 방식은 앙상블 동의도를 반영하지만 종목별 후보 개체 수가 다르면 불공정할 수 있다. 모의투자에서 `합`, `평균`, `sqrt(N)` 보정을 비교해야 한다.

### 12.5 자동 추가매수는 안전장치와 강하게 묶어야 한다

자동 추가매수는 SafetyLayer의 종목당 25%, 주문당 max_notional, 총 노출, 일일 주문 횟수 제한을 반드시 통과해야 한다.

### 12.6 ExitPolicy 체결 모델은 base/stress 두 가지로 저장하는 것이 좋다

보수적 체결은 안전하지만 지나치면 과소평가가 될 수 있다. `base case`와 `stress case`를 모두 저장해 민감도를 봐야 한다.

### 12.7 ADV 기준은 자본 규모에 따라 달라져야 한다

초기 하한은 $25M이지만, 주문금액이 ADV의 1%를 넘지 않도록 `max_order_adv_ratio`도 추가하는 것이 좋다.

### 12.8 long-only를 코드로 강제해야 한다

rulebook에는 short/inverse 흔적이 있으므로 초기 운영에서는 universe 또는 rulebook loading 단계에서 long-only를 강제해야 한다.

### 12.9 현재 6174 스크리닝 결과는 참고용이다

현재 결과는 기존 feature lag와 기존 metadata 기준이다. 최종 표준에서 뉴스 1일 lag, ExitPolicy, metadata가 바뀌면 재실행해야 한다.

---

## 13. 구현 작업 목록 (코드가 사양에 도달하기 위해 필요한 작업)

우선순위는 추후 결정한다.

| 번호 | 작업 | 현재 어긋남 | 우선순위 |
|---:|---|---|---|
| 1 | 뉴스·이벤트 1일 lag 적용 | 현재 백테스트/진단은 당일 sentiment를 읽을 수 있음 | 추후 결정 |
| 2 | 백테스트와 라이브 ExitPolicy 통합 | 백테스트는 `exit_simulator.py`, 라이브는 `position_manager.py`로 분리됨 | 추후 결정 |
| 3 | 표준 메타데이터 도입 | `parameters.json`, `ga_population_dump`에 경계 메타와 hash 부족 | 추후 결정 |
| 4 | 통학습 실전 개체 생성기 구현 | 운영 learn, true-WF, diagnostic 경로가 분리되어 있음 | 추후 결정 |
| 5 | 라이브 앙상블 연결 | 현재 라이브는 단일 `parameters.json` rulebook 중심 | 추후 결정 |
| 6 | CapitalAllocator 구현 | 2단계 자본 배분과 리밸런싱 플랜 생성기 없음 | 추후 결정 |
| 7 | long-only 강제 | rulebook에 `direction=short` 및 inverse 흔적이 남아 있음 | 추후 결정 |
| 8 | 6174 스크리닝 결과 재해석/재실행 기준 결정 | 현재 결과는 최종 lag/metadata/ExitPolicy 표준 전 결과 | 추후 결정 |

---

## 부록 A. 현재 확인된 수치

### A.1 ADV 분포

완료 diagnostic 표본 기준 최근 252일 평균 ADV:

| 백분위 | ADV |
|---:|---:|
| 10% | 약 $25M |
| 25% | 약 $69.5M |
| 50% | 약 $167M |
| 75% | 약 $365M |
| 90% | 약 $816M |

### A.2 diagnostic 시간

```text
평균 약 8.55분/종목
중앙값 약 8.84분/종목
8병렬 6174종목 약 110~114시간
```

### A.3 산출물 어긋남

```text
parameters.json: 11종목
ga_population_dump: 8종목
true-WF swing 결과: 43종목
세 집합 공통: 3종목(AAPL, MSFT, NVDA)
```

기존 산출물을 같은 기준으로 보면 안 된다.
