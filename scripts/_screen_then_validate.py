import sys, os, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_history
from engine.strategies.rulebook import Rulebook
from engine.learning.backtest import run_backtest
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.learning.ensemble_backtest import run_ensemble_backtest
from engine.learning.learner import _detect_sector_name

TICKER = sys.argv[1]
YEARS = 6; TEST_MONTHS = 24; POS_LIMIT = 10_000_000; FITNESS_MODE = "spread"
EXP_MIN = float(os.environ.get("EXP_MIN", "0.5"))
TR_MIN = int(os.environ.get("TR_MIN", "10"))

adapter = get_adapter(TICKER); meta = adapter.meta
df = adapter.load_history(years=YEARS)
date_col = 'date' if 'date' in df.columns else None
dates = pd.to_datetime(df[date_col]) if date_col else df.index
end_date = dates.max(); split_date = end_date - pd.DateOffset(months=TEST_MONTHS)
train_start = dates.min().strftime('%Y-%m-%d'); train_end = split_date.strftime('%Y-%m-%d')
test_start = (split_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d'); test_end = end_date.strftime('%Y-%m-%d')

market_hist = get_market_history(years=max(YEARS + 1, 6))
sector_name = _detect_sector_name(meta.name)
tsent = load_ticker_sentiment(TICKER)
base = dict(position_limit_krw=POS_LIMIT, market_history_df=market_hist,
            sector_name=sector_name, ticker_sentiment=tsent, fitness_mode=FITNESS_MODE)

dump = json.load(open(f"data/_system/ga_population_dump_{TICKER}.json"))

passed = []
for x in dump:
    rb = Rulebook.from_dict(x)
    r = run_backtest(rb, df, start_date=train_start, end_date=train_end, **base)
    if r.avg_return_pct >= EXP_MIN and r.trade_count >= TR_MIN and 40.0 <= r.win_rate <= 95.0:
        passed.append(rb)

best_rb = Rulebook.from_dict(dump[0])
top5 = [Rulebook.from_dict(x) for x in dump[:5]]

def line(tag, r):
    print(f"  {tag:<22}: trades={r.trade_count:>3} win={r.win_rate:>5.1f}% exp={r.avg_return_pct:>+6.2f}%")

print(f"\n############### {TICKER} (TEST 검증) — TRAIN통과 {len(passed)}개 ###############")
sb = run_backtest(best_rb, df, start_date=test_start, end_date=test_end, **base)
line("single best", sb)
e5 = run_ensemble_backtest(top5, df, start_date=test_start, end_date=test_end, **base)
line("fitness top5", e5['raw']); print(f"     portfolio={e5['portfolio_total_pnl_pct']:+.2f}% overlap={e5['overlap_ratio']*100:.0f}%")
if passed:
    ep = run_ensemble_backtest(passed, df, start_date=test_start, end_date=test_end, **base)
    line(f"TRAIN-screened({len(passed)})", ep['raw']); print(f"     portfolio={ep['portfolio_total_pnl_pct']:+.2f}% overlap={ep['overlap_ratio']*100:.0f}%")
else:
    print("  TRAIN-screened        : 통과 0개 -> 거래 금지(제외)")