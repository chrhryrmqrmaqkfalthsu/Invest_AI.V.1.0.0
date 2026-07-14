# AAP 새 fitness v2 — VM 6프로세스 / 노트북 최대 자원 독립 실행 preflight

- 작업일: 2026-07-14
- Seed: `2026071401`
- 요청 규모: qualify population 100 / generations 40 × 3 fold
- 요청 실행 1: VM 독립 parent + 로컬 6프로세스
- 요청 실행 2: Windows 노트북 독립 parent + 로컬 최대 자원
- 최종 상태: **BLOCKED_FAIL_CLOSED**
- GA·백테스트·재학습 시작 여부: **아니오**

## 공통 manifest gate 실패

현재 뉴욕 날짜는 `2026-07-14`다. 미국 시장 calendar cache가 요구하는 최신 완료 거래일은 `2026-07-13`이지만, root 단일 소스 `data/_system/market_history.csv`는 `2026-07-10`까지다.

```text
basis: latest_us_market_session_strictly_before_as_of_new_york_date
expected_latest_session: 2026-07-13
snapshot_last_date: 2026-07-10
missing_sessions: [2026-07-13]
fresh: false
```

지시서의 `거래일 freshness 실패 시 fail-closed`, `auto-fetch·auto-regenerate 차단`, 보호 파일 read-only 조건에 따라 다음을 수행하지 않았다.

- VM GA 시작
- 노트북 GA 시작
- 시장 데이터 자동 갱신
- snapshot 자동 재생성
- 과거 snapshot을 현재 것으로 위장하는 as-of 우회

따라서 VM 결과와 노트북 결과는 생성되지 않았다. 이 디렉터리는 실제 학습 결과가 아니라 preflight 실패 기록이다.

## 노트북 실행 채널 상태

Dask scheduler에 연결된 worker는 다음 VM worker 한 개뿐이었다.

```text
tcp://127.0.0.1:46481
OS: Linux
nthreads: 8
status: running
```

Windows 노트북 worker/control channel은 연결돼 있지 않았다. 따라서 공통 manifest gate가 통과했더라도 이번 시점에는 노트북 standalone parent 프로세스를 원격 기동할 수 없는 상태였다.

## 새 fitness 소스 상태

```text
execution_mode_backtest.py SHA-256
e6901ec6e685ad8ad30499cdbb9dfac2db4ce0a6e5a731ce8f724bf86a64c21a

genetic.py SHA-256
28a5f1b3485ad6fb03b654f58080d847e6f3eec42d0c3003e956b6928c25389f
```

코드에 반영된 entry-scope 규칙:

- 주목표: 비용 차감 실현수익 / 보유일 평균
- 실현손실 벌점: `avg(max(0, -1.0 - pnl_pct))`
- 승: 비용 차감 실현수익 `> 0.5%`
- hard gate: `trade_count >= 12 AND win_rate >= 60%`
- Stage 2 `gene_scope='legacy'` 기본값 유지

이번 preflight 실패 기록을 위해 source 파일은 수정하지 않았다.

## 보호 파일 SHA-256

```text
.env
da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce

market_history.csv
35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38

market_history_v2.csv
b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611
```

## Daemon

필수 daemon PID `494330`을 유지했다. GA 프로세스는 시작하지 않아 PID 충돌이 없었다.

## Git

사전 백업 커밋:

```text
895fde0 AAP 새 fitness VM·노트북 독립 실행 전 fail-closed 상태 백업
```

최종 산출물 커밋은 SHA 검증 후 기록한다.

## 실행 재개에 필요한 조건

두 조건이 모두 충족돼야 실제 두 독립 결과를 만들 수 있다.

1. root `market_history.csv`가 `2026-07-13` 세션까지 포함되고, 새 고정 SHA가 manifest 정책과 일치해야 한다.
2. Windows 노트북 worker/control channel이 다시 연결돼야 한다.

그 전에는 지시서상 fail-closed가 올바른 결과다.
