# CE Event 계수: BOIL형 정적 결함인가 vs 이벤트 활성 의존 동적 현상인가

## 최종 판정

**판정: `DYNAMIC_EVENT_DEPENDENT`**

CE의 Event +4.62는 룰북 계수 자체가 전체 라이브 룰북 분포에서 극단적으로 큰 정적 이상치여서 발생한 것으로 보이지 않는다. CE의 정적 Event 계수 프로파일은 오히려 중간 또는 하위권이다.

- event multiplier: 2.3387, live93 중 66.67백분위
- 평균 절대 Event 계수: 0.7950, 5.38백분위
- 최대 절대 Event 계수: 1.9312, 7.53백분위
- multiplier 반영 전체 절대 용량: 20.4522, 52.69백분위
- multiplier 반영 양수 용량: 10.2426, 56.99백분위
- 절대 경계값 2.0에 붙은 계수: 0개

따라서 CE는 BOIL형의 전형인 “정적 파라미터가 분포 밖 극단값 또는 경계값에 고정된 결함”으로 관찰되지 않는다.

반면 실제 +4.62는 진입 시 활성된 여러 Event 조합과, 앞선 조사에서 확인된 recency decay 미반영 구조에 의존한다. 즉 CE 사례의 주된 문제는 계수 크기보다 **동적 active-event set과 TTL 상태가 원래 크기의 정적 계수를 동시에 켠 것**이다.

## 1. 비교 모집단

`live_candidate_list_20260707.json`에 보존된 라이브 후보 93개 전량의 정확한 rulebook hash를 Stage2 survivor 또는 Stage3 final rulebook 원본에 연결했다.

- 연결 성공: 93/93
- Stage2·Stage3 혼합 라이브 선택 룰북
- 각 룰북의 11개 `event_response_*`, `event_strength_multiplier`, `use_event_block` 추출

분포 비교는 모든 GA 개체가 아니라 실제 라이브 후보로 선택된 93개 룰북을 기준으로 했다. 따라서 운영 선택 집합 안에서의 상대 위치다.

## 2. CE 정적 Event 계수 원값

| Event 계수 | 값 |
|---|---:|
| banking_crisis | -0.0193 |
| earnings_shock | +0.7951 |
| export_ban | -0.3083 |
| fed_statement | +0.1605 |
| geopolitical | -1.9312 |
| inflation | +1.2754 |
| oil_surge | +0.1730 |
| rate_cut | -0.8926 |
| rate_hike | +1.4332 |
| tariff | -1.2140 |
| war | +0.5423 |
| multiplier | 2.3387 |

CE에는 ±2.0 경계값 계수가 하나도 없다. 평균 절대 계수 크기도 live93 중 하위 5.38%에 해당한다.

CE의 multiplier는 중앙값 1.8395보다 높지만 상위 tail은 아니다. live93의 90백분위와 최대값은 3.0이며, CE는 2.3387이다.

## 3. Event 과반 9종목의 정적 위치

Event가 score 과반을 차지한 9종목은 정적 구조가 서로 같지 않았다.

### 정적 상위 tail이 분명한 종목

- BNTX: multiplier 100백분위, scaled L1 97.85백분위, 양수 용량 97.85백분위
- BTBT: multiplier 100백분위, 양수 용량 100백분위
- BMA: multiplier 100백분위, 양수 용량 93.55백분위
- BMI: multiplier 100백분위, 양수 용량 92.47백분위

이 네 종목은 정적 파라미터가 Event 과반 현상을 증폭할 가능성이 관찰된다. 다만 학습 Event 표본 수가 없으므로 BOIL형 과적합으로 확정할 수는 없다.

### 중간 또는 혼합형

- CMC: multiplier 40.86백분위, 양수 용량 76.34백분위
- BGC: multiplier 100백분위지만 평균 절대 계수는 9.68백분위
- ACMR: scaled L1 55.91백분위, 양수 용량 54.84백분위
- CE: scaled L1 52.69백분위, 양수 용량 56.99백분위
- BWXT: raw 계수는 크지만 multiplier 0.5로 8.60백분위, scaled L1 13.98백분위

따라서 Event 과반이라는 결과만으로 정적 계수 결함을 일반화할 수 없다. 일부는 정적 tail이고, 일부는 정상 범위 계수가 여러 활성 Event로 동시에 켜진 동적 현상이다.

## 4. CE +4.62의 정적·동적 분해

관찰값:

- Event contribution: 4.62260455
- multiplier: 2.33874362
- 활성 계수의 원시 합: 4.62260455 / 2.33874362 = 1.97653326

CE의 가장 큰 단일 양수 계수는 rate_hike +1.43315021이다. multiplier를 적용해도 단일 Event 최대 기여는 +3.35177092로, 관찰된 +4.62260455보다 작다.

따라서 **최소 2개 이상의 양수 Event가 동시에 활성됐거나, 양수 여러 개와 음수 Event가 함께 활성된 조합**이어야 한다.

수학적으로는 다음 조합이 관찰 합계를 거의 정확히 재현한다.

- fed_statement + inflation + rate_cut + rate_hike
- 원시 합: 1.97653325
- multiplier 적용: 4.62260454

그러나 당시 active-event payload가 없기 때문에 이 조합이 실제 조합이었다고 주장할 수는 없다. 이는 계수 합의 식별 가능성을 보여주는 수학적 예시일 뿐이다.

정적 성분은 룰북에 박힌 11개 계수와 multiplier다. 동적 성분은 어떤 Event key가 동시에 켜졌는지, 각 key가 TTL 안에 있었는지다. CE의 정적 계수는 중간 수준이지만, 동적 조합이 원시 합 +1.9765를 만들면서 Event가 score의 55.24%가 됐다.

## 5. 계수 학습의 데이터 뒷받침

정확한 `event_activation_sample_count`는 학습 산출물에 저장돼 있지 않았다.

확인 가능한 것은 룰북 전체 trade count와 일부 bull/stress trade count다.

| 종목 | rulebook trades | bull | stress |
|---|---:|---:|---:|
| CE | 10 | 10 | 23 |
| BMA | 10 | 10 | 12 |
| BNTX | 12 | 12 | 18 |
| BTBT | 20 | 20 | 24 |
| CMC | 17 | - | - |
| BWXT | 10 | 10 | 17 |
| BMI | 8 | 8 | 13 |
| BGC | 11 | 11 | 18 |
| ACMR | 13 | 13 | 14 |

이 숫자는 전체 거래 표본이지 Event가 발화한 거래 수가 아니다. 따라서 계수가 극소수 Event 표본으로 학습됐는지는 확정할 수 없다.

CE 재실행 195건에서는 Event가 전부 0이었다. 하지만 재실행이 과거 event context를 복원하지 못했으므로, 이를 학습 Event 샘플 0건의 증거로 사용할 수 없다.

> 계수별 학습 Event 표본 수: **확인 불가**

## 6. BOIL형 게이트 편입 가능성

### CE

CE는 정적 Event 계수 프로파일이 분포 밖 극단이 아니므로, BOIL형 정적 게이트에 CE 전체를 그대로 편입하는 근거는 부족하다.

- 정적 극단: 아니오
- 경계값 포화: 아니오
- 학습 Event 표본 극소 여부: 확인 불가
- 동적 active-event 조합 의존: 예
- recency decay 미반영 의존: 예

따라서 CE는 `DYNAMIC_EVENT_DEPENDENT`가 가장 적합하다.

### 9종목 전체

BNTX·BTBT·BMA·BMI는 정적 multiplier와 양수 Event 용량이 상위 tail이므로 별도 정적 게이트 후보로 검토할 수 있다. 그러나 Event 학습 표본 수가 확인되지 않아 `STATIC_BOIL_TYPE`으로 확정할 수는 없다.

이 종목들은 현재 기준으로 `MIXED_OR_INSUFFICIENT`에 가깝다.

정적 게이트를 검토한다면 종목명 고정 차단보다 다음처럼 파라미터 구조를 기준으로 해야 한다.

- multiplier 상한 포화
- 양수 Event capacity 상위 tail
- 다수 계수 경계값 포화
- Event 학습 활성 표본 최소수

이번 조사는 구현 판단만 수행했으며 실제 게이트나 설정은 변경하지 않았다.

## 최종 판정표

| 대상 | 판정 | 근거 |
|---|---|---|
| CE | `DYNAMIC_EVENT_DEPENDENT` | 정적 계수는 중간·하위권, 관찰 +4.62는 다중 활성 Event 필요, recency decay 미반영 |
| BMA | `INSUFFICIENT_STATIC_CANDIDATE` | 정적 양수 capacity 상위 tail이나 Event 학습 표본 수 미확인 |
| BNTX | `INSUFFICIENT_STATIC_CANDIDATE` | 정적 프로파일 매우 상위 tail이나 학습 표본 수 미확인 |
| BTBT | `INSUFFICIENT_STATIC_CANDIDATE` | 정적 양수 capacity 최대이나 학습 표본 수 미확인 |
| BMI | `INSUFFICIENT_STATIC_CANDIDATE` | multiplier·양수 capacity 상위 tail이나 학습 표본 수 미확인 |
| CMC | `MIXED` | 일부 정적 경계값과 중상위 capacity, multiplier는 중간 |
| BGC | `MIXED` | multiplier 최대지만 raw 계수 평균은 하위권 |
| BWXT | `DYNAMIC_EVENT_DEPENDENT` | raw 계수는 크지만 multiplier와 scaled capacity가 하위권 |
| ACMR | `DYNAMIC_EVENT_DEPENDENT` | scaled 정적 capacity가 중간권 |

## 한 줄 결론

> **CE의 Event +4.62는 BOIL형 정적 계수 폭주가 아니라, 정상 범위의 정적 계수 여러 개가 TTL 내에서 동시에 켜지고 recency decay 없이 합산된 동적 현상으로 판정된다. CE를 BOIL 정적 게이트에 바로 편입할 근거는 없으며, 우선순위는 Event 활성 payload 로깅과 entry-score recency decay 반영 경로다.**

## 확인 불가 항목

- CE 당시 실제 active event flag 조합
- 각 Event의 TTL age와 decay_weight
- 각 계수 학습에 사용된 Event 활성 거래 수
- Event 계수의 유효 표본별 안정성

## 산출물

- `event_coefficient_distribution_9_candidates.csv`
- `event_coefficient_training_support.csv`
- `ce_event_static_dynamic_decomposition.csv`
- `ce_event_static_vs_dynamic_readout.md`
