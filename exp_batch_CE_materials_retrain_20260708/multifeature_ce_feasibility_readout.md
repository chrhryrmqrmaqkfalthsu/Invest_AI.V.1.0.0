# CE 멀티 피처 실험 실행 가능성 read-only 확인

요청 범위: DOW, LYB, EMN, DD, USO, XLI 가격 히스토리 캐시 존재/기간 확인 및 look-ahead 없는 화학 피어 바스켓 선정 절차 정리
실행/수정 제약: 학습 실행 없음, 데이터 다운로드 없음, 소스 수정 없음
확인 기준 구간: 2020-05-18 ~ 2026-06-12

## 결론 요약

DOW, LYB, EMN, DD, USO, XLI 6개 모두 기존 `20260616_stage01_full` 캐시에 존재하고, 확인 기준 구간을 커버한다.

```text
캐시 기준 경로:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache

결측 확인:
2020-05-18 ~ 2026-06-12 구간 Close 결측 0
```

따라서 데이터 가용성만 놓고 보면 CE 멀티 피처 실험은 실행 가능하다.

단, 화학 피어 바스켓을 look-ahead 없이 쓰려면 현재처럼 2026년에 우리가 눈으로 고른 DOW/LYB/EMN/DD를 그대로 “학습 시작 전부터 알고 있던 peer set”으로 간주하면 안 된다. 피어 선정 기준과 기준일(as-of date)을 먼저 고정해야 한다.

## 1. 캐시 확인 결과

### DOW

```text
primary cache:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/DOW.pkl

rows: 1527
first: 2020-05-18
last: 2026-06-15
covers_start: true
covers_end: true
covers_full: true
target_rows: 1526
target_close_valid: 1526
target_close_nulls: 0
source: adapter.load_history(years=6)
```

### LYB

```text
primary cache:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/LYB.pkl

rows: 1526
first: 2020-05-18
last: 2026-06-12
covers_start: true
covers_end: true
covers_full: true
target_rows: 1526
target_close_valid: 1526
target_close_nulls: 0
source: adapter.load_history(years=6)
```

### EMN

```text
primary cache:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/EMN.pkl

rows: 1527
first: 2020-05-18
last: 2026-06-15
covers_start: true
covers_end: true
covers_full: true
target_rows: 1526
target_close_valid: 1526
target_close_nulls: 0
source: adapter.load_history(years=6)
```

### DD

```text
primary cache:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/DD.pkl

rows: 1526
first: 2020-05-18
last: 2026-06-12
covers_start: true
covers_end: true
covers_full: true
target_rows: 1526
target_close_valid: 1526
target_close_nulls: 0
source: adapter.load_history(years=6)
```

### USO

```text
primary cache:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/USO.pkl

rows: 1526
first: 2020-05-18
last: 2026-06-12
covers_start: true
covers_end: true
covers_full: true
target_rows: 1526
target_close_valid: 1526
target_close_nulls: 0
source: adapter.load_history(years=6)
```

### XLI

```text
primary cache:
data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/XLI.pkl

rows: 1526
first: 2020-05-18
last: 2026-06-12
covers_start: true
covers_end: true
covers_full: true
target_rows: 1526
target_close_valid: 1526
target_close_nulls: 0
source: adapter.load_history(years=6)
```

## 2. 없는 종목 목록

요청한 6개 중 primary cache 기준 없는 종목은 없다.

```text
missing: []
```

참고로 더 오래된 `honest_full_6174_20260610` 캐시에도 동일 종목들이 있으나 대부분 2026-06-08 또는 2026-06-09까지만 있어 확인 기준 종료일 2026-06-12를 완전히 커버하지 않는다. 따라서 이번 실험 후보 데이터로는 `20260616_stage01_full` 캐시를 기준으로 보는 것이 맞다.

## 3. look-ahead 없는 화학 피어 바스켓 선정 절차

현재 DOW, LYB, EMN, DD는 2026년 현재 관점에서 CE 관련 피어로 떠올린 목록이다. 이 목록을 그대로 학습 feature에 넣으면 “사후적으로 고른 관련주”라는 look-ahead 위험이 있다.

look-ahead 없이 쓰려면 다음 절차가 필요하다.

### 3-1. as-of date를 먼저 고정

예:

```text
as-of date: 2020-05-18 또는 학습 데이터 시작 직전
```

피어 universe는 이 날짜 이전에 알 수 있었던 정보만으로 정해야 한다.

### 3-2. 피어 선정 규칙을 수익률/성과와 독립적으로 고정

허용 가능한 기준 예:

```text
GICS sector = Materials
GICS industry 또는 sub-industry = Chemicals / Specialty Chemicals / Commodity Chemicals
미국 상장 보통주
일정 유동성 이상
CE와 같은 업종 분류를 가진 종목
```

금지해야 할 기준:

```text
2026년 CE와 같이 움직인 종목
최근 뉴스에서 CE peer로 언급된 종목
2026년 하락/반등 설명에 유용했던 종목
백테스트 성과가 좋게 나오는 종목을 사후 선택
```

### 3-3. 분류 데이터의 시점성을 확인

가장 안전한 방식은 timestamp가 있는 historical company profile 또는 GICS mapping을 쓰는 것이다.

필요 데이터:

```text
ticker
as_of_date
sector
industry
sub_industry
source
valid_from / valid_to 가능하면 포함
```

현재 로컬 `ticker_universe.json`에는 sector/industry/GICS 필드가 없다. 따라서 로컬만으로는 “학습 시작 이전에 알 수 있었던 진짜 peer”를 엄밀히 구성하기 어렵다.

### 3-4. survivor bias 방지

2026년에 존재하는 종목만 고르면 생존편향이 생길 수 있다. 최소한 CE 학습 시작일 기준으로 존재하던 관련 화학 종목 universe를 만들어야 한다.

필요 조건:

```text
2020-05-18 당시 상장/거래 중
학습 구간 내 충분한 OHLCV 데이터 존재
상장폐지/합병 종목을 배제할 경우 그 배제 기준도 as-of로 고정
```

### 3-5. 피어 바스켓 산식은 사전에 고정

예:

```text
chem_peer_equal_weight_return = DOW, LYB, EMN, DD의 equal-weight daily return
chem_peer_momentum_60d = peer basket 60일 수익률
chem_peer_rel_strength = CE 60일 수익률 - peer basket 60일 수익률
```

단, 어떤 feature를 쓸지와 window 길이도 학습 전에 고정해야 한다.

### 3-6. CE와 피어 간 corporate action / ticker history 확인

특히 DOW와 DD는 2019년 전후 기업 구조 변화가 있었다. 이번 실제 캐시는 2020-05-18부터 시작하므로 가격 데이터 구간에는 큰 문제는 없어 보이나, 장기 2009 기준 실험을 하려면 ticker history/분사/합병 이슈를 별도로 처리해야 한다.

## 4. USO/XLI 사용 시 주의

USO는 화학 피어가 아니라 원유/원가 proxy다. XLI는 산업재 ETF로 매크로/경기민감 proxy다. 따라서 이 둘은 “화학 피어 바스켓”이 아니라 별도 설명변수로 분리해야 한다.

권장 분리:

```text
chemical_peers:
  DOW, LYB, EMN, DD

input_cost_proxy:
  USO

industrial_macro_proxy:
  XLI

materials_sector_proxy:
  XLB
```

CE 실험에서 DOW/LYB/EMN/DD/USO/XLI를 모두 한 feature set으로 넣을 수는 있지만, 해석은 분리해야 한다.

## 5. 실행 가능성 판정

```text
DATA_READY_FOR_DRY_DESIGN: true
MISSING_SYMBOLS: []
CACHE_COVERS_2020_05_18_TO_2026_06_12: true
```

단, 바로 학습에 넣기 전 필요한 결정:

```text
1. peer basket을 2020-05-18 as-of 기준으로 고정할 것인지
2. DOW/LYB/EMN/DD를 수동 고정 peer로 인정할 것인지
3. 아니면 GICS/as-of sector mapping을 먼저 확보해 CE peer universe를 자동 산출할 것인지
4. USO/XLI는 peer가 아니라 cost/macro proxy로 분리할 것인지
```

## 6. 최종 결론

가격 데이터 가용성은 통과다.

```text
DOW, LYB, EMN, DD, USO, XLI 모두 캐시에 있음.
확인 구간 전부 커버.
Close 결측 없음.
```

하지만 look-ahead 없는 실험으로 만들려면 “왜 이 종목들이 CE의 피어인가”를 2020-05-18 이전 정보로 증명하거나, 최소한 사전 고정된 industry mapping 규칙으로 선정해야 한다.

따라서 다음 단계는 학습 실행이 아니라:

```text
CE 멀티 피처 실험 설계서 작성
- as-of 기준일
- peer universe 선정 규칙
- feature 산식
- 금지되는 사후 선택 기준
```

이다.
