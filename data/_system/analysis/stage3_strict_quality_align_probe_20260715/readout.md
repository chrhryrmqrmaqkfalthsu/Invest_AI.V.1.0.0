# strict-AND × quality 정합 조건 사전 측정

> **청산 손익 계산은 새 학습이 아니라 각 fold-best 확정 규칙의 산술 적용이다.**

- 작업일: 2026-07-15
- 작업 위치: `scripts/research/stage23_rework_20260713/`
- 대상: `stage3_aap_overlap_entry_v4_20260715/AAP`
- 데이터: `AAP_ohlcv.csv`, 1,526행
- 범위: 3개 fold-best의 strict pass day 65일, quality component, 확정 청산 손익, 시나리오별 EEC
- 미수행: GA, 재학습, 파라미터 탐색, 코드 수정, 원천 데이터 수정, legacy 측정, 외부 fetch
- 판정 코드: **`NO_GLOBAL_QUALITY_GATE`**
- 구현 방향 권고: **전 fold 공통 C1/C2/C3는 적용하지 않고, signal-scaled 경로에만 `quality > 0` 정합을 거는 전략 인지형 C1-S를 다음 지시서에서 검토**

## 핵심 결론

quality=0 strict pass는 train_3만의 현상이 아니다. train_2에도 5일 존재한다.

- train_3 quality=0 17일: 9승 8패, 승률 52.94%, 평균 -0.4832%, 합 -8.2151%p, 최악 -20.5050%
- train_2 quality=0 5일: 5승 0패, 승률 100.00%, 평균 +6.3527%, 합 +31.7636%p
- train_1 quality=0: 0일

따라서 `strict-AND ∧ quality>0`을 전 fold에 일괄 적용하면 train_3 잡음은 제거하지만 train_2의 좋은 거래 5건과 독립 singleton 사건 1개도 제거한다. 실제 체결 기준 전체 승률은 93.75%에서 93.02%로 0.73%p 낮아지고 평균 손익은 5.3276%에서 5.2084%로 낮아진다.

이번 데이터의 quality는 0·1·2·3의 이산값이고, pooled 양수 q25와 pooled 중앙값이 모두 1.0이다. 그 결과 C1, C2, C3는 같은 날짜 집합으로 붕괴한다. 세 조건 중 전 fold 공통 적용으로 “잡음 제거 + 사건 다양성 최대 보존 + 승률 유지”를 동시에 달성하는 조건은 없다.

반면 train_3의 zero-quality 미체결은 `signal_scaled` sizing에서 금액이 0이 되는 정합 문제이고, train_2의 zero-quality 거래는 `fixed` sizing에서 정상 체결된 좋은 거래다. 따라서 전략 인지형 조건이 수치상 가장 타당하다.

---

# STEP 0 — 존재·정합 확인

## 0.1 입력 존재

| 항목 | 확인값 | 상태 |
|---|---|---|
| train_1 fold-best | `17032e9b0fa41be3fb4f8074abc5343db38c47fa7642399f0ae5bdc2599ab3f8` | 확인 |
| train_2 fold-best | `ef5165219f45e8ae79daade130d8c32e860fd4e6ac6a4bea87553cf48d0f204f` | 확인 |
| train_3 fold-best | `35b536dd5ee0e4bbfa657ddef382a115d91276850ee36a4bc257e8cd8d451866` | 확인 |
| 일별 quality component | `SignalResult.components`, raw·final quality | 확인 |
| strict interval pass list | train_1 20일, train_2 15일, train_3 30일 | 확인 |
| OHLCV | `data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv` | 확인 |
| 확정 청산 경로 | T+1 open, strict interval-break, ATR stop, provisional 7일, cooldown 1 | 확인 |

## 0.2 SHA 정합

| 대상 | SHA256 | 상태 |
|---|---|---|
| OHLCV | `6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717` | 기대값 일치 |
| D-5 vectorized `shift(5)` | `0331aa572acbab3ebcf28bda625b3e643ec5a20a48249c1e2272609433b53629` | 기대값 일치 |
| D-5 direct extraction | `0331aa572acbab3ebcf28bda625b3e643ec5a20a48249c1e2272609433b53629` | 기대값 일치 |
| fold-best summary | `6da118e3b78aaef7e7de576e10113991026d7fc598d9718b7faad8eba2e9bf7c` | 확인 |
| fold-best trade log | `82f69b3aa465cb66521b341bdc3158e29f58db4fc526efe2f525271f96e02968` | 확인 |
| candidate rulebooks | `cbeddef553c09a6e46d6534f16c6f44c5decd09e16a3e9adb24908eb6c1623c6` | 확인 |
| concurrency summary | `eb68f0b471910c0a9245186f781f885858c849defcde59931bbe03aead795e63` | 확인 |

D-5는 `evaluator.extract_entry_features()`의 날짜별 직접 추출과 전체 feature series의 `shift(5)` 결과를 대조한 기존 고정 SHA를 다시 확인했다. 48개 기존 체결 snapshot과 mismatch 0, 최대 절대오차 0.0이다.

## 0.3 결정론적 청산 재현 게이트

세 fold의 기존 48건을 동일 rulebook·OHLCV·청산 함수로 재생했다.

| 비교 항목 | 일치 | 불일치 |
|---|---:|---:|
| 진입일·진입가 | 48/48 | 0 |
| 청산일·청산가 | 48/48 | 0 |
| 보유일 | 48/48 | 0 |
| 실현손익률 | 48/48 | 0 |
| MAE | 48/48 | 0 |
| 청산 사유 | 48/48 | 0 |

이 게이트를 통과한 뒤, 현재 미체결일을 포함한 strict pass day 전체에 1-share 정규화로 확정 청산 규칙을 산술 적용했다. 수익률·승패는 share 수에 의존하지 않으며 비용률은 기존 `commission_rate=0.0005`를 그대로 사용했다.

## 0.4 Fold별 sizing 구조

| fold | sizing | base ratio | threshold | multiplier | quality=0 체결 의미 |
|---|---|---:|---:|---:|---|
| train_1 | `signal_scaled` | 0.774256 | 2.0 | 1.604345 | 금액 0이지만 해당 strict pass에는 0일 |
| train_2 | `fixed` | 1.000000 | 2.0 | 0.698052 | quality와 무관하게 120,000 체결 |
| train_3 | `signal_scaled` | 0.619027 | 2.0 | 1.922882 | 금액 0, shares 0, 미체결 |

이 차이 때문에 동일한 quality=0이 train_2에서는 실제 거래이고 train_3에서는 미체결 guard가 된다.

---

# STEP 1 — 전 fold strict pass day quality 분포

## 1.1 Raw·final 관계

세 rulebook 모두 `use_market_entry_adjustment=false`이고 재생 context의 market adjustment는 1.0이었다.

```text
max(abs(raw_quality - final_quality)) = 0.0
final quality values = {0.0, 1.0, 2.0, 3.0}
negative quality count = 0
news/news_topics/events component nonzero count = 0
```

따라서 아래 raw와 final은 모두 같다.

## 1.2 Quantile

| 범위 | min | q25 | median | q75 | max |
|---|---:|---:|---:|---:|---:|
| train_1 | 1.0 | 1.0 | 1.0 | 2.0 | 2.0 |
| train_2 | 0.0 | 0.0 | 1.0 | 2.0 | 3.0 |
| train_3 | 0.0 | 0.0 | 0.0 | 1.75 | 2.0 |
| pooled 65일 | 0.0 | 0.0 | 1.0 | 2.0 | 3.0 |
| pooled 양수 43일 | 1.0 | 1.0 | 2.0 | 2.0 | 3.0 |

전체 q25는 0.0으로 C2의 양수 floor가 되지 못한다. 사전 고지한 방식대로 C2 소량 floor는 **양수 quality 분포 q25=1.0**으로 정의했다. C3 pooled 중앙값도 1.0이다.

## 1.3 Histogram

구간 정의:

- exact zero: `q=0`
- 소량 양수: `0<q<1`
- 중간: `1≤q<2`
- 높음: `q≥2`

| fold | strict pass | exact 0 | 0<q<1 | 1≤q<2 | q≥2 |
|---|---:|---:|---:|---:|---:|
| train_1 | 20 | 0 | 0 | 13 | 7 |
| train_2 | 15 | 5 | 0 | 3 | 7 |
| train_3 | 30 | 17 | 0 | 5 | 8 |
| 전체 | 65 | 22 | 0 | 21 | 22 |

quality=0 strict pass는 train_2와 train_3에 존재한다. 현상 자체는 전 fold 공통은 아니지만 train_3 전용도 아니다. 손익 성격은 fold별로 반대다.

## 1.4 일별 component·raw·final

`all=0`은 ma_align, macd, rsi, bb, volume, news, news_topics, events가 모두 0이라는 뜻이다.

| fold | strict pass day | nonzero component | raw | final | sizing | 현행 체결 |
|---|---|---|---:|---:|---|---|
| train_1 | 2022-07-01 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2022-10-07 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-10-10 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2022-12-16 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-12-19 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-12-20 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-12-21 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-12-22 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-12-23 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_1 | 2022-12-27 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2022-12-28 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2022-12-29 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2022-12-30 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-01-03 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-01-04 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-04-05 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-04-06 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-04-10 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-04-12 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_1 | 2023-04-13 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_2 | 2023-07-25 | all=0 | 0.0 | 0.0 | fixed | Y |
| train_2 | 2023-08-09 | bb=1 | 1.0 | 1.0 | fixed | Y |
| train_2 | 2023-08-22 | rsi=1, bb=1, volume=1 | 3.0 | 3.0 | fixed | Y |
| train_2 | 2023-09-12 | rsi=1, bb=1, volume=1 | 3.0 | 3.0 | fixed | Y |
| train_2 | 2023-09-13 | rsi=1, bb=1 | 2.0 | 2.0 | fixed | Y |
| train_2 | 2023-10-23 | rsi=1, bb=1 | 2.0 | 2.0 | fixed | Y |
| train_2 | 2023-10-25 | rsi=1, bb=1 | 2.0 | 2.0 | fixed | Y |
| train_2 | 2023-10-26 | rsi=1 | 1.0 | 1.0 | fixed | Y |
| train_2 | 2023-10-27 | rsi=1, bb=1 | 2.0 | 2.0 | fixed | Y |
| train_2 | 2023-10-30 | rsi=1 | 1.0 | 1.0 | fixed | Y |
| train_2 | 2023-10-31 | all=0 | 0.0 | 0.0 | fixed | Y |
| train_2 | 2023-11-01 | all=0 | 0.0 | 0.0 | fixed | Y |
| train_2 | 2023-11-02 | all=0 | 0.0 | 0.0 | fixed | Y |
| train_2 | 2023-11-06 | all=0 | 0.0 | 0.0 | fixed | Y |
| train_2 | 2024-06-26 | rsi=1, bb=1 | 2.0 | 2.0 | fixed | Y |
| train_3 | 2024-07-08 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-07-10 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-07-15 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-07-16 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-07-26 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-07-29 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-07-30 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-07-31 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-08-12 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-08-13 | macd=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_3 | 2024-08-19 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-09-25 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-10-10 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-10-11 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-10-16 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_3 | 2024-10-30 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-10-31 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2024-11-04 | macd=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_3 | 2024-11-06 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-11-07 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2024-11-08 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-03-26 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-04-10 | rsi=1, bb=1 | 2.0 | 2.0 | signal_scaled | Y |
| train_3 | 2025-04-28 | rsi=1 | 1.0 | 1.0 | signal_scaled | Y |
| train_3 | 2025-05-12 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-05-13 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-05-14 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-05-15 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-05-16 | all=0 | 0.0 | 0.0 | signal_scaled | N |
| train_3 | 2025-05-29 | ma_align=1 | 1.0 | 1.0 | signal_scaled | Y |

---

# STEP 2 — 정합 조건 시나리오별 후보 변화

## 2.1 시나리오 정의

| 코드 | 조건 | 이번 데이터의 실질 조건 |
|---|---|---|
| 현행 | strict-AND only | 모든 strict pass day |
| C1 | strict-AND ∧ quality > 0 | quality 1·2·3 |
| C2 | strict-AND ∧ quality ≥ positive q25 | `quality ≥ 1.0` |
| C3 | strict-AND ∧ quality ≥ pooled median | `quality ≥ 1.0` |

따라서 **C1=C2=C3**다. 0과 1 사이의 quality 값이 한 건도 없어 세 조건을 손익으로 구분할 수 없다.

표의 `현행 체결`은 현재 fold-best sizing으로 shares>0인 고유 신호일 수다. `비중복 체결`은 현재 sizing으로 실행 가능한 후보만 날짜순으로 처리하고, 이전 포지션 청산 후 cooldown 1거래일까지 건너뛰는 단일포지션 경로의 체결 수다.

## 2.2 후보·체결·EEC

| fold | 시나리오 | 생존 pass day | 현행 sizing 체결 | 비중복 체결 | cluster 수 | EEC | 현행 대비 EEC |
|---|---|---:|---:|---:|---:|---:|---:|
| train_1 | 현행 | 20 | 20 | 5 | 4 | 2.298851 | 기준 |
| train_1 | C1/C2/C3 | 20 | 20 | 5 | 4 | 2.298851 | 0.00% |
| train_2 | 현행 | 15 | 15 | 7 | 6 | 2.528090 | 기준 |
| train_2 | C1/C2/C3 | 10 | 10 | 5 | 5 | 3.125000 | +23.61% |
| train_3 | 현행 | 30 | 13 | 7 | 8 | 4.368932 | 기준 |
| train_3 | C1/C2/C3 | 13 | 13 | 7 | 7 | 5.827586 | +33.39% |

train_3는 pass day가 17일 줄지만 실제 체결과 비중복 체결은 변하지 않는다. 이미 signal-scaled sizing이 zero quality를 0 shares로 만들기 때문이다. train_2는 실제 체결 5건과 비중복 체결 2건이 줄어든다.

## 2.3 생존 거래 손익

모든 strict pass day를 동일 확정 청산 규칙으로 정규화 재집계한 결과다.

| fold | 시나리오 | n | +0.5% 승률 | 평균 손익 | 손익 합 | 음수 | 최악 |
|---|---|---:|---:|---:|---:|---:|---:|
| train_1 | 현행 | 20 | 95.00% | 4.4774% | 89.5471%p | 0 | 0.2083% |
| train_1 | C1/C2/C3 | 20 | 95.00% | 4.4774% | 89.5471%p | 0 | 0.2083% |
| train_2 | 현행 | 15 | 86.67% | 6.7540% | 101.3106%p | 0 | 0.0059% |
| train_2 | C1/C2/C3 | 10 | 80.00% | 6.9547% | 69.5470%p | 0 | 0.0059% |
| train_3 | 현행 strict pass 전체 | 30 | 73.33% | 1.8884% | 56.6525%p | 7 | -20.5050% |
| train_3 | C1/C2/C3 | 13 | 100.00% | 4.9898% | 64.8676%p | 0 | 0.9220% |

train_2는 평균 손익만 0.20%p 오르지만 승률이 6.67%p 낮아지고 손익 합 31.7636%p와 좋은 거래 5건을 잃는다. train_3는 승률·평균·tail risk가 모두 개선된다.

## 2.4 제거 거래 손익

| fold | 제거 n | 승/패 | 승률 | 평균 손익 | 손익 합 | 음수 | 최악 | 판독 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| train_1 | 0 | 0/0 | 해당 없음 | 해당 없음 | 0 | 0 | 해당 없음 | 영향 없음 |
| train_2 | 5 | 5/0 | 100.00% | +6.3527% | +31.7636%p | 0 | +2.3240% | 좋은 거래 제거 |
| train_3 | 17 | 9/8 | 52.94% | -0.4832% | -8.2151%p | 7 | -20.5050% | 평균적으로 잡음 |
| pooled | 22 | 14/8 | 63.64% | +1.0704% | +23.5485%p | 7 | -20.5050% | 전 fold 기준 순수 잡음 아님 |

### train_2 제거일

| strict pass day | 확정 손익 | +0.5% | 사건 위치 |
|---|---:|---|---|
| 2023-07-25 | +3.0681% | 승 | 독립 singleton cluster |
| 2023-10-31 | +9.1953% | 승 | 2023-10~11 cluster |
| 2023-11-01 | +9.4888% | 승 | 2023-10~11 cluster |
| 2023-11-02 | +7.6874% | 승 | 2023-10~11 cluster |
| 2023-11-06 | +2.3240% | 승 | 2023-10~11 cluster |

### train_3 제거일

| strict pass day | 확정 손익 | +0.5% |
|---|---:|---|
| 2024-07-15 | +1.6518% | 승 |
| 2024-07-16 | -2.9527% | 패 |
| 2024-07-26 | +0.0811% | 패 |
| 2024-07-29 | -0.0173% | 패 |
| 2024-07-30 | -3.3118% | 패 |
| 2024-07-31 | -6.4770% | 패 |
| 2024-08-19 | -20.5050% | 패 |
| 2024-09-25 | +2.7320% | 승 |
| 2024-11-06 | +2.5135% | 승 |
| 2024-11-07 | +3.6566% | 승 |
| 2024-11-08 | +5.9469% | 승 |
| 2025-03-26 | +1.0834% | 승 |
| 2025-05-12 | +1.8475% | 승 |
| 2025-05-13 | +4.0702% | 승 |
| 2025-05-14 | +5.3474% | 승 |
| 2025-05-15 | -0.9153% | 패 |
| 2025-05-16 | -2.9664% | 패 |

## 2.5 실제 체결 포트폴리오 관점

현행 실제 체결은 train_1 20 + train_2 15 + train_3 13 = 48건이다. C1/C2/C3를 전 fold에 적용하면 train_2 5건만 실제로 줄고 train_3 실제 체결은 변하지 않는다.

| 지표 | 현행 실제 48건 | C1/C2/C3 실제 43건 | 변화 |
|---|---:|---:|---:|
| +0.5% 승률 | 93.7500% | 93.0233% | -0.7267%p |
| 평균 손익 | 5.3276% | 5.2084% | -0.1192%p |
| 손익 합 | 255.7254%p | 223.9618%p | -31.7636%p |
| 실제 거래수 | 48 | 43 | -5 |

전 fold 공통 quality gate는 실제 체결 성과를 개선하지 않는다.

---

# STEP 3 — 정합 vs 다양성 상호작용

## 3.1 EEC와 절대 cluster 수는 다른 방향일 수 있음

| fold | EEC 변화 | cluster 수 변화 | 독립 사건 보존 판독 |
|---|---:|---:|---|
| train_1 | 2.2989→2.2989 | 4→4 | 완전 보존 |
| train_2 | 2.5281→3.1250 | 6→5 | 집중 완화로 EEC는 상승하지만 좋은 singleton 1개 소실 |
| train_3 | 4.3689→5.8276 | 8→7 | 집중 완화로 EEC는 상승하지만 zero-quality singleton 2개 소실 |

quality 필터는 EEC를 높일 수 있지만 “모든 독립 사건을 보존한다”는 뜻은 아니다. 큰 군집 내부 날짜를 많이 제거하면 concentration이 낮아져 EEC가 오르면서도 absolute cluster count는 감소할 수 있다.

## 3.2 제거되는 독립 사건

- train_2 `2023-07-25`: singleton, +3.0681%, 승
- train_3 `2024-09-25`: singleton, +2.7320%, 승
- train_3 `2025-03-26`: singleton, +1.0834%, 승

세 singleton 모두 +0.5% 승이다. 특히 train_2 singleton은 현재 fixed sizing에서 실제 체결된 거래이므로 전역 quality gate가 다음 단계의 사건 다양성 작업과 직접 충돌한다.

## 3.3 Fold별 판독

### train_1

quality=0이 없어 모든 시나리오가 완전히 같다. 정합 필터의 이익도 손실도 없다.

### train_2

quality=0이 잡음이 아니다. 5건 모두 승리이며 평균 +6.35%다. EEC 증가는 좋은 사건을 제거해 큰 cluster 비중을 낮춘 산술 효과다. 승률 유지와 사건 보존 조건을 실패한다.

### train_3

quality=0 집합은 평균 음수·큰 tail loss로 잡음 성격이 강하다. 해당 날짜는 current signal-scaled sizing에서 이미 0 shares이므로 C1은 실제 거래를 바꾸지 않고 pass-day/EEC bookkeeping만 실행 경로와 맞춘다. 다만 가상 손익상 좋은 singleton 두 건도 제거된다.

## 3.4 다양성 작업과의 충돌

- 전역 C1/C2/C3: 충돌 있음. train_2의 좋은 독립 사건과 실제 거래를 삭제한다.
- signal-scaled 한정 C1-S: 현재 세 fold 기준 실제 거래 변화 0, train_3의 non-executable pass support만 제거한다.
- C2/C3의 추가 이점: 없음. 이번 분포에서는 C1과 같은 집합이다.

---

# STEP 4 — 권고

## 4.1 판정

```text
NO_GLOBAL_QUALITY_GATE
```

전 fold 공통 C1/C2/C3 중 목표를 달성하는 조건은 없다.

- C1: train_3 정합은 개선하지만 train_2 좋은 거래 5건 삭제
- C2: positive q25=1.0이므로 C1과 동일
- C3: pooled median=1.0이므로 C1과 동일

## 4.2 권고 조건

다음 수정 지시서에서 검토할 후보는 전역 quality floor가 아니라 **전략 인지형 C1-S**다.

```text
strict interval pass
AND (
    position_sizing_strategy != "signal_scaled"
    OR quality_score > 0
)
```

수치 근거:

1. train_3 signal-scaled zero-quality 17일은 현재도 shares 0이므로 실제 거래를 바꾸지 않는다.
2. train_3 pass support는 30→13, EEC는 4.3689→5.8276으로 실행 집합과 일치한다.
3. train_2 fixed zero-quality 5건은 보존된다.
4. train_2 실제 승률 86.67%, 평균 +6.7540%, singleton 사건을 그대로 유지한다.
5. 세 fold 실제 48건과 손익은 C1-S에서 변하지 않는다.

이 권고는 새로운 수익 threshold가 아니라 `should_buy` support와 현재 sizing executable path의 정합을 맞추는 것이다.

## 4.3 적용하지 않은 diff 초안

아래는 제안만 하며 이번 작업에서 적용하지 않았다.

```diff
--- a/scripts/research/stage23_rework_20260713/engine/strategies/evaluator.py
+++ b/scripts/research/stage23_rework_20260713/engine/strategies/evaluator.py
@@
-    if strict_entry:
-        should_buy = interval_result.passed
-        reasons = interval_result.reasons + reasons
+    if strict_entry:
+        sizing_strategy = str(
+            getattr(rb, "position_sizing_strategy", "fixed") or "fixed"
+        ).strip().lower()
+        quality_alignment_pass = (
+            sizing_strategy != "signal_scaled"
+            or quality_score > 0.0
+        )
+        should_buy = interval_result.passed and quality_alignment_pass
+        reasons = interval_result.reasons + reasons
+        if interval_result.passed and not quality_alignment_pass:
+            reasons.append(
+                "strict_entry: signal_scaled quality<=0, non-executable"
+            )
```

진단·집계 코드에는 다음 필드를 추가하는 초안을 권고한다.

```diff
+ "strict_interval_pass": interval_result.passed
+ "quality_alignment_pass": quality_alignment_pass
+ "sizing_strategy": sizing_strategy
```

실제 수정 시에는 evaluator 단독 변경이 아니라 daily signal tape, fold-best summary, EEC 집계가 동일 필드를 사용하는지 테스트해야 한다.

## 4.4 부작용·미확인

| 항목 | 상태 |
|---|---|
| AAP 세 fold의 현재 실제 거래 변화 | C1-S는 0건 변화로 산술 확인 |
| 다른 종목·다른 asset type | 미확인 |
| 뉴스·event component가 음수 또는 연속값인 live context | 미확인 |
| market adjustment가 1.0이 아닌 context | 미확인 |
| `kelly_lite`에서 quality=0 의미 | 미확인 |
| gate 변경 후 재학습 시 fold-best 선택 변화 | 미확인 — 이번 작업은 재학습 금지 |
| OOS 성과 | 미확인 |
| 0보다 큰 별도 floor의 우월성 | 미확인 — 현재 양수 최소값이 1.0이라 C1/C2/C3 분리 불가 |

따라서 다음 코드 수정 지시서에서는 우선 C1-S의 실행 정합만 적용하고, 양수 floor 1.0을 수익 필터로 고정하는 것은 보류하는 것이 안전하다.

---

# 보호·상태·Git 감사

## 보호파일 시작·종료 SHA

| 파일 | 시작 SHA256 | 종료 SHA256 | 상태 |
|---|---|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` | 동일 | 불변 |
| `data/_system/market_history.csv` | `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38` | 동일 | 불변 |
| `data/_system/market_history_v2.csv` | `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611` | 동일 | 불변 |

보호파일은 SHA 계산 외 변경하지 않았다.

## Daemon

```text
PID: 494330
state: Sl
start: Sat Jul 11 20:16:00 2026
starttime_ticks: 36014393
command: live_candidate_slots.py daemon --interval 60
상태: 유지
```

## Git 기록

```text
작업 시작 HEAD:
2212765204c9e0a68e3ab8ac32c0fef9a7ad15a9

분석 전 백업 commit:
41797fa

백업 메시지:
strict-AND와 quality 정합 사전 측정 전 기준점 백업: v4 세 fold-best·OHLCV·보호 SHA·daemon 상태를 고정

v4 source commit:
faed59a43761076b9a1544d5f48c0bcf2d867ec8

분석 산출물 commit:
PENDING_AFTER_FIRST_COMMIT
```

분석 산출물 commit SHA를 이 문서에 반영하는 메타데이터 commit은 최종 제출 메시지에 함께 기록한다. 동일 commit 내부에 자기 SHA를 넣는 것은 self-reference라 불가능하다.

## 산출물 SHA

최종 `readout.md` SHA256은 같은 폴더의 `SHA256SUMS.txt`가 정본이다.
