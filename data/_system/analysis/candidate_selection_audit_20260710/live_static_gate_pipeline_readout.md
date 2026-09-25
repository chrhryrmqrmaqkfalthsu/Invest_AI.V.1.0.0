# 라이브 후보 선정 파이프라인 구조와 v3·BOIL 정적 게이트 삽입 지점

- 기준일: 2026-07-11 KST
- 조사 방식: 코드·설정·기존 분석 산출물 read-only
- 운영·라이브·원본 코드 변경: 0건
- 설정·설계 파일 변경: 0건
- 재학습·주문·삭제: 0건

## 1. 최우선 결론

### 정확한 primary 삽입 지점

v3 도달불가와 BOIL형 정적 BLOCK은 다음 두 위치에 동일한 공통 판정으로 들어가야 한다.

- Stage2: `engine/live/elite_shadow_report.py`의 `collect_stage2_elite`, raw candidate가 만들어진 뒤이면서 정렬·ticker dedup·60개 cap 전 — 현재 코드 기준 **382~384행 사이**
- Stage3: 같은 파일의 `collect_stage3_elite`, candidate가 만들어진 뒤이면서 정렬·ticker dedup·80개 cap 전 — 현재 코드 기준 **472~473행 사이**

이 위치가 필요한 이유는 static BLOCK 뒤에 같은 ticker의 차순위 rule이 올라와야 하기 때문이다.

`live_candidate_slots.refresh_slots`의 report 수신 뒤에 차단하면 이미 stage별 ticker dedup과 cap이 끝났기 때문에 차순위 fallback을 복원할 수 없다.

### entry/exit 분리

**CONFIRMED**

위 primary 위치는 신규 후보 report와 신규 entry에만 영향을 준다.

- 실계좌 청산은 `PositionManager.check_exits/_check_one`이 `positions.json`, broker holdings, 포지션의 immutable `rulebook_snapshot`으로 수행한다.
- 이 청산 경로는 `live_candidate_slots`, `build_elite_shadow_report`, v3·BOIL 산출물을 호출하지 않는다.
- 기존 실계좌 보유 포지션을 게이트가 소급 청산하거나 unregister하는 경로는 발견되지 않았다.
- Shadow trader도 열린 포지션을 먼저 MTM·청산한 뒤 신규 후보 entry를 평가한다. report에서 후보가 사라져도 이미 열린 shadow position은 유지되고 exit 조건으로만 닫힌다.

### dry-run 입력과 live 입력

**현재 동일하지 않다.**

후보 ID와 원본 rulebook hash는 연결 가능하지만, dry-run의 핵심인 immutable 학습기간 분포와 BOIL 구조 판정 자료는 live runtime이 현재 읽지 않는다.

따라서 live의 현재 1년 OHLCV로 v3를 재계산하면 dry-run과 다른 정책이 된다. 정확한 parity를 위해서는 dry-run 입력으로 만든 candidate_id 기반 immutable static decision catalog를 사용해야 한다.

### shadow compare

가능하다.

동일한 pre-dedup hook에서 판정을 실행하되:

- `shadow`: would-block 결과만 기록하고 후보를 유지
- `block`: 동일 결과에서 `continue`

로 나누면 shadow와 enforcement가 같은 입력과 같은 판정식을 사용한다.

## 2. 후보 선정 전체 흐름

```text
Stage2 batch 완료 산출물
  └─ central_index.jsonl eligible=true
      └─ collect_stage2_elite
          ├─ stage/eligible
          ├─ expectancy / fitness / trades / win / stress / DD
          ├─ source survivor full rulebook 로드
          ├─ live anti-pattern filter
          ├─ [권장 v3 + BOIL STATIC HOOK]
          ├─ elite_score 정렬
          ├─ ticker dedup
          └─ Stage2 cap 60

Stage3 final_rulebooks.jsonl
  └─ collect_stage3_elite
      ├─ bull/stress metrics 구성
      ├─ expectancy / fitness / win / trades / DD
      ├─ live anti-pattern filter
      ├─ candidate_id와 source payload 구성
      ├─ [권장 v3 + BOIL STATIC HOOK]
      ├─ elite_score 정렬
      ├─ ticker dedup
      └─ Stage3 cap 80

Stage2 + Stage3
  └─ candidate_denylist 적용
      └─ A_core / elite_score report 정렬
          └─ live_candidate_slots.refresh_slots
              ├─ regular-hours gate
              ├─ 기존 20260707 KEEP/DROP_BAD_MAE gate
              ├─ held candidate 제외
              ├─ 현재 OHLCV·가격·시장·뉴스 evaluate_candidate
              ├─ should_buy=true
              ├─ final_score 우선순위 정렬
              └─ live_slots_state candidate_pool / slots / waitlist
                  └─ S2AutoTrader
                      ├─ candidate_pool 첫 후보
                      ├─ 현재 report에서 candidate_id 재조회
                      ├─ evaluate_candidate 재검증
                      ├─ SafetyLayer purpose=entry
                      └─ broker BUY + position 등록
```

보유·청산은 별도다.

```text
positions.json + broker holdings
  └─ PositionManager.check_exits
      └─ _check_one
          ├─ immutable position rulebook_snapshot
          ├─ ExitPolicy 또는 legacy exit
          ├─ stop / trailing / timeout / sell omen
          └─ broker SELL
```

## 3. 현재 필터의 정확한 위치

### Stage2

`engine/live/elite_shadow_report.py`

- `collect_stage2_elite`: 337~395행
- central index `eligible`: 346~350행
- expectancy `>=2.7`: 353~355행
- fitness `>=70`: 356~358행
- OOS 거래 수 `>=15`: 359~361행
- 승률 `>=70`: 362~364행
- stress expectancy `>=0.5`: 365~367행
- worst drawdown `>-18`: 368~370행
- min trade count `>=8`: 371~373행
- 원본 survivor row 로드: 374~377행
- anti-pattern: 378~381행
- raw candidate append: 382행
- elite sort: 384행
- ticker dedup: 385~392행
- cap 60: 393~394행

Stage2의 완료·완성도 전제는 `scripts/research/run_stage23_batch.py` 413~438행에서 `STAGE2_DONE/SKIPPED_EXISTING`만 central index에 쓰고 `eligible=true`를 붙이는 구조다.

이는 dry-run의 별도 completeness 판정과 완전히 같은 검사가 아니다.

### Stage3

`engine/live/elite_shadow_report.py`

- `collect_stage3_elite`: 398~484행
- `final_rulebooks.jsonl` 직접 스캔: 404~407행
- expectancy `>=2.7`: 424~426행
- fitness `>=45`: 427~429행
- 승률 `>=70`: 430~432행
- 거래 수 `>=8`: 433~435행
- worst drawdown `>-18`: 436~438행
- anti-pattern: 439~442행
- candidate 구성: 443~472행
- elite sort: 473행
- ticker dedup: 474~481행
- cap 80: 482~483행

현재 Stage3 live collector는 `final_rulebooks.jsonl`을 직접 읽고 `stage3_profile_catalog` 또는 dry-run의 `origin_complete`를 다시 검증하지 않는다.

### 공통 report

`build_elite_shadow_report`: 572~608행

- Stage2 수집: 573행
- Stage3 수집: 574행
- stage 결합: 575행
- denylist: 576행
- 최종 bucket/elite 정렬: 577행

중요하게도 denylist는 이미 ticker dedup과 stage cap이 적용된 뒤에 실행된다. 따라서 denylist로 빠진 ticker의 차순위 rule도 현재는 보충되지 않는다.

Dry-run은 denylist-before-dedup이므로 이 순서도 다르다.

### live candidate slots

`data/_system/ops/live_candidate_slots.py`

- `refresh_slots`: 346~469행
- report 생성: 381행
- max 93 slice: 382행
- 기존 gate map: 392~398행
- 보유 후보 제외: 399~400행
- 동적 평가: 402~413행
- `should_buy`: 414~418행
- pool 정렬·저장: 451~464행

이 파일은 candidate display/state generator이며 broker 주문을 직접 제출하지 않는다.

## 4. 삽입 지점 평가

### 4.1 권장 primary

#### Stage2 382~384행 사이

이 시점에는 다음이 모두 있다.

- candidate_id
- ticker/stage
- rulebook hash
- source file/row index
- full source rulebook
- metrics

그리고 아직 ticker dedup과 cap은 실행되지 않았다.

#### Stage3 472~473행 사이

이 시점에는 candidate_id와 final rulebook source가 결정됐으며 아직 ticker dedup과 cap 전이다.

### 4.2 권장 입력 형태

현재 live runtime에서 frozen 학습분포를 다시 계산하지 말고 다음 static catalog를 candidate_id로 조회하는 방식이 dry-run parity에 가장 가깝다.

```text
candidate_id
policy_version
v3_status / v3_reason
boil_status / boil_reason
source fingerprints
```

현재 report 82개는 모두 기존 v3 decision catalog에 존재한다.

- catalog coverage: 82/82
- 현재 report 안 v3 FAIL: 23개
- BOIL 전용 FAIL: 2개
- 합집합: 25개

그러나 이를 report 생성 뒤 단순 제거하면 57개만 남고 fallback이 없다. 이 숫자는 dry-run 84개를 재현한 결과가 아니다.

### 4.3 실행 직전 2차 확인

`engine/live/s2_auto_trader.py`

- `_candidate_full_payload`: 300~311행
- `_validate_candidate_signal`: 313~329행
- order plan 후보 재검증: 361~369행

이 지점에 동일 static catalog 재검증을 두는 것은 stale `live_slots_state`를 막는 defense-in-depth로 적합하다.

다만 이 지점만 primary gate로 사용하면 차순위 후보 fallback이 복원되지 않는다.

### 4.4 부적합 지점

`live_candidate_slots.refresh_slots` 381행 이후의 post-report 필터는 부적합하다.

- stage cap과 ticker dedup이 이미 끝남
- 차단된 ticker의 다음 rule이 사라짐
- 정적 gate인데도 현재 시세 조회 직전까지 후보가 남음
- dry-run 후보 수·구성과 parity 불가

`PositionManager`의 exit 경로에는 삽입하면 안 된다.

## 5. entry와 exit 분리 확인

### 실계좌 신규 entry

`S2AutoTrader`는 다음 경로만 사용한다.

```text
live_slots_state.candidate_pool
→ candidate_pool()
→ compute_order_plan()
→ _validate_candidate_signal()
→ SafetyLayer purpose=entry
→ broker.place_buy()
```

현재 설정은 다음과 같이 fail-closed 상태다.

- `master_enabled=false`
- `auto_buy_enabled=false`
- `auto_exit_enabled=false`

### 실계좌 보유·청산

`PositionManager`는 `positions.json`과 broker holdings를 읽고 각 포지션에 저장된 immutable rulebook snapshot으로 exit를 판단한다.

`PositionManager._check_one` 652~690행에서 ExitPolicy 또는 legacy exit를 선택하고, 719~747행에서 SELL을 보낸다.

다음 의존성이 없다.

- `build_elite_shadow_report`
- `live_slots_state.candidate_pool`
- v3 decision catalog
- BOIL decision catalog

따라서 권장 entry hook이 기존 보유 포지션을 소급 차단하거나 청산하는 경로는 없다.

### Shadow trader

`run_shadow_tick`은:

1. 757~770행에서 기존 open position을 먼저 MTM·청산
2. 792~853행에서 신규 candidate BUY를 평가

한다.

공유 report에 static gate를 넣으면 신규 shadow entry는 줄지만 기존 shadow position은 report membership으로 삭제되지 않는다.

## 6. dry-run과 live 입력 정합성

| 항목 | 정합성 | 설명 |
|---|---|---|
| candidate_id | 동일 | 현재 report 82개 모두 v3 catalog에서 식별 가능 |
| rulebook hash/source | 동일 | 같은 batch source row를 찾을 수 있음 |
| full threshold·weight | 부분 동일 | source row에는 있으나 compact candidate payload에는 빠짐 |
| 학습기간 | 불일치 | live report/evaluator에 train_start/end가 없음 |
| p01·p99·min·max | 불일치 | dry-run은 frozen train-window cache, live는 현재 1년 OHLCV |
| v3 판정 | 미연결 | 산출물은 있지만 live가 읽지 않음 |
| HIGH_VOL·nonvolume 구조 | 미연결 | BOIL precomputed 분석을 live가 읽지 않음 |
| completeness/history | 부분 불일치 | live의 eligible·elite 임계가 dry-run과 다름 |
| denylist 순서 | 불일치 | live는 cap 뒤, dry-run은 dedup 전 |
| cap 숫자 | 동일 | Stage2 60, Stage3 80 |
| 최종 후보 | 크게 불일치 | live 82와 dry-run 84의 ID overlap 16개 |

현재 live report:

- 82개
- Stage2 12 / Stage3 70

BOIL BLOCK dry-run 최종:

- 84개
- Stage2 10 / Stage3 74

ID 비교:

- 겹침: 16개
- live-only: 66개
- dry-run-only: 68개

즉 v3·BOIL 두 checker만 현재 live report에 추가하는 것은 “현재 live 정책에 두 blocker를 추가”하는 구현이지 “dry-run 84 파이프라인을 live로 옮기는 구현”은 아니다.

Dry-run 84의 완전한 parity가 목적이면 다음도 함께 정리돼야 한다.

- completeness/history 기준
- elite filter 임계와 score
- denylist-before-dedup
- fallback
- stage별 candidate inventory

## 7. feature flag 위치

기존 entry 설정 관행과 가장 가까운 위치는:

```text
data/_system/live_auto_config.json
└─ selection
```

이다.

개념적으로 필요한 모드는 다음 세 가지다.

```text
off
shadow
block
```

단, 현재 `live_candidate_slots`와 `elite_shadow_report`는 이 config를 읽지 않는다. 실제 구현 시 report 호출마다 읽어야 restart 없이 다음 cycle에 반영된다.

환경변수는 `EXIT_LIVE_POLICY`, `EXIT_LIVE_SHADOW`, direct-order flag처럼 hard cutover 관행에는 맞지만 process restart가 필요하다.

정적 entry gate flag는 `exit` 객체나 `EXIT_LIVE_POLICY`와 결합하면 안 된다. `selection` 아래에 독립적으로 있어야 exit와 격리된다.

## 8. shadow-compare 지점

Primary hook에서 동일한 판정 함수를 호출한다.

### shadow

- 판정 실행
- reason과 policy version 기록
- skipped counter/report summary에 would-block 집계
- 후보는 유지

### block

- 동일 판정 실행
- FAIL이면 같은 위치에서 `continue`
- 이후 elite sort·ticker dedup·stage cap이 fallback을 수행

관측 노출 지점:

- `elite_shadow_report` summary
- `live_slots_state.last_refresh.blocked_summary`
- `live_slots_events.jsonl`

별도의 post-report shadow 계산은 primary enforcement와 후보 inventory가 달라질 수 있으므로 권장하지 않는다.

## 9. 롤백 지점

### 동적 config rollback

설정을 매 report 호출마다 읽는 구조라면:

```text
block → shadow 또는 off
```

변경 후 다음 regular-hours 60초 refresh에서 기존 동작으로 복귀할 수 있다.

### 정규장 밖 주의

`refresh_slots` 371~375행은 정규장 밖에서 cached candidate_pool을 그대로 재사용한다.

따라서 정규장 밖 즉시 rollback에는 다음 중 하나가 필요하다.

- 명시적 force refresh
- daemon 재시작 후 강제 refresh
- 다음 정규장 refresh까지 대기

### 코드 rollback

Python 코드 원복 후 `scripts/live_candidate_slots_guard.sh`의 daemon restart 경로로 재시작해야 import된 코드가 제거된다.

Exit process는 entry gate 변경으로 재시작할 필요가 없다.

### stale state 안전

S2AutoTrader는 order plan 직전에 현재 report에서 candidate_id를 다시 찾는다. Primary report gate가 켜진 뒤 과거 candidate_pool에 남은 후보는 `candidate_not_found_in_current_report`로 fail-closed될 수 있다.

여기에 동일 static catalog 확인까지 추가하면 더 명확한 방어선이 된다.

## 10. 구현 진입 전 확인 조건

실제 구현 전 다음이 필요하다.

1. Dry-run과 동일 입력으로 candidate_id 기반 v3·BOIL static catalog 고정
2. policy version과 source fingerprint 검증
3. Stage2·Stage3 동일 pre-dedup hook 사용
4. shadow mode에서 현재 후보와 would-block 비교
5. denylist 순서와 live elite 기준을 dry-run 84에 맞출지 별도 결정
6. S2 실행 직전 동일 catalog fail-closed 확인
7. PositionManager 파일·호출 그래프 무변경 확인

## 11. 판정

- 정확한 entry 삽입 지점: **확인됨**
- 실계좌 exit·보유 경로 분리: **확인됨**
- 기존 포지션 소급 차단 경로: **발견되지 않음**
- Dry-run과 live 입력 완전 정합성: **아님**
- Candidate ID catalog 연결 가능성: **확인됨, 현재 report 82/82**
- Shadow compare 가능성: **확인됨**
- 현재 상태에서 곧바로 dry-run 84 parity 구현 가능: **아님**

핵심은 static catalog를 pre-dedup collector에 넣는 것이다. `live_candidate_slots` 뒤쪽에 단순 차단을 추가하면 entry/exit 분리는 지켜도 fallback과 dry-run parity를 잃는다.

## 12. 산출물

- `live_candidate_pipeline_filter_locations.csv`
- `live_static_gate_insertion_points.csv`
- `live_static_gate_input_parity.csv`
- `live_static_gate_flag_shadow_rollback.csv`
- `live_static_gate_pipeline_summary.json`
- `live_static_gate_pipeline_readout.md`
