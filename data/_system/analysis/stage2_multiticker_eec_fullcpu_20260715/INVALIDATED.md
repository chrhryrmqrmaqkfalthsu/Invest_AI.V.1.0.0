# INVALIDATED — stage2_multiticker_eec_fullcpu_20260715

Status: **INVALID / DO NOT USE FOR DECISION-MAKING**

This Stage2 multi-ticker chunked run was stopped and invalidated at the user's request after suspected methodological/configuration issues were identified during execution.

## Scope invalidated

The entire run directory is invalidated:

`data/_system/analysis/stage2_multiticker_eec_fullcpu_20260715/`

This includes completed, partial, interrupted, and copied outputs for:

- ALGT
- MPC
- FIX
- CMC
- STM
- PLXS
- CAT
- STLD
- POWI
- IRM
- CEF
- AGI
- CIGI

Any interim survivor counts previously observed in chat, including FIX survivor counts, must be treated as **invalid until a corrected rerun is performed**.

## Stop action

All active Stage2 learning jobs launched by this session were stopped:

- VM `run_chunked.py` / `run_fullcpu.py` jobs
- VM queue scripts
- Notebook `run_chunked.py` / `run_fullcpu.py` jobs
- Notebook `notebook_queue.py`
- Notebook multiprocessing spawn child workers associated with the Stage2 run

Dask worker processes themselves were intentionally left alive.

## Verified after stop

- Notebook `DESKTOP-TO74AR2` reported no remaining Stage2 Python jobs matching this run.
- Notebook memory after cleanup: about 43.5% used, 17.92 GB free of 31.73 GB total.
- Protected file SHA values remained unchanged:
  - `.env`: `da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce`
  - `data/_system/market_history.csv`: `35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38`
  - `data/_system/market_history_v2.csv`: `b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611`
- daemon PID `494330` remained alive.

## Next use

Do not reuse these outputs except for debugging/audit. A corrected run should use a new RUN_ID and explicitly state the corrected assumptions before launch.
