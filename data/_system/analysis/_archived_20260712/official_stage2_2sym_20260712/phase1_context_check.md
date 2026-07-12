# Phase 1a — Stage 2 market context 연결 확인

## 판정

```text
PHASE1_CONTEXT_GATE: PASS
```

정식 `prepare_ticker_context(ticker)`를 호출했다. 단, 복구된 `market_history.csv`가 주말 stale 판정으로 자동 재생성되는 것을 막기 위해 런타임에서 `get_market_history()`만 read-only loader로 고정했다. 정식 Stage 2·backtest·evaluator 코드는 수정하지 않았다.

## 시장 이력

```text
경로: /home/g3000kkw/kingmaker/data/_system/market_history.csv
행수: 1,759
기간: 2019-07-11 ~ 2026-07-10
로드 후 v2 병합 컬럼 수: 30
SHA-256 전/후: 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
파일 변경: 없음
```

## 종목별 context

| ticker | 회사명 | OHLCV 행수 | OHLCV 기간 | sector_name | sector 컬럼 | 존재 | market rows |
|---|---|---:|---|---|---|---|---:|
| AAP | Advance Auto Parts, Inc. | 1,525 | 2020-06-15 ~ 2026-07-10 | tech | `sector_tech` | YES | 1,759 |
| POWI | Power Integrations, Inc. | 1,525 | 2020-06-15 ~ 2026-07-10 | tech | `sector_tech` | YES | 1,759 |

## Gate 결과

두 종목 모두 다음 조건을 통과했다.

```text
market_history_df is not None
market_history_df rows == 1,759
market period covers 2019-07 ~ 2026-07
sector_name is non-empty
sector_tech column exists
market_history.csv SHA unchanged
```

## 실행 범위

```text
prepare_ticker_context: 실행
GA·학습: 미실행
run_stage2: 미실행
market_history refresh: 차단
market_history write: 0건
```
