#!/usr/bin/env python3
from __future__ import annotations
import argparse, copy, hashlib, json, os, random, sys, time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
import numpy as np, pandas as pd
G={}
def sha(p):
 h=hashlib.sha256();
 with open(p,'rb') as f:
  [h.update(b) for b in iter(lambda:f.read(1048576),b'')]
 return h.hexdigest()
def ctx(repo,stage,ticker):
 sys.path.insert(0,str(stage)); from engine.core.indicators import calc_indicators; from engine.pipeline import context as pc; from engine.strategies.rulebook import default_rulebook; import types
 cache=repo/'data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache'/f'{ticker}.pkl'; mh=repo/'data/_system/market_history.csv'; so=repo/'data/_system/ml_sell_omen/sell_omen_scores.csv'
 raw=pd.read_pickle(cache).copy(); raw.index=pd.to_datetime(raw.index,errors='coerce'); raw=raw.sort_index()[['Open','High','Low','Close','Volume']]
 for c in raw.columns: raw[c]=pd.to_numeric(raw[c],errors='coerce')
 df=calc_indicators(raw); df,sell=pc.attach_sell_omen_scores(df,ticker,score_table_path=so); dmin=df.index.min().strftime('%Y-%m-%d'); dmax=df.index.max().strftime('%Y-%m-%d')
 rb=default_rulebook(ticker,asset_type='us_stock',direction='long'); rb.sector_name='healthcare'; meta=types.SimpleNamespace(ticker=ticker,name='Adaptive Biotechnologies Corporation',asset_type='us_stock',direction='long')
 return {'ticker':ticker,'adapter':None,'meta':meta,'df':df,'rows':len(df),'data_min':dmin,'data_max':dmax,'data_start':dmin,'data_end':dmax,'valid_close_ratio':pc._valid_ratio(df['Close'],positive=True),'valid_volume_ratio':pc._valid_ratio(df['Volume'],non_negative=True),'invalid_price_volume_ratio':pc._invalid_price_volume_ratio(df),'splits':pc.make_year_splits(pc.DEFAULT_ROLLING_YEARS,dmin,dmax),'split_count':3,'market_history_df':pd.read_csv(mh),'ticker_sentiment':{},'sentiment_days':0,'sector_name':'healthcare','base_rulebook':rb,'adv_usd_252d':pc.calculate_adv_usd_252d(df),'sell_omen_score':sell,'cache_only_metadata':{'ohlcv_cache_path':str(cache),'ohlcv_cache_sha256':sha(cache),'market_history_sha256':sha(mh)}}
def init(stage,df,kw,sp):
 sys.path.insert(0,stage); G.update(df=df,kw=kw,sp=sp)
def ev(arg):
 i,rb=arg; from engine.learning.execution_mode_backtest import run_backtest_execution_mode; from engine.learning import execution_mode_backtest as bt; import scripts.research.run_stage2 as s
 setattr(rb,bt.ENTRY_GA_SCOPE_MARKER,bt.ENTRY_GA_SCOPE_VALUE); setattr(rb,bt.ENTRY_FITNESS_EEC_TARGET_ATTR,6.0); setattr(rb,bt.ENTRY_FITNESS_EEC_FLOOR_ATTR,0.5)
 try:
  r=run_backtest_execution_mode(rb,G['df'],start_date=G['sp']['train_start'],end_date=G['sp']['train_end'],**G['kw'],entry_execution_mode=s.ENTRY_EXECUTION_MODE,exit_execution_mode=s.EXIT_EXECUTION_MODE,fold_exit_policy=s.FOLD_EXIT_POLICY,live_hard_stop_guard=s.LIVE_HARD_STOP_GUARD)
  return i,float(getattr(r,'fitness',-1e18))
 finally:
  for a in [bt.ENTRY_GA_SCOPE_MARKER,bt.ENTRY_FITNESS_EEC_TARGET_ATTR,bt.ENTRY_FITNESS_EEC_FLOOR_ATTR]:
   try: delattr(rb,a)
   except Exception: pass
def evalpop(ex,pop):
 fut=[ex.submit(ev,(i,rb)) for i,rb in enumerate(pop)]
 for f in as_completed(fut):
  i,fit=f.result(); pop[i].fitness=fit
def tour(pop,k): return max(random.sample(pop,min(k,len(pop))),key=lambda r:float(getattr(r,'fitness',-1e18)))
def ga(base,df,kw,sp,seed,workers,log):
 import scripts.research.run_stage2 as s; from engine.learning.genetic import random_rulebook,mutate,crossover,GAResult
 random.seed(seed); np.random.seed(seed); pop=[random_rulebook(base) for _ in range(s.POPULATION)]; hist=[]; no=0
 with ProcessPoolExecutor(max_workers=workers,initializer=init,initargs=(str(s.PROJECT_ROOT),df,kw,sp)) as ex:
  evalpop(ex,pop); bestall=copy.deepcopy(max(pop,key=lambda r:r.fitness))
  for gen in range(1,s.GENERATIONS+1):
   pop.sort(key=lambda r:r.fitness,reverse=True); best=pop[0]; avg=float(np.mean([r.fitness for r in pop])); hist.append((gen,best.fitness,avg)); log.info('gen=%s split=%s best=%.6f avg=%.6f',gen,sp['label'],best.fitness,avg)
   if best.fitness>bestall.fitness: bestall=copy.deepcopy(best); no=0
   else:
    no+=1
    if no>=s.PATIENCE: break
   elite=max(1,int(s.POPULATION*0.2)); new=[copy.deepcopy(x) for x in pop[:elite]]
   while len(new)<s.POPULATION:
    if random.random()<0.1: child=random_rulebook(base)
    else: child=mutate(crossover(tour(pop,3),tour(pop,3)),0.15,0.2)
    new.append(child)
   pop=new; evalpop(ex,pop)
 pop.sort(key=lambda r:r.fitness,reverse=True); return GAResult(bestall,hist,pop,hist[-1][0] if hist else 0)
def train(ticker,idx,sp,c,seed,workers,log):
 import scripts.research.run_stage2 as s; from engine.core.metadata import compute_rulebook_hash
 t=time.time(); r=ga(c['base_rulebook'],c['df'],s.base_kwargs(c),sp,seed+idx,workers,log); rows=[]
 for rank,rb in enumerate(sorted(r.final_population,key=lambda x:s.safe_float(getattr(x,'fitness',None),float('-inf')),reverse=True),1): rows.append({'ticker':ticker,'train_label':sp['label'],'train_start':sp['train_start'],'train_end':sp['train_end'],'origin_rank':rank,'rulebook_hash':compute_rulebook_hash(rb),'train_fitness':s.safe_float(getattr(rb,'fitness',0.0)),'rulebook':rb.to_dict()})
 hist=[{'train_label':sp['label'],'train_start':sp['train_start'],'train_end':sp['train_end'],'generation':g,'best_fitness':s.safe_float(b),'avg_fitness':s.safe_float(a),'best_rulebook_hash':compute_rulebook_hash(r.final_population[0]),'pid':os.getpid(),'generations_run':r.generations_run,'early_stop_triggered':r.generations_run<s.GENERATIONS,'train_elapsed_sec':time.time()-t} for g,b,a in r.fitness_history]
 return {'split':sp,'rows':rows,'history':hist,'generations_run':r.generations_run,'early_stop':r.generations_run<s.GENERATIONS,'elapsed':time.time()-t,'pid':os.getpid(),'fitness_cache':{}}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--repo-root',default='.'); p.add_argument('--out-dir',required=True); p.add_argument('--ticker',default='ADPT'); p.add_argument('--seed-base',type=int,default=2026071401); p.add_argument('--workers',type=int,default=28); a=p.parse_args()
 repo=Path(a.repo_root).resolve(); stage=repo/'scripts/research/stage23_rework_20260713'; sys.path.insert(0,str(stage)); os.chdir(repo); import scripts.research.run_stage2 as s
 out=(repo/a.out_dir).resolve() if not Path(a.out_dir).is_absolute() else Path(a.out_dir).resolve(); out.mkdir(parents=True,exist_ok=False); log=s._configure_logging(out); start=time.time(); c=ctx(repo,stage,a.ticker.upper()); periods=[]
 for pp in s.PERIODS_TEMPLATE:
  rr=dict(pp); rr['start']=rr['start'] or c['data_start']; rr['end']=rr['end'] or c['data_end']; periods.append(rr)
 (out/'launch_command.json').write_text(json.dumps({'host':__import__('socket').gethostname(),'python':sys.executable,'workers':a.workers,'actual_worker_processes':a.workers,'notebook_only':True,'parallel_unit':'GA population evaluation','seed_base':a.seed_base,'adpt_ohlcv_sha256':c['cache_only_metadata']['ohlcv_cache_sha256'],'run_stage2_sha256':sha(stage/'scripts/research/run_stage2.py')},ensure_ascii=False,indent=2),encoding='utf-8')
 trs=[]; rows=[]; hist=[]
 for i,sp in enumerate(s.TRAIN_SPLITS,1):
  log.info('fullcpu train start split=%s workers=%s',sp['label'],a.workers); tr=train(a.ticker.upper(),i,sp,c,a.seed_base,a.workers,log); trs.append(tr); rows+=tr['rows']; hist+=tr['history']; log.info('fullcpu train done split=%s gen=%s elapsed=%.1f',sp['label'],tr['generations_run'],tr['elapsed'])
 s.write_jsonl(out/'rulebooks_all.jsonl',rows); s.write_csv(out/'ga_history.csv',hist,s.GA_HISTORY_FIELDS); reps,orig=s.build_representatives(rows); evr=s.evaluate_periods(ticker=a.ticker.upper(),ctx=c,periods=periods,representative_by_hash=reps,origin_rows_by_hash=orig,logger=log,out_dir=out)
 s.write_csv(out/'period_metrics_all.csv',evr['period_metrics_rows'],s.PERIOD_METRICS_FIELDS); s.write_csv(out/'early_cut_log.csv',evr['early_cut_rows'],s.EARLY_CUT_FIELDS); s.write_jsonl(out/'survivors.jsonl',evr['survivor_rows']); s.write_jsonl(out/'trades.jsonl',evr['trade_rows']); s.write_jsonl(out/'rl_replay_trades.jsonl',evr['rl_replay_trade_rows'])
 fc=Counter(str(r.get('failed_period_label') or 'SURVIVED') for r in evr['early_cut_rows']); gens=[r['generations_run'] for r in trs]
 summ={'ticker':a.ticker.upper(),'status':'COMPLETE','generated_rulebook_rows':len(rows),'unique_rulebook_hashes':len(evr['unique_hashes']),'survivor_count':len(evr['survivors']),'survivor_hashes':evr['survivors'],'fail_counts_by_first_failed_period':dict(fc),'ga_generations_run_by_train':{r['split']['label']:r['generations_run'] for r in trs},'ga_early_stop_triggered_by_train':{r['split']['label']:r['early_stop'] for r in trs},'ga_average_generations_run':float(mean(gens)) if gens else None,'elapsed_sec':time.time()-start,'parallel':{'notebook_only':True,'workers':a.workers,'unit':'GA population evaluation'},'outputs':{'rulebooks_all':str(out/'rulebooks_all.jsonl'),'period_metrics_all':str(out/'period_metrics_all.csv'),'early_cut_log':str(out/'early_cut_log.csv'),'survivors':str(out/'survivors.jsonl'),'trades':str(out/'trades.jsonl'),'ga_history':str(out/'ga_history.csv'),'summary':str(out/'summary.json')}}
 s.write_text_json(out/'summary.json',summ); print(json.dumps(summ,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
