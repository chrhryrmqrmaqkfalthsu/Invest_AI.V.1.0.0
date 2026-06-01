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

TICKERS = sys.argv[1:] or ['AAPL', 'MSFT', 'NVDA', 'JPM', 'KO', 'XOM']
POS_LIMIT = 10_000_000
FITNESS_MODE = 'spread'
YEARS = 6
MIN_VALID_RULES = 3
MIN_TEST_TRADES = 10
TEST_PERIODS = [
    ('2023', '2023-01-01', '2023-12-31'),
    ('2024', '2024-01-01', '2024-12-31'),
    ('2025', '2025-01-01', '2025-12-31'),
]


def _date_series(df):
    if 'date' in df.columns:
        return pd.to_datetime(df['date'])
    return pd.Series(pd.to_datetime(df.index), index=df.index)


def _buy_hold_return(df, dates, start, end):
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    mask = (dates >= s) & (dates <= e)
    sub = df.loc[mask] if hasattr(mask, 'index') and mask.index.equals(df.index) else df[mask]
    if len(sub) < 2 or 'Close' not in sub.columns:
        return None
    first = float(sub.iloc[0]['Close'])
    last = float(sub.iloc[-1]['Close'])
    if first <= 0:
        return None
    return (last / first - 1.0) * 100.0


for ticker in TICKERS:
    adapter = get_adapter(ticker)
    meta = adapter.meta
    df = adapter.load_history(years=YEARS)
    dates = _date_series(df)
    market_hist = get_market_history(years=7)
    sector = _detect_sector_name(meta.name)
    tsent = load_ticker_sentiment(ticker)
    base = dict(
        position_limit_krw=POS_LIMIT,
        market_history_df=market_hist,
        sector_name=sector,
        ticker_sentiment=tsent,
        fitness_mode=FITNESS_MODE,
    )
    dump = json.load(open(f'data/_system/ga_population_dump_{ticker}.json'))

    print(f'\n===== {ticker} WALK FORWARD V2 =====')
    print('period|rules|pnl%|bh%|alpha%|trades|exp%|status')
    raw_pos = 0
    alpha_pos = 0
    reliable_alpha = 0
    total = 0
    alpha_sum = 0.0

    for label, test_start_raw, test_end_raw in TEST_PERIODS:
        test_start_ts = pd.Timestamp(test_start_raw)
        test_end_ts = pd.Timestamp(test_end_raw)
        data_min = pd.Timestamp(dates.min())
        data_max = pd.Timestamp(dates.max())
        if test_end_ts < data_min or test_start_ts > data_max:
            continue

        test_start = max(test_start_ts, data_min).strftime('%Y-%m-%d')
        test_end = min(test_end_ts, data_max).strftime('%Y-%m-%d')
        train_start = data_min.strftime('%Y-%m-%d')
        train_end = (pd.Timestamp(test_start) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        if pd.Timestamp(train_end) <= data_min:
            continue

        bh = _buy_hold_return(df, dates, test_start, test_end)
        if bh is None:
            bh = 0.0

        scored = []
        for x in dump[:20]:
            rb = Rulebook.from_dict(x)
            tr = run_backtest(rb, df, start_date=train_start, end_date=train_end, **base)
            score = max(0.0, tr.avg_return_pct) * math.log1p(max(0, tr.trade_count))
            if score > 0.0:
                scored.append((score, rb, tr))

        valid = sorted(scored, key=lambda z: z[0], reverse=True)
        if len(valid) < MIN_VALID_RULES:
            pnl = 0.0
            alpha = pnl - bh
            status = f'NO_TRADE(valid={len(valid)})'
            print(f'{label}|0|{pnl:+.2f}|{bh:+.2f}|{alpha:+.2f}|0|+0.00|{status}')
        else:
            top = [rb for score, rb, tr in valid[:5]]
            res = run_ensemble_backtest(top, df, start_date=test_start, end_date=test_end, **base)
            pnl = float(res['portfolio_total_pnl_pct'])
            trades = int(res['raw'].trade_count)
            exp = float(res['raw'].avg_return_pct)
            alpha = pnl - bh
            status = 'OK' if trades >= MIN_TEST_TRADES else 'LOW_TRADES'
            print(f'{label}|{len(top)}|{pnl:+.2f}|{bh:+.2f}|{alpha:+.2f}|{trades}|{exp:+.2f}|{status}')
            if pnl > 0:
                raw_pos += 1
            if alpha > 0:
                alpha_pos += 1
            if alpha > 0 and trades >= MIN_TEST_TRADES:
                reliable_alpha += 1
        total += 1
        alpha_sum += alpha

    avg_alpha = alpha_sum / total if total else 0.0
    if reliable_alpha == 3:
        grade = 'A'
    elif reliable_alpha == 2:
        grade = 'B'
    elif reliable_alpha == 1:
        grade = 'C'
    else:
        grade = 'D'
    print(f'GRADE={grade} raw_pos={raw_pos}/{total} alpha_pos={alpha_pos}/{total} reliable_alpha={reliable_alpha}/{total} avg_alpha={avg_alpha:+.2f}%')
