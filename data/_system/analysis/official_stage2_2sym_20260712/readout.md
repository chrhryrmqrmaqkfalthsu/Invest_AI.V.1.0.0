# 정식 Stage 2 AAP·POWI 시장 점수 연결 검증

# 최종 판정: **MARKET_NEUTRAL**

시장 점수는 정식 경로에 정상 연결됐고 GA chromosome과 실제 trade signal에 사용됐다. 그러나 AAP·POWI 모두 OOS 기간에 도달한 후보가 0개였고 survivor도 0개였다.

따라서 이번 실행은 다음 두 사실을 동시에 확인한다.

```text
시장 점수가 빠진 상태는 아님: 확인됨
시장 점수가 stress/OOS 일반화 벽을 해결함: 확인되지 않음
```

## Phase 1 연결 게이트

```text
PHASE1_PASS = TRUE
```

| 확인 항목 | AAP | POWI |
|---|---|---|
| `market_history_df` | 1,759행 | 1,759행 |
| 시장 기간 | 2019-07-11 ~ 2026-07-10 | 2019-07-11 ~ 2026-07-10 |
| sector mapping | `tech` | `tech` |
| sector column | `sector_tech` 존재 | `sector_tech` 존재 |
| D-1 lookup | PASS | PASS |
| 5개 lookup 값 변동 | PASS | PASS |
| 기본값 50/50/18 고정 | NO | NO |
| market SHA 변경 | 없음 | 없음 |

Phase 1의 5개 날짜에서 score, `sector_tech`, VIX가 실제로 변했고 모든 선택 시장일은 D-1 cutoff 이하로 확인됐다.

## 실행 계약

정식 파일 `scripts/research/run_stage2.py`는 수정하지 않았다.

복구된 `market_history.csv`의 마지막 거래일이 금요일 2026-07-10이라 정식 `get_market_history()`의 달력일 stale 판정이 자동 refresh와 파일 쓰기를 유발할 수 있었다. 이를 막기 위해 런타임에서 `get_market_history()`만 복구 CSV를 직접 읽는 read-only loader로 치환했다.

유지된 정식 경로:

```text
prepare_ticker_context
→ market_history_df
→ lookup_market_at_lagged(D-1)
→ evaluate_signal(market_score, sector_score, vix)
→ Rulebook market adjustment
→ 정식 Stage 2 GA와 early-cut gate
```

차단한 동작:

```text
stale cache 자동 재생성
market_history.csv 쓰기
```

종목은 순차 실행했고 각 종목 train split 3개만 병렬 처리했다. 관측된 최대 동시 worker는 3개로 제한 6개 이하였다.

## 정식 Stage 2 결과

### Gate 흐름

| ticker | 생성 | stress 통과 | 다음 train gate | 그다음 gate | OOS 도달 | survivor |
|---|---:|---:|---:|---:|---:|---:|
| AAP | 300 | 3 / 300 (1.0%) | `train_3`: 0 / 3 | 미도달 | 0 | 0 |
| POWI | 300 | 27 / 300 (9.0%) | `train_3`: 15 / 27 | `train_2`: 0 / 15 | 0 | 0 |

AAP는 stress를 통과한 3개가 다음 train period에서 전부 탈락했다. POWI는 stress 통과 27개 중 15개가 `train_3`을 통과했으나 `train_2`에서 전부 탈락했다.

### Stress 지표

정식 Stage 2에는 pilot의 binary label precision과 같은 지표가 없다. 아래 `win_rate`는 실제 거래 승률이며 pilot의 precision과 동일한 수치가 아니다.

| ticker | stress 평가 후보 | 평균 win rate | 중앙 win rate | stress 통과 후보 평균 win rate | 통과 후보 평균 expectancy |
|---|---:|---:|---:|---:|---:|
| AAP | 300 | 48.6068% | 51.2821% | 73.7354% | +1.4379% |
| POWI | 300 | 53.7249% | 52.8595% | 66.2581% | +1.7097% |

POWI의 `train_3` 통과 후보 15개는 평균 win rate 78.2277%, 평균 expectancy +4.0418%였지만 다음 `train_2`에서는 평균 win rate 21.9153%, 평균 expectancy -1.7999%로 붕괴했다.

### OOS

```text
AAP OOS reached: 0 / 300
POWI OOS reached: 0 / 300
```

OOS 미도달은 OOS precision 0%라는 뜻이 아니다. 평가 표본이 없으므로 precision과 coverage는 `N/A`다.

## 시장 gene 학습 여부

### 전체 300개 rulebook

| ticker | `use_market_entry_adjustment=True` | 시장 gene 활성 rulebook | 활성 비율 |
|---|---:|---:|---:|
| AAP | 152 | 93 | 31.0% |
| POWI | 196 | 196 | 65.3% |

활성 정의:

```text
use_market_entry_adjustment=True
AND market_adjustment_strength > 0
AND market/sector/VIX weight 중 하나 이상 nonzero
```

### 각 train split 최상위 rulebook

| ticker | train | market weight | sector weight | VIX weight | strength | switch | 활성 |
|---|---|---:|---:|---:|---:|---|---|
| AAP | train_1 | -0.2125 | +0.2487 | -0.8660 | 0.0866 | OFF | NO |
| AAP | train_2 | +0.3746 | +0.4569 | -1.0000 | 0.0617 | OFF | NO |
| AAP | train_3 | -0.4091 | +1.0000 | -0.8908 | 0.7414 | ON | YES |
| POWI | train_1 | -0.2481 | -0.5109 | -0.8808 | 0.6369 | ON | YES |
| POWI | train_2 | -0.6592 | -0.8995 | +0.5581 | 0.6134 | ON | YES |
| POWI | train_3 | +0.7927 | +0.8230 | +0.1913 | 0.5987 | OFF | NO |

시장은 chromosome에 존재했고 일부 최상위 모델에서 실제로 켜졌다. 따라서 `MARKET_UNUSED`는 아니다.

그러나 일반화 gate를 가장 깊게 통과한 후보에서 시장 활성 비율은 낮아졌다.

```text
AAP stress 통과 3개 중 활성 1개
POWI stress 통과 27개 중 활성 14개
POWI train_3 통과 15개 중 활성 2개
```

## 실제 trade 시장 보정

| ticker | 기록된 평가 trade | adjustment ≠ 1 | 적용 비율 | adjustment 평균 | 최소 | 최대 |
|---|---:|---:|---:|---:|---:|---:|
| AAP | 7,430 | 2,877 | 38.7214% | 1.0291 | 0.5838 | 1.9079 |
| POWI | 5,306 | 3,521 | 66.3588% | 0.9997 | 0.3631 | 2.0000 |

`entry_market_adjustment != 1`인 모든 행에서 `entry_signal_score`도 `entry_signal_raw_score`와 달라졌다.

즉 시장 데이터가 단순히 로드만 된 것이 아니라 실제 buy signal score를 증감시켰다.

## Pilot과의 대조

| 항목 | pilot floored λ=0 | 정식 AAP | 정식 POWI |
|---|---:|---:|---:|
| 시장 연결 | 없음 | 있음 | 있음 |
| 시장 보정 trade 비율 | N/A | 38.72% | 66.36% |
| stress 지표 | binary precision 43.26% | trade win rate 48.61%, gate 1% | trade win rate 53.72%, gate 9% |
| OOS precision | 58.06% | N/A | N/A |
| OOS coverage | 18.45% | N/A | N/A |
| OOS 도달 | pilot 선택 후보 존재 | 0 / 300 | 0 / 300 |
| survivor | 0 | 0 | 0 |

중요한 제한:

```text
pilot: +3%/2일 binary label의 14-feature grouped interval 모형
정식 Stage 2: swing trade rulebook GA와 거래 성과 gate
```

두 실험은 target, chromosome, fitness, precision 정의가 다르므로 숫자를 직접 우열 비교할 수 없다.

## 판정 근거

### MARKET_HELPS가 아닌 이유

- 두 종목 모두 OOS 도달 후보 0개
- survivor 0개
- 시장 활성 후보가 일반화 gate에서 일관되게 우세하다는 증거 없음
- 특히 POWI의 deepest 15개 중 시장 활성은 2개뿐

### MARKET_UNUSED가 아닌 이유

- Rulebook에 시장 gene이 실제 학습됨
- AAP 31.0%, POWI 65.3% rulebook에서 시장 gene 활성
- 평가 trade 중 AAP 38.72%, POWI 66.36%에서 실제 signal score 변경

따라서 지정된 판정 중 **MARKET_NEUTRAL**이 맞다.

## 해석 범위

이번 결과로 오늘의 “5일 raw 가격 feature 정보량이 낮다”는 측정이 계산 오류였다고 할 수는 없다. 그 측정은 raw feature 자체에 대한 사실이다.

동시에 이번 정식 Stage 2는 다른 target과 다른 fitness를 사용하므로, 그 raw-feature 정보 천장을 독립적으로 확정하는 실험도 아니다.

확인된 사실은 다음으로 한정된다.

```text
복구된 시장 점수는 정식 경로에서 정상 소비됐다.
시장 보정은 실제 신호에 적용됐다.
그러나 이 2종목 정식 Stage 2에서는 OOS 일반화 개선 증거가 나오지 않았다.
```
