# Runtime dry-run process audit

```text
workspace: scripts/research/stage23_rework_20260713/
ticker: AAP
fold: train_1
population: 10
generations: 3
seed: 20260713
fetch: 0
```

실행 입력:

```text
data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv
```

실행 경로:

```text
build_entry_feature_domain
run_ga(gene_scope=entry, entry_feature_domain=...)
run_entry_backtest_period
run_backtest_period (경로 분리 대조용 1회)
```

사전 백업:

```text
backup/pre_stage3_runtime_dry_run_20260713T072532Z.tar.gz
backup/pre_stage3_runtime_dry_run_20260713T072532Z.manifest.sha256
```

첫 실행은 잘못된 상대경로로 인해 AAP 파일 로드 전에 중단됐다. GA 실행은 없었다. 저장소 루트 절대경로로 수정 후 초소형 dry-run을 한 번 실행했다.

정식 Stage 3 GA, exit GA, validate 전체 pipeline은 실행하지 않았다.
