# Kingmaker 운영 백로그

## 운영 안정성

- [ ] SafetyLayer / runner 차단 사유를 `manual_buy_intent.json`과 `central_buy_candidates.json`에 원문 code/reason으로 저장한다.
  - 배경: 현재 수동 매수 intent와 후보 파일에는 `manual_timing`, `runner blocked or did not attempt order`처럼 뭉뚱그려 기록되어, 실제 원인(`DAILY_LOSS`, `LIMIT_NOTIONAL`, `EXISTING_POSITION`, `same-day reentry` 등)을 로그 grep으로만 확인해야 한다.
  - 목표: 대시보드와 JSON 파일만 봐도 차단 원인을 즉시 알 수 있게 한다.
  - 구현 메모: `SafetyDecision.code`, `SafetyDecision.reason`, central-control의 same-day reentry 제외 사유를 candidate/intent record의 `block_code`, `block_reason`, `note` 등에 일관되게 남긴다.
  - 주의: stale/terminal 후보를 임의로 pending으로 되돌리는 기능과 섞지 않는다. 원인 표시 개선만 먼저 수행한다.
