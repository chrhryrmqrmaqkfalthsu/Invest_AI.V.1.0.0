# Stage3 청산 GA 학습·혼입 상태 감사

## 판정

**Stage3 exit GA는 이번 rolling 파일럿에서 학습되지 않았고 호출되지도 않았다.**

- 학습 여부: `NOT_RUN`
- rolling 점수 청산과 exit GA 동시 존재: `NO`
- 충돌·중복: `NO`
- 복사본 기준 커밋: `96ee50edfefcb9d06dab13ba67689a8d5c6ff477`

## 현재 Stage3 호출 경로

현재 복사본 `scripts/research/run_stage3_aggressive.py`의 SHA-256은 `0df54a52daea67ca438708bc7708790eeeb3bc95138b6e5215cd725450b5728d`다.

해당 파일 21~25행은 Stage3 전용 qualify→entry→exit orchestration을 실행하지 않고, 복사본 `run_stage2_path_filter`가 노출하는 `run_stage2.main()`으로 바로 위임한다.

```python
from scripts.research.run_stage2_path_filter import run_stage2


def main(argv: list[str] | None = None) -> int:
    return run_stage2.main(argv)
```

현재 실행 파일에는 다음이 없다.

- `engine.pipeline.exit_gene` import
- `run_exit_ga()` 호출
- `EXIT_POPULATION`, `EXIT_GENERATIONS` 참조
- `entry_rulebooks.jsonl` 또는 `final_rulebooks.jsonl` 생성
- `max_holding_days` 기반 청산 연결

## 실제 학습·백테스트 호출 경로

복사본 `scripts/research/run_stage2.py` 45~50행은 이번 파일럿의 유일한 GA인 `train_interval_ga`를 import한다. 374~383행에서 train regime의 12개 feature와 `label_2d3pct`로 interval GA를 한 번 학습한다.

```text
run_stage2_path_filter.py
→ run_stage2.main()
→ run_pilot()
→ train_symbol_worker()
→ train_interval_ga()
→ regime별 strict-AND score 생성
→ rolling_score_backtest()
```

같은 파일 439~447행에서 학습된 interval gene으로 train·stress·OOS의 일별 mask와 score를 만들고, 517~524행에서 rolling과 fixed-two-session 비교 백테스트를 각각 별도 method로 실행한다.

`fixed_two_day_backtest`는 비교군일 뿐 rolling 거래에 청산 조건을 주입하지 않는다. `rolling_vs_fixed_backtest.csv`에서도 두 결과는 `method` 열로 분리돼 있다.

## 과거 Stage3 exit GA 코드가 존재하는 이유

복사본에는 수정 전 동적 원본 orchestration을 보존한 다음 파일이 함께 있다.

- `scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001`
- SHA-256: `bc3e191a449d6b67980dd0884a2a510acf9ecda719a8b12e76b0e8178de33004`

이 보존 파일에는 실제 Stage3 exit GA 구현이 있다.

- 102~103행: `EXIT_POPULATION = 60`, `EXIT_GENERATIONS = 25`
- 790~852행: `_run_exit_ga_for_entry()` 구현
- 855~884행: `run_exit_ga()`가 entry rulebook별 exit GA를 실행하고 `final_rulebooks.jsonl`을 기록
- 1456~1466행: 과거 CLI의 `--stage exit` 또는 `--stage all` 경로에서 `run_exit_ga()` 호출

또한 `engine/pipeline/exit_gene.py`도 dependency snapshot으로 복사돼 있다. 그러나 현재 실행되는 `.py` 파일을 대상으로 한 import·호출 검색에서 이 모듈을 참조하는 경로는 0건이었다. 과거 구현은 `.bak` 증거로만 존재하며 현재 진입점에서 로드되지 않는다.

## 산출물 증거

파일럿 산출물과 worker 결과 50개를 조사한 결과:

- `training_log.csv` 열은 interval GA의 fitness·precision·generation·gene 검증 정보뿐이다.
- worker 결과 JSON 50개의 top-level key는 `training_rows`, `bounds_rows`, `metric_rows`, `backtest_rows`, `whipsaw_rows` 등이며 exit-GA 관련 key가 0개다.
- `entry_rulebooks.jsonl`: 없음
- `final_rulebooks.jsonl`: 없음
- `last_run_summary.json`: 없음
- `exit_ga_log.csv`: 없음
- `stage3_exit_ga_gen` 이벤트 산출물: 없음
- `max_holding_days` 또는 exit gene이 적용된 백테스트 row: 없음

반면 기존 rolling 결과는 전부 `method=rolling_same_threshold_no_holding_cap`으로 저장돼 있고, 재구성한 150개 종목×regime 조합이 기존 집계와 정확히 일치했다.

## rolling 청산과의 관계

현재 rolling 백테스트의 청산 경로는 두 가지뿐이다.

1. 일별 strict-AND 점수가 동일 임계선 아래로 하락하면 D0 시가 청산
2. regime 마지막 날까지 active면 마지막 D0 종가로 강제 mark-to-market

별도 exit gene, max holding, stop-loss, take-profit, 2일 라벨 미달 청산은 결합되지 않았다.

## 결론

과거 Stage3 청산 GA 코드는 복사본 안에 보존돼 있지만 **실행 파일이 아니라 `.bak` 및 미참조 dependency로만 존재한다.** 이번 파일럿은 Stage3 exit GA를 학습하거나 rolling score 청산과 결합하지 않았다. 따라서 exit GA로 인한 중복·충돌·오염은 없다.
