# CRWD exitga

{
  "best": {
    "avg_exp": 6.031592966243176,
    "composite_fitness": 10.254593081912143,
    "min_exp": 4.826821429069768,
    "rulebook_hash": "de38cbfa0e0beb13866c0928bf53de6998297d4f0750666afe11733b82c569e6",
    "total_trades": 19,
    "worst_dd_abs": 16.162467311262276
  },
  "elapsed_seconds": 526.3116118907928,
  "evaluated_unique": 3300,
  "execution": {
    "entry": "t_plus_1_open",
    "exit": "conservative_core",
    "fitness_mode": "swing",
    "fold_exit_policy": "fold_end_mark_to_market",
    "live_hard_stop_guard": true
  },
  "fitness_formula": "avg_exp + 2*min_exp - .15*stdev - .20*avg_abs_dd - .25*worst_abs_dd - 5*neg_count",
  "generations": 40,
  "population": 100,
  "seed": 202606122006,
  "ticker": "CRWD",
  "workers": 3
}
