# 단방향 threshold 설계 의도 근거

## 확인된 코드 근거

### 최종 매수 판정

`engine/strategies/evaluator.py` 모듈 설명과 구현은 동일하게 단방향을 명시한다.

```python
# 점수 ≥ rulebook.signal_threshold 이면 매수 신호
should_buy = final_score >= rb.signal_threshold
```

`Rulebook`에는 `signal_threshold` 하나만 있고 `signal_upper_threshold`, `score_high`, `score_band` 같은 필드는 없다. `PARAM_RANGES`도 `signal_threshold: (1.5, 4.0)` 하나만 탐색한다.

### RSI는 예외적으로 양단 학습

```python
rsi_ok = rsi_low <= rsi <= rsi_high
```

`Rulebook`과 `PARAM_RANGES`에 `rsi_low`, `rsi_high`가 모두 존재한다. 따라서 현 구조가 band 자체를 전혀 표현하지 못하는 범용 엔진은 아니며, 축별로 스키마를 별도 설계한 구조다.

### 일부 블록에는 명시적 상한 존재

NewsTopics는 `news_block_cap`으로 `[-cap,+cap]` clamp를 수행하고, 시장 보정은 `market_adjustment_strength`로 상하한을 둔다. 반면 Event와 최종 score에는 상한이 없다.

## 과적합 방지 의도 탐색 결과

다음 표현을 코드·주석·문서·커밋에서 검색했다.

- 과적합 방지
- 파라미터 축소
- band 미사용
- 상한 미사용
- score upper bound
- one-sided threshold

최종 score를 단방향으로 둔 이유가 과적합 방지라는 직접 근거는 찾지 못했다.

`engine/learning/backtest.py`의 complexity penalty는 다음 세 mask만 센다.

- `use_news_global`
- `use_event_block`
- `use_market_entry_adjustment`

즉 feature block 활성 개수에 대한 penalty이지, threshold 파라미터 수나 band 사용 여부에 대한 penalty가 아니다. 기본 인자도 `complexity_penalty_per_mask=0.0`이다.

과적합 관련 문서와 promotion gate는 존재하지만, score 상한을 두지 않는 이유와 연결된 근거는 없다.

## Git 이력

`should_buy = final_score >= rb.signal_threshold`는 최초 전략·학습 엔진 도입 커밋 `59b8a47`에서 들어왔고 현재까지 유지됐다.

Git 이력에서 다음 근거는 확인되지 않았다.

- 양단 score band를 제거했다는 변경
- 상한을 실험했지만 과적합 때문에 폐기했다는 기록
- 파라미터 수 축소를 위해 upper threshold를 의도적으로 제외했다는 기록

따라서 단방향 구조가 오래 유지된 사실은 확인되지만, 그 목적이 과적합 방지였다는 근거는 없다.
