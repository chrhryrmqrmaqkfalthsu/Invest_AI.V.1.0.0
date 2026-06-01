import sys, os, json, math
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd

from engine.adapters.factory import get_adapter
from engine.learning.backtest import run_backtest
from engine.learning.ensemble_backtest import run_ensemble_backtest
from engine.learning.genetic import GAConfig, run_ga
from engine.learning.learner import _detect_sector_name
from engine.learning.stock_grade import _grade_from_counts, _period_status
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.rulebook import default_rulebook

TICKERS = sys.argv[1:] or ['AAPL', 'MSFT', 'NVDA', 'JPM', 'KO', 'XOM']
YEARS = 6
POSITION_LIMIT_KRW = 10_000_000
FITNESS_MODE = 'spread'
BASE_SEED = 4242
MIN_VALID_RULES = 3
TOP_N = 5
MAX_CANDIDATES = 20
TEST_YEARS = [2023, 2024, 2025]


def _date_series(df):
    if 'date' in df.columns:
        return pd.Series(pd.to_datetime(df['date']), index=df.index)
    return pd.Series(pd.to_datetime(df.index), index=df.index)


def _cut_by_date(df, end_ts):
    if df is None:
        return None
    end_ts = pd.Timestamp(end_ts)
    if 'date' in df.columns:
        dates = pd.to_datetime(df['date'])
        return df.loc[dates <= end_ts].copy()
    if isinstance(df.index, pd.DatetimeIndex):
        return df.loc[df.index <= end_ts].copy()
    return df


def _range_df(df, dates, start_ts, end_ts):
    mask = (dates >= pd.Timestamp(start_ts)) & (dates <= pd.Timestamp(end_ts))
    return df.loc[mask].copy()


def _score_from_train_result(r):
    return max(0.0, float(r.avg_return_pct)) * math.log1p(max(0, int(r.trade_count)))


def _evaluate_ticker(ticker):
    adapter = get_adapter(ticker)
    meta = adapter.meta
    df = adapter.load_history(years=YEARS)
    dates = _date_series(df)
    data_min = pd.Timestamp(dates.min())
    data_max = pd.Timestamp(dates.max())
    market_hist_full = get_market_history(years=max(YEARS + 1, 7))
    sector_name = _detect_sector_name(meta.name)
    ticker_sentiment = load_ticker_sentiment(ticker)

    periods = []
    pass_count = 0
    weak_count = 0
    positive_count = 0

    print(f'\n===== {ticker} TRUE-WF GRADE =====')
    print('year|grade_status|valid|used|trades|exp%|pnl%|ga_best|ga_gen')

    for year in TEST_YEARS:
        test_start_ts = max(pd.Timestamp(f'{year}-01-01'), data_min)
        test_end_ts = min(pd.Timestamp(f'{year}-12-31'), data_max)
        train_start_ts = data_min
        train_end_ts = test_start_ts - pd.Timedelta(days=1)
        if train_end_ts <= train_start_ts or test_end_ts <= test_start_ts:
            continue

        train_start = train_start_ts.strftime('%Y-%m-%d')
        train_end = train_end_ts.strftime('%Y-%m-%d')
        test_start = test_start_ts.strftime('%Y-%m-%d')
        test_end = test_end_ts.strftime('%Y-%m-%d')

        train_df = _cut_by_date(df, train_end_ts)
        eval_df = _cut_by_date(df, test_end_ts)
        market_train = _cut_by_date(market_hist_full, train_end_ts)
        market_eval = _cut_by_date(market_hist_full, test_end_ts)

        base_rb = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
        base_rb.sector_name = sector_name

        base_train_kwargs = dict(
            position_limit_krw=POSITION_LIMIT_KRW,
            market_history_df=market_train,
            sector_name=sector_name,
            ticker_sentiment=ticker_sentiment,
            fitness_mode=FITNESS_MODE,
        )
        base_eval_kwargs = dict(
            position_limit_krw=POSITION_LIMIT_KRW,
            market_history_df=market_eval,
            sector_name=sector_name,
            ticker_sentiment=ticker_sentiment,
            fitness_mode=FITNESS_MODE,
        )

        def evaluate_fn(rb):
            r = run_backtest(
                rb,
                train_df,
                start_date=train_start,
                end_date=train_end,
                **base_train_kwargs,
            )
            return r.fitness

        ga_cfg = GAConfig(random_seed=BASE_SEED + year)
        ga_result = run_ga(base_rulebook=base_rb, evaluate_fn=evaluate_fn, ga_config=ga_cfg)

        population = sorted(
            list(ga_result.final_population),
            key=lambda rb: (rb.fitness if getattr(rb, 'fitness', None) is not None else -1e9),
            reverse=True,
        )[:MAX_CANDIDATES]

        scored = []
        for rb in population:
            train_r = run_backtest(
                rb,
                train_df,
                start_date=train_start,
                end_date=train_end,
                **base_train_kwargs,
            )
            score = _score_from_train_result(train_r)
            if score > 0.0:
                scored.append((score, rb, train_r))

        valid = sorted(scored, key=lambda x: x[0], reverse=True)
        if len(valid) < MIN_VALID_RULES:
            status = f'NO_TRADE(valid={len(valid)})'
            period = {
                'year': year,
                'train_period': [train_start, train_end],
                'test_period': [test_start, test_end],
                'valid_rules': len(valid),
                'used_rules': 0,
                'trades': 0,
                'expectancy_pct': 0.0,
                'portfolio_pnl_pct': 0.0,
                'status': status,
                'ga_best_fitness': float(getattr(ga_result.best, 'fitness', 0.0) or 0.0),
                'ga_generations_run': ga_result.generations_run,
            }
            periods.append(period)
            print(f'{year}|{status}|{len(valid)}|0|0|+0.00|+0.00|{period["ga_best_fitness"]:.2f}|{period["ga_generations_run"]}')
            continue

        top = [rb for score, rb, train_r in valid[:TOP_N]]
        ens = run_ensemble_backtest(
            top,
            eval_df,
            start_date=test_start,
            end_date=test_end,
            **base_eval_kwargs,
        )
        raw = ens['raw']
        trades = int(raw.trade_count)
        exp_pct = float(raw.avg_return_pct)
        pnl_pct = float(ens['portfolio_total_pnl_pct'])
        status = _period_status(trades, exp_pct)

        if status == 'PASS':
            pass_count += 1
        elif status == 'WEAK':
            weak_count += 1
        if exp_pct > 0:
            positive_count += 1

        period = {
            'year': year,
            'train_period': [train_start, train_end],
            'test_period': [test_start, test_end],
            'valid_rules': len(valid),
            'used_rules': len(top),
            'trades': trades,
            'expectancy_pct': exp_pct,
            'portfolio_pnl_pct': pnl_pct,
            'status': status,
            'ga_best_fitness': float(getattr(ga_result.best, 'fitness', 0.0) or 0.0),
            'ga_generations_run': ga_result.generations_run,
        }
        periods.append(period)
        print(f'{year}|{status}|{len(valid)}|{len(top)}|{trades}|{exp_pct:+.2f}|{pnl_pct:+.2f}|{period["ga_best_fitness"]:.2f}|{period["ga_generations_run"]}')

    total = len(periods)
    grade, mode = _grade_from_counts(pass_count, weak_count, positive_count)
    avg_exp = sum(p['expectancy_pct'] for p in periods) / total if total else 0.0
    avg_trades = sum(p['trades'] for p in periods) / total if total else 0.0

    result = {
        'ticker': ticker,
        'type': 'true_walk_forward',
        'validated': True,
        'method': 'true_wf_ga_score_v1',
        'grade': grade,
        'mode': mode,
        'seed': BASE_SEED,
        'criteria': {
            'train_window': 'expanding',
            'ga_seed': 'BASE_SEED + test_year',
            'score': 'max(0, train_avg_return_pct) * log1p(train_trade_count)',
            'min_valid_rules': MIN_VALID_RULES,
            'top_n': TOP_N,
            'note': 'GA is retrained separately for each test year using only data before that year.',
        },
        'summary': {
            'periods': total,
            'pass_count': pass_count,
            'weak_count': weak_count,
            'positive_count': positive_count,
            'avg_expectancy_pct': avg_exp,
            'avg_trades': avg_trades,
        },
        'periods': periods,
    }
    print(f'GRADE={grade} mode={mode} pass={pass_count}/{total} weak={weak_count} avg_exp={avg_exp:+.2f}% avg_trades={avg_trades:.1f}')
    return result


out_dir = Path('data/_system')
out_dir.mkdir(parents=True, exist_ok=True)
for ticker in TICKERS:
    result = _evaluate_ticker(ticker)
    out_path = out_dir / f'true_wf_grade_{ticker}.json'
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    print(f'saved: {out_path}')
