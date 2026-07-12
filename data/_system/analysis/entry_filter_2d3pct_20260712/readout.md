# 진입 필터 개체 R&D — 5일 → 2거래일 내 +3% 선별

- 실행일: 2026-07-12
- 범위: label 생성 → 개체별 train-only GA → stress/OOS 이중 검증
- live 연결: **없음**
- 라이브 코드·설정 변경: **0**
- 최종 판정: **VIABLE — Shadow 한정 권고, BLOCK 비권고**

## 1. 결론

현재 라이브 적격 rulebook 57개에 대해 저장된 entry signal 3,430개를 학습·검증했다.

- stress: 982개
- train: 1,578개
- OOS: 870개
- 전체 +3% positive: 1,481개 / 3,430개 = **43.18%**

Train에서만 GA를 학습하고 frozen champion을 stress와 OOS에 각각 적용한 결과, 두 구간을 모두 통과한 survivor는 2개다.

- `stage3:AMSC:57bf3f342f43`
- `stage3:AVAV:3d66945d7c65`

명시된 판정 규칙상 stress·OOS 양쪽에 survivor가 있으므로 `VIABLE`이다.

다만 57개 중 2개만 생존했고, survivor pool 통과 신호가 두 종목에만 집중되므로 즉시 BLOCK으로 승격할 근거는 부족하다. 연구 artifact를 frozen 상태로 보존하고 **Shadow 관찰만 권고**한다.

## 2. 데이터와 label 분포

### 저장 신호 범위

- 최초 신호: 2021-03-05
- 최종 label 가능 신호: 2026-07-02
- 개체 수: 57
- 데이터 오류: 0

Stress 시작일은 2020-01-01로 정의했지만 저장된 `entry_signal_date`는 2021-03-05부터 시작한다. 2020년 theoretical should-buy 일별 ledger는 `NOT_STORED`다.

### 구간별 positive rate

| 구간 | 신호 | +3% positive | positive rate | 미래 2일 최대수익률 평균 | 중앙값 |
|---|---:|---:|---:|---:|---:|
| stress | 982 | 439 | 44.70% | 3.6643% | 2.5989% |
| train | 1,578 | 614 | 38.91% | 3.4718% | 2.3065% |
| OOS | 870 | 428 | 49.20% | 4.1385% | 2.9465% |
| 전체 | 3,430 | 1,481 | 43.18% | 3.6960% | 2.5421% |

OOS positive rate가 train보다 약 10.29%p 높아 구간 분포가 동일하지 않다. 따라서 단순 precision 수치만이 아니라 각 구간 baseline 대비 lift를 함께 봐야 한다.

## 3. 학습 구조

각 rulebook마다 독립된 진입필터 개체를 학습했다.

- population: 128
- 최대 generation: 60
- elite: 16
- early-stop patience: 18
- 최대 활성 numeric feature: 5
- 개체별 고정 seed
- 학습 개체: 57개
- training log rows: 2,182
- generation 중앙값: 35
- early-stop 개체: 52개

Feature와 label의 정확한 정의는 `label_spec.md`에 기록했다.

Fitness는 forward return의 크기가 아니라 `label_2d3pct`의 precision·lift·recall·coverage를 사용한다. 따라서 +40~65% 같은 극단값도 positive 1건으로만 처리된다.

## 4. Survivor 결과

### AMSC

Candidate:

`stage3:AMSC:57bf3f342f43`

학습 gene:

```text
ret_d3_pct ∈ [-0.4330%, 16.4497%]
AND
pullback_from_high5_pct ∈ [2.3241%, 13.7888%]
```

| 구간 | baseline | 통과표본 | precision | recall | coverage |
|---|---:|---:|---:|---:|---:|
| train | 68.57% | 10 | 100.00% | 41.67% | 28.57% |
| stress | 68.42% | 5 | 80.00% | 30.77% | 26.32% |
| OOS | 72.22% | 5 | 80.00% | 30.77% | 27.78% |

Train→stress 및 train→OOS precision gap은 모두 정확히 20%p 미만 경계 안이다.

### AVAV

Candidate:

`stage3:AVAV:3d66945d7c65`

학습 gene:

```text
up_days5 ∈ [1, 2]
AND
fade_after_surge_score ∈ [3.6656, 20.9570]
```

| 구간 | baseline | 통과표본 | precision | recall | coverage |
|---|---:|---:|---:|---:|---:|
| train | 36.84% | 8 | 62.50% | 71.43% | 42.11% |
| stress | 50.00% | 5 | 60.00% | 50.00% | 41.67% |
| OOS | 36.36% | 4 | 75.00% | 75.00% | 36.36% |

AVAV는 OOS precision이 train보다 높아 precision 붕괴가 없었다.

## 5. Survivor pool 미필터 대비 비교

두 survivor 개체만 합산한 비교다.

| 구간 | 방식 | 원신호 | 통과 | precision | coverage | baseline 대비 lift |
|---|---|---:|---:|---:|---:|---:|
| stress | 미필터 | 31 | 31 | 61.29% | 100% | — |
| stress | 필터 | 31 | 10 | 70.00% | 32.26% | +8.71%p |
| train | 미필터 | 54 | 54 | 57.41% | 100% | — |
| train | 필터 | 54 | 18 | 83.33% | 33.33% | +25.93%p |
| OOS | 미필터 | 29 | 29 | 58.62% | 100% | — |
| OOS | 필터 | 29 | 9 | 77.78% | 31.03% | +19.16%p |

OOS에서는 29개 원신호 중 9개만 통과했고, 그중 7개가 2거래일 내 +3%를 달성했다.

## 6. 탈락 현황

- survivor: 2개
- failed gate: 55개

주요 실패 원인:

- train→stress precision gap >20%p: 38개
- train→OOS precision gap >20%p: 38개
- stress precision floor 미달: 다수
- OOS precision floor 미달: 다수
- stress 최소 통과표본 미달: 11건의 조건 실패
- OOS 최소 통과표본 미달: 19건의 조건 실패
- train precision 50% 미달: 12개

대부분의 train champion이 stress 또는 OOS에서 일반화되지 않았다. 이는 5일 compact feature만으로 개체별 +3% hit를 안정적으로 분리하는 문제가 쉽지 않음을 보여준다.

## 7. 과적합·집중도 점검

### 극단값 집중

Fitness는 binary label만 사용하므로 극단 return 크기를 직접 최적화하지 않는다.

Survivor별 통과 positive return 중 상위 3건의 비중:

- AMSC: 37.50%
- AVAV: 32.13%

설정한 60% concentration flag를 넘지 않았다.

### 종목 집중

Survivor pool 전체 통과 신호:

- 총 37개
- survivor entity: 2개
- 최대 종목 비중: 54.05%
- ticker HHI: 0.5033
- concentration flag: `True`

따라서 개별 survivor 내부 극단값 쏠림은 심하지 않지만, 시스템 전체로는 두 종목에만 의존한다.

### 표본 크기

OOS 통과표본:

- AMSC 5개
- AVAV 4개
- 합계 9개

정량 결과는 긍정적이지만 아직 작은 표본이다. Shadow에서 신호를 누적해 최소 수십 건 이상의 prospective 표본을 확보해야 한다.

## 8. CRS 참고 결과

대상:

`stage3:CRS:8695c9ce3320`

CRS 자체의 train champion gene:

```text
up_days5 ∈ [2.0, 2.9497]
AND
days_since_high5 ∈ [2.6172, 4.0]
```

2026-07-09 최초 신호 직전 완료 세션 feature:

- `up_days5 = 1`
- `days_since_high5 = 2`
- `close_pos5 = 0.2302`
- `pullback_from_high5_pct = 6.5279%`
- `cumulative_ret5_pct = -4.7354%`
- `fade_after_surge_score = 8.7915`

Train champion 기준 selector 결과:

- `selector_pass = False`

즉 raw learned filter라면 CRS 최초 신호를 차단했을 것이다.

그러나 CRS 개체는 최종 survivor가 아니다.

- train precision: 50.00%, passed 8
- stress precision: 33.33%, passed 3
- OOS precision: 0%, passed 0
- 최종 상태: `FAILED_GATE`

따라서 CRS를 차단한 champion을 실제 사용 가능한 필터라고 인정할 수 없다. 최종 survivor-only 정책에서는 CRS용 필터 artifact가 배포 대상이 아니므로, CRS 차단 결과는 참고값일 뿐이다.

CRS 2026-07-09 신호의 실제 2거래일 label은 2026-07-13 세션이 아직 도래하지 않은 2026-07-12 기준으로 확정할 수 없어 `NOT_STORED`다.

## 9. 누수 감사

확인된 경계:

- feature는 D-6 Close 및 D-5~D-1 완료봉만 사용
- D0 일봉·D0 gap 사용 안 함
- ETF gap 사용 안 함
- flow/orderbook 사용 안 함
- train quantile·gene은 train에서만 생성
- stress·OOS는 champion 선택에 미사용
- label은 feature 생성 후 D+1·D+2 high로 별도 계산
- live candidate row·설정·daemon 미변경

## 10. 제한사항

1. **저장 신호 표본 제한**
   - `rl_replay_trades.jsonl`의 실제 entry signal을 사용했다.
   - 포지션 보유 중 발생했을 잠재 should-buy 일별 신호는 `NOT_STORED`다.

2. **현재 적격 universe 한정**
   - 현재 elite 57개 rulebook을 대상으로 했다.
   - 전체 2,009 ticker의 모든 과거 rulebook을 학습한 것은 아니다.

3. **신호가격 근사**
   - Historical signal price는 signal-day Close로 정의했다.
   - next-open 체결가격이나 intraday 발생가격 기준 label과는 다를 수 있다.

4. **survivor 수와 OOS 통과표본이 작음**
   - 2개 entity, OOS pass 9개다.

5. **포트폴리오 수익성 검증 미포함**
   - 본 연구는 2일 +3% label precision을 평가했다.
   - 실제 exit policy·자본 배분을 포함한 CAGR/MDD 비교는 수행하지 않았다.

## 11. 최종 판정과 롤아웃 권고

### 판정: **VIABLE**

사용자 지정 기준인:

```text
stress survivor 존재
AND OOS survivor 존재
AND 동일 개체가 두 구간 모두 precision·최소표본 유지
```

를 AMSC와 AVAV가 충족했다.

### 권고: **Shadow 연결 가능, BLOCK 승격은 보류**

권고 순서:

1. 현재 두 survivor gene을 frozen artifact로 보존
2. live 후보를 변경하지 않는 shadow decision만 기록
3. 신호일 feature snapshot과 pass/fail 저장
4. D+2 종료 후 label 자동 확정
5. 종목별 prospective pass 최소 20건 이상 확보
6. 미필터 대비 precision·recall·후속 포트폴리오 성과 재검증
7. 그 후 BLOCK 여부를 별도로 결정

본 작업에서는 live 연결을 수행하지 않았다.

## 산출물

- `run_entry_filter_2d3pct.py`
- `run_entry_filter_2d3pct_fixed.py` — 전체 Stage2/Stage3 source path를 처리하는 재현 실행 진입점
- `label_spec.md`
- `signal_dataset.csv`
- `label_distribution.csv`
- `training_log.csv`
- `survivor_summary.csv`
- `survivors.jsonl`
- `per_regime_metrics.csv`
- `pooled_survivor_metrics.csv`
- `overfit_check.csv`
- `crs_filter_result.csv`
- `summary.json`
- `immutability_check.csv`
- `manifest.sha256`
