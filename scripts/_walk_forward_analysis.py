import sys, os, json, math
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
from engine.adapters.factory import get_adapter
from engine.market.context import get_market_history
from engine.strategies.rulebook import Rulebook
from engine.learning.backtest import run_backtest
from engine.learning.ensemble_backtest import run_ensemble_backtest
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.learning.learner import _detect_sector_name

TICKERS=['AAPL','MSFT','NVDA','JPM','KO','XOM']
POS_LIMIT=10000000
FITNESS_MODE='spread'
YEARS=6

for ticker in TICKERS:
    adapter=get_adapter(ticker)
    meta=adapter.meta
    df=adapter.load_history(years=YEARS)
    dates=pd.to_datetime(df['date']) if 'date' in df.columns else pd.to_datetime(df.index)
    end=dates.max()
    market_hist=get_market_history(years=7)
    sector=_detect_sector_name(meta.name)
    tsent=load_ticker_sentiment(ticker)
    base=dict(position_limit_krw=POS_LIMIT, market_history_df=market_hist, sector_name=sector, ticker_sentiment=tsent, fitness_mode=FITNESS_MODE)
    dump=json.load(open(f'data/_system/ga_population_dump_{ticker}.json'))

    print(f'\n===== {ticker} WALK FORWARD =====')
    positive=0; total=0
    for months in [36,24,12]:
        split=end-pd.DateOffset(months=months)
        train_start=dates.min().strftime('%Y-%m-%d')
        train_end=split.strftime('%Y-%m-%d')
        test_start=(split+pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        test_end=end.strftime('%Y-%m-%d')

        scored=[]
        for x in dump[:20]:
            rb=Rulebook.from_dict(x)
            tr=run_backtest(rb, df, start_date=train_start, end_date=train_end, **base)
            score=max(0.0,tr.avg_return_pct)*math.log1p(max(0,tr.trade_count))
            scored.append((score,rb,tr))

        top=[rb for score,rb,tr in sorted(scored,key=lambda z:z[0], reverse=True)[:5]]
        if not top:
            print(f'WF-{months}: NO_TRADE')
            continue
        res=run_ensemble_backtest(top, df, start_date=test_start, end_date=test_end, **base)
        pnl=res['portfolio_total_pnl_pct']
        total+=1
        if pnl>0: positive+=1
        print(f'WF-{months}: pnl={pnl:+.2f}% trades={res["raw"].trade_count} exp={res["raw"].avg_return_pct:+.2f}%')

    ratio=(positive/total*100) if total else 0
    grade='A' if ratio>=75 else ('B' if ratio>=50 else 'C')
    print(f'GRADE={grade} positive={positive}/{total} ({ratio:.0f}%)')