# 진입 필터 개체 재학습 — SignalCollector replay 기반 편향 제거판

- 실행일: 2026-07-12
- 범위: replay → 5일 feature → 2거래일 내 +3% label → 개체별 GA → stress/OOS survivor
- 라이브 연결: **없음**
- 라이브 코드·설정·daemon 변경: **0**
- 최종 판정: **VIABLE — Shadow 한정 권고, BLOCK 승격 비권고**

## 1. 이번 universe의 정확한 성격

이번 표본은 기존 `rl_replay_trades.jsonl`을 사용하지 않았다.

대신 조사 시점 라이브 적격 rulebook 57개를 현재 코드 계약으로 과거 날짜에 다시 평가했다.

```text
현재 eligible rulebook
→ 현재 SignalCollector
→ 현재 evaluate_signal
→ 현재 market/sentiment context loader
→ use_llm_events=false
→ 과거 날짜별 should_buy=True 전수 수집
```

따라서 이 universe는:

### **현재 rulebook을 과거에 재평가한 신규 신호 universe**

이며:

### **원래 Stage2/Stage3 실험의 historical signal log 복원본이 아니다.**

원 실험 당시와 현재 사이에는 evaluator·indicator·Event·market context·sentiment cache 차이가 있을 수 있다.

## 2. Replay signal universe

SignalCollector가 평가한 기간:

- 설정 평가 범위: 2021-01-01 ~ 2026-07-02
- 실제 replay should-buy 발생 범위: 2021-01-04 ~ 2026-06-15
- 실제 label 가능 범위: 2021-01-04 ~ 2026-06-11

결과:

- 전체 독립 `should_buy=True`: **18,245개**
- D+1·D+2 label 가능: **18,226개**
- label 불가: **19개**
- 중복 `(candidate_id, signal_date)`: 0
- 데이터 오류: 0

19개는 ticker별 cache 끝부분에서 이후 두 거래일 가격이 없어:

`UNLABELED_FUTURE_2_NOT_AVAILABLE`

로 남겼다. 학습·검증에는 사용하지 않았다.

## 3. 로그 기반 대비 표본 증가

| 기준 | 로그 기반 | Replay 기반 | 변화 |
|---|---:|---:|---:|
| 전체 should-buy / entry signal | 3,430 | 18,245 | +14,815 |
| Label 가능 신호 | 3,430 | 18,226 | +14,796 |
| Label 가능 표본 배수 | 1.00x | 5.314x | — |

Replay universe는 실제 entry만 남긴 로그와 달리 다음을 포함한다.

- 포지션 보유 중 반복 should-buy
- 연속 발생 should-buy
- 포트폴리오 한도 때문에 체결되지 않은 신호
- allocation 순위에서 밀린 신호

따라서 holding-period 및 실제 진입 선택 편향은 크게 줄었다.

하지만 현재 evaluator/context로 다시 평가했으므로 증가분 전체를 단순히 “로그에서 누락된 원신호”로 해석할 수는 없다.

## 4. Label 정의와 누수 방지

신호일을 D0라고 했다.

Feature:

```text
D-6 Close
D-5~D-1 High/Low/Close
```

사용 feature:

- D-5~D-1 일별 수익률 5개
- 5일 누적수익률
- 상승일·하락일 수
- 최근 상승→하락 전환
- 5일 high/low
- 고점 발생 후 경과일
- `close_pos5`
- `pullback_from_high5_pct`
- 최대 단일 상승일
- `fade_after_surge_score`

제외:

- D0 Open/High/Low/Close feature
- `STK_gap_d0`
- ETF `gap_d0`
- flow/orderbook
- 미래 가격 기반 feature

Label:

```text
signal_price = Close[D0]
future_max_high = max(High[D+1], High[D+2])
label_2d3pct = 1 if future_max_high / signal_price - 1 >= 0.03
```

Replay 신호 생성은 D0까지의 데이터로 이뤄지지만, 후단 필터 feature는 D-1에서 명시적으로 절단했다.

GA는 train에서만 학습했다. Stress와 OOS는 champion 선택·quantile 산출·fitness에 사용하지 않았다.

## 5. 구간별 label 분포

| 구간 | Replay 신호 | +3% positive | Positive rate | 미래 최대수익률 평균 | 중앙값 |
|---|---:|---:|---:|---:|---:|
| Stress | 4,945 | 1,822 | 36.85% | 3.1165% | 2.1336% |
| Train | 10,289 | 3,856 | 37.48% | 3.3166% | 2.1755% |
| OOS | 2,992 | 1,441 | 48.16% | 4.2883% | 2.8416% |
| 전체 | 18,226 | 7,119 | 39.06% | 3.4218% | 2.2591% |

OOS positive rate가 train보다 10.68%p 높다. 따라서 absolute precision만이 아니라 각 개체·구간 baseline 대비 lift를 함께 확인했다.

## 6. 로그 기반 label 분포와 비교

| 구간 | 로그 기반 positive rate | Replay positive rate | 변화 |
|---|---:|---:|---:|
| Stress | 44.70% | 36.85% | -7.86%p |
| Train | 38.91% | 37.48% | -1.43%p |
| OOS | 49.20% | 48.16% | -1.03%p |
| 전체 | 43.18% | 39.06% | -4.12%p |

로그 기반 entry 표본이 전체적으로 더 높은 positive rate를 가졌다. 이는 실제 진입 과정이 상대적으로 강한 신호를 남겼거나 보유기간·allocation이 표본을 선택했을 가능성을 뒷받침한다.

다만 이 차이는 순수한 entry-bias 인과효과가 아니다. Replay는 현재 evaluator와 context를 사용했으므로 코드·context 버전 차이도 포함한다.

## 7. GA 학습

이전 로그 기반 연구와 동일한 GA 설정을 유지했다.

- Population: 128
- 최대 generation: 60
- Elite: 16
- Early-stop patience: 18
- 최대 활성 numeric feature: 5
- Seed: `SHA256(candidate_id)` 기반 고정
- Train-only quantile mapping
- Precision·lift 중심 fitness
- 최소 통과표본 제약

결과:

- 대상 entity: 57개
- Train 가능 entity: 51개
- Train 부족 entity: 6개
- Training log rows: 2,164
- 최종 survivor: 1개

Train 부족 entity:

- AA
- ABEV
- ACLS
- AES
- CLDX
- DHI

## 8. 최종 survivor

### AEVA

Candidate:

`stage3:AEVA:3c4e598fa5c7`

학습 gene:

```text
ret_d2_pct ∈ [-8.2525%, -2.3305%]
AND
pullback_from_high5_pct ∈ [12.8165%, 48.7179%]
```

[추정] 최근 5일 중 D-2에 큰 하락이 있었고 D-1 기준 5일 고점에서 크게 밀린 상태에서, 이후 두 거래일 +3% 반등 가능성을 선별한 mean-reversion 형태다.

### 구간별 성능

| 구간 | 원신호 | Baseline | 필터 통과 | Positive | Precision | Recall | Coverage | Lift |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Stress | 148 | 58.11% | 23 | 18 | 78.26% | 20.93% | 15.54% | +20.15%p |
| Train | 317 | 64.04% | 47 | 37 | 78.72% | 18.23% | 14.83% | +14.69%p |
| OOS | 106 | 70.75% | 17 | 15 | 88.24% | 20.00% | 16.04% | +17.48%p |

Precision gap:

- Train - Stress: +0.46%p
- Train - OOS: -9.51%p

Stress와 OOS 모두 최소표본·precision floor·train gap 조건을 통과했다.

## 9. 이전 survivor와의 변화

로그 기반 survivor:

- AMSC
- AVAV

Replay 기반 survivor:

- AEVA

공통 survivor는 0개다.

### AMSC

| 표본 | Train precision | Stress precision | OOS precision |
|---|---:|---:|---:|
| 로그 기반 | 100.00% | 80.00% | 80.00% |
| Replay 기반 | 86.05% | 28.57% | 57.89% |

Replay 표본에서 stress 일반화가 붕괴해 탈락했다.

### AVAV

| 표본 | Train precision | Stress precision | OOS precision |
|---|---:|---:|---:|
| 로그 기반 | 62.50% | 60.00% | 75.00% |
| Replay 기반 | 62.96% | 26.09% | 36.36% |

Replay 표본에서 stress·OOS 모두 붕괴했다.

### AEVA

| 표본 | Train precision | Stress precision | OOS precision |
|---|---:|---:|---:|
| 로그 기반 | 100.00% | 66.67% | 75.00% |
| Replay 기반 | 78.72% | 78.26% | 88.24% |

로그 기반에서는 작은 train 표본의 100% precision 때문에 validation gap 조건을 실패했다. Replay에서는 표본이 늘면서 train precision이 정상화됐고 stress·OOS가 안정적으로 유지됐다.

이 변화는 기존 2-survivor 결과가 entry-log 표본 정의에 민감했음을 보여준다.

## 10. 과적합·집중도

AEVA 전체 통과 신호:

- Stress: 23
- Train: 47
- OOS: 17
- 합계: 87

Positive return 합계 중 상위 3건 비중:

- 10.13%
- Extreme-value concentration flag: `False`

Fitness는 binary label만 사용하므로 ANET·CVNA 같은 초대형 수익률 크기가 fitness를 직접 키우지 않았다.

그러나 survivor pool은 AEVA 한 종목뿐이다.

- Survivor entity: 1
- Ticker HHI: 1.0
- 최대 ticker 비중: 100%
- 종목 집중 flag: `True`

개별 survivor 표본은 이전보다 커졌지만 시스템 차원의 종목 분산은 오히려 더 나쁘다.

## 11. 필터 적용 vs 미적용

최종 survivor가 AEVA 하나이므로 pooled 결과는 AEVA 결과와 같다.

OOS:

- 미필터: 106개, positive 75개, precision 70.75%
- 필터: 17개, positive 15개, precision 88.24%
- Precision lift: +17.48%p
- Coverage: 16.04%

즉 precision 개선은 확인되지만 원신호의 약 84%를 차단한다.

수익률·CAGR·MDD·position overlap까지 반영한 포트폴리오 결과는 이번 범위에 포함하지 않았다.

## 12. CRS 결과

대상:

`stage3:CRS:8695c9ce3320`

Replay train champion gene:

```text
ret_d5_pct ∈ [0.7502%, 3.9543%]
AND
ret_d3_pct ∈ [0.0850%, 5.3101%]
AND
ret_d1_pct ∈ [-2.9767%, 0.6298%]
```

2026-07-09 신호 직전 feature:

- `ret_d5_pct = -1.0829%`
- `ret_d4_pct = -2.1175%`
- `ret_d3_pct = +3.6853%`
- `ret_d2_pct = -4.6669%`
- `ret_d1_pct = -0.4607%`
- `up_days5 = 1`
- `down_days5 = 4`
- `cumulative_ret5_pct = -4.7354%`
- `pullback_from_high5_pct = 6.5279%`

Selector result:

- `selector_pass = False`

따라서 CRS용 replay champion은 2026-07-09 신호를 차단한다.

하지만 CRS entity는 최종 survivor가 아니다.

- `survivor_entity = False`

따라서 실제 배포 가능한 filter가 CRS를 차단한 것으로 볼 수 없으며 참고 결과다.

2026-07-12 시점에는 2026-07-13 두 번째 거래세션이 아직 도래하지 않아 CRS 실제 2일 label은 `NOT_STORED`다.

## 13. 편향 개선 여부

### 개선된 점

- 실제 entry 여부와 무관한 모든 독립 should-buy 포함
- 보유기간·position state 표본 누락 제거
- 표본 5.3배 확대
- AMSC·AVAV의 숨겨진 일반화 붕괴 확인
- AEVA의 작은 로그 표본 train precision 왜곡 완화

### 남은 편향

1. **Current-survivor selection bias**
   - 현재 적격 rulebook 57개를 과거 전체 기간에 고정 적용했다.
   - 과거에 실패·폐기된 rulebook은 포함하지 않았다.

2. **Current-code replay bias**
   - 원 실험 당시 evaluator가 아니라 현재 evaluator를 사용했다.

3. **Context snapshot mismatch**
   - 신호는 현재 저장된 market/sentiment context로 과거 재평가했다.
   - label은 실제 과거 가격이다.
   - 당시 뉴스/Event 입력이 완전히 보존되지 않았을 수 있다.

4. **Cache endpoint variation**
   - ticker별 cache 종료일이 달라 최신 19개 신호는 label 불가였다.

5. **Portfolio feasibility 미검증**
   - 연속 should-buy를 모두 독립 표본으로 취급했다.
   - 실제 포지션·자본 제약 시 성과는 별도다.

## 14. 시점 불일치와 누수 판정

SignalCollector는 각 평가일에 `df.iloc[:idx+1]`만 evaluator에 제공한다. 5일 필터 feature는 여기서 다시 D-1까지 잘라 사용한다.

따라서 OHLCV 미래누수는 확인되지 않았다.

다만 historical context의 완전한 원본 snapshot이 아니라 현재 보존된 context를 사용하므로:

- 당시 존재하지 않았던 수정 데이터
- 누락된 뉴스/Event
- 현재 fallback 값

이 개입할 가능성이 있다. 이는 명시적 미래 가격 누수라기보다 **historical context 재현 불완전성**이다.

## 15. 최종 판정

### **VIABLE — Shadow 연결 권고**

사용자 지정 survivor 조건을 AEVA가 충족했다.

- Stress survivor: 있음
- OOS survivor: 있음
- 동일 entity가 두 구간 모두 precision 유지
- 최소 통과표본 유지
- 극단값 집중 낮음
- OOS precision 88.24%, pass 17개

하지만 다음 이유로 BLOCK 승격은 권고하지 않는다.

- survivor 1개
- ticker HHI 1.0
- 이전 survivor와 공통 0개
- current-code replay와 historical context mismatch
- 포트폴리오 CAGR/MDD 미검증

권고 순서:

1. AEVA frozen gene을 연구 artifact로 고정
2. live pool을 바꾸지 않는 shadow logging만 연결
3. prospective should-buy마다 D-1 feature와 pass/fail 저장
4. D+2 이후 label 확정
5. AEVA prospective pass 최소 20~30개 확보
6. 추가 rulebook retraining 또는 pooled model로 survivor 분산 확인
7. 실제 포트폴리오 제약을 포함한 CAGR/MDD 비교
8. 이후 BLOCK 여부 별도 결정

## 16. 산출물

- `run_entry_filter_2d3pct_replay.py`
- `finalize_replay_outputs.py`
- `replay_signal_universe.csv`
- `unlabeled_replay_signals.csv`
- `label_distribution.csv`
- `training_log.csv`
- `survivor_summary.csv`
- `survivors.jsonl`
- `per_regime_metrics.csv`
- `pooled_survivor_metrics.csv`
- `overfit_check.csv`
- `bias_comparison.csv`
- `comparison_to_log_based.csv`
- `survivor_transition.csv`
- `crs_filter_result.csv`
- `summary.json`
- `immutability_check.csv`
- `manifest.sha256`
