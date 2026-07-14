# Market score 진입 국면 타당성 검정

- 작업일: 2026-07-14
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 분석 universe: TRAIN 3-fold 완전 커버 5,600종목
- GA·백테스트·재학습: 실행하지 않음
- OOS·STRESS: TRAIN 판정 이후 참고만 사용
- 최종 판정: **SCORE_ENTRY_JUSTIFIED**

## STEP 0 — Lookahead 판정

```text
SCORE_CAUSAL_OK
```

### Score 생성 경로

확인 파일:

```text
engine/market/context.py
scripts/research/regen_market_history.py
engine/core/feature_lag.py
```

`market_history.csv`의 기본 score는 날짜 `d`마다 다음 입력만 사용한다.

```text
S&P500 Close.loc[:d]의 60거래일 수익
VIX Close.loc[:d]의 마지막 값
```

공식:

```text
sp500_score = clip((sp500_60d + 10) × 2.5, 0, 50)
vix_score   = clip(50 - (vix - 10) × 1.67, 0, 50)
score       = clip((sp500_score + 0.5 × vix_score) × 100/75, 0, 100)
```

Centered rolling, 양방향 smoothing, 미래 날짜 참조는 없다. 실제 1,759행을 저장된 `sp500_60d`·`vix`로 재계산한 결과:

```text
공식 오차 > 1e-12: 0행
최대 절대오차: 4.263256414560601e-14
regime 불일치: 0행
score 결측: 0행
중복 날짜: 0행
```

현재 snapshot은 `scripts/research/regen_market_history.py`로 2026-07-12에 사후 재생성됐다. 따라서 vendor가 과거 가격을 수정한 restatement 가능성은 있으나, 계산식 자체는 각 날짜의 과거·당일 데이터만 사용한다.

기존 백테스트는 `lookup_market_at_lagged(..., lag_days=1)`로 signal D에서 D-1 score를 사용한다. 이번 벡터 검정은 **D 종가 후 확정된 score로 D+1 Open 진입**을 정의했으므로 역시 causal하다.

주의할 소스 edge case:

- `lookup_market_at()`은 요청일이 history 최초일보다 이르면 첫 행을 반환하지만, 현재 TRAIN은 2022년 이후이고 history는 2019-07-11부터라 영향 없음.
- builder에는 S&P 날짜가 VIX 마지막 날짜보다 늦으면 VIX 최종값을 쓰는 fallback이 있다. 현재 재생성 fetch는 두 지수 모두 2026-07-10 종료라 해당 행은 0개다. 향후 재생성 시 VIX 종료일이 뒤처지면 fail-closed 검사가 필요하다.

`market_history_v2.csv`는 score 컬럼이 없고 과거 뉴스의 GPT 사후 해석이 완전한 point-in-time이었다는 provenance를 확정하기 어려워 이번 국면 축에서 제외했다.

## STEP 1 — 과거 GA 사용 이력

### 코드상 사용 위치

1. `engine/strategies/evaluator.py`
   - `market_score_weight`를 quality score 배수에 사용.
   - `crash_buy_enabled`이면 낮은 score에서 +2 보너스.
   - strict-entry에서는 `should_buy = interval_result.passed`이므로 market score가 직접 진입을 막지 않음.
   - legacy non-strict에서는 quality score가 signal threshold를 넘는지에 간접 영향.
2. `calc_position_size_krw`
   - market score를 직접 받지 않고 signal/quality score를 받음.
   - `signal_scaled`일 때만 market adjustment가 포지션 크기에 간접 영향.
3. Exit
   - score < 40: `stop_loss_atr_bear`
   - score >= 70: `take_profit_atr_bull`
4. `engine/learning/genetic.py`
   - entry scope에서 `market_score_weight`, `market_adjustment_strength`, `use_market_entry_adjustment`, sizing strategy를 진화시킴.
   - `gene_scope='legacy'` 기본값은 유지.

명시적인 historical hard entry gate인 `market_score >= X` 또는 `<= X` 실험은 코드·로그에서 확인되지 않았다.

### 최근 AAP strict-entry 로그

최근 AAP v2 fold-best rulebook:

| Fold | market adjustment | sizing | score weight |
|---|---|---|---:|
| train_1 | OFF | signal_scaled | -0.1275 |
| train_2 | ON | fixed | +1.0000 |
| train_3 | OFF | fixed | +0.7675 |

train_2는 adjustment가 ON이어도 strict-entry의 진입일을 바꾸지 않고 fixed sizing이라 포지션 크기도 바꾸지 않는다. train_1·3은 adjustment 자체가 OFF다. 세 fold 모두 crash-buy는 OFF였다.

동일 lag 규칙으로 fold-best 36거래의 score를 복원한 참고값:

| Regime | 거래 | 0.5% 초과 승률 | 평균 PnL |
|---|---:|---:|---:|
| Bear (<40) | 7 | 85.71% | +3.0825% |
| Neutral | 9 | 100.00% | +8.1112% |
| Bull (>=70) | 20 | 90.00% | +2.8731% |

하지만 bear stop ATR은 일반 stop ATR과 모두 2.0으로 같았고, bull 20건에서도 take-profit 청산은 0건이었다. 대부분 `entry_interval_break` 청산이므로 최근 fold-best 성과에 score exit branch가 실제 영향을 줬다는 증거는 없다.

과거 Stage2 smoke 423거래에서는 market adjustment가 160건에서 1.0이 아니었고, score별 0.5% 승률은 bear 54.55%, neutral 46.60%, bull 58.33%였다. 이는 3종목·pop4/gen2의 비통제 smoke 로그라 인과 증거가 아니라 경로 활성 흔적만 의미한다.

## STEP 2 — Market score 단독 null 검정

지난 null 프레임과 동일하게 사용했다.

```text
TRAIN 3 folds
D score → D+1 Open 진입 → 7거래일 보유
비용 차감 실현수익 > 0.5% = 승
일관성 점수 = 세 fold 승률 최솟값
fold당 최소 3건
seed 2026071401
각 null 100회
VM 6-process, ticker-index 병합
```

Market score는 모든 종목에 공통인 날짜 변수다. 따라서 ground-truth null은 횡단면 동조를 보존하도록 수정했다.

1. Block null: 각 fold의 동일한 5거래일 block 순열을 모든 종목에 동시에 적용.
2. Cross-ticker null: 종목 B를 derangement로 연결한 뒤, 모든 종목에 동일한 비영점 5거래일 block 순환 이동 적용.

### Score 10분위

```text
[0.0000, 29.0042, 45.0462, 58.4593, 65.9356,
 70.8537, 75.1224, 80.1396, 87.9135, 95.0212, 97.2946]
```

전체 score 조건 분포:

| 분포 | q95 | q99 | p95 | p99 |
|---|---:|---:|---:|---:|
| Actual | 33.33% | 48.15% | - | - |
| Synchronized block null | 31.25% | 38.98% | 0.0297 | 0.0099 |
| Cross-ticker + shift null | 31.03% | 39.39% | 0.0297 | 0.0099 |

두 null 모두 유의하게 초과했다.

신호를 만든 핵심 구간은 최저 score 10분위다.

```text
score: [0, 29.0042)
fold 신호일: train_1 42일 / train_2 4일 / train_3 27일
actual q95/q99: 51.85% / 64.29%
block p95/p99: 0.0099 / 0.0099
cross p95/p99: 0.0099 / 0.0099
종목별 최소 일수익 중앙값: +0.1219%/일
```

나머지 9개 score 구간은 두 null을 함께 통과하지 못했다. 특히 높은 score는 신호가 아니었다. 따라서 유효 방향은 **bull score 선호가 아니라 극저점 score에서의 contrarian 반등 조건**이다.

중요한 제한: train_2의 극저점 score 날짜는 4일뿐이다. 최소 3건 규칙은 통과하지만 시간 사건 수가 매우 적으므로 즉시 production hard gate로 고정하기보다 별도 event-cluster 안정성 검정이 필요하다.

## STEP 3 — Score 국면 × 기존 feature

TRAIN tertile 경계:

```text
LOW  : [0, 61.0503)
MID  : [61.0503, 78.5159)
HIGH : [78.5159, 97.2946]
```

Fold별 날짜 수:

| Regime | train_1 | train_2 | train_3 |
|---|---:|---:|---:|
| LOW | 125 | 36 | 81 |
| MID | 82 | 73 | 87 |
| HIGH | 36 | 133 | 74 |

조건 유효성:

```text
기존 feature-only 무효율: 8.73%  (24,457 / 280,000)
score-regime × feature 무효율: 47.20% (396,504 / 840,000)
```

표본 분할로 거의 절반이 fold당 3건 기준을 충족하지 못했다. HIGH 국면의 `bb_position`, `volume_ratio`는 유효 조건이 0개였다.

Regime별 pooled feature 분포는 어느 것도 두 동기화 null을 안정적으로 통과하지 못했다.

| Regime | Actual q95/q99 | Block p95/p99 | Cross p95/p99 | 판정 |
|---|---|---|---|---|
| LOW | 33.33% / 42.86% | 0.1683 / 0.0198 | 0.1287 / 0.1287 | 없음 |
| MID | 33.33% / 44.44% | 0.4158 / 0.0990 | 0.4158 / 0.2574 | 없음 |
| HIGH | 25.00% / 37.50% | 1.0000 / 0.9901 | 1.0000 / 0.9703 | 없음 |

### ma_trend 비교

| 조건 | Actual q95/q99 | Block p95/p99 | Cross p95/p99 | 판정 |
|---|---|---|---|---|
| 국면 미분할, 지난 검정 | 33.33% / 42.86% | 0.0099 / 0.0099 | 0.0099 / 0.0099 | 유의 |
| LOW score × ma_trend | 36.84% / 50.00% | 0.0099 / 0.0099 | 0.0198 / 0.1089 | 불안정 |
| MID score × ma_trend | 33.33% / 45.45% | 0.2475 / 0.0495 | 0.2772 / 0.1584 | 없음 |
| HIGH score × ma_trend | 24.80% / 37.50% | 1.0000 / 0.9901 | 0.9901 / 0.9802 | 없음 |

LOW score에서 ma_trend의 수치상 tail은 커졌지만 cross-shift q99가 유의하지 않았다. 15개 feature-regime scope의 max-stat 보정 후 cross q99 p는 0.3168이다. 따라서 score를 3국면 축으로 추가해 기존 feature가 더 강해졌다는 근거는 없다.

## STEP 4 — 최종 판정

```text
SCORE_ENTRY_JUSTIFIED
```

근거:

- Score 단독 전체 분포가 두 synchronized null의 q95·q99를 모두 유의하게 초과.
- 핵심은 score < 29.0042의 contrarian 저점 국면.
- Score 국면 × 기존 feature는 추가 이득 없음.
- 따라서 `SCORE_REGIME_CONDITIONAL`이 아니라 score 자체를 별도 진입 라벨 후보로 인정한다.

다만 production 적용 방향은 다음처럼 제한해야 한다.

```text
정당한 방향: 극저점 score의 반등 후보 라벨
근거 없는 방향: 높은 score일수록 매수하는 일반 bull gate
즉시 hard gate: 보류 — train_2 저점 사건이 4일뿐
```

## 참고 OOS·STRESS

TRAIN score-only 상위 8종목은 FAS, SOXL, KORU, DUSL, ONIT, APG, MOD, ASX였다. 모두 score <29.0042 조건이다.

| 구간 | 총 신호 | 양의 평균 일수익 | 승률 60% 이상 | 평균 일수익 단순평균 |
|---|---:|---:|---:|---:|
| STRESS | 424 | 8/8 | 1/8 | +0.4401%/일 |
| OOS | 24 | 8/8 | 8/8 | +3.2140%/일 |

OOS는 종목당 3신호뿐이며 동일한 3개 시장 날짜를 공유한다. 24개의 독립 관측으로 해석할 수 없다. 또한 상위 목록에 FAS·SOXL·KORU·DUSL 같은 레버리지·고베타 상품이 많아, score 신호는 일반 종목 선택력보다 시장 급락 후 beta rebound를 포착했을 가능성이 높다. 이 관찰은 선택·판정에 사용하지 않았다.

## 보호 상태

시작 SHA:

```text
.env
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce
market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

Daemon PID `494330`, 시작 시각 `Sat Jul 11 20:16:00 2026`을 유지했다. Source code·시장 데이터·OHLCV는 수정하지 않았다.

사전 백업 커밋:

```text
8ef5a5c 시장 점수 진입 국면 null 검정 산출물 기록 전 상태 백업
```
