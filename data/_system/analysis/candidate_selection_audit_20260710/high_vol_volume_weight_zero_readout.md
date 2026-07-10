# 고변동성 종목 거래량 가중치 소실 원인 추적

- 기준일: 2026-07-10
- 참조: `9282ee1`
- 분석 모드: read-only 원본 룰북/원본 데이터, 재학습 없음, 주문 없음
- near-zero 정의: `abs(weight_volume_surge) <= 0.05`
- 종합 판정: **MIXED**

## 0. 판정부터

**BOIL의 `weight_volume_surge=0`은 저장·로드·실행 중에 사라진 값이 아니다. Stage3 진입 GA가 선택한 룰에 이미 0으로 저장되어 있었고, Stage3 청산 학습과 라이브 후보 적재가 그 값을 그대로 보존했다.** 따라서 HIGH_VOL 분기, 직렬화 누락, 청산 룰 합성, 진입 점수 정규화가 거래량 가중치를 지운 런타임 버그는 확인되지 않았다.

다만 이를 순수한 `LEARNED_ZERO`라고도 볼 수 없다. BOIL 룰은 학습기간에 `volume_surge_ratio=2.50`을 충족한 날이 **0/250일**, 선택 진입 19건의 신호일에서도 **0건**이었다. 즉 GA는 거래량의 성과 상관을 보고 0으로 깎은 것이 아니라, 거래량 유전자가 한 번도 발현되지 않아 적합도에서 식별되지 않는 상태에서 하한 클리핑으로 만들어진 0을 그대로 선택했다. 활동도/식별성 검사가 없는 구조적 설계 구멍이 학습 결과와 결합된 사례다.

따라서 판정은 다음과 같다.

> **MIXED = 합법적인 GA 학습 산출물 + 비활성 유전자 식별성 결손.**  
> HIGH_VOL 전용 소실 코드는 없지만, 임계값이 희귀 이벤트 영역으로 올라간 상태에서 가중치가 0으로 클리핑되어도 선택·배포를 막지 않는 구조는 시스템 전반에 존재한다.

## 1. BOIL 가중치 소실 경로

### 1.1 파라미터 생성과 0 발생 메커니즘

- `engine/strategies/rulebook.py:28`: 기본값은 `weight_volume_surge=1.0`.
- `engine/strategies/rulebook.py:154`: GA 범위는 `0.0~2.0`.
- `engine/strategies/rulebook.py:179`: `volume_surge_ratio` 범위는 `1.2~2.5`.
- `engine/learning/genetic.py:172-183`: 실수 유전자는 가우시안 변이 후 `max(lo, min(hi, val))`로 경계 클리핑된다. 하한 아래로 내려간 변이는 정확히 `0.0`이 된다.
- 랜덤 초기화는 연속 균등분포이므로 정확한 0이 직접 뽑힐 가능성은 사실상 없고, 관측된 다수의 exact-zero는 하한 클리핑 후 교차·선택으로 보존된 것으로 보는 것이 가장 일관된다.
- GA 로그에는 세대별 최고/평균 적합도만 있고 개별 유전자 계보가 저장되지 않아, BOIL 0이 최초 생성된 정확한 세대와 부모 룰은 복원할 수 없다.

### 1.2 첫 영속 확인 지점

`exp_batch_stage123_2009_20260616_full/tickers/BOIL/stage3/entry_rulebooks.jsonl`의 진입 룰:

- entry hash: `5e5a7b98d720ead8051a143672006b09eef095e0445780869fbcd03f0874b6e9`
- `weight_volume_surge=0.0`
- `volume_surge_ratio=2.5`
- `pool_rank=85`, `selected_rank=17`
- train expectancy `6.4516268424%`, trade count `19`

즉 첫 저장 확인 시점부터 이미 0이다. Stage3 exit 학습이 만든 값이 아니다.

### 1.3 최종 룰로 전달

`engine/pipeline/exit_gene.py:68-82`의 `apply_exit()`는 `EXIT_FIELDS`만 덮어쓰고 진입·포지션·시장반응 필드는 그대로 복사한다. 최종 라이브 룰:

- final hash: `9044dc2c67a3d3bc0c5d93cece5f266c162c4f0ecf3ed5bc8ded1edbafb4bd67`
- `weight_volume_surge=0.0`
- `volume_surge_ratio=2.5`

따라서 Stage3 청산 최적화, 최종 룰북 저장, 후보 우주 적재 과정에서 소실된 것이 아니다.

### 1.4 런타임 반영

`engine/strategies/evaluator.py:124-129`는 다음과 같이 직접 계산한다.

```python
vol_ok = is_volume_surge(row, threshold=rb.volume_surge_ratio)
s_vol = rb.weight_volume_surge * (1.0 if vol_ok else 0.0)
```

HIGH_VOL 전용 분기, 거래량 가중치 정규화, 후단 클리핑은 없다. 따라서 실시간 `Volume_ratio=2.50`이 임계값을 충족해도 BOIL은 `0.0 * 1.0 = 0.0`이 되어 진입 점수에 반영되지 않는다.

## 2. 학습 데이터에서 거래량이 실제로 무관했는가

### 2.1 BOIL 선택 룰의 활동도

동결 OHLC 스냅샷에서 Stage3 진입 학습기간 `2024-07-01~2025-06-30`을 재계산했다.

| 항목 | 결과 |
|---|---:|
| 학습 거래일 | 250 |
| `Volume_ratio >= 2.50` 일수 | 0 |
| 선택 진입 신호일 | 19 |
| 진입 신호일 중 `>=2.50` | 0 |
| 선택 진입 신호일 최대 Volume ratio | 1.635279 |

결론: 이 룰의 거래량 항은 학습 적합도 계산에서 한 번도 켜지지 않았다. 따라서 “과거 데이터에서 거래량-성과 상관이 없어서 0으로 학습됐다”는 해석은 근거가 부족하다. 더 정확한 설명은 **임계값이 관측영역 밖에 있어 가중치가 비식별이었고, GA 경계 클리핑으로 생긴 0이 적합도 손실 없이 살아남았다**이다.

### 2.2 원시 전방수익 참고

같은 학습기간 BOIL 전체 거래일의 단순 종가 전방수익은 다음 방향을 보였다.

- 임계값 1.20: 급증일 50일, 5일 전방수익 평균 `+2.0379%`; 비급증일 `-0.3278%`.
- 임계값 1.50: 급증일 15일, 5일 전방수익 평균 `+3.2584%`; 비급증일 `-0.0534%`.
- 임계값 2.50: 관측 0일.

이는 전략 조건부 반사실이 아니라 원시 기술통계이므로 인과 증거는 아니다. 다만 “BOIL에서 거래량이 성과와 무관했다”는 단정과는 맞지 않으며, 선택된 2.50 임계값이 유효 표본을 제거했다는 해석을 지지한다.

## 3. 동일 현상 전수 스캔

### 3.1 라이브 93개

HIGH_VOL 31개 중 near-zero는 **8개(25.81%)**, exact-zero는 **7개(22.58%)**였다.

| ticker | weight | ratio | candidate |
|---|---:|---:|---|
| AMBA | 0.000000 | 1.200000 | `stage3:AMBA:5e057e3cfc2d` |
| BILL | 0.002571 | 1.905776 | `stage3:BILL:eb2fe6599396` |
| BNTX | 0.000000 | 2.500000 | `stage3:BNTX:d667608bc166` |
| BOIL | 0.000000 | 2.500000 | `stage3:BOIL:9044dc2c67a3` |
| CCL | 0.000000 | 1.985506 | `stage3:CCL:82d5bafb7e78` |
| CGC | 0.000000 | 2.293778 | `stage3:CGC:650b7578c8c7` |
| CHWY | 0.000000 | 1.967400 | `stage3:CHWY:a46752a05345` |
| CRMD | 0.000000 | 1.383712 | `stage3:CRMD:1a48a9a1b768` |

참조 `9282ee1`의 “BOIL 단일 사례”는 `ratio>=1.5`와 `weight_bb_near_lower>=1.5`를 동시에 추가한 좁은 스캔 결과다. 본 지시의 조건인 `HIGH_VOL + volume weight≈0`만 적용하면 BOIL은 유일하지 않다.

### 3.2 원천 풀

93개 후보의 원천 티커를 기준으로 스캔했다.

| 그룹 | 풀 | 전체 | near-zero | 비율 | exact-zero |
|---|---|---:|---:|---:|---:|
| LOW_VOL | Stage2 | 9,000 | 1,019 | 11.32% | 866 |
| MID_VOL | Stage2 | 9,000 | 705 | 7.83% | 566 |
| HIGH_VOL | Stage2 | 8,700 | 940 | 10.80% | 781 |
| LOW_VOL | Stage3 entry | 498 | 62 | 12.45% | 62 |
| MID_VOL | Stage3 entry | 520 | 73 | 14.04% | 65 |
| HIGH_VOL | Stage3 entry | 620 | 101 | 16.29% | 96 |

HIGH_VOL의 Stage2 원천 풀에서는 31개 티커 중 26개가 적어도 하나의 near-zero 룰을 가졌고, Stage3 선택 진입 풀에서는 11개 티커가 해당됐다. Stage3 final의 303건은 고유 진입 룰 101개가 청산 변형 3개씩 반복된 값이므로 영향 개체 수는 101개로 해석해야 한다.

이 현상은 HIGH_VOL에만 존재하지 않는다. 다만 Stage3 entry의 near-zero 비율은 HIGH_VOL이 16.29%로 LOW 12.45%, MID 14.04%보다 높다. HIGH_VOL 전용 코드 결함의 증거는 아니지만, 고변동성 후보에서도 무시할 수 없는 범위다.

`high_vol_volume_weight_zero_entities.csv`에는 라이브 8개와 Stage3 고유 진입 풀 near-zero 101개를 전수 수록했다. 상류 Stage2 GA population 940개는 배포 개체가 아니라 진화 중간 개체이므로 `scan_summary.csv`에 범위·빈도·exact-zero를 집계했으며 원본 위치는 각 티커의 `stage2/rulebooks_all.jsonl`이다.

## 4. 성과 연계

원본 동결 OOS 청산 거래 `oos_trades_frozen.csv`를 사용했다. HIGH_VOL near-zero 8개와 HIGH_VOL positive-weight 23개를 비교했다.

| 지표 | near-zero 8개 | positive 23개 | 차이 |
|---|---:|---:|---:|
| 후보 동일가중 평균 PnL | 1.2258% | 3.9136% | **-2.6877%p** |
| 후보 동일가중 승률 | 51.7061% | 58.6205% | **-6.9144%p** |
| 후보 동일가중 평균 MAE | -7.3458% | -7.8468% | +0.5010%p |
| 후보 동일가중 평균 MFE | 9.1439% | 14.0211% | **-4.8772%p** |
| 거래수 | 1,310 | 3,250 |  |

후보별 평균 PnL 차이를 후보 단위 부트스트랩 20,000회로 본 95% 구간은 `-4.5737~-0.8365%p`였다. 표본 내 연관은 음수다.

청산 분포도 near-zero 패턴은 time-out 비중이 높고 trailing 비중이 낮았다.

- near-zero: time_out 583/1,310 = 44.50%, trailing 116/1,310 = 8.85%.
- positive: time_out 989/3,250 = 30.43%, trailing 729/3,250 = 22.43%.

BOIL 자체의 원본 OOS 성과는 135건, 승률 54.81%, 평균 PnL 2.0389%, 평균 MAE -7.0837%, 최악 MAE -49.2770%, 평균 MFE 9.2857%였다.

중요한 제한: 이 비교는 관측적 그룹 비교다. near-zero 후보는 종목·룰·청산 구조가 동시에 다르므로, 성과 격차 전체를 거래량 무시의 인과효과로 돌릴 수 없다. 재학습 및 반사실 재평가가 금지된 조건에서는 “실제 악화와 연계돼 있다”까지는 말할 수 있지만 “거래량 0이 악화의 유일 원인”이라고 확정할 수 없다.

## 5. 최종 분류 근거

### LEARNED_ZERO만으로 분류하지 않은 이유

- BOIL의 선택 임계값 2.50은 학습기간에 한 번도 발현되지 않았다.
- 따라서 거래량 신호의 성과 기여가 낮아 0으로 벌점 받은 것이 아니라, 가중치가 적합도에 영향을 주지 못하는 비식별 상태였다.
- 낮은 임계값에서 원시 전방수익은 오히려 양의 방향이었다.

### STRUCTURAL_DEFECT만으로 분류하지 않은 이유

- HIGH_VOL 조건에서 값을 강제로 0으로 만드는 코드가 없다.
- 저장·로드·exit 합성·평가 과정에서 가중치가 손실되지 않는다.
- 동일한 exact-zero 점질량은 LOW/MID/HIGH 전 그룹에 나타난다.
- 0은 현재 파라미터 범위 안의 합법 값이며 GA가 선택한 값이다.

### MIXED로 분류한 이유

1. GA가 합법적으로 0을 만들고 선택했다는 점은 학습 결과다.
2. 그러나 임계값-가중치 조합의 활동도/식별성을 검사하지 않고, 하한 클리핑으로 exact-zero 점질량을 만들며, 비활성 거래량 유전자가 좋은 적합도로 통과할 수 있다는 점은 구조적 설계 결손이다.
3. 그 결과 BOIL 단일 종목이 아니라 라이브 HIGH_VOL 8개, Stage3 진입 풀 101개에 같은 형태가 존재한다.
4. 해당 라이브 패턴은 표본 OOS에서 positive-weight HIGH_VOL보다 평균 PnL·승률·MFE가 낮았다. 인과 확정은 아니지만 무해하다고 볼 근거도 없다.

## 6. 영향 범위

- 영향 기능: 거래량 급증 점수 항 하나. 다른 기술/뉴스/시장 항은 정상 계산된다.
- 영향 조건: `weight_volume_surge≈0`인 모든 룰. 임계값 충족 여부와 무관하게 거래량 기여가 사라진다.
- 확대 조건: 높은 `volume_surge_ratio` 때문에 학습 중 발현 횟수가 적거나 0이면 가중치가 비식별이 되어 0 클리핑이 적합도 손실 없이 생존하기 쉽다.
- 범위: HIGH_VOL 전용은 아니나, 라이브 HIGH_VOL의 25.81%가 near-zero라 고변동성 운용에서 실제 노출이 크다.

## 7. 산출물

- `high_vol_volume_weight_zero_boIL_trace.csv`: BOIL 산출·저장·실행 경로.
- `high_vol_volume_weight_zero_entities.csv`: 라이브 8개 + Stage3 고유 진입 풀 101개 전수 목록.
- `high_vol_volume_weight_zero_scan_summary.csv`: LOW/MID/HIGH 및 Stage2/Stage3 풀 전수 집계.
- `high_vol_volume_weight_zero_train_activity.csv`: 라이브 8개 학습기간 거래량 항 발현도.
- `high_vol_volume_weight_zero_performance_linkage.csv`: 후보별 및 패턴별 원본 OOS 성과·MAE·MFE·exit 분포.

## 8. 감사 메모

진단 중 BOIL 실행 재현을 한 차례 호출했을 때 무시 대상 시장 컨텍스트 캐시가 갱신될 수 있다는 로그가 발생했다. 즉시 `git status`와 tracked diff를 확인했고 변경은 0이었다. 해당 재현 결과는 데이터 시점 차이로 저장 거래수와 불일치해 폐기했으며, 본 판정과 모든 수치는 동결 OHLC·동결 OOS 거래·원본 JSONL만 사용했다. 코드, 설정, 룰북, 원본 룰풀은 수정하지 않았다.
