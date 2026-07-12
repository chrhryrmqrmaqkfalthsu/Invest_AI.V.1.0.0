# 사양 대조 — 기존 “2일 내 +3% GA selector”

## 최종 사양 판정

요청된 exact selector는 **GENUINELY_NOT_FOUND**다.

다만 이전 조사에서 하나로 충분히 묶지 못했던 두 개의 유력 연구 계보를 이번에 구조적 앵커로 확정했다.

1. `run_stage2_path_filter.py`
   - 기존 rulebook의 `should_buy=True` 신호를 대상으로 함
   - 신호 직전 D-5~D-1 데이터를 사용
   - GA가 rulebook별 path-filter gene을 진화시킴
   - 실제 진입 신호를 차단하는 구조
2. `run_range_predictor_stage2_v3.py` / payoff 계보
   - 최근 5일 lag feature 사용 버전 존재
   - GA individual마다 quantile band, feature weight, score cut이 다름
   - 2020년 stress 구간부터 train/OOS를 나눠 평가
   - 미래 상승·하락 target을 사용

그러나 두 계보 모두 exact `2거래일 내 +3%` target과 일치하지 않고, 서로 결합된 구현도 없다.

## 요구 사양별 대조

| 요구 사양 | Stage2 path filter | Range/payoff predictor | exact 구현 판정 |
|---|---|---|---|
| 이미 학습된 rulebook 신호 대상 | `should_buy=True` 뒤에 path gate 적용 | Stage2 lag score/component를 feature로 사용하지만 현재 rulebook candidate 자체의 후단 gate는 아님 | path filter만 일치 |
| 2020년부터 신호 전수 | Stage2 stress start가 데이터 시작점부터 2022-06-30이며 train은 2022-07 이후; 2020 고정은 아님 | payoff periods는 2020-07-01부터 시작 | 부분 일치 |
| 신호 직전 5일 입력 | D-5~D-1 OHLCV path를 명시적으로 사용 | historical 5-day 버전에서 lag1~5 사용 | 일치 |
| GA | 기존 Stage2 GA에 path gene monkey patch | 독립 GA population/mutation/crossover | 일치 |
| 개체별 threshold | rulebook마다 min/max path gene이 다름 | individual마다 q_low/q_high, feature weight, up_cut/low_cut 또는 dense cut이 다름 | 일치 |
| 2거래일 horizon | 없음 | next-day target | 불일치 |
| +3% target | 없음 | +2% event, coarse bins 또는 ATR target | 불일치 |
| candidate_pool 전 prefilter | 연구 backtest 안에서 진입 signal을 차단하지만 live 미연결 | 연구 signal detector이며 live 미연결 | live 기준 불일치 |
| OOS/hold-out 검증 | 코드 구조는 Stage2 stress/train/OOS를 재사용하나 실행 산출물 없음 | 일부 survivor/OOS artifact 존재 | exact target 검증 없음 |
| 라이브 연결 | 없음 | 없음 | 불일치 |

## 가장 가까운 구현 1: Stage2 five-day path filter

파일:

`script/research/run_stage2_path_filter.py`가 아니라 정확한 경로는:

`scripts/research/run_stage2_path_filter.py`

최초 commit:

`85e0d2d0a2a7492612c8c512f69c5a406a9aca48`

시각:

`2026-07-04T03:53:51Z`

코드가 직접 설명하는 목적:

- 기존 rulebook이 `should_buy=True`를 낸 신호를 대상으로 함
- D-5~D-1 전체 가격 경로 분석
- “먹을 신호”와 “떨어질 신호”를 구분하는 진입 filter
- path 조건을 GA gene으로 학습
- Stage2 rolling train과 survivor gate 재사용

근거 위치:

- 목적과 연구 전용 선언: `scripts/research/run_stage2_path_filter.py:3-27`
- gene 범위: `59-86`
- 5일 feature 생성: `228-288`
- rulebook별 block 판정: `291-355`
- GA parameter 주입: `413-427`
- 기존 `should_buy` 뒤 block 적용: `439-475`

학습 gene 예:

- `path_filter_max_days_since_high5`
- `path_filter_min_close_pos5`
- `path_filter_max_close_pos5`
- `path_filter_min_pullback_high5_pct`
- `path_filter_max_pullback_high5_pct`
- `path_filter_max_up_days5`
- `path_filter_max_down_days5`
- `path_filter_block_recent_turn_down`
- `path_filter_max_fade_after_surge_score`

그러나 fitness는 정확한 hit label이 아니다.

```text
mean(pnl_pct / max(1, holding_days))
```

근거:

- `scripts/research/run_stage2_path_filter.py:361-410`
- manifest 설명: `496-520`

즉 이것은 “2일 안에 +3%가 될 신호”를 직접 지도학습한 selector가 아니라, 전체 거래 결과의 일평균 수익률을 개선하도록 5일 path gate를 진화시킨 것이다.

또한 실행 산출물이 없다.

- `path_filter_manifest.json`: NOT_FOUND
- path-filter survivor pool: NOT_FOUND
- fitness trace: NOT_FOUND
- per-rulebook learned path gene table: NOT_FOUND

백업 tar에는 script와 pyc만 있고 실행 결과는 없다.

운영 감사 판정:

- `ONE_OFF_OR_LEGACY_CODE`
- `operational_reachable=X`
- `active_reference_count=0`

## 가장 가까운 구현 2: Range predictor / payoff GA

### Historical five-day range predictor

commit:

`3048579f792f0d3d213034c6956f33973cc8130b`

이 버전은 기존 Stage2 component와 최근 5일 pattern을 결합하고, GA individual마다 feature·quantile interval·weight를 진화시켰다.

그러나 target은 다음 날 high/low 범위 bin이다.

### Next-day +2% predictor

commit:

`33605eb80b6a1f25c09aacd2813b4da348f91b8b`

- prior 5-day feature 유지
- 다음 날 HIGH +2% event 분류
- FIX 실험에서 final survivor 17개 보존

보존 artifact:

`exp_fix_range_predictor_stage2_v3_rolling_event2pct_high_20260704_001/final_survivors.jsonl`

- rows: 17
- SHA-256: `238ff50136f39598b10ee5bcc376c1a5369586046f90d396ff303c1bf7b8f625`
- ticker: FIX 한 종목
- individual별 q_low, q_high, weight, softness, signature 저장

하지만 horizon과 target이 다르다.

- horizon: next day
- target: +2%

### Two-gene payoff detector

파일:

`scripts/research/run_payoff_two_gene_ga.py`

구조:

- `Gene_UP`과 `Gene_LOW`
- 각 gene이 quantile interval, feature weight, score cut 학습
- 최종 signal: `UP_score >= up_cut AND LOW_score >= low_cut`
- stress: 2020-07-01~2022-06-30
- OOS: 2025-07-01~2026-06-30

근거:

- gene·cut 구조: `4-20`
- 기간: `40-47`
- ATR target: `104-112`
- mutation/cut: `315-366`
- serialized individual cut: `409-428`
- OOS survivor 조건: `516-523`

중요한 오탐:

`run_payoff_two_gene_ga.py:329`의 `q_low + 0.03`은 **quantile interval 최소 폭**이다. +3% 수익 target이 아니다.

## “2020년부터 모든 신호” 대조

- Path filter가 재사용하는 Stage2 train split은 2022-07-01부터다.
- stress period는 start가 `None`이어서 해당 ticker 데이터 시작일부터 2022-06-30까지 평가한다.
- 따라서 데이터가 2020년에 시작하면 2020 구간이 포함될 수 있지만, 코드상 `start=2020` 고정 사양은 아니다.
- Payoff detector는 stress를 `2020-07-01`로 명시한다.

정확히 “2020년부터 신호 발생 시점 전수로 2일 +3% label을 생성”하는 loop는 `NOT_FOUND`다.

## 왜 이전 조사가 놓친 것처럼 보였는가

이전 조사는 exact target 이름과 range/payoff predictor를 중심으로 조사했다. 그 결과 “live 미연결” 결론은 맞았지만, 사용자 설명과 구조적으로 가까운 `run_stage2_path_filter.py`를 핵심 후보로 충분히 강조하지 못했다.

이번에는 다음을 새로 확정했다.

- 5일 rulebook-signal prefilter GA 자체는 실제 존재함
- per-rulebook path gene 구조도 실제 존재함
- 하지만 target이 2일 +3%가 아님
- 실행 artifact가 남지 않음
- live 연결이 없음

따라서 이전 조사의 **미연결 결론은 유지**되지만, “가장 가까운 기존 구현”은 range/payoff predictor 하나가 아니라 `stage2_path_filter`와 range/payoff 두 계보로 수정해야 한다.
