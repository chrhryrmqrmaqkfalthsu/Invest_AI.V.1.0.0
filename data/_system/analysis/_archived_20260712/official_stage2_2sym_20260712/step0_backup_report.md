# Step 0 — 정식 Stage 2 2종목 실행 전 백업

## 신규 출력 경로 생성 전 상태

```text
data/_system/analysis/official_stage2_2sym_20260712: NOT_EXISTS
Git worktree: clean
market_history.csv SHA-256: 35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38
daemon PID 494330: running
```

## 사전 백업

```text
backup/pre_official_stage2_2sym_20260712T204738Z.tar.gz
backup/pre_official_stage2_2sym_20260712T204738Z.manifest.sha256
```

포함 범위:

- 복구된 `market_history.csv`
- `market_history_v2.csv`
- `market_history.csv.before_6y`
- 정식 Stage 2 진입점과 market consumer 코드
- Stage 2 gate·rulebook·GA 관련 코드
- 오늘 pilot floored·consistency·lambda 비교 원본

Archive manifest 검증: `OK`

## 실행 보호 원칙

현재 `market_history.csv` 마지막 거래일은 2026-07-10이고, 정식 `get_market_history()`는 달력일 차이가 1일을 초과하면 자동 재생성 후 같은 파일에 쓴다. 따라서 정식 Stage 2 코드는 변경하지 않고 런타임에서 `get_market_history()`만 복구 CSV를 직접 읽는 read-only loader로 치환한다.

이 치환은 다음만 차단한다.

```text
stale cache 자동 refresh
market_history.csv 쓰기
```

다음 정식 경로는 유지한다.

```text
prepare_ticker_context
D-1 lookup_market_at_lagged
evaluate_signal market adjustment
정식 Rulebook GA
정식 Stage 2 gate
```
