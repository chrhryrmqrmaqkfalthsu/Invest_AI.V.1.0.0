# 기존 “2일 내 +3% GA selector” 발굴 및 라이브 연결 상태 확인

- 조사일: 2026-07-12
- 조사 대상 exact 사양:
  - 학습된 rulebook의 신호
  - 2020년 이후 신호 전수
  - 신호 직전 5일 입력
  - GA
  - 개체별 threshold/cut
  - 이후 2거래일 내 +3% 도달 target
  - candidate_pool 편입 전 prefilter
- 코드·설정·daemon 변경: **0**

## 최종 판정

### Exact 사양: **GENUINELY_NOT_FOUND**

### 근접 구현: **FOUND_RESEARCH_ONLY**

정확한 selector는 모든 검색 범위에서 발견되지 않았다. 다만 사용자 기억의 구성 요소 대부분을 나눠 가진 두 개의 기존 연구 계보를 발굴했다.

1. **Stage2 five-day path filter**
   - 기존 rulebook 신호를 대상으로 함
   - D-5~D-1 입력
   - rulebook마다 다른 GA path gene
   - 실제 entry signal을 block하는 연구 구조
2. **Range/payoff predictor**
   - 최근 5일 feature 사용 버전
   - GA individual별 quantile band·feature weight·cut
   - 2020년 stress부터 OOS까지 평가
   - 미래 high/low 또는 payoff target 사용

그러나 어느 쪽도 exact `2거래일 내 +3%` label을 사용하지 않으며, 두 계보를 결합한 구현도 없다.

## 1. 이번 정밀 검색 범위

이전 조사보다 검색 범위를 확장했다.

- 현재 working tree
- 모든 local/remote branch tip
- Git ref에 도달 가능한 848개 commit
- reflog에만 남은 commit 12개
- unreachable commit/tree/blob
- Git object DB의 8MB 이하 text blob 2,229개
- ignored/untracked analysis·research 파일
- `exp_*` experiment 디렉터리
- 2026-07-10 cleanup manifest와 operational audit
- backup tar.gz 29개
- current live candidate paths와 전체 Git 연결 이력

구조적 앵커를 결합해 검색했다.

- GA: genetic, population, individual, fitness, generation, mutate, crossover
- per-entity: q_low/q_high, quantile band, feature weight, cut, individual threshold
- 5일: D-5~D-1, lookback=5, lag1~5, rolling(5)
- 2일: horizon=2, next2, shift(-2), 2 trading days
- +3%: 0.03, 1.03, target 3%, tp3
- 2020: start/since/period 2020
- rulebook signal/candidate/filter/gate
- OOS/hold-out/survivor/validation

모든 anchor가 동시에 잡힌 Git blob은 이번 지시와 이전 포렌식 산출물뿐이었다. exact selector source blob은 발견되지 않았다.

## 2. 가장 가까운 실제 구현 — Stage2 five-day path filter

파일:

`scripts/research/run_stage2_path_filter.py`

최초 commit:

`85e0d2d0a2a7492612c8c512f69c5a406a9aca48`

시각:

`2026-07-04T03:53:51Z`

이 코드는 사용자 설명과 매우 가깝다.

### 일치하는 부분

- 대상: 기존 rulebook이 `should_buy=True`를 낸 signal
- 입력: D-5~D-1 완료봉
- 방식: Stage2 GA에 path-filter gene을 추가
- 개체성: 각 Rulebook이 서로 다른 min/max path gene을 가짐
- 용도: backtest 중 진입 전 signal block
- 검증 구조: stress → train3 → train2 → train1 → OOS early-cut과 survivor gate 재사용

근거:

- 목적: `scripts/research/run_stage2_path_filter.py:3-27`
- gene 범위: `59-86`
- five-day path feature: `228-288`
- 개체별 block: `291-355`
- GA 주입: `413-427`
- 기존 `should_buy` 후 gate 적용: `439-475`

학습되는 path gene에는 다음이 포함된다.

- 5일 고점 이후 경과일
- 5일 range 내 종가 위치의 최소·최대
- 5일 고점 대비 pullback의 최소·최대
- 5일 상승·하락일 수
- 최근 상승→하락 전환 차단
- 급등 후 fade score
- 5일 최대 단일 상승률

### 일치하지 않는 부분

결정적으로 target이 다르다.

Path filter의 fitness:

```text
mean(pnl_pct / max(1, holding_days))
```

즉 정상 exit rule로 끝난 전체 trade의 일평균 수익률을 개선한다. 다음 binary label은 만들지 않는다.

```text
max(high[t+1], high[t+2]) / signal_price - 1 >= 0.03
```

따라서:

- horizon=2: 없음
- +3% label: 없음
- +3% precision/recall: 없음
- exact selector threshold: 없음

### 실행 산출물 상태

현재와 backup에서 다음은 발견되지 않았다.

- `path_filter_manifest.json`
- `path_filter_hold3_manifest.json`
- path-filter survivor pool
- GA fitness history
- 학습된 rulebook별 path gene table
- OOS pass row

운영 감사에서는 두 script를 다음처럼 판정했다.

- `ONE_OFF_OR_LEGACY_CODE`
- `operational_reachable=X`
- `active_reference_count=0`

즉 코드만 존재하며 실행 결과는 `NOT_STORED`다.

## 3. `hold3`의 의미

파일:

`scripts/research/run_stage2_path_filter_hold3.py`

최초 commit:

`92b99ae3cee96645d7900724aa41c74bc1443ca1`

이름의 `3`은 +3%가 아니다.

```text
max_holding_days ∈ [1, 3]
```

을 뜻한다. 주석은 기존 path-filter 실패 원인을 긴 보유일 25~28일로 보고 보유일만 1~3일로 clamp하는 실험이라고 명시한다.

- `run_stage2_path_filter_hold3.py:3-21`
- `MAX_HOLDING_RANGE=(1,3)`: `40-41`

또한 2일 target이 아니라 최대 3일 보유 gene이며 fitness도 그대로 일평균 수익률이다.

## 4. 가장 가까운 predictor — 5일 GA + per-individual cut

### Historical five-day range predictor

commit:

`3048579f792f0d3d213034c6956f33973cc8130b`

시각:

`2026-07-04T12:05:21Z`

이 버전은:

- Stage2 lag component
- stock D-1~D-5 feature
- market feature
- GA individual별 feature/quantile interval/weight

을 사용했다.

그러나 target은 **다음 날 high/low range bin**이다.

### 다음 날 +2% event predictor

commit:

`33605eb80b6a1f25c09aacd2813b4da348f91b8b`

- 입력: 기존 five-day feature
- target: 다음 날 HIGH +2%
- ticker: FIX
- 보존 final survivor: 17개

Artifact:

`exp_fix_range_predictor_stage2_v3_rolling_event2pct_high_20260704_001/final_survivors.jsonl`

- rows: 17
- SHA-256: `238ff50136f39598b10ee5bcc376c1a5369586046f90d396ff303c1bf7b8f625`
- individual마다 `q_low`, `q_high`, `weight`, `softness`, `signature`가 다름

이 구조가 사용자 기억의 “개체별 band/cut”과 가장 가깝다. 그러나:

- horizon: 1일
- target: +2%
- 학습 대상: FIX 한 종목의 predictor individual
- live 연결: 없음

### `true3` 오탐

`exp_fix_range_predictor_stage2_v3_true3_stage2gate_fixed_20260705_001`

의 `true3`는 +3%가 아니라 **3개 coarse class**다.

- final survivor: 51개
- target: next-day HIGH/LOW coarse3
- 첫 survivor OOS HIGH lift: `-9.5436%p`

따라서 +3% selector로 볼 수 없다.

## 5. Payoff two-gene GA

파일:

`scripts/research/run_payoff_two_gene_ga.py`

이 구현은 다음 anchor를 충족한다.

- GA population·mutation·crossover
- individual별 UP/LOW gene
- feature별 quantile interval
- individual별 `up_cut`, `low_cut`
- stress 시작 `2020-07-01`
- OOS `2025-07-01~2026-06-30`

근거:

- gene/cut 구조: `4-20`
- 기간: `40-47`
- target: `104-112`
- mutation/crossover: `315-366`
- serialized cut: `409-428`
- OOS survivor condition: `516-523`

그러나 target은 다음 날 ATR 배수다.

```text
GOOD_SIGNAL = next_high_atr >= good_high_atr
              AND next_low_atr <= good_max_low_atr
```

`q_low + 0.03`은 +3% 수익률이 아니라 quantile interval 최소 폭이다.

5일 wrapper도 존재하지만 next-day ATR target을 유지한다.

## 6. 학습 artifact 상태

보존된 artifact:

- next-day +2% FIX final survivor 17개
- next-day coarse3 FIX final survivor 51개
- HIGH-only dense FIX final survivor 4개

삭제 또는 미보존:

- 대부분의 `predictors_all.jsonl`
- period metrics
- payoff `all_candidates.jsonl`
- payoff final validation rows
- path-filter 실행 결과 전체

2026-07-10 감사 결과:

- range/payoff: `TERMINATED_EXPERIMENT_DATA`
- `operational_reachable=X`
- `active_reference_count=0`
- path-filter scripts: `ONE_OFF_OR_LEGACY_CODE`

따라서 학습된 exact 2d/+3% 개체와 threshold table이 삭제된 흔적도 확인되지 않는다. Cleanup manifest의 과거 경로명에도 `3pct`, `tp3`, `2day`, `two_day`, `horizon2`가 없었다.

## 7. 라이브 연결 상태

현재 live candidate path에서 다음 token을 검색했다.

- `path_filter`
- `range_predictor`
- `payoff_two_gene`
- `final_survivors`
- `dense_high_cut`
- `GOOD_SIGNAL`
- `up_cut`

현재 import/call: **0개**.

Git 전체 이력에서 다음 경로에 대한 연결·제거 commit도 0개였다.

- `engine/live/elite_shadow_report.py`
- `engine/live/elite_shadow_trader.py`
- `data/_system/ops/live_candidate_slots.py`
- `scripts/export_real_dashboard_buy_candidates.py`

따라서 “한때 라이브 연결됐다가 끊긴” 이력은 `NOT_FOUND`다.

현재 live는 다음을 사용한다.

### Stage2 source

`engine/live/elite_shadow_report.py:283-309`

- central index Stage2 survivor
- OOS expectancy/fitness/trade/win-rate
- stress expectancy
- drawdown
- anti-pattern filter
- upstream v3·BOIL gate

### Stage3 source

`engine/live/elite_shadow_report.py:312-378`

- `*/stage3/final_rulebooks.jsonl`
- stored rulebook metrics
- `signal_threshold`
- anti-pattern filter
- upstream gate

### candidate_pool

`data/_system/ops/live_candidate_slots.py:343-465`

- elite report
- KEEP gate
- `evaluate_candidate()`
- `should_buy=True`, 즉 `final_score >= threshold`
- final score 정렬

현재 후보 10개 row 모두에서 다음 field가 없었다.

- path_filter
- predictor
- lookback
- horizon
- target
- up_cut/low_cut
- dense cut
- quantile band

따라서 현재 candidate pool은 exact selector와 근접 연구 selector 모두 거치지 않는다.

## 8. 이전 조사와의 차이

이전 `two_day_target_logic`과 `payoff_predictor_status`는 range/payoff 계보를 중심으로 조사했다. “predictor live 미연결”과 “exact target NOT_FOUND” 결론은 맞았다.

이번 조사에서 새로 보강된 핵심은 다음이다.

- `run_stage2_path_filter.py`가 사용자 설명의 **rulebook signal + D-5~D-1 + GA + per-rulebook gate** 부분을 실제로 구현함
- 단, fitness가 2일 +3%가 아니라 일평균 trade return임
- 실행 artifact는 남지 않음
- live 연결은 없음

따라서 이전 조사가 정확한 selector를 못 찾은 이유는 단순히 파일명이 달랐기 때문만은 아니다. 이름이 다른 근접 구현은 실제로 있었지만, exact target 부분은 그 파일에도 존재하지 않았다.

## 9. 판정표

| 판정 대상 | 결과 |
|---|---|
| Exact “5일→2일+3% GA selector” | **GENUINELY_NOT_FOUND** |
| Five-day per-rulebook GA prefilter | **FOUND_RESEARCH_ONLY** |
| Five-day per-individual predictor | **FOUND_RESEARCH_ONLY** |
| Trained exact selector individual | NOT_FOUND |
| Exact per-entity threshold table | NOT_FOUND |
| Exact OOS/hold-out result | NOT_FOUND |
| Current live connection | NOT_FOUND |
| Historical live attach/remove | NOT_FOUND |

## 10. 최종 결론

사용자가 기억하는 설계는 저장소의 실제 연구 요소와 상당히 겹친다.

```text
Stage2 path filter:
rulebook signal + D-5~D-1 + GA + per-rulebook gate

Range/payoff predictor:
2020 stress + future target + per-individual quantile/cut
```

하지만 저장된 코드에서는 두 구조가 합쳐지지 않았고, exact `2거래일 내 +3%` label이 없다.

따라서 최종 판정은:

### **GENUINELY_NOT_FOUND — exact 사양 기준**

보조 판정:

### **FOUND_RESEARCH_ONLY — 가장 가까운 기존 구현은 `run_stage2_path_filter.py`와 range/payoff predictor 계보**

세부 산출물:

- `discovery_inventory.csv`
- `selector_artifacts.csv`
- `spec_match.md`
- `live_connection_status.csv`
- `immutability_check.csv`
- `manifest.sha256`
