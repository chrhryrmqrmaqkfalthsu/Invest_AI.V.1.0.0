# Replay 포지션 의존성 코드 감사

## 판정

**INCLUDED — replay `should_buy` 생성은 포지션 상태와 무관하다.**

- `engine/central/signal_collector.py:212-261`의 `signal_for_date(entity, date)`는 입력이 개체와 날짜뿐이다. 포지션/보유 상태 인자가 없으며 해당 날짜까지의 `df.iloc[:idx+1]`로 `evaluate_signal()`을 호출한 뒤 `sig.should_buy`를 그대로 반환한다.
- `data/_system/analysis/entry_filter_2d3pct_replay_20260712/finalize_replay_outputs.py:71-123`은 각 개체의 모든 OHLCV 거래일을 순회하고 `snap.should_buy`만 포함 조건으로 사용한다. 진입 후 날짜 점프나 보유 여부 조회가 없다.
- `engine/portfolio/daily_signal_replay.py:285-323, 440-496`도 진입일부터 청산일까지 각 날짜마다 evaluator를 다시 호출한다. 다만 이 모듈의 `daily_records`에는 명시적 `should_buy` 컬럼이 없고 `signal_valid`는 strength 계산 가능 여부이므로, 18,245행 universe의 직접 생성기는 아니다.

## 코드 검색 결론

`SignalCollector.signal_for_date`와 replay universe 생성 루프에는 `position`, `holding`, `open_position`, `in_position`을 조건으로 `should_buy`를 억제하는 분기가 없다. 따라서 2층 replay는 **순수 날짜별 should_buy 재평가**다.

## 역사 재현성 주의

이 replay는 과거 저장 로그의 bit-exact 복원이 아니라 **현재 rulebook + 현재 evaluator/context의 역사 재평가**다. 동일 candidate/date 기준으로 로그 전용 신호가 2,172개 존재하므로, 포지션 누락은 메웠지만 과거 로그의 strict superset은 아니다.
