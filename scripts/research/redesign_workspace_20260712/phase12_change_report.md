# Strict-AND redesign Phase 1·2 변경 보고

## 변경 전 SHA-256

```text
docs/redesign/strict_and_interval_redesign_20260712.md
b9a752fd9ee5f97d364f9458577c55a8fce213ac63442eaf932f13a1f782c9c7

engine/strategies/rulebook.py (workspace)
c7b2892f410cd1b25b8090fe26b2b6daaa0aa4bfeaa28555cf4c8b6d12cb15dc

engine/strategies/evaluator.py (workspace)
d7ce157564c3311d95ba73de79f41dfad3d7d1134727dd8a5fa776487cd83584

engine/learning/genetic.py (workspace)
89611d799fdca69d7a8e149898f5652f7e4ef5d020349f567919a548bf4361ad

engine/learning/execution_mode_backtest.py (workspace)
efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0
```

## 변경 후 SHA-256

```text
docs/redesign/strict_and_interval_redesign_20260712.md
bb00d6e30f264476d8578bc8722232132bb2459e5f3d525d9828f5a5e318d544

engine/strategies/rulebook.py (workspace)
c883ac1b58376d1d283c401e889bf793216a1f3e98a0d8e53ab320ae67dcda65

engine/strategies/evaluator.py (workspace)
3342aee01555a5a4db53c370e2500e9b152f0055c5c9725c2f37ef22c6bf2743

engine/learning/genetic.py (workspace)
96b1548f23bb77c2ac4c4c8afb5aa13645414dbc28977284363a970b1fc516ac

engine/learning/execution_mode_backtest.py (workspace)
8c2406f11ea99fef737aed03d794dd41aad5b721d90a288553e684d7f8168f5d
```

## Phase 1

- Fitness를 거래별 `pnl_pct / max(holding_days, 1)`의 평균으로 확정했다.
- MDD 사고/방치 구분은 미결로 유지하고 필요한 일별 진단 필드를 명세했다.
- 5개 연속 feature의 bilateral `[0,1]` interval schema를 추가했다.
- 편측, NaN/Inf, domain 밖, `high<=low`, 최소폭 미달, near-full 남발을 거부한다.

## Phase 2

- 합산 점수 진입을 제거하고 5개 feature strict-AND로 교체했다.
- 시장 context는 boolean 진입이 아니라 position-sizing quality만 보정한다.
- low/high pair-preserving crossover와 pair mutation을 구현했다.
- 보유 중 매 거래일 strict-AND를 재평가한다.
- interval break, ATR stop, 고정 7일 상한만 청산에 사용한다.
- 익절·trailing은 workspace execution에서 제거했다.
- 기술 feature는 workspace 전용 D-5 lag, context는 기존 D-1 계약을 유지한다.

## 구조 검증

```text
초기 개체 500개 interval invalid: 0
교배·변이 1,000회 invalid: 0
편측 payload: 거부
NaN payload: 거부
한 feature만 interval 밖: 전체 신호 FAIL
fitness 예시: (+6/3 + -2/2) / 2 = +0.5
execution smoke: holding_days <= 7
```

## Diff 요약

```text
5 files changed
1,211 insertions
1,147 deletions
```

정식 원본 4개 파일 SHA는 Phase 0 값과 동일하다.
