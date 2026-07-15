#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, socket, sys, types
from pathlib import Path
import pandas as pd

def sha(p: Path) -> str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',default='.'); ap.add_argument('--out-dir',required=True); ap.add_argument('--ticker',default='ADPT'); ap.add_argument('--seed-base',type=int,default=2026071401); ap.add_argument('--workers-record',type=int,default=28); ap.add_argument('--parallel',action='store_true')
    a=ap.parse_args(); repo=Path(a.repo_root).resolve(); stage=repo/'scripts/research/stage23_rework_20260713'; sys.path.insert(0,str(stage))
    from engine.core.indicators import calc_indicators
    from engine.pipeline import context as pc
    from engine.strategies.rulebook import default_rulebook
    import scripts.research.run_stage2 as st2
    t=a.ticker.upper().strip(); out=Path(a.out_dir); out=(repo/out).resolve() if not out.is_absolute() else out; out.parent.mkdir(parents=True,exist_ok=True)
    cache=repo/'data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache'/f'{t}.pkl'; mh=repo/'data/_system/market_history.csv'; so=repo/'data/_system/ml_sell_omen/sell_omen_scores.csv'
    meta=types.SimpleNamespace(ticker=t,name='Adaptive Biotechnologies Corporation',asset_type='us_stock',direction='long',currency='USD',market='NYSE/NASDAQ')
    def prepare(ticker: str):
        ticker=ticker.upper().strip(); raw=pd.read_pickle(cache).copy(); raw.index=pd.to_datetime(raw.index,errors='coerce'); raw=raw.sort_index()[['Open','High','Low','Close','Volume']]
        for c in raw.columns: raw[c]=pd.to_numeric(raw[c],errors='coerce')
        df=calc_indicators(raw); df,sell=pc.attach_sell_omen_scores(df,ticker,score_table_path=so); dmins=df.index.min().strftime('%Y-%m-%d'); dmaxs=df.index.max().strftime('%Y-%m-%d'); rb=default_rulebook(ticker,asset_type=meta.asset_type,direction=meta.direction); rb.sector_name='healthcare'; splits=pc.make_year_splits(pc.DEFAULT_ROLLING_YEARS,dmins,dmaxs)
        return {'ticker':ticker,'adapter':None,'meta':meta,'df':df,'rows':len(df),'data_min':dmins,'data_max':dmaxs,'data_start':dmins,'data_end':dmaxs,'valid_close_ratio':pc._valid_ratio(df['Close'],positive=True),'valid_volume_ratio':pc._valid_ratio(df['Volume'],non_negative=True),'invalid_price_volume_ratio':pc._invalid_price_volume_ratio(df),'splits':splits,'split_count':len(splits),'market_history_df':pd.read_csv(mh),'ticker_sentiment':{},'sentiment_days':0,'sector_name':'healthcare','base_rulebook':rb,'adv_usd_252d':pc.calculate_adv_usd_252d(df),'sell_omen_score':sell,'cache_only_metadata':{'ohlcv_cache_path':str(cache),'ohlcv_cache_sha256':sha(cache),'market_history_sha256':sha(mh)}}
    pc.prepare_ticker_context=prepare; st2.prepare_ticker_context=prepare
    st2.run_stage2(ticker=t,out_dir=out,seed_base=a.seed_base,parallel=bool(a.parallel),use_fitness_cache=False)
    meta_out={'host':socket.gethostname(),'python':sys.executable,'cwd':os.getcwd(),'repo_root':str(repo),'out_dir':str(out),'ticker':t,'seed_base':a.seed_base,'NOTEBOOK_MAX_record':a.workers_record,'actual_stage2_parallel_processes':3 if a.parallel else 1,'parallel':bool(a.parallel),'ohlcv_cache_sha256':sha(cache),'run_stage2_sha256':sha(stage/'scripts/research/run_stage2.py'),'sector_name_runtime':'healthcare','ticker_sentiment_runtime':'empty'}
    (out/'launch_meta.json').write_text(json.dumps(meta_out,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__': main()
