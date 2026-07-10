# 확장형 통합 후보 게이트 설계 및 원본 17,071개 dry-run

- 기준일: 2026-07-11 KST
- 기준 원본: Stage2 `survivors.jsonl` + canonical Stage3 `final_rulebooks.jsonl`
- 정책 제안 버전: `integrated-gate-v1-evidence-safe`
- 작업 범위: 설계·read-only 시뮬레이션만 수행
- 구현·주문·삭제·재학습·운영 후보 파일 변경: **0건**

## 0. 결론

통합 게이트의 권장 위치는 다음과 같다.

```text
Stage2 survivors + Stage3 final_rulebooks
→ 원본 안정성 확인·증분 수집
→ [통합 정적 게이트]
→ 기존 elite static filter·elite_score
→ candidate_denylist
→ ticker dedup·stage cap
→ 후보별 실시간 evaluate_candidate
→ [통합 동적 게이트]
→ should_buy
→ 새 후보 파일 atomic publish
```

사용자가 요청한 OR 의미는 유지한다.

```text
활성 BLOCK 검사기 중 하나라도 FAIL → 전체 FAIL
FAIL은 없지만 HOLD/ERROR가 하나라도 존재 → HOLD, 라이브 미게시
모든 활성 BLOCK 검사기 PASS/NOT_APPLICABLE → PASS
MONITOR 검사기는 이유와 증거만 기록하고 전체 PASS/FAIL에는 영향 없음
```

다만 현재 OOS·holdout 근거를 검토한 결과, 모든 요청 조건을 즉시 BLOCK으로 승격하는 것은 권고하지 않는다.

| 검사기 | 요청 형태 | 근거 기반 권고 |
|---|---|---|
| 완성도·validate 적격성 | BLOCK | **BLOCK** |
| 이력 표본 부족 | 차단/보류 | **HOLD** |
| 평균 PnL `<0` | BLOCK | **BLOCK** |
| 승률 하위권 | BLOCK | **MONITOR** |
| BOIL형 | BLOCK | **MONITOR** |
| CE형 | BLOCK | **MONITOR** |

승률·BOIL·CE를 monitor-only로 내린 이유는 차단 효과가 실제 최신 holdout에서 검증되지 않았거나 기존 frozen OOS에서 악화됐기 때문이다. 엄격하게 모두 차단하는 시나리오도 별도 sensitivity 결과로 남겼다.

## 1. 원본 및 dry-run 범위

| 원본 | 개체 수 |
|---|---:|
| Stage2 survivors | 1,162 |
| Stage3 final rulebooks | 15,909 |
| 합계 | **17,071** |

Stage2는 완료 marker가 있는 최신 `stage2*` 디렉터리의 `survivors.jsonl`을 직접 읽었다. `central_index.jsonl`은 동일 survivor를 세 번씩 중복 보유하므로 원본 열거에 사용하지 않았다.

Stage3는 canonical `tickers/*/stage3/final_rulebooks.jsonl`을 읽었다. 분석 시점에 Stage3 학습 프로세스는 실행 중이지 않았지만, 설계는 향후 파일이 계속 증가하거나 변경되는 상황을 전제로 한다.

이력 데이터:

- Stage2: 각 survivor의 `trades.jsonl`
- Stage3: 각 final rulebook의 `exit_trades.jsonl`

Stage2에는 `exit_trades.jsonl`이 없으므로 동일 역할의 `trades.jsonl`을 stage-specific provider로 사용한다.

## 2. 아키텍처

### 2.1 검사기 인터페이스

제안 protocol:

```python
class CandidateGateChecker(Protocol):
    name: str
    version: str
    phase: Literal["STATIC", "DYNAMIC"]
    enforcement: Literal["BLOCK", "MONITOR"]
    required_inputs: tuple[str, ...]

    def evaluate(self, context: CandidateGateContext) -> GateCheckResult:
        ...
```

결과 schema:

```text
status: PASS | FAIL | HOLD | NOT_APPLICABLE | ERROR
reason_codes: list[str]
evidence: dict
source_fingerprints: dict
evaluated_at: ISO-8601
checker_version: str
policy_version: str
```

검사기 추가는 registry에 클래스를 등록하는 방식으로 제한한다. 기존 OR aggregator나 후보 생성 코드를 수정하지 않고도 새 조건을 추가할 수 있어야 한다.

### 2.2 정적·동적 분리

정적 검사기는 원본·과거 거래·validate 결과만 사용한다.

```text
artifact_completeness
exit_history_quality
high_vol_volume_weight_zero
```

동적 검사기는 현재 가격·기술 컴포넌트·시장 컨텍스트가 필요하다.

```text
ce_margin_concentration
향후 regime/liquidity/news checker
```

CE형을 원본 17,071개 전체에 60초마다 평가하면 OHLCV·뉴스·시장 조회 비용이 과도하다. 먼저 정적 PASS와 elite_score로 후보를 정렬한 뒤 ticker별 후보를 순차적으로 동적 평가한다. 1순위가 denylist 또는 동적 gate에서 탈락하면 같은 ticker의 2순위를 평가하는 lazy fallback 구조가 적합하다.

## 3. 검사기 A1 — 완성도 및 validate 적격성

### Stage2

PASS 조건:

```text
_stage2_done.json 존재
survivors row에 5개 평가 기간 존재
```

Stage2 survivors는 이미 Stage2 5단계 gate를 통과한 결과이므로 1,162개 모두 완성도 PASS다.

### Stage3

PASS 조건:

```text
_stage3_done.json 존재
해당 final_rulebook_hash가 stage3_profile_catalog.jsonl에 존재
eligible_stage3_basic=True
```

이 조건은 현재 라이브가 `final_rulebooks`를 직접 읽어 validate 결과를 우회하는 문제를 해소한다.

| 항목 | 수 |
|---|---:|
| Stage3 final rulebooks | 15,909 |
| validate/profile 적격 | 2,012 |
| 미완성·미검증 차단 | **13,897** |

기존 분석에서 현역 Stage3 70개 중 profile catalog와 겹친 것은 9개뿐이었다. 새 구조는 profile membership이 없는 개체를 라이브 후보로 승격하지 않는다.

## 4. 검사기 A2 — 과거 실적

### 4.1 데이터 누수 방지 분리

임계 도출과 검증을 같은 기간에서 하지 않도록 이력을 분리했다.

| 단계 | 임계 도출용 development history | 진단 holdout |
|---|---|---|
| Stage2 | stress + train_1/2/3 | `oos_2025h2` |
| Stage3 | stress + train_1/2 | `recent_1y` |

### 4.2 표본수 선행 관문

임의의 20·30·50건을 지정하지 않고 완성 개체의 development 거래 수 분포 하위 10%를 최소 신뢰 표본으로 사용했다.

| 단계 | 완성 개체 | 거래 수 P10 | Q25 | 중앙값 | 권고 |
|---|---:|---:|---:|---:|---|
| Stage2 | 1,162 | **35** | 40 | 46 | `<35` HOLD |
| Stage3 | 2,012 | **24** | 31 | 38 | `<24` HOLD |

표본 부족은 “성과가 나쁘다”가 아니라 평균·승률을 신뢰할 수 없다는 뜻이므로 FAIL이 아닌 HOLD다.

- Stage2 HOLD: 103
- Stage3 HOLD: 188
- 합계: **291**

### 4.3 평균 PnL

경제적 손익분기점인 `평균 PnL < 0%`를 차단선으로 사용한다. 분포에서 임의 분위수를 고른 것이 아니라 수익성의 부호 기준이다.

현재 완성 개체에서는 Stage2·Stage3 모두 음수 평균 PnL 개체가 0개였다. 따라서 이 조건은 현재 후보를 줄이지 않지만, 향후 신규 Stage3 개체에 음수 이력이 생길 경우 자동 차단한다.

### 4.4 승률

참고 임계는 development 승률 분포 P10이다.

| 단계 | 승률 P10 |
|---|---:|
| Stage2 | 58.5274% |
| Stage3 | 50.0000% |

그러나 holdout 검증 결과 승률 P10 미만을 BLOCK으로 쓸 근거가 없었다.

| 그룹 | 개체 | holdout 후보동일가중 평균 PnL | holdout 승률 |
|---|---:|---:|---:|
| Stage2 승률 P10 미만 | 110 | 2.2711% | 60.02% |
| Stage2 나머지 | 1,052 | 2.4031% | 69.62% |
| Stage3 승률 P10 미만 | 178 | **6.3199%** | 56.04% |
| Stage3 나머지 | 1,834 | 3.4329% | 65.62% |

Stage3 저승률군은 승률은 낮지만 평균 PnL이 오히려 더 높았다. 손익비가 높은 구조일 가능성이 있어 승률 단독 차단은 잘못된 개체를 제거할 수 있다.

기존 45% 기준도 검증되지 않았다.

- Stage2 `<45%`: 1개, holdout 평균 PnL 1.0547%
- Stage3 `<45%`: 69개, holdout 평균 PnL 6.0080%

따라서 승률은 `WIN_RATE_LT_STAGE_P10` 사유만 기록하고 MONITOR로 둔다.

### A2 최종 권고

```text
표본 < stage P10 → HOLD
표본 충분 + 평균 PnL < 0 → FAIL
승률 < stage P10 → MONITOR warning
그 외 → PASS
```

이는 `9dd8e02`에서 단순 margin/집중 gate가 OOS를 개선하지 못한 전례와도 일치한다. “나빠 보이는 특징”을 곧바로 차단하지 않고 독립 holdout 근거를 요구한다.

## 5. 검사기 A3 — BOIL형

계승 기준:

```text
HIGH_VOL
AND abs(weight_volume_surge) <= 0.05
```

`0.05`는 기존 `high_vol_volume_weight_zero` 감사의 near-zero 정의를 그대로 계승했다.

변동성 분류:

1. 기존 OOS volatility reference에 ticker가 있으면 저장된 `vol_group` 사용.
2. 신규 ticker는 과거 거래의 평균 ATR% proxy 사용.
3. proxy HIGH_VOL 경계는 reference group 중앙값의 MID/HIGH 중간점으로 도출.

```text
LOW median ATR%  = 3.4512
MID median ATR%  = 4.9365
HIGH median ATR% = 6.1146
proxy boundary   = (4.9365 + 6.1146) / 2
                 = 5.5255%
```

엄격 검사 시 완성 개체 hit:

- Stage2: 2
- Stage3: 33

하지만 full-origin proxy cohort의 holdout 방향은 기존 live93 관찰과 충돌했다.

| 그룹 | holdout 평균 PnL |
|---|---:|
| Stage2 BOIL형 2개 | 3.1266% |
| Stage3 BOIL형 33개 | 5.2474% |

기존 live93에서는 HIGH_VOL near-zero 그룹이 positive-weight 그룹보다 평균 PnL·승률·MFE가 낮았다. 반면 원본 전체의 proxy 분류에서는 해당 패턴이 손실군으로 재현되지 않았다. 신규 ticker 상당수가 exact vol_group이 아닌 ATR proxy라는 제한도 있다.

따라서 검사기와 증거 필드는 설계하되 v1에서는 MONITOR로 둔다. 정확한 전체 ticker volatility 분류와 별도 OOS 검증 후 BLOCK 승격 여부를 판단한다.

현재 권고 최종 후보 93개 중 BOIL monitor hit는 1개다.

```text
stage3:CVNA:2f6d067a7826
```

## 6. 검사기 A4 — CE형

계승 기준:

```text
should_buy=True
AND final_score / threshold < 1.25
AND core technical Top2 share >= 90%
```

임계 근거:

- ratio `<1.25`: `live93_three_symptom_scan`의 기존 경계.
- Top2 `>=90%`: 기존 집중도 증상 경계.
- 두 조건의 AND만 사용. Top2 단독은 93개 중 82개에 걸려 판별력이 없다.

이 검사는 현재 기술 컴포넌트가 필요하므로 DYNAMIC phase다.

`9dd8e02`의 frozen OOS 결과:

- BB+RSI Gate A: OOS MDD 악화, 차단군 평균 PnL이 양수.
- ratio 1.05/1.10: OOS 중립.
- ratio 1.15/1.25: OOS 저하.
- A+B 조합: OOS MDD 악화.

따라서 v1에서는 MONITOR다.

이번 dry-run은 새로운 실시간 평가를 실행하지 않았고 기존 `live93_three_symptom_scan.csv`의 역사적 신호 snapshot만 연결했다. 권고 후보 93개 중:

- 역사 snapshot에서 CE PASS: 5
- CE FAIL: 0
- 현재 동적 판정 필요: **88**

88개를 데이터 없음으로 fail-closed하면 최종 후보가 5개로 급감한다. 이는 개체 품질이 아니라 동적 데이터 coverage 문제이므로 올바른 정책이 아니다.

## 7. dry-run 결과

### 7.1 조건별 수

| 조건 | Stage2 | Stage3 | 합계 | 권고 처리 |
|---|---:|---:|---:|---|
| 완성도 PASS | 1,162 | 2,012 | 3,174 | 나머지 FAIL |
| 완성도 FAIL | 0 | 13,897 | 13,897 | BLOCK |
| 표본 부족 | 103 | 188 | 291 | HOLD |
| 평균 PnL 음수 | 0 | 0 | 0 | BLOCK |
| 승률 P10 미만 | 110 | 178 | 288 | MONITOR |
| BOIL형 | 2 | 33 | 35 | MONITOR |
| CE형, 역사 snapshot 전체 | — | — | 7 | MONITOR |

검사기 hit는 서로 중복될 수 있으므로 단순 합계가 최종 탈락 수와 같지 않다.

### 7.2 OR 최종 상태

근거 안전 권고 정책:

| 상태 | Stage2 | Stage3 | 합계 |
|---|---:|---:|---:|
| PASS | 1,059 | 1,824 | **2,883** |
| HOLD | 103 | 188 | **291** |
| FAIL | 0 | 13,897 | **13,897** |

모든 요청 조건을 엄격 BLOCK으로 간주한 sensitivity:

| 상태 | 합계 |
|---|---:|
| PASS | 2,599 |
| HOLD | 273 |
| FAIL | 14,199 |

엄격 정책도 후보가 0이 되지는 않지만, 승률·BOIL 조건은 holdout에서 차단 타당성이 확인되지 않았다.

### 7.3 elite 정렬 이후

| 시나리오 | Stage2 | Stage3 | 최종 |
|---|---:|---:|---:|
| 근거 안전 정적 PASS 원본 | 1,059 | 1,824 | 2,883 |
| elite filter + 점수 + denylist-before-dedup + cap | 13 | 80 | **93** |
| 현재 순서인 dedup 후 denylist | 12 | 79 | **91** |
| CE를 snapshot 미판정까지 fail-closed | 2 | 3 | **5** |

통과 0 위험은 없다. 권고 정책은 기존 stage cap을 모두 채운다.

전체 93개 후보 목록은 `integrated_gate_pass_candidates.csv`에 기록했다.

## 8. 충돌 교정안

### 8.1 denylist를 ticker dedup 앞으로 이동

권고 순서:

```text
정적 PASS 후보
→ elite score
→ denylist
→ ticker별 첫 통과 후보
→ stage cap
```

현재 순서와 비교해 2개 fallback 후보가 회복됐다.

```text
stage2:HCC:e1b25d2e778b
stage3:CDE:0d2be772e8d9
```

기존 차단 11개 ticker 전부가 회복되지는 않는다. 현재 원본과 gate에서 차순위 후보까지 통과한 ticker가 2개이기 때문이다.

### 8.2 고정 93-ID MAE/MFE join 제거

삭제 대상 논리가 아니라 코드 연결 교정 대상이다.

현재:

```text
live_candidate_list_20260707.json candidate_id lookup
없으면 gate_missing 차단
```

권고:

```text
candidate hash별 현재 checker 결과 조회
policy_version과 source fingerprint 일치 확인
PASS/HOLD/FAIL 사용
```

신규 Stage3 개체는 자동으로 정적 checker를 거치므로 과거 후보 ID 목록에 없다는 이유로 차단되지 않는다.

### 8.3 central_index 3중 중복 제거 반영

통합 gate의 origin scanner는 `central_index`를 사용하지 않고 직접 다음을 읽는다.

```text
Stage2: canonical effective stage2*/survivors.jsonl
Stage3: canonical stage3/final_rulebooks.jsonl
```

고유키:

```text
(stage, ticker, full rulebook_hash, source fingerprint)
```

따라서 Stage2 survivor 1,162개가 3,486행으로 부풀어 필터 통계와 스캔 비용을 왜곡하는 문제가 사라진다. `central_index` 자체 재작성은 이번 설계 범위가 아니다.

### 8.4 죽은 Stage3 pool 참조

`engine/live/central_control.py`의 다음 경로는 정리 대상이다.

```text
data/_system/central/stage3_live_pool/stage3_live_pool.jsonl
```

해당 파일은 이미 비활성 잔재로 삭제됐다. 구현 단계에서는 다음 중 하나를 명시적으로 선택해야 한다.

1. dead Stage3-mix 경로와 옵션 제거.
2. 옵션을 유지할 경우 새 통합 gate output을 명시적 입력으로 변경.

파일이 없을 때 조용히 빈 pool로 진행하는 구조는 금지하는 편이 안전하다.

## 9. 연속 업데이트 메커니즘

### 9.1 60초 cycle

```text
1. survivors/final_rulebooks/profile/done/trade 파일 inventory
2. source fingerprint 비교
3. 새 hash·변경 hash·신규 trade row만 증분 집계
4. 정적 checker 실행
5. static_catalog atomic write
6. elite filter·score
7. denylist-before-dedup fallback selection
8. 선택 후보에만 동적 evaluate_candidate/checker 실행
9. live_candidates atomic write
10. live_slots_state/export가 같은 policy_version 결과 소비
```

### 9.2 fingerprint와 checkpoint

제안 fingerprint:

```text
path
inode 또는 stable identity
size
mtime_ns
tail hash
last parsed byte offset
```

학습 중 파일을 읽을 때는 읽기 전후 `size+mtime_ns`가 같아야 결과를 commit한다. 변하면 해당 파일은 `SOURCE_UNSTABLE`로 이번 cycle에서 HOLD하고 다음 60초에 다시 읽는다.

### 9.3 증분 처리

- JSONL append: 마지막 byte offset 이후만 parse.
- 동일 hash의 trade row: running count/sum/win만 갱신.
- profile catalog에 새 hash가 생기면 해당 final hash만 completeness 재평가.
- final rulebook이 새로 생기면 신규 hash만 checker 실행.
- source가 줄거나 inode가 바뀌면 append assumption을 폐기하고 해당 파일 전체 재처리.

### 9.4 전체 재처리 조건

- policy/checker version hash 변경
- 임계값 변경
- source size 감소 또는 inode 교체
- profile/done marker 변경
- state schema 변경
- 수동 `--full-rebuild`

원본 파일은 수정하지 않는다. 모든 결과는 새 디렉터리에만 atomic write한다.

## 10. 새 후보 파일 설계

제안 경로:

```text
data/_system/integrated_candidate_gate/gate_state.json
data/_system/integrated_candidate_gate/static_catalog.jsonl
data/_system/integrated_candidate_gate/ranked_candidates.jsonl
data/_system/integrated_candidate_gate/live_candidates.json
```

### static catalog row

```json
{
  "candidate_id": "stage3:ABC:0123456789ab",
  "origin": {
    "stage": "stage3",
    "ticker": "ABC",
    "rulebook_hash": "full-hash",
    "source_file": ".../final_rulebooks.jsonl",
    "source_row_index": 42,
    "source_fingerprint": "sha256-policy-input"
  },
  "checks": {
    "artifact_completeness": {
      "status": "PASS",
      "reason_codes": [],
      "evidence": {"done_marker": true, "profile_eligible": true},
      "version": "1"
    },
    "exit_history_quality": {
      "status": "PASS",
      "reason_codes": [],
      "evidence": {"n": 38, "avg_pnl_pct": 2.8, "win_rate_pct": 55.0},
      "version": "1"
    }
  },
  "overall_static_status": "PASS",
  "fail_reasons": [],
  "monitor_reasons": ["WIN_RATE_LT_STAGE_P10"],
  "policy_version": "integrated-gate-v1-evidence-safe",
  "evaluated_at": "ISO-8601"
}
```

### ranked candidates

정적 PASS와 기존 elite static filter를 통과한 전체 순위 후보를 저장한다. ticker dedup 전 후보를 보존해 fallback이 가능해야 한다.

필수 필드:

```text
candidate_id, stage, ticker, rulebook_hash
elite_score, stage_rank, ticker_rank
 denylisted, deny_reason
overall_static_status, monitor_reasons
policy_version, source_fingerprint
```

### live candidates

실시간 평가를 통과한 후보만 저장한다.

```text
candidate_id
static_catalog_fingerprint
policy_version
dynamic_check_results
score, threshold, ratio, components
should_buy
published_at, market_context_asof
```

`live_slots_state`와 dashboard export는 이 파일의 policy version과 fingerprint를 검증해야 한다.

## 11. 라이브 연결 지점

### `engine/live/elite_shadow_report.py`

- origin 후보를 만든 직후 공통 gate 결과를 결합.
- ticker dedup 전에 FAIL/HOLD 제거.
- denylist를 dedup보다 앞에 적용.
- ranked fallback 후보를 보존.

### `data/_system/ops/live_candidate_slots.py`

- 기존 `load_gate_list()`와 고정 `MAX_CANDIDATES=93` 의존 제거 대상.
- 60초마다 integrated gate state를 증분 refresh.
- ranked 후보를 ticker별로 lazy dynamic 평가.
- 정적·동적 policy version이 일치한 후보만 pool에 추가.

### `scripts/export_real_dashboard_buy_candidates.py`

- 독자적으로 다른 gate를 계산하지 않고 shared checker 결과를 재사용.
- export 당시 시장 데이터가 달라 재평가할 경우 동일 checker version으로 새 dynamic result를 기록.

### `engine/live/central_control.py`

- 삭제된 `stage3_live_pool` 기본 경로를 새 통합 gate output으로 바꾸거나 경로 자체 제거.

이번 단계에서는 어느 파일도 수정하지 않았다.

## 12. 최종 권고

구현 1차 버전은 다음 범위가 안전하다.

```text
BLOCK:
- Stage3 profile 미검증/미완성
- Stage2 구조 불완전
- 표본 충분하지만 평균 PnL < 0

HOLD:
- Stage2 base history < 35 trades
- Stage3 base history < 24 trades
- source 파일이 쓰이는 중이거나 읽기 전후 fingerprint 변경

MONITOR:
- 승률 stage-specific P10 미만
- HIGH_VOL + abs(volume weight)<=0.05
- should_buy + ratio<1.25 + Top2>=90%
```

승률·BOIL·CE는 별도 exact OOS 검증에서 차단군의 수익·MDD 개선이 확인될 때만 BLOCK으로 승격한다.

이 설계로 현재 원본을 처리하면 정적 PASS 2,883개가 남고, 기존 elite 기준·stage cap을 모두 채워 최종 93개 후보를 만들 수 있다. 통과 0 위험은 없으며, denylist 순서 교정으로 기존 방식보다 2개 ticker fallback을 추가 회복한다.

## 13. 산출물

- `integrated_gate_architecture.json`
- `integrated_gate_thresholds.json`
- `integrated_gate_candidate_dryrun.csv` — 원본 17,071개 전수 판정
- `integrated_gate_condition_summary.csv`
- `integrated_gate_history_validation.csv`
- `integrated_gate_checker_evidence.csv`
- `integrated_gate_scenario_summary.csv`
- `integrated_gate_pass_candidates.csv` — 권고 최종 후보 93개
- `integrated_gate_simulation_summary.json`
- `integrated_candidate_gate_design_readout.md`
- 재현 스크립트:
  - `integrated_gate_sim_core.py`
  - `integrated_gate_holdout.py`
  - `run_integrated_gate_simulation.py`
  - `finalize_integrated_gate_simulation.py`

운영 코드·주문·원본 룰풀·학습 프로세스에는 변경을 가하지 않았다.

## 14. 확정 추가 — 단방향 임계 p99 도달가능성 BLOCK

정책 버전 `integrated-gate-v2-p99-reachability-block`에서 `one_sided_threshold_p99_reachability`를 STATIC BLOCK으로 확정했다. 활성 가중치가 양수인 단방향 `>=` 임계는 학습 p99 초과, 단방향 `<=` 임계는 학습 p01 미만을 같은 activation-tail p99 초과로 판정한다. MA/MACD 이벤트형과 RSI 밴드형은 이 checker에서 제외한다. 운영 구현은 이번 작업 범위에 포함되지 않았으며 상세 dry-run은 `threshold_p99_block_readout.md`를 기준으로 한다.
