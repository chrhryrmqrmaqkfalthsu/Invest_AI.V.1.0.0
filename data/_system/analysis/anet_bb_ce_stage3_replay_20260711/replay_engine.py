from __future__ import annotations
import contextlib,logging,sys
from typing import Any
from replay_common import PROJECT
if str(PROJECT) not in sys.path:sys.path.insert(0,str(PROJECT))
from loguru import logger
from engine.adapters.factory import get_adapter
from engine.core.data_loader import load_ohlcv
from engine.core.indicators import calc_indicators
from engine.learning.execution_mode_backtest import run_backtest_execution_mode
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_sentiment
from engine.pipeline.context import attach_sell_omen_scores
from engine.strategies.rulebook import Rulebook

def execute(ticker:str,run_date:str,rulebook_dict:dict[str,Any],period:dict[str,Any]):
    logging.disable(logging.CRITICAL);logger.remove()
    with contextlib.redirect_stdout(sys.stderr):
        df=calc_indicators(load_ohlcv(ticker,years=6,end_date=run_date,use_cache=False))
        df,sell_info=attach_sell_omen_scores(df,ticker)
        market_history=get_market_history(years=7);sentiment=load_sentiment(ticker)
        try:
            from engine.learning.learner import _detect_sector_name
            sector=_detect_sector_name(get_adapter(ticker).meta.name)
        except Exception:sector='tech'
        result=run_backtest_execution_mode(Rulebook.from_dict(rulebook_dict),df,position_limit_krw=120000.0,
            market_history_df=market_history,sector_name=sector,ticker_sentiment=sentiment,fitness_mode='swing',
            use_llm_events=False,start_date=period.get('start'),end_date=period.get('end'),
            entry_execution_mode='t_plus_1_open',exit_execution_mode='conservative_core',
            fold_exit_policy='fold_end_mark_to_market',live_hard_stop_guard=True)
    return df,sell_info,result
