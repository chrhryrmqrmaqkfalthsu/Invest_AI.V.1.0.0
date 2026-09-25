# 가상 트랙 시세 부하·격리 실태 파악

- 기준일: 2026-07-11 KST
- 조사 범위: 코드·설정·로그·기존 게이트 산출물 read-only
- 판정: **NEEDS_THROTTLE**
- 60초 전 후보 트랙을 반드시 요구할 경우: **NEEDS_SEPARATE_BUDGET**
- 코드·설계·운영 구현 변경: 0건

## 1. 결론

전 후보 가상 트랙의 시세 부하 단위는 17,071개 룰이 아니라 **531개 고유 ticker**다.

현재 정규장 live candidate daemon은 60초마다 gate 통과 후보 63개, 고유 ticker 61개를 평가한다. 최근 정규장 100 cycle에서 처리시간은 중앙값 7.145초, p95 13.382초, 최대 14.462초였다.

현재 구조를 그대로 531 ticker로 늘리면 다음 문제가 발생한다.

- 가격 논리 호출: 61회/분 → 531회/분
- OHLCV 평균 논리 호출: 6.1회/분 → 53.1회/분
- 총 평균 논리 호출: 67.1회/분 → **584.1회/분**
- 10분 OHLCV 갱신 cycle burst: 122회 → **1,062회**
- ticker 수 선형 환산 p95 cycle: 13.382초 → **116.49초**

따라서 전 후보를 현재와 같은 60초 cadence로 직접 평가하면 live cycle을 밀어낼 가능성이 높다.

저장소에는 yfinance의 공식 분당·일일 quota가 명시돼 있지 않고, 별도 rate limiter나 429 보호 예산도 없다. 현재 로그에서는 명시적 HTTP 429 또는 YFRateLimitError가 발견되지 않았지만, 이는 현재 약 61 ticker 부하가 동작했다는 뜻일 뿐 531 ticker headroom을 보장하지 않는다.

**기본 구현 방향은 LIVE 30~60초 유지, VIRTUAL 15분 순환, ticker snapshot 공유, virtual 30 ticker/분 상한이다.**

## 2. 가상 트랙 대상 규모

| pool | 후보 | 고유 ticker | 용도 |
|---|---:|---:|---|
| 전 후보 | **17,071** | **531** | BLOCK 포함 전체 가상 트랙 |
| v3 BLOCK | 4,491 | 247 | 도달불가 임계 차단군 |
| BOIL BLOCK | 371 | 18 | v3 미포섭 거래량 무시 차단군 |
| v3·BOIL BLOCK 합집합 | 4,862 | 257 | 기존 정적 차단 비교군 |
| v3·BOIL 미차단 | 12,209 | 468 | 도달성·BOIL 기준 생존군 |
| 통합 정적 유효 pool | 2,115 | 339 | completeness/history/v3/BOIL PASS |
| elite·denylist 통과, dedup 전 | 398 | 83 | 실용 후보 pool |
| 최종 LIVE | **84** | **83** | 라이브 우선 lane |

### 실제 full virtual pool

BLOCK 게이트가 좋은 후보를 잘못 막았는지 관측하려면 `BLOCK_v3`와 `BLOCK_boil`을 가상 트랙에 포함해야 한다. 따라서 “전 후보 가상 트랙”의 실제 quote pool은 `ALL_17071`, 즉 **531 ticker**다.

정적 PASS 후보만 추적하는 축소형 실험이라면 2,115개 후보·339 ticker로 줄일 수 있지만, 이 경우 BLOCK 게이트의 false negative 성과를 검증할 수 없다.

LIVE 83 ticker의 snapshot을 공용 캐시에서 재사용하면 full virtual track이 추가로 요구하는 ticker는 **448개**다.

### 등급 ticker 중복

등급은 후보 단위이므로 동일 ticker에 LIVE 룰과 BLOCK 룰이 함께 존재한다.

- LIVE ticker: 83
- v3 BLOCK 후보가 존재하는 ticker: 247
- BOIL BLOCK 후보가 존재하는 ticker: 18
- LIVE와 v3 또는 BOIL 후보가 함께 있는 ticker: **46**

따라서 등급별로 별도 시세를 가져오면 안 된다. ticker별 시세를 한 번 가져온 뒤 동일 ticker의 모든 룰에 fan-out해야 한다.

## 3. 현재 시세 API 실태

### 3.1 현재 실행 상태

조사 시점에 실행 중인 관련 가격 process는 다음 하나였다.

```text
live_candidate_slots.py daemon --interval 60
```

Elite Shadow 300초 daemon과 실제 central live runner는 실행 중이지 않았다. 토요일·정규장 외 시간에는 regular-hours gate가 evaluation을 건너뛰고 cached pool만 재사용한다.

현재 report:

- report 후보: 82개
- report 고유 ticker: 80개
- 기존 gate 통과 평가 후보: 63개
- 실제 평가 고유 ticker: **61개**
- 중복 ticker: ALGT, CAT 각 2개 후보

### 3.2 후보 가격

코드 경로:

```text
live_candidate_slots.py
→ elite_shadow_trader.evaluate_candidate
→ _latest_price
→ yf.Ticker(ticker).history(period="1d", interval="1m", prepost=True)
```

특성:

- provider: yfinance/Yahoo
- 인증: 없음
- 호출 방식: ticker별 개별 호출
- 가격 캐시: 프로세스 메모리, TTL 30초
- daemon 주기: 60초

TTL 30초가 cycle 60초보다 짧으므로 정규장에서는 매 cycle마다 평가 대상 고유 ticker를 다시 조회한다.

### 3.3 후보 OHLCV

코드 경로:

```text
elite_shadow_trader._load_ohlcv
→ adapter.load_history(years=1)
→ engine.core.data_loader.load_ohlcv
→ yf.download(single ticker)
```

캐시:

- Elite evaluator outer cache: 600초
- core data loader inner cache: 300초
- 둘 다 프로세스 메모리

현재 61 ticker 기준 평균 6.1 ticker fetch/분이지만, 캐시 만료 시점에는 최대 61 ticker가 한 cycle에 몰릴 수 있다.

### 3.4 시장 컨텍스트

`engine.market.context`는 다음 8개를 yfinance로 조회한다.

- S&P 500
- VIX
- 6개 섹터 ETF

`data/_system/market_state.json`에 60분 캐시가 있어 후보 수 증가와 무관하다. 평균 부하는 약 0.133 symbol fetch/분이다.

### 3.5 Alpaca

실제 broker adapter는 다음 인증 이름을 사용한다.

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- 기본 URL: paper API

실제 키·계정 내용은 읽지 않았다.

현재 `get_current_price()`는 `StockLatestTradeRequest`에 단일 ticker를 넘기지만 설치된 SDK는 `symbol_or_symbols: str | List[str]`를 지원한다. 따라서 multi-symbol batch 가능성은 있다.

현재 candidate daemon은 Alpaca 가격을 사용하지 않고 yfinance를 사용한다.

### 3.6 rate limit

코드·설정·저장소 문서에서 확인된 사항:

- yfinance 분당·초당·일일 quota: **명시 없음**
- Alpaca market-data quota: **명시 없음**
- 후보 가격용 token bucket: 없음
- provider 429 전용 backoff: 없음
- recent candidate/live 로그 명시적 429: 0건

따라서 quota 기준 headroom은 계산할 수 없다. 계산 가능한 것은 cycle 처리시간 headroom뿐이다.

## 4. 현재 사용량과 full-pool 부하

### 현재 live

| 항목 | 값 |
|---|---:|
| 가격 ticker fetch/분 | 61.0 |
| OHLCV 평균 fetch/분 | 6.1 |
| 총 평균 논리 fetch/분 | **67.1** |
| OHLCV refresh cycle 최대 논리 fetch | 122 |
| cycle 중앙값 | 7.145초 |
| cycle p95 | 13.382초 |
| cycle 최대 | 14.462초 |
| p95 시간 여유 | 46.618초 |

“논리 fetch”는 코드상 ticker 조회 1회를 뜻한다. yfinance 내부 HTTP 요청 수는 이보다 많을 수 있다.

### 전 후보를 60초로 확대

| 항목 | 추정값 |
|---|---:|
| 가격 fetch/분 | 531.0 |
| OHLCV 평균 fetch/분 | 53.1 |
| 총 평균 fetch/분 | **584.1** |
| refresh cycle burst | **1,062** |
| 현재 대비 ticker 배수 | 8.70배 |
| 선형 cycle 중앙값 | 62.19초 |
| 선형 cycle p95 | **116.49초** |
| 선형 cycle 최대 | 125.89초 |

정확한 latency가 선형이라는 보장은 없지만, 최소한 현재 60초 budget 안에 안전하다는 근거는 없다.

### 축소 pool

- 통합 정적 유효 339 ticker를 60초 구조로 돌려도 예상 논리 호출은 약 372.9회/분이다.
- elite 유효 83 ticker는 현재 live 규모와 유사하다.

따라서 full virtual의 핵심 문제는 후보 17,071개가 아니라 **531 ticker를 어느 주기로 갱신하느냐**다.

## 5. 공유·캐싱 구조

### 존재하는 캐시

- 후보 price: 프로세스 메모리, 30초
- 후보 OHLCV: 프로세스 메모리, 600초
- core OHLCV: 프로세스 메모리, 300초
- market context: 파일 공유, 60분
- shadow 1분봉 MTM: 프로세스 메모리, 45초
- Alpaca display price: broker instance 메모리, 15초

### 없는 것

- 라이브·가상 공용 ticker quote snapshot
- cross-process quote cache
- LIVE/VIRTUAL 우선순위 queue
- source별 token bucket
- virtual-only circuit breaker
- quota reservation

`live_candidate_slots`와 `elite_shadow_trader`가 별도 프로세스로 실행되면 Python module cache는 공유되지 않는다. 프로세스를 나누는 것만으로는 같은 yfinance endpoint와 host IP 예산이 분리되지 않는다.

### batch 지원

- `yfinance.download`: multi-ticker 입력 지원
- `StockLatestTradeRequest`: symbol list 지원
- 현재 candidate evaluation: ticker별 `Ticker.history` 개별 호출

Batch는 latency와 connection overhead를 줄일 수 있지만, provider quota가 명시되지 않은 상태에서는 batch 자체를 “별도 예산”으로 간주하면 안 된다.

## 6. 판정

### NEEDS_THROTTLE

근거:

1. full virtual pool은 531 ticker로 현재 평가 ticker의 8.70배다.
2. 현재 구조의 60초 확대 추정 cycle p95가 116초다.
3. yfinance quota가 코드·계정에 명시돼 있지 않아 531 ticker headroom을 보증할 수 없다.
4. cache가 process-local이라 라이브와 가상 트랙이 중복 호출한다.
5. live 우선 queue와 가상 전용 호출 상한이 없다.

따라서 현재 공유 예산에 full virtual을 60초로 붙이는 것은 금지해야 한다.

## 7. 격리·캐싱 권고

### 7.1 우선순위

```text
LIVE quote lane > open-position/exit lane > VIRTUAL lane
```

가상 트랙은 live 요청이 없을 때만 token을 사용해야 한다. 가상 요청 때문에 live가 queue에서 기다리는 구조는 허용하면 안 된다.

### 7.2 공용 ticker snapshot

후보별 API 호출을 없애고 아래 키로 ticker snapshot을 한 번만 생성해야 한다.

```text
(provider, ticker, interval, prepost, asof_bucket)
```

한 ticker의 snapshot을 LIVE, BLOCK_v3, BLOCK_boil 및 기타 룰 모두가 공유한다.

### 7.3 권장 TTL·주기

| lane | 가격 TTL·주기 | 역사 OHLCV |
|---|---|---|
| LIVE | 30~60초 | 기존 live 필요 수준 유지 |
| VIRTUAL full pool | 기본 **15분** | 역사 bulk는 1거래일 persistent cache |
| VIRTUAL soak test | 최소 10분 | current-day overlay만 별도 갱신 |

권장 virtual cap:

- 30 ticker/분
- LIVE snapshot 재사용 후 추가 ticker 448개
- 한 full sweep 약 **14.93분**
- ticker를 30개씩 stagger해 448/531개 동시 burst 금지

10분 주기는 soak test에서 timeout·429·live latency 영향이 없을 때만 허용한다.

### 7.4 실패 격리

- 429 또는 provider timeout 발생 시 VIRTUAL만 지수 backoff
- LIVE lane은 backoff를 공유하지 않음
- VIRTUAL cycle이 이전 sweep을 끝내지 못했으면 신규 sweep 중첩 금지
- cache miss 폭증 시 VIRTUAL skip, stale snapshot 허용 범위 기록
- provider health가 나쁘면 VIRTUAL 정지, LIVE 유지

### 7.5 별도 예산이 필요한 경우

전 후보 531 ticker를 60초 또는 그에 가까운 실시간 cadence로 반드시 추적해야 한다면 판정은 `NEEDS_SEPARATE_BUDGET`으로 바뀐다.

가능한 방식:

- 가상 트랙 전용 market-data 계정·키·token budget
- 별도 유료 batch/stream feed
- 별도 Alpaca data budget과 multi-symbol latest-trade request

같은 host에서 동일 yfinance endpoint를 별도 process로 호출하는 것은 별도 예산이 아니다.

## 8. 구현 방식 결정

| 요구사항 | 결정 |
|---|---|
| 15분 전 후보 가상 트랙 | 공유 cache + LIVE 우선 + virtual 30 ticker/분 cap |
| 10분 전 후보 가상 트랙 | soak test 통과 후 조건부 허용 |
| 5분 전 후보 가상 트랙 | 현재 공유 yfinance 예산에서 위험; 별도 budget 권고 |
| 60초 전 후보 가상 트랙 | **별도 budget 필수** |
| 60초 elite 83 ticker만 | 현재 live 규모와 유사하나 공용 cache 필요 |

## 9. 산출물

- `virtual_track_quote_pool_summary.csv`
- `virtual_track_quote_ticker_grade_overlap.csv`
- `virtual_track_quote_api_headroom.csv`
- `virtual_track_quote_load_scenarios.csv`
- `virtual_track_quote_cache_isolation_inventory.csv`
- `virtual_track_quote_load_summary.json`
- `virtual_track_quote_load_readout.md`

운영 코드·설정·라이브 프로세스는 변경하지 않았다.
