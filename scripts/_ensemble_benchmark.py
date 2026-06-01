import sys, os, json, math
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_history
from engine.strategies.rulebook import Rulebook
from engine.learning.backtest import run_backtest
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.learning.ensemble_backtest import run_ensemble_backtest
from engine.learning.learner import _detect_sector_name

TICKER=sys.argv[1]
YEARS=6; TEST_MONTHS=24; POS_LIMIT=10000000; FITNESS_MODE='spread'

adapter=get_adapter(TICKER); meta=adapter.meta
df=adapter.load_history(years=YEARS)
dates=pd.to_datetime(df['date']) if 'date' in df.columns else df.index
end_date=dates.max(); split_date=end_date-pd.DateOffset(months=TEST_MONTHS)
train_start=dates.min().strftime('%Y-%m-%d'); train_end=split_date.strftime('%Y-%m-%d')
test_start=(split_date+pd.Timedelta(days=1)).strftime('%Y-%m-%d'); test_end=end_date.strftime('%Y-%m-%d')

market_hist=get_market_history(years=max(YEARS+1,6))
sector_name=_detect_sector_name(meta.name)
tsent=load_ticker_sentiment(TICKER)
base=dict(position_limit_krw=POS_LIMIT, market_history_df=market_hist, sector_name=sector_name, ticker_sentiment=tsent, fitness_mode=FITNESS_MODE)

dump=json.load(open(f'data/_system/ga_population_dump_{TICKER}.json'))

train_stats=[]
for x in dump:
    rb=Rulebook.from_dict(x)
    r=run_backtest(rb, df, start_date=train_start, end_date=train_end, **base)
    score=max(0.0, r.avg_return_pct)*math.log1p(max(0, r.trade_count))
    train_stats.append((rb,r,score))

best_rb=Rulebook.from_dict(dump[0])

def filt(exp_min,tr_min):
    return [rb for rb,r,_ in train_stats if r.avg_return_pct>=exp_min and r.trade_count>=tr_min and 40<=r.win_rate<=95]

candidates={
 'fitness_top5':[Rulebook.from_dict(x) for x in dump[:5]],
 'screen_0.5_10':filt(0.5,10),
 'screen_1.0_10':filt(1.0,10),
 'screen_0.5_7':filt(0.5,7),
 'score_top5':[rb for rb,_,_ in sorted(train_stats,key=lambda z:z[2],reverse=True)[:5]],
}

print(f'\n===== {TICKER} =====')
sb=run_backtest(best_rb, df, start_date=test_start, end_date=test_end, **base)
print(f'SINGLE trades={sb.trade_count} exp={sb.avg_return_pct:+.2f}%')

for name,rules in candidates.items():
    if not rules:
        print(f'{name}: NO_TRADE')
        continue
    e=run_ensemble_backtest(rules, df, start_date=test_start, end_date=test_end, **base)
    print(f'{name}: n={len(rules)} trades={e["raw"].trade_count} exp={e["raw"].avg_return_pct:+.2f}% pnl={e["portfolio_total_pnl_pct"]:+.2f}% overlap={e["overlap_ratio"]*100:.0f}%')