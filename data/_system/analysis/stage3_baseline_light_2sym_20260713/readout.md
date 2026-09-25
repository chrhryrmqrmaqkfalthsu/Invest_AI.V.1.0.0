# Stage 3 baseline 축소 실행 — AAP / POWI

## 실행 성격

이 결과는 정식 규모가 아닌 이번 실행 한정 축소 검증이다.

```text
execution_scale = LIGHT_ONLY_NOT_FULL_STAGE3
```

적용 규모:

```text
qualify: population 10 × generation 3 × train fold 3
entry:   population 10 × generation 3
exit:    population 10 × generation 3
qualify top: 3 / fold
entry pool: 3
entry survivor 최대: 3
exit top: 1 / entry
```

기술 신호:

```text
5-feature strict-AND
ma_trend, macd_hist, rsi, bb_position, volume_ratio
기술 feature D-5 snapshot
시장 context D-1
진입 D+1 open
```

Entry-phase 청산 우선순위:

```text
1. ATR hard stop
2. strict interval-break
3. 7거래일 provisional cap
```

## 최종 판정

| 항목 | AAP | POWI |
|---|---:|---:|
| qualify 통과 | 아니오 | 아니오 |
| all-3-fold qualify 개체 | 0 | 0 |
| entry survivor | 0 | 0 |
| exit candidate | 0 | 0 |
| validate survivor | 0 | 0 |
| 종료 사유 | train_1 통과 개체 0 | train_2 통과 개체 0 |
| 실행 시간 | 54.33초 | 64.70초 |

두 종목 모두 qualify 단계에서 fail-closed 조기 종료됐다. 따라서 entry GA, exit GA, validate는 실행되지 않았다.

```text
AAP: train_1에서 9개 cross-fold 후보 중 qualify 통과 0
POWI: train_1 통과 2개였으나 train_2 통과 0
```

이 결과는 축소 규모이므로 정식 규모에서 통과 가능성이 완전히 없다는 뜻은 아니다. 다만 현재 설정에서 바로 entry·exit로 진행할 근거는 나오지 않았다.

---

# 실행 전 게이트

두 종목 모두 실행 시작 전에 다음 검증을 통과했다.

## 시장 snapshot

```text
primary:
/home/g3000kkw/kingmaker/data/_system/market_history.csv
SHA-256 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
최종 날짜 2026-07-10

v2:
/home/g3000kkw/kingmaker/data/_system/market_history_v2.csv
SHA-256 b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
최종 날짜 2026-06-05
```

Freshness:

```text
뉴욕 기준 확인일: 2026-07-13
기대 최신 거래일: 2026-07-10
snapshot 최신일: 2026-07-10
fresh = true
```

차단 상태:

```text
auto_fetch_enabled = false
auto_regenerate_enabled = false
fail_closed = true
```

## OHLCV snapshot

```text
AAP
/home/g3000kkw/kingmaker/data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
SHA-256 6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717
기간 2020-06-08 ~ 2026-07-06

POWI
/home/g3000kkw/kingmaker/data/_system/analysis/ohlc_snapshot_20260707/POWI_ohlcv.csv
SHA-256 bd683b376899ff9784eb86a807bd154f74502c8f0bb025ceb120b3e16b8fb400
기간 2020-06-08 ~ 2026-07-06
```

외부 OHLCV fetch는 사용하지 않았다.

---

# Qualify 세대별 best fitness

## AAP

| fold | generation 1 | generation 2 | generation 3 | 최종 best |
|---|---:|---:|---:|---:|
| train_1 | 29.821 | 31.582 | 31.582 | 31.582 |
| train_2 | 61.193 | 61.193 | 83.057 | 83.057 |
| train_3 | 26.923 | 65.086 | 65.086 | 65.086 |

각 fold가 정확히 3세대까지 진행했다. 세대 번호가 순차적으로 증가했고 CPU 사용도 유지되어 무한루프나 정지 징후는 없었다.

## POWI

| fold | generation 1 | generation 2 | generation 3 | 최종 best |
|---|---:|---:|---:|---:|
| train_1 | 42.249 | 42.249 | 55.406 | 55.406 |
| train_2 | 40.483 | 40.483 | 43.660 | 43.660 |
| train_3 | 12.854 | 12.854 | 12.854 | 12.854 |

POWI train_3은 3세대 동안 개선이 없어 설정된 no-improvement 기준에 맞춰 3세대에서 정상 종료됐다.

전체 세대 기록:

```text
AAP/generation_best_fitness.jsonl
POWI/generation_best_fitness.jsonl
```

---

# 신호 통계

아래 통계는 각 train fold에서 해당 fold GA의 best 개체를 감사한 값이다. Qualify 개체 본문은 감사 후 폐기했다.

## AAP

| fold | eligible 일수 | strict-AND 통과 | 통과율 | high quality지만 strict 차단 | 실제 trade |
|---|---:|---:|---:|---:|---:|
| train_1 | 251 | 13 | 5.18% | 10 | 6 |
| train_2 | 250 | 41 | 16.40% | 103 | 11 |
| train_3 | 250 | 21 | 8.40% | 66 | 10 |

Feature별 단독 통과율:

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio | 주요 병목 |
|---|---:|---:|---:|---:|---:|---|
| train_1 | 46.61% | 33.47% | 43.03% | 54.18% | 40.64% | macd_hist |
| train_2 | 60.40% | 39.20% | 74.00% | 80.00% | 74.00% | macd_hist |
| train_3 | 51.60% | 56.00% | 53.20% | 32.00% | 52.40% | bb_position |

AAP는 train_1·2에서 `macd_hist`, train_3에서 `bb_position`이 가장 강한 병목이었다.

## POWI

| fold | eligible 일수 | strict-AND 통과 | 통과율 | high quality지만 strict 차단 | 실제 trade |
|---|---:|---:|---:|---:|---:|
| train_1 | 251 | 20 | 7.97% | 86 | 12 |
| train_2 | 250 | 36 | 14.40% | 23 | 12 |
| train_3 | 250 | 41 | 16.40% | 13 | 13 |

Feature별 단독 통과율:

| fold | ma_trend | macd_hist | rsi | bb_position | volume_ratio | 주요 병목 |
|---|---:|---:|---:|---:|---:|---|
| train_1 | 58.17% | 63.35% | 40.24% | 53.78% | 56.57% | rsi |
| train_2 | 54.40% | 51.20% | 45.20% | 54.00% | 55.20% | rsi |
| train_3 | 28.80% | 57.20% | 62.40% | 81.20% | 60.00% | ma_trend |

POWI는 train_1·2에서 `rsi`, train_3에서 `ma_trend`가 가장 강한 병목이었다.

## Quality score override

```text
AAP quality_score override: 0
POWI quality_score override: 0
```

Quality score가 높더라도 strict-AND를 우회해 진입한 사례는 없었다.

전체 신호 통계:

```text
AAP/signal_statistics.jsonl
POWI/signal_statistics.jsonl
```

---

# Trade-level 감사

Qualify best 개체의 entry-phase 거래를 기록했다.

각 row에는 다음이 포함된다.

```text
entry signal date
D+1 open entry fill date / entry price
exit date / exit price
exit reason
holding days
pnl_pct
진입시점 5개 feature
각 interval check
quality score / threshold
market score / sector score / VIX
```

## AAP

```text
총 거래: 27
승: 21
패: 6
평균 수익률: +2.2744%
```

청산 사유:

```text
entry_interval_break: 26
entry_provisional_max_holding: 1
entry_provisional_atr_stop: 0
```

## POWI

```text
총 거래: 37
승: 19
패: 18
평균 수익률: +1.3114%
```

청산 사유:

```text
entry_interval_break: 36
entry_provisional_max_holding: 1
entry_provisional_atr_stop: 0
```

이번 표본에서는 interval-break가 거의 모든 provisional exit를 지배했다. 우선순위 코드는 ATR stop을 먼저 검사했으나 실제 해당 조건이 발동한 거래는 없었다.

전체 거래 상세:

```text
AAP/trade_level_details.jsonl
POWI/trade_level_details.jsonl
```

---

# Qualify 판정 상세

## AAP

```text
고유 후보: 9
train_1 pass count: 0
early stop: required_split_has_zero_passing_candidates
```

Train_1 후보 member score 분포:

```text
min 3.750
mean 50.000
max 95.625
```

일부 후보의 member score는 높았지만, `min_trades >= 5`, `member_score >= 10`, `expectancy >= 2%`를 동시에 만족한 후보가 없었다.

## POWI

```text
고유 후보: 9
train_1 pass count: 2
train_2 pass count: 0
early stop: required_split_has_zero_passing_candidates
```

Member score 분포:

```text
train_1: min 4.375 / mean 50.000 / max 90.000
train_2: min 5.000 / mean 50.000 / max 92.500
```

POWI는 train_1에서 2개가 통과했지만 train_2에서 재현되지 않았다.

---

# CE / BOIL 감사

Survivor가 없어 persisted entry/final 개체 수는 0이다.

```text
one-sided count: 0
missing domain count: 0
validator error count: 0
quality score override count: 0
CE/BOIL violation candidate: 0
```

이는 위반 개체가 살아남지 않았다는 뜻이며, 후보가 0이므로 survivor 기준에서는 공집합 판정이다. Qualify best 개체는 통계·거래 감사 후 본문을 폐기했다.

---

# 보호 파일 및 daemon

시작 SHA와 종료 SHA가 동일하다.

```text
.env
da8173082d40ef3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

data/_system/market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

data/_system/market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

```text
잔존 Stage 3 GA worker: 0
daemon PID 494330: Sl, 유지
```

---

# 산출물 구조

```text
data/_system/analysis/stage3_baseline_light_2sym_20260713/
├── readout.md
├── SHA256SUMS.txt
├── AAP/
│   ├── manifest.json
│   ├── qualify_result.json
│   ├── light_final_summary.json
│   ├── last_run_summary.json
│   ├── generation_best_fitness.jsonl
│   ├── signal_statistics.jsonl
│   ├── trade_level_details.jsonl
│   └── run.log
└── POWI/
    ├── manifest.json
    ├── qualify_result.json
    ├── light_final_summary.json
    ├── last_run_summary.json
    ├── generation_best_fitness.jsonl
    ├── signal_statistics.jsonl
    ├── trade_level_details.jsonl
    └── run.log
```

경량 runner:

```text
scripts/research/stage23_rework_20260713/scripts/research/run_stage3_baseline_light.py
SHA-256 c19a41e52f330e11c2a8eac7dc03ad54f6209fc66031a34d1c1c65d4caef046b
```

사전 백업:

```text
backup/pre_stage3_baseline_light_runner_20260713T104257Z.tar.gz
backup/pre_stage3_baseline_light_runner_20260713T104257Z.manifest.sha256
```

## 결론

```text
축소 Stage 3 baseline에서 AAP·POWI 모두 qualify 실패
entry·exit·validate 미진입
연속성 조건은 추가하지 않은 N=1 baseline 유지
정식 규모 실행 여부는 별도 결정 필요
```
