#!/usr/bin/env python3
from __future__ import annotations
import argparse, copy, hashlib, json, os, random, sys, time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
import numpy as np
G={}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def load_s2(stage,cutoff):
 st=str(stage)
 if st in sys.path: sys.path.remove(st)
 sys.path.insert(0,st)
 from scripts.research import run_stage2 as s
 patch_cutoff(s,cutoff)
 return s
def patch_cutoff(s,cutoff_text):
 cd=date.fromisoformat(cutoff_text); tgt=s._stage3_entry_reference
 orig=getattr(tgt,'_stage2_chunked_orig_freshness',None)
 if orig is None:
  orig=tgt._primary_freshness; setattr(tgt,'_stage2_chunked_orig_freshness',orig)
 def f(last_date,*,as_of_date=None):
  if last_date!=cd: raise RuntimeError(f'available-data cutoff mismatch: expected {cd}, actual {last_date}')
  r=orig(last_date,as_of_date=cd+timedelta(days=1)); r.update({'basis':'user_approved_available_snapshot_last_session_stage2_chunked','user_approved_available_data_only':True,'available_data_cutoff_date':cd.isoformat(),'wall_clock_local_date':datetime.now().astimezone().date().isoformat(),'stage2_chunked_cutoff_patch':True}); return r
 tgt._RESEARCH_MARKET_SNAPSHOT_CACHE.clear(); tgt._primary_freshness=f
 return {'cutoff_date':cd.isoformat(),'patch_target':getattr(tgt,'__file__',str(tgt))}
def init(stage,cutoff,df,kw,sp):
 s=load_s2(Path(stage),cutoff); G.clear(); G.update(s=s,df=df,kw=kw,sp=sp)
def ev1(i,rb):
 s=G['s']; r=s.run_stage3_entry_phase_backtest(rb,G['df'],start_date=G['sp']['train_start'],end_date=G['sp']['train_end'],kwargs=G['kw']); return i,float(getattr(r,'fitness',-1e18))
def evchunk(ch): return [ev1(i,rb) for i,rb in ch]
def chunks(pop,n):
 n=max(1,min(n,len(pop))); b=[[] for _ in range(n)]
 for i,rb in enumerate(pop): b[i%n].append((i,rb))
 return [x for x in b if x]
def evalpop(ex,pop,w):
 for fut in as_completed([ex.submit(evchunk,c) for c in chunks(pop,w)]):
  for i,fit in fut.result(): pop[i].fitness=fit
def tour(pop,k): return max(random.sample(pop,min(k,len(pop))),key=lambda x:float(getattr(x,'fitness',-1e18)))
def ga(s,base,df,kw,sp,seed,w,stage,cutoff,log):
 from engine.learning.genetic import GAResult,random_rulebook,mutate,crossover
 random.seed(seed); np.random.seed(seed); dom=s.build_entry_feature_domain({'df':df},start=sp['train_start'],end=sp['train_end'])
 pop=[random_rulebook(base,gene_scope='entry',entry_feature_domain=dom) for _ in range(s.POPULATION)]; hist=[]; no=0
 with ProcessPoolExecutor(max_workers=w,initializer=init,initargs=(str(stage),cutoff,df,kw,sp)) as ex:
  evalpop(ex,pop,w); bestall=copy.deepcopy(max(pop,key=lambda x:x.fitness))
  for gen in range(1,s.GENERATIONS+1):
   pop.sort(key=lambda x:x.fitness,reverse=True); best=pop[0]; avg=float(np.mean([x.fitness for x in pop])); hist.append((gen,best.fitness,avg)); log.info('chunked gen=%s split=%s best=%.6f avg=%.6f chunks=%s chunk_size≈%.2f',gen,sp['label'],best.fitness,avg,min(w,len(pop)),len(pop)/max(1,min(w,len(pop))))
   if best.fitness>bestall.fitness: bestall=copy.deepcopy(best); no=0
   else:
    no+=1
    if no>=s.PATIENCE: break
   elite=max(1,int(s.POPULATION*0.2)); new=[copy.deepcopy(x) for x in pop[:elite]]
   while len(new)<s.POPULATION:
    if random.random()<0.1: child=random_rulebook(base,gene_scope='entry',entry_feature_domain=dom)
    else:
     child=crossover(tour(pop,3),tour(pop,3),gene_scope='entry',entry_feature_domain=dom)
     child=mutate(child,0.15,0.2,gene_scope='entry',entry_feature_domain=dom)
    new.append(child)
   pop=new; evalpop(ex,pop,w)
 pop.sort(key=lambda x:x.fitness,reverse=True); return GAResult(bestall,hist,pop,len(hist))
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',default='.'); ap.add_argument('--out-dir',required=True); ap.add_argument('--ticker',required=True); ap.add_argument('--seed-base',type=int,default=2026071401); ap.add_argument('--workers',type=int,required=True); ap.add_argument('--market-cutoff-date',required=True); a=ap.parse_args()
 repo=Path(a.repo_root).resolve(); stage=repo/'scripts/research/stage23_rework_20260713'; os.chdir(repo); s=load_s2(stage,a.market_cutoff_date); out=Path(a.out_dir); out=(repo/out if not out.is_absolute() else out).resolve(); out.mkdir(parents=True,exist_ok=False); log=s._configure_logging(out); tkr=a.ticker.upper(); start=time.time(); log.info('stage2 chunked stage3sem start ticker=%s workers=%s cutoff=%s',tkr,a.workers,a.market_cutoff_date)
 ctx=s.prepare_ticker_context(tkr); ds=ctx.get('data_start') or ctx.get('data_min'); de=ctx.get('data_end') or ctx.get('data_max'); periods=[]
 for p in s.PERIODS_TEMPLATE:
  r=dict(p); r['start']=r['start'] or ds; r['end']=r['end'] or de; periods.append(r)
 config=s.build_config(ticker=tkr,out_dir=out,seed_base=a.seed_base,parallel=False,ctx=ctx,periods=periods,started=start,use_fitness_cache=False,code_commit=s.resolve_code_commit(s.PROJECT_ROOT)); config['runner']={'name':Path(__file__).name,'sha256':sha(Path(__file__).resolve()),'workers':a.workers,'parallel_unit':'GA population chunk evaluation','market_cutoff_patch':patch_cutoff(s,a.market_cutoff_date)}; s.write_text_json(out/'config.json',config); s.write_text_json(out/'launch_command.json',{'host':__import__('socket').gethostname(),'python':sys.executable,'ticker':tkr,'workers':a.workers,'seed_base':a.seed_base,'market_cutoff_date':a.market_cutoff_date,'stage2_sha256':sha(stage/'scripts/research/run_stage2.py'),'runner_sha256':sha(Path(__file__).resolve()),'out_dir':str(out)})
 trs=[]; rows=[]; hist=[]
 for idx,sp in enumerate(s.TRAIN_SPLITS,1):
  log.info('train start label=%s ticker=%s workers=%s',sp['label'],tkr,a.workers); t0=time.time(); res=ga(s,ctx['base_rulebook'],ctx['df'],s.base_kwargs(ctx),sp,a.seed_base+idx,a.workers,stage,a.market_cutoff_date,log); elapsed=time.time()-t0
  from engine.core.metadata import compute_rulebook_hash
  pop=sorted(list(res.final_population),key=lambda rb:s.safe_float(getattr(rb,'fitness',None),float('-inf')),reverse=True); rr=[]
  for rank,rb in enumerate(pop,1): rr.append({'ticker':tkr,'train_label':sp['label'],'train_start':sp['train_start'],'train_end':sp['train_end'],'origin_rank':rank,'rulebook_hash':compute_rulebook_hash(rb),'train_fitness':s.safe_float(getattr(rb,'fitness',0.0)),'rulebook':rb.to_dict()})
  hh=[{'train_label':sp['label'],'train_start':sp['train_start'],'train_end':sp['train_end'],'generation':g,'best_fitness':s.safe_float(b),'avg_fitness':s.safe_float(av),'best_rulebook_hash':compute_rulebook_hash(pop[0]),'pid':os.getpid(),'generations_run':res.generations_run,'early_stop_triggered':res.generations_run<s.GENERATIONS,'train_elapsed_sec':elapsed} for g,b,av in res.fitness_history]
  tr={'split':sp,'rows':rr,'history':hh,'generations_run':res.generations_run,'early_stop':res.generations_run<s.GENERATIONS,'elapsed':elapsed,'pid':os.getpid(),'fitness_cache':{}}; trs.append(tr); rows+=rr; hist+=hh; log.info('train done label=%s gen=%s rows=%s elapsed=%.1f',sp['label'],res.generations_run,len(rr),elapsed)
 hist.sort(key=lambda r:(r['train_label'],r['generation'])); s.write_jsonl(out/'rulebooks_all.jsonl',rows); s.write_csv(out/'ga_history.csv',hist,s.GA_HISTORY_FIELDS); reps,orig=s.build_representatives(rows); ev=s.evaluate_periods(ticker=tkr,ctx=ctx,periods=periods,representative_by_hash=reps,origin_rows_by_hash=orig,out_dir=out,logger=log)
 s.write_csv(out/'period_metrics_all.csv',ev['period_metrics_rows'],s.PERIOD_METRICS_FIELDS); s.write_csv(out/'early_cut_log.csv',ev['early_cut_rows'],s.EARLY_CUT_FIELDS); s.write_jsonl(out/'survivors.jsonl',ev['survivor_rows']); s.write_jsonl(out/'trades.jsonl',ev['trade_rows']); s.write_jsonl(out/'rl_replay_trades.jsonl',ev['rl_replay_trade_rows'])
 fc=Counter(str(r.get('failed_period_label') or 'SURVIVED') for r in ev['early_cut_rows']); gens=[s.safe_int(r['generations_run']) for r in trs]; summ={'ticker':tkr,'status':'COMPLETE','generated_rulebook_rows':len(rows),'unique_rulebook_hashes':len(ev['unique_hashes']),'survivor_count':len(ev['survivors']),'survivor_hashes':ev['survivors'],'fail_counts_by_first_failed_period':dict(fc),'ga_generations_run_by_train':{r['split']['label']:r['generations_run'] for r in trs},'ga_early_stop_triggered_by_train':{r['split']['label']:r['early_stop'] for r in trs},'ga_average_generations_run':float(mean(gens)) if gens else None,'elapsed_sec':time.time()-start,'market_cutoff_patch':config['runner']['market_cutoff_patch'],'outputs':{'summary':str(out/'summary.json'),'survivors':str(out/'survivors.jsonl'),'period_metrics_all':str(out/'period_metrics_all.csv'),'trades':str(out/'trades.jsonl')}}; s.write_text_json(out/'summary.json',summ); print(json.dumps(summ,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
