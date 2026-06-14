# Project Structure

## Runtime entry points

- `scripts/run_live.py`: primary paper/live trading entry point.
- `scripts/run_bot.py`: legacy Telegram-oriented entry point; keep separate from `run_live.py` while migration compatibility is required.
- `run_parallel_learn.sh`: parallel learning launcher.

## Core packages

- `engine/core`: configuration, data loading, indicators, metadata, feature-lag rules, and exit policy primitives.
- `engine/adapters`: market and asset-type adapters.
- `engine/market`: market context and sentiment data integration.
- `engine/strategies`: rulebooks, signal evaluation, news features, and exit simulation.
- `engine/learning`: backtesting, genetic optimization, learner orchestration, and grading.
- `engine/pipeline`: screening, rolling validation, Stage 2/3 gates, scoring, and full training orchestration.
- `engine/portfolio`: portfolio-level replay, allocation, rebalance, and bias probes.
- `engine/live`: broker integration, scheduler, order reconciliation, safety controls, reporting, and Telegram operations.
- `engine/storage`: persistence abstraction.
- `engine/ai`: assistant and background training coordination.

## Supporting areas

- `scripts/pipeline`: normal pipeline launchers and reports.
- `scripts/research`: experimental gates, sweeps, and research runs.
- `scripts/live`: dry-run and live migration verification tools.
- `scripts/analysis`, `scripts/screening`, `scripts/comparison`: offline inspection utilities.
- `tests`: the only pytest discovery root.
- `docs`: stable specifications, designs, and completed gate reports.
- `config/policy.yaml`: runtime trading and safety policy. Do not store credentials here.

## Generated and local-only areas

- `data`: market data, learned artifacts, runtime state, and manual backups. It is large and must not be scanned by pytest.
- `logs`: runtime logs.
- `live`: runtime state.
- `exp_*`: generated experiment outputs. Keep reusable code under `scripts/research`; promote durable conclusions to `docs`.
- `backup`, `*.bak*`, `*.before_*`, `*_old.py`: temporary historical copies. Git history is the preferred long-term source of old code.
- `venv`, `__pycache__`, `.pytest_cache`: local environment and caches.

## Current execution flow

### Research and training

`pipeline/orchestrator.py` -> screening -> rolling validation -> full training -> GA/backtest -> Stage 2/3 gates -> promotion artifacts.

### Paper/live trading

`scripts/run_live.py` -> broker factory + live universe -> `Runner` -> market context/rulebook -> approval and safety layers -> pending-order/position reconciliation -> broker -> reporting and Telegram notifications.

## Cleanup rules

1. Preserve the active `run_live.py` process and its current files during cleanup.
2. Do not read, move, or commit `.env`, credentials, broker tokens, or SSH material.
3. Do not mix generated `exp_*` outputs with reusable source code.
4. Move durable experiment conclusions into `docs`; keep large raw trades and intermediate artifacts outside Git.
5. Remove source backups only after confirming the current file is tracked and the backup is archived or reproducible from Git history.
6. Run focused tests before broad tests because the working tree may contain active research changes.
