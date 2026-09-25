# 섹터 오분류가 학습/점수에 실제로 큰 영향을 주는지 read-only 판정

점검 목적: `잘못된 섹터 tech fallback -> 보정 꺼짐 -> CE 학습/점수 왜곡` 가설 검증
점검 범위: frozen 후보 93개 원본 full rulebook, 현재 live slot 점수, CE/XLB 당일 수익률
소스/룰북 수정: 없음

## 최종 판정

```text
INCONCLUSIVE
```

단, `SECTOR_MATTERS_MUCH` 쪽 증거는 아직 약하고, 현재 관측값만 보면 `SECTOR_MATTERS_LITTLE`에 더 가깝다. 하지만 materials/XLB를 정식 feature로 넣어 재학습/재검증한 결과가 없으므로 최종적으로 "영향 작음"을 확정할 수는 없다.

판정 이유:

1. `tech fallback -> use_market_entry_adjustment OFF` 상관은 데이터에서 뚜렷하지 않다.
2. fallback tech 그룹과 명시 감지 그룹 모두 보정 ON/OFF가 거의 반반이다.
3. CE 원본 full rulebook은 `use_market_entry_adjustment=false`라 sector_score를 바꿔도 원본 기준 final_score는 변하지 않는다.
4. CE compact/default 가정에서 보정을 강제로 켜도, CE는 sector_strength_weight가 음수라 tech 100이 오히려 점수를 낮추는 방향이다.
5. CE 7/8 하락률은 XLB 하락률과 거의 같은 크기라, 하루 움직임만으로 "섹터 오분류 학습 탓"이라고 연결할 근거는 약하다.
6. 다만 materials/XLB feature를 추가한 뒤 full 재학습을 돌려보지 않았으므로, 룰북 자체가 달라졌을 가능성은 배제할 수 없다.

## 1. use_market_entry_adjustment 분포: fallback tech vs 명시 감지 그룹

대상: frozen 후보 93개. 원본 full rulebook을 source `final_rulebooks.jsonl`에서 hash matching으로 읽었다.

source 매칭 상태:

```text
candidate_count: 93
source hash_match: 80
source_missing: 13
```

13개는 source 파일이 현재 경로에서 누락되어 원본 full rulebook을 확인하지 못했다. 이 13개는 분포 계산에서 unknown으로 남겼다.

### 1-1. tech fallback 그룹

정의:

```text
_detect_sector_name(name) == tech
reason == default_fallback_to_tech
```

결과:

```text
n: 77
use_market_entry_adjustment ON: 31
use_market_entry_adjustment OFF: 33
unknown/source_missing: 13
ON rate: 40.26%
OFF rate: 42.86%
```

source sector_name 분포:

```text
tech: 64
unknown/source_missing: 13
```

### 1-2. 명시 감지 전체 그룹

정의:

```text
reason != default_fallback_to_tech
```

결과:

```text
n: 16
ON: 8
OFF: 8
unknown: 0
ON rate: 50.00%
OFF rate: 50.00%
```

감지 섹터 분포:

```text
healthcare: 2
tech: 9
energy: 4
industrials: 1
```

### 1-3. 명시 non-tech 그룹만 분리

정의:

```text
healthcare / energy / industrials
explicit tech 제외
```

결과:

```text
n: 7
ON: 4
OFF: 3
ON rate: 57.14%
OFF rate: 42.86%
```

### 1-4. 1번 가설 판정

가설:

```text
잘못된 섹터 tech fallback으로 떨어진 종목일수록 보정이 꺼진다.
```

관측:

```text
fallback tech: ON 31 / OFF 33 / unknown 13
explicit detected all: ON 8 / OFF 8
explicit non-tech: ON 4 / OFF 3
```

판정:

```text
강하게 지지되지 않음.
```

보정 ON/OFF는 fallback 여부와 뚜렷하게 연결되어 있지 않다. fallback tech 그룹도 거의 반반이고, 명시 감지 그룹도 거의 반반이다.

## 2. sector_strength_weight 분포 비교

### 2-1. fallback tech 그룹

```text
n: 64
mean: -0.0152
median: -0.0101
min: -1.0
max: 1.0
positive: 31
negative: 33
zero: 0
abs_mean: 0.5245
p25: -0.5440
p75: 0.4295
```

### 2-2. 명시 감지 전체 그룹

```text
n: 16
mean: -0.1998
median: -0.2236
min: -1.0
max: 1.0
positive: 5
negative: 11
zero: 0
abs_mean: 0.6111
p25: -0.8522
p75: 0.2757
```

### 2-3. 명시 non-tech 그룹만

```text
n: 7
mean: -0.5305
median: -0.8381
min: -1.0
max: 0.8658
positive: 1
negative: 6
zero: 0
abs_mean: 0.7779
p25: -1.0
p75: -0.6423
```

### 2-4. 해석

fallback tech 그룹의 sector_strength_weight는 양수/음수가 거의 반반이다.

```text
positive: 31
negative: 33
```

즉 fallback tech 그룹이라고 해서 sector_strength_weight가 특정 방향으로 강하게 몰려 있지는 않다.

오히려 명시 non-tech 그룹은 음수 쪽이 더 강하다.

```text
explicit non-tech: positive 1 / negative 6
median -0.8381
```

따라서 `잘못된 tech fallback 때문에 보정이 체계적으로 꺼지거나 특정 방향으로 학습됐다`는 가설은 이 분포만으로는 확인되지 않는다.

## 3. CE 시뮬레이션

CE 원본 full rulebook:

```text
ticker: CE
sector_name: tech
use_market_entry_adjustment: false
market_score_weight: 0.008840218406678813
sector_strength_weight: -0.6208615991099308
vix_sensitivity: -1.0
market_adjustment_strength: 0.10684213221952549
```

CE 현재 저장값:

```text
raw_score: 8.363246295633697
final_score: 8.363246295633697
market_score: 85.8
vix_level: 17.95
stored sector_score: 100.0
```

### 3-1. 원본 full rulebook 기준

원본 full rulebook은 `use_market_entry_adjustment=false`이므로 sector_score가 무엇이든 final_score는 변하지 않는다.

```text
sector_score 100 -> final_score 8.363246295633697
sector_score 50  -> final_score 8.363246295633697
sector_score 0   -> final_score 8.363246295633697
sector_score 38.9 -> final_score 8.363246295633697
sector_score 34.48(XLB 60-row 기반 추정) -> final_score 8.363246295633697
```

따라서 현재 원본 룰북 기준으로는:

```text
CE final_score는 materials/XLB sector_score를 반영해도 바뀌지 않는다.
```

### 3-2. compact/default 룰북으로 다시 로드한 가정

frozen/current candidate compact rulebook에는 `use_market_entry_adjustment`, `vix_sensitivity` 등이 빠져 있다. 이것을 `Rulebook.from_dict()`로 다시 로드하면 기본값이 살아난다.

```text
use_market_entry_adjustment: true
vix_sensitivity: 0.0
sector_name: tech
```

이 가정에서 sector_score별 CE 가상 점수는 다음과 같다.

```text
sector_score 100 -> score 7.8141, adjustment 0.9343
sector_score 50  -> score 8.3689, adjustment 1.0007
sector_score 0   -> score 8.9237, adjustment 1.0670
sector_score 38.9(energy current) -> score 8.4921, adjustment 1.0154
sector_score 73.9(industrials current) -> score 8.1037, adjustment 0.9690
```

CE는 `sector_strength_weight`가 음수다. 그래서 sector_score가 높을수록 점수가 낮아지고, sector_score가 낮을수록 점수가 오른다.

즉 CE에 대해서는:

```text
tech 100 fallback은 CE 점수를 부풀린 게 아니라 compact/default 가정에서는 오히려 낮췄다.
```

이 점은 "CE가 tech 100을 먹어서 잘못 높게 뽑혔다"는 직관과 반대다.

### 3-3. XLB 기반 materials sector_score 추정

현재 system market context에는 materials/XLB가 없다. 그래서 동일한 `_sector_strength()` 수식으로 XLB 60-row 수익률을 계산해 가정값을 만들었다.

공식:

```text
sector_strength = clip((ret_60d_pct + 10) * 5, 0, 100)
```

로컬 `/api/real/candles/XLB?interval=1d&refresh=true` 기준:

```text
XLB 60-row return: -3.1032%
materials/XLB sector_strength 추정: 34.4841
```

이 값을 compact/default 가정에 넣으면 CE 점수는 8.492 전후로, 기존 8.363보다 약간 높아지는 쪽이다. 원본 full rulebook 기준으로는 변하지 않는다.

### 3-4. CE 순위 영향

이전 sector component 제거 시뮬레이션에서 CE는:

```text
stored rank: 4
rank_without_sector_component: 4
rank_delta: 0
```

따라서 현재 자료 기준으로는 CE가 sector 처리 때문에 상위권에서 크게 이동한다는 증거가 약하다.

## 4. CE 7/8 하락이 소재 섹터 전반인지 개별인지

로컬 대시보드 daily candles 기준:

```text
CE 2026-07-07 close: 48.68
CE 2026-07-08 close: 47.30
CE return: -2.8348%

XLB 2026-07-07 close: 51.51
XLB 2026-07-08 close: 50.16
XLB return: -2.6209%
```

차이:

```text
CE - XLB: 약 -0.2140%p
```

해석:

```text
CE는 XLB보다 조금 더 약했지만, 하락의 대부분은 소재 섹터 동행으로 설명 가능하다.
```

따라서 2026-07-08 하루 움직임만으로:

```text
CE 하락 = 섹터 오분류 학습의 직접 결과
```

라고 말하기는 어렵다.

## 5. 판정 기준별 정리

### SECTOR_MATTERS_MUCH 조건

다음 중 여러 개가 보여야 한다.

```text
1. fallback tech 그룹에서 보정 OFF가 압도적으로 높음
2. 명시 섹터 그룹에서는 보정 ON이 압도적으로 높음
3. CE가 올바른 materials/XLB 점수를 넣으면 final_score/순위가 크게 바뀜
4. CE 하락이 XLB와 무관하게 개별적으로 과도함
```

현재 관측:

```text
1. 아님. fallback tech ON/OFF 거의 반반.
2. 아님. 명시 감지도 ON/OFF 반반.
3. 아님. CE 원본 full rulebook 기준 final_score 변화 없음. compact/default 가정에서도 순위 급변 증거 약함.
4. 아님. CE는 XLB와 거의 같이 하락.
```

따라서 `SECTOR_MATTERS_MUCH`로 판정할 근거는 부족하다.

### SECTOR_MATTERS_LITTLE 조건

다음이 보여야 한다.

```text
1. sector feature가 대부분 꺼져 있거나 점수에 영향이 작음
2. CE 순위가 크게 안 바뀜
3. CE 하락이 섹터 동행으로 설명 가능
```

현재 관측:

```text
1. CE 원본은 보정 OFF. 전체는 ON/OFF 혼재.
2. CE는 sector 제거 시뮬레이션에서도 4위 유지.
3. CE와 XLB 당일 하락률이 매우 비슷함.
```

따라서 현재 관측만 보면 `SECTOR_MATTERS_LITTLE` 쪽에 더 가깝다.

하지만 materials/XLB를 정식 추가하고 전체 재학습한 결과는 아직 없으므로 확정 판정은 보류한다.

## 6. 최종 판정

```text
INCONCLUSIVE, leaning SECTOR_MATTERS_LITTLE for CE/current snapshot
```

정확한 문장으로 정리하면:

```text
현재 데이터만으로는 CE가 섹터 오분류 때문에 잘못 뽑혔다고 보기 어렵다.
CE 원본 룰북은 시장/섹터 보정이 꺼져 있고, sector 제거/대체 시뮬레이션에서도 CE 순위는 크게 흔들리지 않는다.
오늘 CE 하락도 XLB 하락과 거의 동행했다.
다만 섹터 feature 설계가 부실한 것은 사실이고, materials/XLB를 넣은 재학습 결과가 없으므로 장기적으로는 재검증 대상이다.
```

## 7. 다음 의사결정

오늘 당장 룰북 재생성까지 갈 필요는 낮다.

권장:

```text
오늘:
  CE 포지션은 기존 TP/SL 관리 유지
  섹터 feature는 read-only 판정까지만 완료

다음 작업일:
  1. full rulebook 직렬화/복원 정합성부터 점검
  2. materials/XLB 포함 sector universe 설계
  3. 신뢰 가능한 ticker->sector mapping 확보
  4. fallback tech 제거 방안 설계
  5. 별도 브랜치에서 재학습/재검증
```

중요:

```text
섹터 수정은 즉시 실전 반영 금지.
검증 없이 반영하면 기존 frozen 성과와 다른 전략이 된다.
```
