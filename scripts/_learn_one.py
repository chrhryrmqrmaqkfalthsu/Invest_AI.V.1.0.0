import sys, time
from engine.learning.learner import learn
from engine.learning.genetic import GAConfig
import engine.learning.backtest as bt

T = sys.argv[1]
bt._SPREAD_CTX_CACHE.clear(); bt._SPREAD_RET_CACHE.clear()
cfg = GAConfig(population=40, generations=50, elite_ratio=0.2,
               early_stop_no_improve=10, random_seed=42)
t0 = time.time()
res = learn(T, years=6, position_limit_krw=10_000_000,
            ga_config=cfg, test_months=24, fitness_mode="spread")
el = time.time() - t0
tr, te = res.train_result, res.test_result
print(f"=== {T} 완료 ({el:.0f}s) ===")
print(f"[TRAIN] fit={tr.fitness:+.3f} 거래={tr.trade_count} 승률={tr.win_rate:.0f}% exp={tr.avg_return_pct:+.2f}%")
print(f"[TEST]  fit={te.fitness:+.3f} 거래={te.trade_count} 승률={te.win_rate:.0f}% exp={te.avg_return_pct:+.2f}%")
print(f"overfit_ratio={res.overfit_ratio:.2f}")
b = tr.rulebook
print(f"룰북: thr={b.signal_threshold:.2f} ma={b.weight_ma_align:.2f} macd={b.weight_macd_golden:.2f} rsi={b.weight_rsi_zone:.2f} hold={b.max_holding_days} sl={b.stop_loss_atr:.2f} tp={b.take_profit_atr:.2f}")
