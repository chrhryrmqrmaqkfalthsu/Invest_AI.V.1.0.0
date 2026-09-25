# 라이브 후보 18개 Event 기여 vs 최초신호 후 미실현 변동

## 분석 대상

소스:

```text
data/_system/live_slots_state.json
section=candidate_pool
```

현재 후보 수:

```text
18개
```

가격 기준:

- 최초 가격: 각 row의 `first_signal_price`
- 현재 가격: 각 row의 `price`
- 현재 가격 timestamp: `2026-07-10T19:59:53.001495+00:00`

현재 가격은 live-slot 후보 평가 시 저장된 스냅샷이다. 실시간 체결가 또는 현재 브로커 quote와 차이가 날 수 있다.

모든 변동률은:

```text
(price / first_signal_price - 1) × 100
```

으로 계산한 미실현 스냅샷이다.

## Event 기여와 OFF 예상 score 계산

`reasons` 문자열의:

```text
이벤트반응(+x.xx)
```

을 direct Event raw contribution으로 사용했다.

저장 상태에는 별도 `technical_component` 필드가 없으므로 다음 값을 비-Event raw 기여로 기록했다.

```text
non_event_raw_contribution = raw_score - event_contribution
```

현재 candidate rows에서 News·NewsTopics 별도 contribution은 reason에 표시되지 않았다. 따라서 이 값은 저장 상태 기준 기술신호 중심의 비-Event raw contribution이지만, 엄밀히는 모든 비-Event raw component의 합이다.

Event OFF 예상 score:

```text
market_multiplier = final_score / raw_score
off_estimated_score = (raw_score - event_contribution) × market_multiplier
```

즉 market score와 market adjustment는 그대로 두고 direct Event만 제거한 관찰형 계산이다.

## 그룹 분류 결과

| 그룹 | 표본 수 | 티커 |
|---|---:|---|
| EVENT_DEPENDENT | 7 | BTBT, BMA, BMI, BNTX, CMC, BWXT, ACMR |
| EVENT_AMPLIFIED | 3 | CRS, BCS, ADMA |
| EVENT_NONE_OR_NEG | 8 | ALGT, BN, BB, ANET, ARKW, CBRL, CRK, AEIS |

분류 기준:

```text
EVENT_DEPENDENT
= Event > 0 AND Event 제거 예상 score < threshold

EVENT_AMPLIFIED
= Event > 0 AND Event 제거 예상 score >= threshold

EVENT_NONE_OR_NEG
= Event <= 0
```

## Event OFF 시 탈락 예상 후보

표본 수:

```text
7개
```

| 티커 | Event 기여 | 비-Event raw | 현재 final | threshold | OFF 예상 final | 최초→현재 변동률 |
|---|---:|---:|---:|---:|---:|---:|
| BTBT | +11.81 | 1.6464 | 14.8621 | 1.9113 | 1.8184 | +3.3537% |
| BMA | +10.24 | 1.9650 | 12.2050 | 2.8484 | 1.9650 | +3.4370% |
| BMI | +5.17 | 1.2955 | 9.6653 | 2.4293 | 1.9366 | -1.8425% |
| BNTX | +5.04 | 1.1418 | 6.1818 | 1.9607 | 1.1418 | -2.6862% |
| CMC | +3.37 | 1.0535 | 4.4235 | 2.3905 | 1.0535 | +1.7457% |
| BWXT | +2.78 | 1.0101 | 3.7901 | 2.0158 | 1.0101 | -0.1584% |
| ACMR | +1.40 | 1.4101 | 3.1097 | 1.6722 | 1.5604 | -3.7926% |

이 표는 실제 OFF shadow 재평가 결과가 아니라 저장된 component와 multiplier로 계산한 예상치다.

## Event가 양수지만 OFF 후에도 통과 예상

표본 수:

```text
3개
```

| 티커 | Event 기여 | 비-Event raw | 현재 final | threshold | OFF 예상 final | 최초→현재 변동률 |
|---|---:|---:|---:|---:|---:|---:|
| CRS | +4.38 | 4.6601 | 9.0401 | 2.5575 | 4.6601 | -3.6714% |
| BCS | +1.85 | 4.9303 | 6.7803 | 3.3017 | 4.9303 | -0.6959% |
| ADMA | +0.58 | 2.0000 | 3.5483 | 2.1793 | 2.7506 | -1.5727% |

## Event가 0 또는 음수인 후보

표본 수:

```text
8개
```

| 티커 | Event 기여 | 최초→현재 변동률 |
|---|---:|---:|
| ALGT | 0.00 | +1.0273% |
| BN | -0.37 | +0.5632% |
| BB | 0.00 | -0.6793% |
| ANET | 0.00 | +13.6596% |
| ARKW | 0.00 | +1.4940% |
| CBRL | 0.00 | +2.8433% |
| CRK | -0.69 | -0.6391% |
| AEIS | 0.00 | +6.1251% |

음수 Event 후보 BN·CRK는 Event OFF 시 score가 오르는 방향이다.

## 그룹별 최초신호→현재 변동 대조

### EVENT_DEPENDENT

```text
표본 7
BTBT +3.3537%
BMA +3.4370%
BMI -1.8425%
BNTX -2.6862%
CMC +1.7457%
BWXT -0.1584%
ACMR -3.7926%
참고 평균 +0.0081%
```

양수·음수가 혼재한다.

### EVENT_AMPLIFIED

```text
표본 3
CRS -3.6714%
BCS -0.6959%
ADMA -1.5727%
참고 평균 -1.9800%
```

현재 timestamp에서는 3개 모두 음수지만 표본이 3개뿐이다.

### EVENT_NONE_OR_NEG

```text
표본 8
ALGT +1.0273%
BN +0.5632%
BB -0.6793%
ANET +13.6596%
ARKW +1.4940%
CBRL +2.8433%
CRK -0.6391%
AEIS +6.1251%
참고 평균 +3.0492%
```

ANET와 AEIS의 큰 양수 변동이 그룹 참고 평균에 크게 영향을 준다.

## 관찰 한계

이 결과로 그룹 우열이나 Event OFF의 수익성 효과를 단정할 수 없다.

이유:

1. 전체 표본 18개
2. 그룹별 표본 7·3·8개
3. 최초신호 날짜가 개체마다 다름
4. 보유 시간과 시장 노출 시간이 동일하지 않음
5. 현재 가격이 동일 timestamp의 live-slot 저장값이지 최신 실시간 quote가 아님
6. 수수료·슬리피지·실제 주문 체결 여부를 반영하지 않은 미실현 변화
7. 후보 pool에 남은 생존 개체만 본 것이므로 탈락 후보를 포함하지 않는 생존편향 가능성
8. Event OFF 예상 score는 저장 component 기반 계산이며 모든 개체의 실제 OFF shadow 결과는 아님

따라서 본 산출물은 현재 후보 pool에서 Event 의존도와 저장 가격 변동을 나란히 놓은 관찰 자료다.

## 핵심 관찰 사실

- 현재 후보 18개 중 Event OFF 시 탈락 예상: 7개
- Event가 양수지만 OFF 후에도 통과 예상: 3개
- Event가 0 또는 음수: 8개
- Event 의존 7개 중 현재 미실현 양수 3개, 음수 4개
- Event 증폭 3개는 현재 모두 음수
- Event 없음/음수 8개 중 양수 6개, 음수 2개

마지막 세 항목은 해당 timestamp의 개별 관찰 개수일 뿐 통계적 우열을 의미하지 않는다.

## 산출물

- `data/_system/analysis/live_pool_event_contribution_vs_return_20260711/candidate_full_table.csv`
- `data/_system/analysis/live_pool_event_contribution_vs_return_20260711/group_classification.csv`
- `data/_system/analysis/live_pool_event_contribution_vs_return_20260711/group_return_comparison.csv`
- `data/_system/analysis/live_pool_event_contribution_vs_return_20260711/readout.md`

운영 코드·설정 변경: 0건
