import sys, os, json, math
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_history
from engine.strategies.rulebook import Rulebook
from engine.learning.backtest import run_backtest
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.learning.learner import _detect_sector_name

TICKERS=['AAPL','MSFT','NVDA','JPM','KO','XOM']
YEARS=6; TEST_MONTHS=24; POS_LIMIT=10000000; FITNESS_MODE='spread'

for TICKER in TICKERS:
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

    rows=[]
    for i,x in enumerate(dump[:20], start=1):
        rb=Rulebook.from_dict(x)
        train_r=run_backtest(rb, df, start_date=train_start, end_date=train_end, **base)
        test_r=run_backtest(rb, df, start_date=test_start, end_date=test_end, **base)
        score=max(0.0, train_r.avg_return_pct)*math.log1p(max(0, train_r.trade_count))
        rows.append((i, x.get('fitness'), train_r.trade_count, train_r.avg_return_pct, test_r.trade_count, test_r.avg_return_pct, score))

    score_top=set(r[0] for r in sorted(rows,key=lambda z:z[6], reverse=True)[:5])
    print(f'===== {TICKER} =====')
    print('rk|fitness|tr_tr|tr_exp|te_tr|te_exp|score|score5')
    for r in rows:
        print(f'{r[0]:2d}|{float(r[1] or 0):7.2f}|{r[2]:5d}|{r[3]:7.2f}|{r[4]:5d}|{r[5]:7.2f}|{r[6]:7.2f}|{"Y" if r[0] in score_top else "N"}')