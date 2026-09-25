# 섹터 시스템 현황 read-only 점검

점검 목적: CE `sector_name` 기본값 tech 이슈의 구조적 원인 파악
점검 범위: 룰북 생성/학습/후보 승격/실전 평가/시장 컨텍스트/로컬 섹터 데이터
소스 수정: 없음

## 결론 요약

이번 섹터 문제는 세 겹이다.

1. 섹터 universe 자체가 부족하다.
   - 현재 `SECTOR_ETFS`에는 `tech`, `finance`, `energy`, `healthcare`, `consumer_disc`, `industrials`만 있다.
   - `materials`, `chemicals`, `utilities`, `real_estate`, `communication`이 없다.
   - CE처럼 소재/화학 종목은 들어갈 정상 bucket이 없다.

2. 최종 후보 compact 룰북에 `sector_name`이 빠진다.
   - 원본 `final_rulebooks.jsonl`에는 `sector_name`이 있다.
   - 그러나 `engine/live/elite_shadow_report.py`가 후보용 compact rulebook을 만들 때 `sector_name`, `ticker`, `direction`, `use_market_entry_adjustment`, `vix_sensitivity` 등 많은 필드를 제외한다.
   - 그 결과 frozen `candidate_universe.json`의 93개 후보는 전부 `rulebook.sector_name`이 없다.

3. 빈 sector 처리 방식이 위험하다.
   - `Rulebook.sector_name` 기본값은 `tech`다.
   - `_detect_sector_name()`도 알 수 없는 종목명을 최종적으로 `tech`로 반환한다.
   - 따라서 "모름"이 "tech"가 된다.

추가로 중요한 반전이 있다.

- CE 원본 룰북에는 `sector_name: tech`가 이미 들어 있다.
- 즉 CE는 저장 단계에서만 tech가 된 게 아니라, 원본 학습/선별 경로에서도 materials/chemicals가 아니라 tech로 분류됐다.
- 다만 CE 원본 룰북은 `use_market_entry_adjustment: false`라서 원본 룰북 기준으로는 sector_score가 final_score에 직접 반영되지 않는다.
- 반대로 compact 후보 룰북을 `Rulebook.from_dict()`로 다시 로드하면 `use_market_entry_adjustment`와 `vix_sensitivity`가 기본값으로 복원되어 원본 룰북과 다른 동작이 될 수 있다.

따라서 이 문제는 단순히 "CE가 sector_score=100을 먹어서 산 종목"으로 단정할 수는 없다. 더 정확히는:

```text
섹터 feature 설계/저장/기본값 처리가 모두 불완전하다.
CE의 표시 sector_score=100은 신뢰하면 안 된다.
하지만 CE 원본 룰북 자체는 시장/섹터 보정이 꺼져 있어, 현재 CE 진입 원인을 sector_score 하나로 돌리면 안 된다.
```

## 1. sector_name이 최종 후보 93개에서 비어 있는 근본 원인

### 1-1. 원본 룰북에는 sector_name이 존재함

CE 원본:

```text
source_file: exp_batch_stage123_2009_20260616_full/tickers/CE/stage3/final_rulebooks.jsonl
matched line: 17
source rulebook sector_name: tech
source rulebook sector_strength_weight: -0.6208615991099308
source rulebook use_market_entry_adjustment: false
source rulebook vix_sensitivity: -1.0
```

93개 후보의 source `final_rulebooks.jsonl` 매칭 결과:

```text
matched source rows: 80
source rulebook sector_name present: 80
source rulebook sector_name missing: 0
```

즉 “학습 산출물 원본에 sector_name이 전혀 없다”는 것은 아니다.

### 1-2. 후보 compact rulebook 생성 단계에서 sector_name이 빠짐

문제 위치:

```text
engine/live/elite_shadow_report.py
```

stage2 compact candidate 생성부:

```text
_stage2_candidate_from_row()
```

stage3 compact candidate 생성부:

```text
collect_stage3_elite()
```

두 곳 모두 candidate["rulebook"]을 아래 일부 필드만 포함하는 compact dict로 재구성한다.

```text
exit_strategy
max_holding_days
sell_omen_enabled
market_adjustment_strength
market_score_weight
sector_strength_weight
take_profit_atr
stop_loss_atr
trailing_atr
signal_threshold
position_sizing_strategy
```

여기에 빠지는 주요 필드:

```text
sector_name
ticker
direction
asset_type
use_market_entry_adjustment
vix_sensitivity
use_event_block
use_news_global
event_response_*
news weights
rsi/macd/bb parameters
```

CE source rulebook vs frozen candidate compact rulebook 비교:

```text
source keys: 88
candidate_universe compact keys: 11
missing_in_candidate includes:
  sector_name
  ticker
  direction
  use_market_entry_adjustment
  vix_sensitivity
  event_response_oil_surge
  event_response_geopolitical
  event_response_war
  weight_news_*
```

따라서 93개 후보가 `sector_name` 없이 보이는 직접 원인은 `elite_shadow_report.py`의 compact rulebook whitelist에 `sector_name`이 빠져 있기 때문이다.

### 1-3. 그러나 CE의 원본 sector_name도 잘못됨

CE 원본 `sector_name`은 `tech`였다.

이 값은 `engine/pipeline/context.py::prepare_ticker_context()`와 `engine/learning/learner.py::_detect_sector_name()` 경로에서 온다.

```text
sector_name = _detect_sector_name(meta.name)
base_rulebook.sector_name = sector_name
```

CE meta name:

```text
Celanese Corp - Series A
```

현재 `_detect_sector_name()` 결과:

```text
_detect_sector_name("Celanese Corp - Series A") -> tech
reason: default_fallback_to_tech
```

즉 CE는 원본 룰북 단계에서도 materials/chemicals가 아니라 tech였다.

## 2. 현재 _detect_sector_name() 처리 목록과 빠진 섹터

현재 함수:

```text
engine/learning/learner.py::_detect_sector_name(meta_name)
```

처리 키워드:

```text
tech:
  반도체, tech, qqq, kodex, tiger, s&p, 나스닥, semi, it

energy:
  에너지, energy, oil, 원유

finance:
  금융, finance, bank, 은행, 보험

healthcare:
  헬스, health, bio, 제약

consumer:
  소비, consumer, 리테일

industrials:
  산업, industrial

fallback:
  tech
```

빠진 주요 섹터/업종:

```text
materials / basic materials
chemicals
metals / mining
steel
aluminum
gold / silver
utilities
real_estate / REITs
communication_services
consumer_staples
transportation / airlines / travel
financials의 세부 분류 일부
```

현재 CE, CMC, AA, CDE, CC, CLF, STLD 같은 소재/금속/화학 계열이 대부분 tech fallback으로 갈 수 있다.

예시:

```text
Celanese Corp - Series A -> tech
Commercial Metals Company -> tech
Alcoa Corp -> tech
Chemours Company -> tech
Cleveland-Cliffs Inc -> tech
Steel Dynamics Inc -> tech
```

## 3. sector_strength_weight가 final_score에 들어가는 정확한 수식

위치:

```text
engine/strategies/evaluator.py
```

수식:

```text
market_norm = (market_score - 50) / 50
sector_norm = (sector_score - 50) / 50
vix_norm = (18 - vix_level) / 10

correlation_adj = (
    market_norm * rb.market_score_weight
    + sector_norm * rb.sector_strength_weight
    + vix_norm * rb.vix_sensitivity
)

strength = clamp(rb.market_adjustment_strength, 0, 1)
market_adjustment = 1.0 + clamp(correlation_adj * strength, -strength, +strength)

if rb.use_market_entry_adjustment is False:
    market_adjustment = 1.0

final_score = raw_score * market_adjustment
should_buy = final_score >= rb.signal_threshold
```

sector_score별 sector_norm:

```text
sector_score = 100 -> sector_norm = +1
sector_score = 50  -> sector_norm = 0
sector_score = 0   -> sector_norm = -1
```

따라서 `sector_strength_weight > 0`인 룰북은 sector_score가 높을수록 final_score가 올라가고, `sector_strength_weight < 0`인 룰북은 sector_score가 높을수록 final_score가 내려간다.

## 4. CE에서 sector_score 0 vs fallback tech 100 차이

CE 현재 저장값:

```text
raw_score: 8.363246295633697
final_score: 8.363246295633697
market_score: 85.8
sector_score 표시값: 100.0
vix_level: 17.95
```

CE 원본 full rulebook:

```text
sector_name: tech
market_score_weight: 0.008840218406678813
sector_strength_weight: -0.6208615991099308
vix_sensitivity: -1.0
market_adjustment_strength: 0.10684213221952549
use_market_entry_adjustment: false
```

CE 원본 full rulebook 기준 결과:

```text
sector_score 100 -> final_score 8.363246295633697
sector_score 50  -> final_score 8.363246295633697
sector_score 0   -> final_score 8.363246295633697
```

이유:

```text
use_market_entry_adjustment=false
=> market_adjustment = 1.0 강제
=> sector_score가 final_score에 반영되지 않음
```

반대로 frozen/current candidate compact rulebook만 `Rulebook.from_dict()`로 다시 로드하면 누락 필드가 기본값으로 복원된다.

CE compact-from-dict 상태:

```text
sector_name: tech
market_score_weight: 0.008840218406678813
sector_strength_weight: -0.6208615991099308
vix_sensitivity: 0.0       # 원본 -1.0이 compact에서 빠져 기본값 0.0
market_adjustment_strength: 0.10684213221952549
use_market_entry_adjustment: true  # 원본 false가 compact에서 빠져 기본값 true
```

이 compact/default 기준의 가상 점수:

```text
sector_score 100 -> score 7.814133027321698, adjustment 0.9343420905111115
sector_score 50  -> score 8.368902087910461, adjustment 1.0006762675732408
sector_score 0   -> score 8.923671148499222, adjustment 1.06701044463537
energy 38.9      -> score 8.492060819361166, adjustment 1.0154024548810334
industrials 73.9 -> score 8.103722476949033, adjustment 0.968968530937543
```

CE는 `sector_strength_weight`가 음수라, tech 100 fallback은 오히려 CE 점수를 낮추는 방향이다. 하지만 이 compact/default 계산은 원본 룰북과 다르기 때문에 “실제 CE 원본 점수”로 보면 안 된다.

핵심은 다음이다.

```text
CE 원본 룰북 기준:
  sector 보정 꺼짐 -> sector_score 영향 없음

compact 후보 룰북 재로드 기준:
  누락 필드가 기본값으로 살아남 -> 원본과 다른 market/sector 보정 가능
```

## 5. 93개 후보에 붙일 수 있는 “진짜 섹터” 데이터가 로컬에 있는지

확인한 로컬 후보/심볼 파일:

```text
data/_system/ticker_universe.json
data/_system/ticker_universe.json.bak
data/_system/symbols.json
exp_batch_stage123_2009_20260616_full/tickers/*/stage*/final_rulebooks.jsonl
```

`data/_system/ticker_universe.json` 구조:

```text
rows: 6174
keys: symbol, name, exchange, type, ipo
sector/industry/gics 관련 key: 없음
```

CE row:

```json
{
  "symbol": "CE",
  "name": "Celanese Corp - Series A",
  "exchange": "NYSE",
  "type": "Stock",
  "ipo": "2005-01-21"
}
```

`data/_system/symbols.json`:

```text
rows: 12
sector/industry/gics 관련 key: 없음
CE 없음
```

원본 `final_rulebooks.jsonl`:

```text
sector_name은 있음
하지만 이 값은 GICS/공식 섹터가 아니라 현재 `_detect_sector_name(meta.name)` 기반 추정값이다.
CE의 경우 tech로 잘못 추정됨.
```

따라서 현재 로컬 시스템 안에는 93개 후보 각각에 대해 신뢰 가능한 GICS sector/industry를 붙일 수 있는 데이터가 없다.

필요한 것은 별도의 신뢰 가능한 ticker->sector 매핑 소스다. 예:

```text
GICS sector / GICS industry
IEX/Polygon/FMP/Nasdaq/SEC company facts 등에서 가져온 profile sector
수동 검증된 ticker_sector_map.json
```

단, 이 매핑을 도입하면 feature 정의가 바뀌므로 기존 frozen 성과는 다시 검증해야 한다.

## 6. 왜 이런 일이 벌어졌나

원인 체인:

```text
1. market context 섹터 universe가 6개뿐이고 materials/chemicals가 없다.
2. sector 감지 함수가 종목명 키워드 기반의 매우 약한 heuristic이다.
3. 알 수 없는 종목명을 tech로 fallback한다.
4. 학습/원본 룰북에는 이 fallback 결과가 sector_name으로 저장된다.
5. 후보 승격/elite shadow report 단계에서 compact rulebook을 만들며 sector_name 등 주요 필드를 제거한다.
6. 실전/대시보드/재현 경로에서 compact rulebook을 다시 Rulebook.from_dict()로 읽으면 빠진 필드가 기본값으로 복원된다.
7. 기본값 sector_name=tech, use_market_entry_adjustment=true, vix_sensitivity=0.0 등이 원본과 다른 동작을 만들 수 있다.
```

즉 단일 버그가 아니라 다음 세 가지가 겹쳤다.

```text
섹터 범위 부족
+ 섹터 데이터 품질 부족
+ compact serialization 누락 및 위험한 기본값
```

## 7. 수정 전 원칙

섹터 보정 수정은 단순 버그 픽스가 아니라 전략 변경이다.

왜냐하면:

```text
sector_name이 바뀌면 sector_score가 바뀐다.
sector_score가 바뀌면 market_adjustment가 바뀐다.
market_adjustment가 바뀌면 final_score와 should_buy가 바뀐다.
후보 우선순위와 백테스트 거래 집합이 바뀐다.
```

따라서 바로 실전에 반영하면 안 된다.

권장 다음 단계:

1. `materials`, `utilities`, `real_estate`, `communication_services`, `consumer_staples` 등 sector universe 확장안을 만든다.
2. 로컬 또는 외부 신뢰 소스로 ticker->sector mapping을 만든다.
3. fallback 정책을 `tech`가 아니라 `unknown` 또는 neutral/no-sector-adjustment로 바꾼다.
4. compact candidate rulebook에 최소한 다음 필드를 보존한다.
   - `sector_name`
   - `use_market_entry_adjustment`
   - `vix_sensitivity`
   - `ticker`
   - `direction`
   - 가능하면 full rulebook 또는 hash-verified source lookup
5. 수정 버전으로 frozen OOS 재현을 다시 돌려 CAGR/승률/MAE/MFE/turnover/slot 우선순위를 재검증한다.
6. 재검증 통과 전에는 실전 BUY 정렬에 반영하지 않는다.

## 8. 오늘 결론

오늘 할 일은 여기까지가 맞다.

```text
CE 포지션:
  기존 TP/SL 관리 유지

섹터 시스템:
  원인 파악 완료
  즉시 코드 수정 금지
  다음 단계는 별도 검증 계획 수립 후 진행
```
