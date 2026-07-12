# Validation report

- 검증 시각: 2026-07-12 UTC
- 대상 디렉터리: `data/_system/analysis/crs_full_forensics_20260711/`
- CSV 파싱 오류: 0
- JSONL 파싱 오류: 0
- 정적 source inventory SHA-256 불일치: 0
- 산출 파일 수: 17 (`manifest.sha256`, 본 보고서 포함)
- mutable source 예외: `data/_system/live_slots_state.json`, `logs/live_candidate_slots_daemon_guard.log`
- 원본 코드 변경: 0

검증 결론: **PASS**

`manifest.sha256`는 본 보고서 생성 직전의 15개 핵심 산출물 해시를 고정한다. 본 보고서와 manifest 자체의 최종 Git blob 무결성은 Git commit으로 추가 고정한다.
