# Step 0 — 직전 파일럿 보존 및 실행 전 무결성

- 작업일: 2026-07-12 UTC
- 직전 결과 보존 경로: `data/_system/analysis/stage2_3_rediscovery_pilot_20260712/_prev_run_gaShrunk_exitBug/`
- 삭제한 파일: 0
- 이동 보존한 최상위 항목: 24개
- 직전 `_worker_tmp`, `_aborted`, CSV·JSON·Markdown·manifest를 모두 하위 폴더로 이동했다.
- 새 실행은 동일한 최상위 산출 경로를 재사용한다.
- 새 50종목 목록은 보존된 `_prev_run_gaShrunk_exitBug/symbol_list.csv`를 정확히 재사용한다.

## 실행 전 기준선

| 대상 | SHA-256 / 상태 |
|---|---|
| `.env` | `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce` |
| `data/_system/ops/live_candidate_slots.py` | `259d3bec12901591c84cd1ad9aec01612d914c9120c0976b54bb34adfe684dbb` |
| `engine/central/signal_collector.py` | `fc0768235189c5a6f95926d2c4f42aa78401e11b8fa2a8ab95992515a700f497` |
| 원본 `engine/learning/execution_mode_backtest.py` | `efd0a9edea250efaa6b70163bd5d44b5695098be74c485b0cb78643a559bcae0` |
| 원본 `engine/strategies/evaluator.py` | `d7ce157564c3311d95ba73de79f41dfad3d7d1134727dd8a5fa776487cd83584` |
| 원본 `scripts/research/run_stage2.py` | `9a83b1490b669176fbfdd50d6ce48c1fbdfdd9fa1c6525d91ed83af82c70165c` |
| 원본 `scripts/research/run_stage2_path_filter.py` | `52fda4ce2b047561f3b2eda5f6d5985e0b24232f2eade96a3a199734ad155a44` |
| 원본 `scripts/research/run_stage3_aggressive.py` | `8f275ca52745b6b9f92d56e0e24d8043ccef8644b5c5d996217b9c6226e701c0` |
| daemon | PID `494330`, 실행 중 |
| Git 기준 커밋 | `ac16d8c94bace68f375632ed12da6bceb8a38e53` |

## 사전 백업

- `backup/pre_rolling_pilot_rerun_gaOriginal_targetExit_20260712.tar.gz`
- `backup/pre_rolling_pilot_rerun_gaOriginal_targetExit_20260712.manifest.sha256`
- 사전 manifest 검증: `OK`

라이브 코드·후보 풀·정렬·daemon 설정·`.env`는 수정하지 않는다.
