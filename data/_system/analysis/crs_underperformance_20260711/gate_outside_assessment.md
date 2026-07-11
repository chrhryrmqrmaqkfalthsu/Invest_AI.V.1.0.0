# CRS 부진과 Event·v3·BOIL 관계

## 게이트 재확인

### Event OFF

저장된 2026-07-10 비교 자료:

```text
event contribution=+4.38
off estimated score=4.660072469611355
threshold=2.5574757832651467
pass_off_estimated=true
classification=EVENT_AMPLIFIED
```

CRS는 Event가 점수를 키웠지만 Event가 없어도 통과했다. 따라서 당시 부진을 direct Event 의존 후보 문제로 분류할 수 없다.

현재 direct Event가 꺼진 뒤의 최신 state에서도:

```text
score=5.533926668171177
threshold=2.5574757832651467
```

로 통과 중이다.

단, **최초 신호 시각의 exact Event component는 저장되지 않아 UNRECOVERABLE**이다. 위 Event 수치는 최초 신호가 아니라 이후 저장 snapshot이다.

### v3

권위 catalog:

```text
data/_system/analysis/candidate_selection_audit_20260710/
threshold_p99_weightless_block_candidate_decisions.csv
```

CRS:

```text
final_p99_weightless_block_status=PASS
p99_weightless_fail_components=""
bb_reachability_label=REACHABLE
volume_reachability_label=REACHABLE
```

### BOIL

권위 catalog:

```text
data/_system/analysis/candidate_selection_audit_20260710/
boil_block_exclusive_targets.csv
```

CRS candidate ID는 exclusive target에 없다.

보조 frozen 자료:

```text
vol_group=MID_VOL
weight_volume_surge=1.233207754645207
boil_check=PASS
```

따라서 BOIL 조건인 HIGH_VOL·near-zero volume weight에 해당하지 않는다.

## 오늘 게이트 축 밖인지

판정:

```text
OUTSIDE_EVENT_V3_BOIL=true
```

Event OFF·v3·BOIL 모두 CRS를 통과시킨다. 이 손실 사례는 오늘 다룬 세 축의 차단 대상이 아니다.

다만 이것만으로 시스템 결함이 확정되지는 않는다. 현재 증거로 구분하면 다음과 같다.

## 관찰 가능한 원인 후보

### 1. 진입 위치

`PARTIAL_SUPPORT`

- 신호 가격은 당일 최종 고점의 0.125% 아래였다.
- 이전 종가보다 2.36% 높았다.
- 20-session 첫 종가 대비 14.68% 상승한 상태였다.
- 20-session range 상단 80.13% 위치였다.
- 그러나 이전 5·10·20-session 고점보다는 모두 4.01% 낮았다.

따라서 “다일 신고가에서 추격”은 아니지만 “당일 고점 부근에서 진입”은 맞다.

### 2. BB근접 조건의 위치 민감도

`ENTRY_LOCATION_WEAKNESS_SUSPECTED`

코드상 long 후보의 `BB근접`은:

```text
Close <= BB_lower × bb_proximity
```

이다. 상단 돌파 신호가 아니다.

CRS 룰북:

```text
bb_proximity=1.1402337701369225
BB_lower=528.3553681607892
pass threshold=602.4486334100584
```

실제 evaluator의 마지막 완성 일봉 종가는 587.63으로 밴드 위치 56.07%였고 조건을 통과했다. 저장 신호 가격 600.86을 같은 밴드에 놓아도 68.59% 위치인데 threshold보다 0.264% 낮아 통과한다.

즉 명칭은 lower-band proximity지만, 이 룰북의 14.02% proximity는 밴드 상단 쪽 가격까지 허용했다. 이는 Event·v3·BOIL 밖의 **진입 위치 필터 공백 후보**다. 한 사례만으로 기준 변경을 정당화할 수는 없다.

### 3. 개별 악재

`UNRESOLVED`

남아 있는 raw ticker-news cache에는 신호 전에 다음 단서가 있다.

- 2026-07-06: 사상 최고가와 과매수·고평가 경고
- 2026-07-06: 내재가치 대비 고평가 및 내부자 매도 언급
- 2026-07-08: Russell 계열 지수 제외와 기계적 매도 가능성 언급

그러나 특정 기사와 7월 9일 장 후반 하락·7월 10일 갭다운 사이의 인과를 증명하는 timestamped market-reaction 자료는 없다.

또한 evaluator는 `DEFAULT_LAG_DAYS=1`을 사용했다. 신호 당시 technical `signal_date`는 2026-07-08이었고 실제 선택된 뉴스 행은 2026-07-07이었다.

```text
selected sentiment_avg=+0.1488
topic contribution diagnostic=-1.3137241491157623
```

2026-07-08의 부정 뉴스 행은 이 판정에 직접 사용되지 않았다. 이는 뉴스 lag 설계의 결과이며, 해당 악재를 놓친 것이 손실 원인인지는 확인할 수 없다.

### 4. 단순 변동

`PLAUSIBLE`

재구성한 신호 입력 ATR은 23.3871이다. 저장 신호 가격과 Alpaca 최신 거래가 차이는 21.72달러로 약 0.929 ATR이다.

따라서 -3.61%는 손실이지만 CRS 자체 변동성 대비 극단적인 움직임이라고 단정할 수 없다.

## 최종 판정

```text
게이트 범위: Event/v3/BOIL 밖
고점 진입 가설: 부분 지지
가장 직접적인 관찰: 당일 고점 부근 진입 후 장 마감 전 약화와 다음 날 갭다운
개별 악재 인과: UNRESOLVED
시스템 결함 확정: 불가
진입 위치/BB proximity 공백 후보: 존재
```
