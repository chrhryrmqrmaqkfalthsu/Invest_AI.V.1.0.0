#!/usr/bin/env bash
set -euo pipefail
cd ~/kingmaker
RUN_ROOT="data/_system/analysis/stage2_multiticker_eec_fullcpu_20260715"
LOG_ROOT="$RUN_ROOT/_logs"
mkdir -p "$LOG_ROOT"
TICKERS=(CAT STLD POWI IRM CEF AGI CIGI)
for T in "${TICKERS[@]}"; do
  OUT="$RUN_ROOT/${T}_chunked/run"
  LOG="$LOG_ROOT/${T}_chunked_vm_driver.log"
  echo "[$(date -Is)] QUEUE ticker=$T" | tee -a "$LOG_ROOT/vm_queue.log"
  if [[ -f "$OUT/summary.json" ]]; then
    echo "[$(date -Is)] SKIP completed ticker=$T" | tee -a "$LOG_ROOT/vm_queue.log"
    continue
  fi
  if pgrep -f "run_chunked.py.*--ticker $T" >/dev/null 2>&1; then
    echo "[$(date -Is)] WAIT existing process ticker=$T" | tee -a "$LOG_ROOT/vm_queue.log"
    while pgrep -f "run_chunked.py.*--ticker $T" >/dev/null 2>&1; do sleep 30; done
    if [[ -f "$OUT/summary.json" ]]; then
      echo "[$(date -Is)] EXISTING completed ticker=$T" | tee -a "$LOG_ROOT/vm_queue.log"
      continue
    fi
  fi
  if [[ -d "$OUT" && ! -f "$OUT/summary.json" ]]; then
    echo "[$(date -Is)] REMOVE incomplete output ticker=$T" | tee -a "$LOG_ROOT/vm_queue.log"
    rm -rf "$OUT"
  fi
  mkdir -p "$RUN_ROOT/${T}_chunked"
  echo "[$(date -Is)] START ticker=$T workers=6" | tee -a "$LOG_ROOT/vm_queue.log"
  PYTHONPATH=scripts/research/stage23_rework_20260713 \
  KINGMAKER_MARKET_CUTOFF_DATE=2026-07-10 \
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  venv/bin/python data/_system/analysis/stage2_multiticker_eec_fullcpu_20260715/run_chunked.py \
    --repo-root . \
    --out-dir "$OUT" \
    --ticker "$T" \
    --seed-base 2026071401 \
    --workers 6 > "$LOG" 2>&1
  echo "[$(date -Is)] DONE ticker=$T rc=$?" | tee -a "$LOG_ROOT/vm_queue.log"
done
echo "[$(date -Is)] VM_QUEUE_DONE" | tee -a "$LOG_ROOT/vm_queue.log"
