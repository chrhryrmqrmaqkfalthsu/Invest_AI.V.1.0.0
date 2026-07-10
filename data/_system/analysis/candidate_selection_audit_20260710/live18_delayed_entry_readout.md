# 라이브 후보 18개 개체별 지연 진입 성과 매핑

범위: 코드·설정·룰북·주문 변경 없음. 새 신호 시뮬레이션이나 새 청산 시뮬레이션을 실행하지 않고, 기존 frozen OOS 거래행과 해당 frozen OHLC snapshot만 후처리했다.

산출물:

- `data/_system/analysis/candidate_selection_audit_20260710/live18_delayed_entry_mapping.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/live18_delayed_entry_condition_summary.csv`
- `data/_system/analysis/candidate_selection_audit_20260710/live18_delayed_entry_readout.md`

## 1. 분석 대상과 매칭

대상 live 18개:

```text
BMA, ADMA, BTBT, BMI, BCS, ALGT, CMC, BN, BGC,
ACMR, CRS, BTE, BB, BWXT, ANET, ARKW, CBRL, AEIS
```

매칭 결과:

```text
live 개체: 18
frozen OOS rule_hash 매칭: 18
UNMAPPED: 0
해당 개체의 frozen OOS signal rows: 3,132
D+0 baseline episode: 821
candidate × D+n mapping rows: 288
```

candidate_id와 full rule_hash는 `live18_delayed_entry_condition_summary.csv`에 저장했다.

## 2. 중요 데이터 제한

### 2.1 first_signal_at

frozen 거래 로그에는 라이브의 `first_signal_at`이 저장돼 있지 않다. 따라서 다음 proxy를 사용했다.

```text
first signal date proxy = 같은 candidate_id에서 연속 거래일로 이어진 signal row 묶음의 첫 signal_date
first signal price proxy = 해당 날짜의 frozen OHLC Close
```

연속 signal episode는 D+0 baseline 거래의 exit_date 전까지만 확장했다.

### 2.2 D+n 진입과 청산 성과

새 진입·청산 시뮬레이션은 하지 않았다.

frozen OOS 로그는 신호가 발생한 각 날짜마다 이미 다음 정보를 가진 독립 거래행을 포함한다.

```text
signal_date
D+1 entry_date / entry_price
exit_date / exit_reason
net_pct / MAE / MFE
```

따라서 D+n은 해당 연속 신호 episode의 n번째 frozen 거래행이며, 그 행에 이미 기록된 D+n+1 시가와 기존 청산 결과를 그대로 사용했다.

### 2.3 BB/RSI/MACD/volume/MA 구성점수

frozen 거래 로그에는 다음 구성요소 점수가 저장돼 있지 않다.

```text
BB, RSI, MACD, volume, MA
```

새 재평가 시뮬레이션 금지 원칙에 따라 재계산하지 않았다. CSV에는 전부 다음으로 기록했다.

```text
UNKNOWN_NOT_STORED_IN_FROZEN_LOG
```

`final_score`, `signal_threshold`, `score_ratio`, `raw_score`, `market_adjustment`는 frozen 로그 저장값을 사용했다.

## 3. 조건 탐색 정의

임의의 +5%, +10% 같은 고정 추격 임계값은 넣지 않았다.

각 candidate의 관측 delayed rows에서 다음 경계를 탐색했다.

```text
max delay = 실제 관측 D+n
min score_ratio = candidate별 관측 분포의 0~90% 분위수
max entry_chase = candidate별 관측 분포의 10~100% 분위수
```

각 condition에서는 episode마다 조건을 처음 만족한 delayed row 하나만 선택했다.

표본 기준:

```text
서로 다른 episode n >= 20
```

안전 조건:

```text
delayed 평균 PnL >= 전체 D+0 평균 PnL
AND delayed 승률 >= 전체 D+0 승률
AND delayed 평균 PnL >= 동일 episode paired D+0 평균 PnL
AND delayed 승률 >= 동일 episode paired D+0 승률
```

최종 라벨:

```text
CONDITION_FOUND
NO_SAFE_CONDITION
INSUFFICIENT_SAMPLE
UNMAPPED
```

주의: 경계는 동일 OOS 안에서 탐색한 post-hoc 결과다. 별도 forward validation이 없으므로 바로 라이브 gate로 사용할 수 있는 검증값이 아니다.

## 4. 최종 라벨 분포

```text
CONDITION_FOUND: 8
NO_SAFE_CONDITION: 8
INSUFFICIENT_SAMPLE: 2
UNMAPPED: 0
```

### CONDITION_FOUND

| ticker | baseline n | boundary | condition 성과 | paired D+0 |
|---|---:|---|---|---|
| ADMA | 54 | D+8 이하, ratio≥1.010363, chase≤-1.657102%, n=24 | PnL -0.021%, win 54.17% | PnL -3.090%, win 37.50% |
| AEIS | 56 | D+13 이하, ratio≥1.140968, chase≤13.763933%, n=39 | PnL 4.795%, win 69.23% | PnL 3.583%, win 64.10% |
| ALGT | 42 | D+18 이하, ratio≥1.323828, chase≤0.470986%, n=23 | PnL 5.002%, win 43.48% | PnL 1.891%, win 30.43% |
| BCS | 51 | D+16 이하, ratio≥1.493435, chase≤3.331687%, n=25 | PnL 4.115%, win 80.00% | PnL 3.952%, win 76.00% |
| BMA | 47 | D+9 이하, ratio≥1.002105, chase≤4.526847%, n=24 | PnL 8.403%, win 75.00% | PnL 7.823%, win 62.50% |
| BN | 34 | D+19 이하, ratio≥1.010810, chase≤9.461535%, n=27 | PnL 1.489%, win 59.26% | PnL 0.632%, win 51.85% |
| BWXT | 41 | D+19 이하, ratio≥1.241452, chase≤4.660121%, n=26 | PnL 2.001%, win 65.38% | PnL 1.877%, win 42.31% |
| CMC | 51 | D+9 이하, ratio≥1.002593, chase≤12.857145%, n=31 | PnL 2.769%, win 83.87% | PnL 2.367%, win 83.87% |

### NO_SAFE_CONDITION

```text
ACMR, ANET, ARKW, BB, BGC, BTBT, CBRL, CRS
```

이 개체들은 n≥20 조건 셀을 탐색했지만 평균 PnL과 승률을 global 및 paired D+0 대비 동시에 유지하는 셀이 없었다.

탐색 셀 수:

```text
ACMR 220
ANET 467
ARKW 289
BB 53
BGC 28
BTBT 466
CBRL 437
CRS 984
```

### INSUFFICIENT_SAMPLE

```text
BMI: delayed episode 14
BTE: delayed episode 16
```

최소 20 episode 기준을 만족하지 못해 조건을 만들지 않았다.

## 5. 현재 live 상태와 발견 경계 비교

현재 live snapshot 기준으로 condition 안에 들어오는 개체:

```text
AEIS, BCS, BMA, BN, BWXT, CMC
```

condition은 발견됐지만 현재 live 값이 경계를 벗어나는 개체:

```text
ADMA: 조건 chase≤-1.657%, 현재 +0.542%
ALGT: 조건 chase≤+0.471%, 현재 +4.227%
```

이 비교의 live delay는 2026-07-10 premarket 시점에서 first_signal 날짜를 거래일 D+n으로 환산한 proxy다.

## 6. +5% 초과 추격 상태

현재 canonical live snapshot에서 +5% 초과는 두 개다.

```text
ANET +11.987842%
AEIS +6.600713%
```

### 6.1 ANET

```text
candidate_id: stage3:ANET:fe220620802b
label: NO_SAFE_CONDITION
baseline episode n: 72
valid delayed episode n: 44
tested condition cells n>=20: 467
safe cells: 0
```

D+0 baseline:

```text
avg PnL +0.639%
win 65.28%
avg MAE -5.320%
avg MFE +5.990%
```

표본이 충분한 exact delay:

| delay | n | avg chase | avg PnL | win |
|---:|---:|---:|---:|---:|
| D+1 | 44 | -0.026% | +0.244% | 50.00% |
| D+2 | 24 | +0.084% | -0.494% | 50.00% |

현재 +11.99%를 상한으로 하여 각 episode에서 처음 만족한 delayed row를 선택한 진단:

```text
n=43
avg PnL +0.174%
win 48.84%
avg MAE -4.774%
avg MFE +6.732%
paired D+0 avg PnL -0.143%
paired D+0 win 62.79%
```

해석:

- 평균 PnL은 paired subset보다 높지만 전체 baseline +0.639%보다 낮다.
- 승률은 global baseline 65.28% 및 paired baseline 62.79%보다 크게 낮다.
- 따라서 현재 추격률 부근을 안전하다고 판정할 수 없다.
- ANET은 `NO_SAFE_CONDITION` 유지가 맞다.

### 6.2 AEIS

```text
candidate_id: stage3:AEIS:6e26f08a7c6d
label: CONDITION_FOUND
boundary: D+13 이하, ratio≥1.140968, chase≤13.763933%, n=39
현재 proxy: D+2, ratio 1.491945, chase +6.600713%
live_inside_boundary=True
```

D+0 baseline:

```text
avg PnL +4.137%
win 62.50%
avg MAE -6.217%
avg MFE +10.048%
```

condition 성과:

```text
n=39
avg PnL +4.795%
win 69.23%
paired D+0 avg PnL +3.583%
paired D+0 win 64.10%
```

현재 +6.60% chase만 상한으로 한 진단:

```text
n=37
avg PnL +5.114%
win 70.27%
avg MAE -5.658%
avg MFE +10.356%
paired D+0 avg PnL +3.116%
paired D+0 win 62.16%
```

데이터상 AEIS는 현재 추격률이 발견된 조건 경계 안에 위치한다.

다만 이전 가격 신선도 분석에서 AEIS의 live snapshot 가격은 current-session 1분봉이 아니라 이전 세션 가격이었다. 따라서 `+6.60%` 위치는 reference-only이며, 실제 주문 판단에는 fresh execution quote가 필요하다.

## 7. mapping CSV 읽는 법

`live18_delayed_entry_mapping.csv`는 candidate별 한 행이며 `daily_delay_map` 안에 D+0부터 관측 마지막 D+n까지 들어 있다.

예시:

```text
D2[n=24;ok20=1;score=3.877;ratio=1.469;chase=0.084;
pnl=-0.494;win=50.0;mae=-7.071;mfe=6.087;
exit=breakeven_stop:11/stop_loss:4/take_profit:3/time_out:1/trailing:5]
```

필드 의미:

```text
n      = 해당 D+n episode 표본 수
ok20   = n>=20 여부
score  = 평균 final_score
ratio  = 평균 final_score / threshold
chase  = first signal close 대비 D+n+1 entry open 평균 괴리율
pnl    = 평균 net_pct
win    = 승률
mae/mfe
exit   = frozen exit_reason 분포
```

구성요소 점수는 저장돼 있지 않아 별도 column에 UNKNOWN으로 표시했다.

## 8. 해석상 주의

1. `CONDITION_FOUND`는 동일 OOS를 탐색하고 평가한 결과라 data-mining 가능성이 있다.
2. 개체별 경계를 라이브에 적용하려면 별도 holdout 또는 walk-forward 검증이 필요하다.
3. 표본 n은 거래행 수가 아니라 서로 다른 first-signal episode 수다.
4. first_signal_price는 frozen signal-date Close proxy다.
5. 기업행위로 entry chase 절대값이 100%를 넘는 row는 조건 탐색에서 제외했다.
6. 현재 live chase는 candidate snapshot 기준이며 broker execution quote와 다를 수 있다.

## 9. 최종 판정

```text
18개 rule_hash 전부 frozen OOS 매칭.
조건 존재 후보: 8
안전 조건 없음: 8
표본 부족: 2
UNMAPPED: 0
```

현재 가장 중요한 개체별 결론:

```text
ANET +11.99%:
NO_SAFE_CONDITION. 지원되는 D+1/D+2에서도 승률이 65.3% -> 50.0%로 하락.
현재 chase 상한 진단도 win 48.84%로 baseline을 훼손.

AEIS +6.60%:
CONDITION_FOUND 경계 안에 위치하고 frozen 후처리 성과는 baseline 유지/개선.
단, live 가격이 stale reference였으므로 실제 execution-time 위치는 UNKNOWN.
```

이번 작업은 frozen 로그 후처리만 수행했으며 코드, 룰북, 설정, 주문, 라이브 후보 상태를 변경하지 않았다.
