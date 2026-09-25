# CE형 판정값 원본 전체 coverage 분석

- 기준일: 2026-07-11 KST
- 대상 원본: Stage2 survivors 1,162개 + Stage3 final_rulebooks 15,909개
- 합계: **17,071개**
- 범위: 저장된 원본·기존 live93 snapshot에 대한 산술 계산만 수행
- 신규 시뮬레이션·동적 재평가·GA 재학습·주문·원본/라이브 파일 변경: **0건**

## 1. 기존 CE 계산식 확인

기존 all-block dry-run이 사용한 의미는 다음과 같다.

```text
ratio = final_score / signal_threshold
CE FAIL = should_buy=True
          AND ratio < 1.25
          AND realized core Top2 share >= 90%
```

`final_score`는 단순한 룰북 가중치 합이 아니다.

```text
core component = 저장 weight × 현재 지표 활성 여부(0/1)
raw_score = MA + MACD + RSI + BB + volume
            + news + topic news + events + crash bonus
final_score = raw_score × market_adjustment
```

`market_adjustment`는 현재 market score, sector score, VIX와 룰북 보정 유전자로 결정된다.

Top2 집중도는 설정 weight의 Top2가 아니라 **현재 활성화된** core component만 사용한다.

```text
positive_core = max(active MA/MACD/RSI/BB/volume component, 0)
Top2 share = 상위 2개 positive_core 합 / positive_core 전체 합 × 100
```

따라서 ratio와 Top2는 동일 룰북이라도 평가 날짜·가격·시장·뉴스·이벤트 상태에 따라 바뀐다.

## 2. 왜 기존에는 34개만 판정됐나

원인은 세 가지다.

### 2.1 원본에는 동적 결과가 없다

17,071개 원본 모두 다음 정적 정보는 보유한다.

- `signal_threshold`: 17,071/17,071
- MA/MACD/RSI/BB/volume weight: 17,071/17,071

그러나 다음 값은 원본에 한 건도 없다.

- 현재 `final_score`: 0/17,071
- 현재 활성 core components: 0/17,071
- 현재 market/sector/VIX context: 0/17,071
- 현재 news/topic/event context: 0/17,071

즉 (a) 구성 점수가 원본에 남아 있지 않고, (b) 현재 동적 입력이 필수다.

### 2.2 파이프라인은 live93만 동적 평가했다

`live93_three_symptom_scan.csv`에는 93개 후보의 read-only 동적 평가 snapshot이 있다.

- ratio 저장: 93개
- realized Top2 계산 가능: 86개
- positive core component가 0이라 Top2가 정의되지 않음: 7개
- `should_buy=True`: 34개
- `should_buy=False`: 59개

기존 `integrated_gate_sim_core.py`는 `eval_ok AND should_buy`인 경우에만 CE를 PASS/FAIL로 분류했다. 그래서 34개만 판정되고 59개도 UNJUDGED로 들어갔다.

### 2.3 과거 거래 로그는 현재 CE값을 대체하지 못한다

- Stage2 유효 trade 파일 1,994개는 과거 진입 시점의 score/components를 저장한다.
- Stage3 `exit_trades` 269개 파일에는 entry score/components가 없다.

Stage2 과거 값은 날짜별로 여러 개이며 현재 시점 CE값이 아니다. 이를 최신 값으로 간주하면 정의가 바뀐다. Stage3는 해당 저장값 자체가 없다. 따라서 과거 거래 로그로 원본 전체의 현재 CE값을 메우지 않았다.

## 3. 정적 원본만으로 전수 복원이 가능한가

판정: **불가능**.

원본 weight만으로 다음 진단값은 17,071개 모두 계산했다.

- 모든 core weight가 동시에 활성화된다고 가정한 weight 합
- 설정 weight Top2 비중
- core weight 합 / signal threshold

하지만 이 값들은 CE ratio·realized Top2가 아니다. 결과 CSV에 다음 플래그를 명시했다.

```text
static_weight_metric_is_ce_equivalent = False
static_ratio_metric_is_ce_equivalent = False
```

기존 93개와 비교한 결과도 대체 불가능성을 확인했다.

| 비교 | 정확 일치 | 평균 절대 차이 | 최소 절대 차이 |
|---|---:|---:|---:|
| 설정 weight Top2 vs realized Top2 | 0/86 | **36.0230%p** | 2.7315%p |
| core weight 합/threshold vs realized ratio | 0/93 | **2.0087** | 0.0974 |

억지 정적 대체값을 CE 판정에 사용하지 않았다.

## 4. 기존 snapshot parity check

기존 live93 저장값을 같은 공식으로 다시 계산했다.

| 항목 | 결과 |
|---|---:|
| 대상 | 93개 |
| should_buy 일치 | 93/93 |
| threshold 최대 차이 | 4.96e-7 |
| ratio 최대 차이 | 2.48e-6 |
| Top2 유효 비교 | 86개 |
| Top2 최대 차이 | 8.41e-6%p |
| zero-core 양쪽 미정의 | 7개 |
| 활성 component와 원본 weight 최대 차이 | 4.80e-7 |

차이는 CSV 반올림 정밀도 범위다. 기존 ratio·Top2 정의와 재계산 공식은 일치한다.

상세 결과: `ce_origin_live93_parity.csv`

## 5. coverage 재분류 결과

기존 all-block CE 상태:

| 상태 | 수 |
|---|---:|
| PASS | 27 |
| FAIL | 7 |
| UNJUDGED | 17,037 |

이번 분석에서는 동적 snapshot이 있으나 `should_buy=False`인 59개를 CE 조건 비적용으로 분리했다.

| 상태 | Stage2 | Stage3 | 합계 |
|---|---:|---:|---:|
| PASS | 2 | 25 | **27** |
| FAIL | 0 | 7 | **7** |
| `NOT_APPLICABLE_NO_BUY` | 11 | 48 | **59** |
| `UNJUDGED_DYNAMIC_INPUT_REQUIRED` | 1,149 | 15,829 | **16,978** |

UNJUDGED는 **17,037 → 16,978**, 59개 감소했다.

중요: 이 감소는 원본 정적 복원이 아니라 기존 93개 snapshot의 `should_buy=False`를 올바르게 비적용으로 분리한 결과다.

## 6. CE형 FAIL 재판정

기존 all-block 의미를 유지하면 FAIL은 **7개로 동일**하다.

```text
stage3:ANET:fe220620802b
stage3:BB:f1bdfe7f8ad9
stage3:BOIL:9044dc2c67a3
stage3:BTE:4ba9af200f79
stage3:CDE:ceb9fe0512dc
stage3:CE:998b0b638c66
stage3:CWK:2970595abcd4
```

상세 ratio·Top2·활성 component·원본 weight는 `ce_origin_fail_rejudged.csv`에 기록했다.

참고로 `should_buy` 조건을 제거하고 기존 live93 93개 전체에 ratio+Top2만 적용하면 59개가 hit한다. 이는 기존 all-block 정의를 바꾸는 별도 정책이므로 이번 재판정에는 사용하지 않았다.

## 7. 잔존 UNJUDGED

잔존: **16,978개**

공통 사유:

```text
CURRENT_OHLCV_ACTIVATION_AND_MARKET_NEWS_EVENT_CONTEXT_NOT_STORED_IN_ORIGIN
```

이 개체들을 exact 판정하려면 평가 기준 시점의 다음 데이터가 필요하다.

- 지표가 계산된 OHLCV 마지막 봉
- market score
- sector score
- VIX
- news sentiment
- topic features
- event flags

이는 원본 파일 산술만으로는 만들 수 없고 `evaluate_signal`에 해당 시점 컨텍스트를 주는 동적 평가가 필요하다. 이번 지시의 “새 시뮬레이션 금지” 원칙에 따라 실행하지 않았다.

전체 목록: `ce_origin_residual_unjudged.csv`

## 8. 전수 계산표

`ce_origin_full_calculation.csv`에는 17,071개 전체를 기록했다.

주요 필드:

- 원본 ID·경로·row index
- `signal_threshold`
- 5개 core 설정 weight
- 설정 weight Top2와 정적 core/threshold 진단값
- exact ratio·realized Top2 계산 가능 여부
- 기존 dynamic snapshot 값
- active core component 5개
- `PASS/FAIL/NOT_APPLICABLE/UNJUDGED`
- 잔존 UNJUDGED 사유
- 필요한 동적 입력 목록

exact 값이 없는 행은 빈 값으로 남겼다. 정적 proxy를 exact 값처럼 채우지 않았다.

## 9. 산출물

- `ce_origin_full_calculation.csv` — 원본 17,071개 전체
- `ce_origin_residual_unjudged.csv` — 잔존 16,978개
- `ce_origin_fail_rejudged.csv` — CE FAIL 7개
- `ce_origin_live93_parity.csv` — 93개 공식 parity
- `ce_origin_unjudged_cause_summary.csv`
- `ce_origin_coverage_summary.json`
- `ce_origin_coverage_readout.md`
- `run_ce_origin_coverage_analysis.py`
- `finalize_ce_origin_coverage.py`
- `finalize_ce_origin_parity.py`

## 10. 최종 결론

원본 자체만으로 CE ratio와 realized Top2를 17,071개 전부 계산할 수 있다는 가정은 성립하지 않았다. 원본에는 임계와 가중치는 있으나 지표 활성 상태와 동적 컨텍스트가 없다.

정확하게 복원 가능한 것은 기존 live93 동적 snapshot 93개뿐이다. 이 중 기존 CE 의미로 PASS 27, FAIL 7, should_buy 비적용 59개다. 잔존 16,978개는 억지 계산 없이 UNJUDGED로 유지했다.
