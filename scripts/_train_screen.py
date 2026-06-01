import sys, os, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_history
from engine.strategies.rulebook import Rulebook
from engine.learning.backtest import run_backtest
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.learning.learner import _detect_sector_name

TICKER = sys.argv[1]
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
end_date = dates.max()
split_date = end_date - pd.DateOffset(months=TEST_MONTHS)
train_start = dates.min().strftime('%Y-%m-%d')
train_end = split_date.strftime('%Y-%m-%d')

market_hist = get_market_history(years=max(YEARS + 1, 6))
sector_name = _detect_sector_name(meta.name)
ticker_sentiment = load_ticker_sentiment(TICKER)

dump = json.load(open(f"data/_system/ga_population_dump_{TICKER}.json"))

common = dict(position_limit_krw=POS_LIMIT, market_history_df=market_hist,
              sector_name=sector_name, start_date=train_start, end_date=train_end,
              ticker_sentiment=ticker_sentiment, fitness_mode=FITNESS_MODE)

print(f"\n############### {TICKER} (TRAIN 재백테스트) ###############")
print(f"{'rk':>3} {'fit':>7} | {'TEST_exp':>8} {'TEST_tr':>7} | {'TRAIN_exp':>9} {'TRAIN_tr':>8} {'TRAIN_win':>9}")
rows = []
for x in dump:
    rb = Rulebook.from_dict(x)
    r = run_backtest(rb, df, **common)
    rows.append((x, r))
    print(f"{x.get('rank'):>3} {x.get('fitness'):>7.2f} | "
          f"{x.get('expectancy_pct'):>8.2f} {x.get('trade_count'):>7} | "
          f"{r.avg_return_pct:>9.2f} {r.trade_count:>8} {r.win_rate:>9.1f}")

# 기준 통과 개수 (TRAIN 기준): exp>=0.5%, trades>=10, win 40~95%
def passes(r):
    return (r.avg_return_pct >= 0.5 and r.trade_count >= 10
            and 40.0 <= r.win_rate <= 95.0)

n_pass = sum(1 for _, r in rows if passes(r))
print(f">>> {TICKER}: TRAIN 기준(exp>=0.5%, tr>=10, win 40~95%) 통과 = {n_pass}/{len(rows)}개")