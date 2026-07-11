# direct Event OFF 전환 — 2단계

## 변경

`config/policy.yaml`의 한 줄만 변경했다.

```diff
-  direct_event_enabled: true
+  direct_event_enabled: false
```

운영 코드 변경은 0건이다.

다음은 변경하지 않았다.

- `engine/market/context.py`
- `ctx.score`
- `active_events`
- 학습·backtest·연구 경로
- 룰북 artifact

## 커밋

정책 전환 커밋:

```text
7d709cb
라이브 direct Event 진입 축 비활성화: 정책 스위치를 false로 전환
```

## 재시작 전 안전성

Alpaca live open orders:

```text
0건
```

`live_slots_state.json` JSON 정합성:

```text
정상
slots=8
candidate_pool=18
```

따라서 daemon 재시작을 진행했다.

## daemon 재적재

기존 PID:

```text
452686
```

SIGTERM 후 3초 내 정상 종료했다.

Guard 재기동 결과 새 PID:

```text
460254
started 2026-07-11 14:06:08 UTC
```

새 process에서 정책 확인:

```text
direct_event_enabled=False
```

따라서 최신 설정이 적재됐다.

## 실제 OFF 재평가

현재 `live_slots_state.json` candidate_pool의 18개 candidate ID를 원본 elite report와 exact match한 뒤, 동일 live evaluator인:

```text
engine.live.elite_shadow_trader.evaluate_candidate()
```

로 실제 OFF 평가했다.

평가 결과:

```text
evaluated=18
missing=0
```

### 실제 탈락 — 7개

```text
BTBT
BMA
BMI
BNTX
CMC
BWXT
ACMR
```

### 실제 생존 — 11개

```text
CRS
BCS
ALGT
BN
ADMA
BB
ANET
ARKW
CBRL
CRK
AEIS
```

예상 탈락 7개와 실제 탈락 7개가 정확히 일치했다.

## 실제 OFF score

| 티커 | OFF score | threshold | 결과 |
|---|---:|---:|---|
| BTBT | 1.8154976301 | 1.9112577024 | DROP |
| BMA | 1.9607261011 | 2.8483915860 | DROP |
| BMI | 1.9097334984 | 2.4293027201 | DROP |
| CRS | 4.6620737034 | 2.5574757833 | SURVIVE |
| BCS | 4.9308408026 | 3.3016768174 | SURVIVE |
| BNTX | 1.1370508448 | 1.9606991236 | DROP |
| ALGT | 5.5972934959 | 2.2937281833 | SURVIVE |
| CMC | 1.0491896730 | 2.3905477216 | DROP |
| BN | 3.0183079336 | 2.6480219614 | SURVIVE |
| BWXT | 1.0138423397 | 2.0158091740 | DROP |
| ADMA | 2.7507334851 | 2.1792906247 | SURVIVE |
| BB | 3.2909034678 | 2.7919511742 | SURVIVE |
| ACMR | 1.5623539703 | 1.6721522264 | DROP |
| ANET | 3.0250970653 | 2.6390902780 | SURVIVE |
| ARKW | 3.5339493058 | 2.5633833255 | SURVIVE |
| CBRL | 2.7996952679 | 1.9047037428 | SURVIVE |
| CRK | 1.8851749773 | 1.6845297733 | SURVIVE |
| AEIS | 2.4454730236 | 1.6391176454 | SURVIVE |

## market_score 불변 검증

실제 OFF 18개 평가에 동일 MarketContext를 사용했다.

```text
actual OFF batch market_score=73.84502206332408
```

추가로 동일 입력에서 explicit ON/OFF pair를 18개 모두 계산했다.

각 pair에서:

```text
market_score_on == market_score_off
```

결과:

```text
18/18 true
```

pair 진단 batch의 market score는:

```text
73.8
```

실제 OFF batch와 pair batch의 값 차이는 서로 다른 `get_market_context()` 호출 시점의 context 표현 차이다. 핵심 불변식은 각 동일 입력 pair 내부에서 ON/OFF가 같은 market score를 사용한다는 것이며, 18개 모두 성립했다.

## score 변화 불변식

동일 입력 ON/OFF pair에서 다음을 검증했다.

```text
market_adjustment_on == market_adjustment_off
score_on - score_off == event_component × market_adjustment
```

결과:

```text
market adjustment equality: 18/18
score delta invariant: 18/18
```

따라서 score 변화는 direct Event 제거분만으로 설명된다.

양수 Event 후보는 score가 내려갔다.

음수 Event 후보는 OFF 시 score가 올라갔다.

- BN
- ADMA
- CRK

Event 0 후보는 score 변화가 없었다.

- ALGT
- BB
- ANET
- ARKW
- CBRL
- AEIS

## 주의

`config/policy.yaml`의 주석에는 과거 설명인 “기본값 ON” 문구가 남아 있다. 사용자 제약이 한 줄 변경이었으므로 주석은 수정하지 않았다. 실제 runtime 값은 `false`다.

## 롤백

### config 한 줄 복구

```yaml
live:
  direct_event_enabled: true
```

변경 후 daemon 재시작이 필요하다.

### 커밋 revert

```bash
cd ~/kingmaker
git revert 7d709cb
git push
```

그 뒤 live candidate slots daemon을 재시작한다.

## 산출물

- `data/_system/analysis/direct_event_off_phase2_20260711/actual_off_results.csv`
- `data/_system/analysis/direct_event_off_phase2_20260711/on_off_pair_invariants.csv`
- `data/_system/analysis/direct_event_off_phase2_20260711/readout.md`
