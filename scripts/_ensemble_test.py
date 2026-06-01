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
TOPN = int(sys.argv[2]) if len(sys.argv) > 2 else 5
YEARS = 6
TEST_MONTHS = 24
POS_LIMIT = 10_000_000
FITNESS_MODE = "spread"

adapter = get_adapter(TICKER)
meta = adapter.meta
df = adapter.load_history(years=YEARS)

date_col = 'date' if 'date' in df.columns else None
if date_col:
    dates = pd.to_datetime(df[date_col])
elif isinstance(df.index, pd.DatetimeIndex):
    dates = df.index
else:
    dates = None

end_date = dates.max()
split_date = end_date - pd.DateOffset(months=TEST_MONTHS)
train_start = dates.min().strftime('%Y-%m-%d')
train_end = split_date.strftime('%Y-%m-%d')
test_start = (split_date + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
test_end = end_date.strftime('%Y-%m-%d')

market_hist = get_market_history(years=max(YEARS + 1, 6))
sector_name = _detect_sector_name(meta.name)
ticker_sentiment = load_ticker_sentiment(TICKER)

dump = json.load(open(f"data/_system/ga_population_dump_{TICKER}.json"))
best_rb = Rulebook.from_dict(dump[0])
top = [Rulebook.from_dict(x) for x in dump[:TOPN]]
print(f"=== {TICKER}: best + 상위 {len(top)}개 룰 로드 ===")
for i, d in enumerate(dump[:TOPN], 1):
    print(f"  #{i} fit={d.get('fitness'):+.3f} thr={d.get('signal_threshold'):.2f} ma={d.get('weight_ma_align'):.2f} macd={d.get('weight_macd_golden'):.2f} rsi={d.get('weight_rsi_zone'):.2f}")

common = dict(position_limit_krw=POS_LIMIT, market_history_df=market_hist, sector_name=sector_name, ticker_sentiment=ticker_sentiment, fitness_mode=FITNESS_MODE)

def fmt(r):
    return f"trades={r.trade_count} win={r.win_rate:.1f}% exp={r.avg_return_pct:+.2f}% fit={r.fitness:+.3f}"

for label, s, e in [("TRAIN", train_start, train_end), ("TEST", test_start, test_end)]:
    single = run_backtest(best_rb, df, start_date=s, end_date=e, **common)
    ens = run_ensemble_backtest(top, df, start_date=s, end_date=e, **common)
    print(f"\n=== {TICKER} {label} ===")
    print(f"Single  : {fmt(single)}")
    print(f"Ensemble: {fmt(ens['raw'])}")
    print(f"Portfolio pnl={ens['portfolio_total_pnl_pct']:+.2f}% overlap={ens['overlap_ratio']*100:.0f}% (entries={ens['n_entries']}, unique_days={ens['n_unique_entry_dates']})")