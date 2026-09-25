# 위험구조 게이트 1단계 — HIGH_VOL × 거래량 누락 진입

- 기준일: 2026-07-11 KST
- 원본: Stage2 survivors 1,162개 + Stage3 final_rulebooks 15,909개
- 전체 개체: **17,071개**, ticker 531개
- 작업: 저장된 룰·기존 변동성 분류·기존 ATR 이력만 사용한 read-only 계산
- 원본 수정·라이브 변경·재학습·주문·삭제: **0건**

## 1. 판정 규칙

이번 1단계는 HIGH_VOL 개체만 본다.

```text
HIGH_VOL
AND
volume을 제외한 MA/MACD/RSI/BB 부분집합 중 하나가
보수적 시장배수 상한에서 signal_threshold를 넘을 수 있음
→ RISK
```

거래량 가중치가 0인지 여부는 필요조건이 아니다.

- `weight_volume_surge=0`이어도 다른 지표로 진입 가능하면 RISK.
- 거래량 가중치가 양수여도 거래량 없이 다른 지표로 진입 가능하면 RISK.
- 필요한 비-volume 지표 수 N은 제한하지 않았다. 1개·2개뿐 아니라 3개·4개가 필요해도 거래량 없이 진입 가능하면 RISK다.

시장배수는 앞 분석과 동일한 보수적 상한을 적용했다.

```text
use_market_entry_adjustment=True:
  multiplier = 1 + clamp(market_adjustment_strength, 0, 1)

False:
  multiplier = 1.0
```

뉴스·토픽·이벤트·crash bonus는 0으로 두었다. 따라서 RISK로 잡힌 개체는 core 지표만으로도 거래량 없이 진입 가능한 구조다. 반대로 미포착 개체는 비-core 가산점까지 포함해 완전히 안전하다는 뜻은 아니다.

## 2. 변동성 분류 방식

### 2.1 정확 분류가 존재하는 ticker

기존 frozen 변동성 분석은 IS 거래가 30건 이상인 93개 후보를 `avg_std20` 삼분위로 분류했다.

```text
LOW/MID 경계  = 0.0242767496
MID/HIGH 경계 = 0.0344724649
```

93개 후보는 91개 ticker에 해당하며, 동일 ticker 내 그룹 충돌은 0건이었다. 이 91개 ticker는 기존 정확 분류를 그대로 사용했다.

- exact ticker: 91개
- exact 분류가 적용된 원본 개체: 4,966개

### 2.2 정확 분류가 없는 ticker

나머지 440개 ticker는 전체 ticker용 frozen `avg_std20` 분류가 존재하지 않는다. 이전 BOIL 한계를 그대로 인정하고 ATR proxy를 사용했다.

기존 그룹별 ATR 중앙값:

| 그룹 | ATR% 중앙값 |
|---|---:|
| LOW_VOL | 3.4512% |
| MID_VOL | 4.9365% |
| HIGH_VOL | 6.1146% |

Proxy 경계:

```text
LOW/MID  = (3.4512 + 4.9365) / 2 = 4.1938%
MID/HIGH = (4.9365 + 6.1146) / 2 = 5.5255%
```

각 ticker에 속한 원본 개체들의 `history_avg_atr_pct` 중앙값을 계산해 하나의 ticker 그룹으로 고정했다.

- proxy ticker: 440개
- proxy가 적용된 원본 개체: 12,105개
- ATR 값이 없어 UNJUDGED인 ticker: 0개

### 2.3 최종 ticker 분포

| 그룹 | ticker 수 | 원본 개체 수 |
|---|---:|---:|
| LOW_VOL | 382 | 10,263 |
| MID_VOL | 89 | 3,724 |
| HIGH_VOL | **60** | **3,084** |

한계: 440개 proxy ticker의 그룹은 exact frozen IS std20 삼분위가 아니라 과거 거래 진입 ATR 중앙값 기반이다. 따라서 다음 단계 전에 전체 ticker OHLC 기반 exact 분류를 별도로 구축하면 분류 경계가 바뀔 수 있다.

전체 ticker 분류: `high_vol_volume_blind_ticker_classification.csv`

## 3. 1단계 위험구조 결과

| 항목 | 개체 수 | 원본 대비 | HIGH_VOL 대비 |
|---|---:|---:|---:|
| 원본 전체 | 17,071 | 100% | — |
| HIGH_VOL | 3,084 | 18.07% | 100% |
| 거래량 없이 진입 가능 RISK | **3,036** | **17.78%** | **98.44%** |
| HIGH_VOL이지만 거래량 없이 진입 불가 | 48 | 0.28% | 1.56% |

단계별 RISK:

| 단계 | RISK |
|---|---:|
| Stage2 | 24 |
| Stage3 | 3,012 |

완성도 검증 원본 3,174개만 보면:

- HIGH_VOL: 317개
- RISK: 305개
- 완성 원본 대비: 9.61%
- 완성 HIGH_VOL 대비: 96.21%

### 거래량 없이 필요한 최소 지표 수

HIGH_VOL 3,084개 안에서:

| 비-volume 최소 지표 수 | 개체 수 |
|---|---:|
| 1 | 1,401 |
| 2 | 1,468 |
| 3 | 163 |
| 4 | 4 |
| 불가능 | 48 |

N 제한을 제거했기 때문에 3개·4개가 필요한 167개도 RISK에 포함된다.

### 범위 판정

이 조건은 기존 N≤2 전역 규칙 90.18%보다 훨씬 좁다.

```text
기존 N≤2: 원본 90.18%
이번 1단계: 원본 17.78%
```

따라서 전역 시스템을 거의 멈추는 수준은 아니다. 다만 HIGH_VOL 내부에서는 98.44%가 걸리고, 60개 HIGH_VOL ticker 모두 적어도 하나의 RISK 개체를 가진다.

즉 이 게이트는 전체 범위는 통제되지만 HIGH_VOL 내부에서는 거의 보편적인 구조 규칙이다. HIGH_VOL 후보군 안에서 추가 선별력을 만들려면 이후 단계에서 유동성·거래량 임계 발현도·섹터 등 다른 축이 필요하다.

전체 HIGH_VOL 판정표: `high_vol_volume_blind_all_high_vol.csv`  
RISK 전체 목록: `high_vol_volume_blind_risk_candidates.csv`

## 4. BOIL parity

기존 BOIL형 개체: 444개

기존 BOIL 분류는 exact ticker가 없을 때 후보별 ATR proxy를 사용했다. 이번에는 ticker별 ATR 중앙값으로 그룹을 하나로 고정했기 때문에 40개가 MID/LOW로 재분류됐다.

| 최종 그룹 | BOIL 개체 |
|---|---:|
| HIGH_VOL | **404** |
| MID_VOL | 31 |
| LOW_VOL | 9 |

이번 1단계의 HIGH_VOL BOIL 포섭:

```text
404 / 404 = 100%
```

MID/LOW로 재분류된 40개는 구조 미포착이 아니라 1단계 범위 밖이다.

앞 N≤2 분석에서 놓쳤던 개체 중:

- `stage2:GSAT:7d070e5b4580`은 최종 HIGH_VOL이며 비-volume 3개로 진입 가능해 이번 단계에서 잡힌다.
- CRM 3개는 ticker 중앙 ATR 기준 LOW_VOL로 재분류되어 이번 1단계 범위 밖이다.

따라서 N 제한을 없앤 이번 조건은 **최종 HIGH_VOL로 확정된 BOIL 개체를 전부 포섭**한다.

## 5. CE parity

기존 CE형 FAIL: 7개

| 최종 그룹 | CE 개체 |
|---|---:|
| HIGH_VOL | **4** |
| MID_VOL | 1 |
| LOW_VOL | 2 |

HIGH_VOL CE 포섭:

```text
4 / 4 = 100%
```

포섭된 HIGH_VOL CE:

```text
stage3:BB:f1bdfe7f8ad9
stage3:BOIL:9044dc2c67a3
stage3:BTE:4ba9af200f79
stage3:CDE:ceb9fe0512dc
```

ANET·CE·CWK는 MID/LOW라 이번 단계에서 판정하지 않는다.

상세 parity: `high_vol_volume_blind_boil_ce_parity.csv`

## 6. exact/proxy별 범위

HIGH_VOL 개체 구성:

| 분류 방식 | HIGH_VOL | RISK | 거래량 없이 진입 불가 |
|---|---:|---:|---:|
| Exact frozen IS std20 | 1,867 | 1,846 | 21 |
| ATR ticker-median proxy | 1,217 | 1,190 | 27 |

Proxy와 exact 모두 HIGH_VOL 내부 RISK 비율이 높다. 따라서 98.44%라는 결과가 proxy 분류만의 산물은 아니다.

## 7. 판정 해석

1. 원본 전체의 17.78%만 차단 후보라 기존 90% 전역 조건보다 범위가 크게 줄었다.
2. 최종 HIGH_VOL BOIL 404개와 HIGH_VOL CE 4개를 모두 잡는다.
3. 거래량 weight가 양수여도 거래량 없이 threshold를 넘을 수 있으면 포함한다.
4. N을 제한하지 않아 기존 N≤2에서 빠졌던 HIGH_VOL GSAT도 잡는다.
5. HIGH_VOL 내부에서는 98.44%가 걸리므로, 이 조건은 “HIGH_VOL에서 거래량이 반드시 필요해야 한다”는 매우 강한 구조 원칙이다.
6. 실제 시장배수가 항상 상한에 도달하는 것은 아니다. 본 결과는 위험 과소평가를 막기 위한 가능성 상한 판정이다.
7. MID/LOW는 의도적으로 OUT_OF_SCOPE이며 다음 단계에서 별도 축으로 다뤄야 한다.
8. 차단 여부는 성과가 아니라 구조로 판정했다.

## 8. 산출물

- `high_vol_volume_blind_ticker_classification.csv` — 531 ticker 분류와 exact/proxy 근거
- `high_vol_volume_blind_all_high_vol.csv` — HIGH_VOL 3,084개 전체 판정
- `high_vol_volume_blind_risk_candidates.csv` — RISK 3,036개 전체 목록
- `high_vol_volume_blind_boil_ce_parity.csv`
- `high_vol_volume_blind_scope_summary.csv`
- `high_vol_volume_blind_summary.json`
- `run_high_vol_volume_blind_gate_analysis.py`
- `high_vol_volume_blind_gate_readout.md`

원본과 라이브 후보 파일은 불변이다.
