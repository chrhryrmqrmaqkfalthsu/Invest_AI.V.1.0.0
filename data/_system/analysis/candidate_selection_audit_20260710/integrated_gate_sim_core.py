from __future__ import annotations

import json, math, sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
BATCH=ROOT/'exp_batch_stage123_2009_20260616_full'; TICKERS=BATCH/'tickers'
AUDIT=ROOT/'data/_system/analysis/candidate_selection_audit_20260710'

from engine.live import elite_shadow_report as elite

def jload(p:Path)->Any:
 try:return json.loads(p.read_text(encoding='utf-8'))
 except:return None

def jsonl(p:Path):
 if not p.is_file(): return
 with p.open('r',encoding='utf-8',errors='ignore') as f:
  for i,line in enumerate(f,1):
   if not line.strip():continue
   try:yield i,json.loads(line)
   except:continue

def effective_stage2_dirs():
 out=[]
 for td in TICKERS.iterdir():
  if not td.is_dir():continue
  ds=[d for d in td.iterdir() if d.is_dir() and d.name.startswith('stage2') and (d/'_stage2_done.json').is_file() and (d/'survivors.jsonl').is_file()]
  if ds:out.append(max(ds,key=lambda d:(d/'_stage2_done.json').stat().st_mtime))
 return sorted(out)

def stage2_metrics(row):
 periods={str(x.get('period_label')):x for x in row.get('periods') or [] if x.get('period_label')}
 return elite._metrics_summary(periods),periods

def load_origins():
 rows=[]; history_sources=[]
 for d in effective_stage2_dirs():
  ticker=d.parent.name; targets=set()
  for idx,r in jsonl(d/'survivors.jsonl') or []:
   h=str(r.get('rulebook_hash') or ''); rb=r.get('rulebook') or {}; m,periods=stage2_metrics(r)
   if not h:continue
   cid=f'stage2:{ticker}:{h[:12]}'; targets.add(h)
   rows.append({'candidate_id':cid,'stage':'stage2','ticker':ticker,'rulebook_hash':h,'rulebook':rb,'metrics':m,'source_file':str((d/'survivors.jsonl').relative_to(ROOT)),'source_row_index':idx,'done_marker':True,'profile_eligible':True,'period_count':len(periods),'origin_complete':len(periods)==5,'history_file':str((d/'trades.jsonl').relative_to(ROOT))})
  if targets and (d/'trades.jsonl').is_file():history_sources.append(('stage2',d/'trades.jsonl',targets))
 for d in sorted(TICKERS.glob('*/stage3')):
  f=d/'final_rulebooks.jsonl'
  if not f.is_file():continue
  ticker=d.parent.name; done=(d/'_stage3_done.json').is_file(); profiles={str(r.get('rulebook_hash') or '') for _,r in (jsonl(d/'stage3_profile_catalog.jsonl') or [])}
  targets=set()
  for idx,r in jsonl(f) or []:
   h=str(r.get('rulebook_hash') or ''); rb=r.get('rulebook') or {}; raw=r.get('bull_metrics') or r.get('stress_metrics') or {}
   if not h:continue
   m={'periods':['stage3_bull'],'min_expectancy_pct':elite._safe_float(raw.get('expectancy_pct') or raw.get('avg_return_pct')),'avg_expectancy_pct':elite._safe_float(raw.get('expectancy_pct') or raw.get('avg_return_pct')),'oos_expectancy_pct':elite._safe_float(raw.get('expectancy_pct') or raw.get('avg_return_pct')),'stress_expectancy_pct':elite._safe_float((r.get('stress_metrics') or {}).get('expectancy_pct')),'oos_fitness':elite._safe_float(raw.get('fitness')),'min_fitness':elite._safe_float(raw.get('fitness')),'worst_drawdown_pct':elite._safe_float(raw.get('max_drawdown_pct')),'oos_drawdown_pct':elite._safe_float(raw.get('max_drawdown_pct')),'oos_win_rate':elite._safe_float(raw.get('win_rate')),'min_win_rate':elite._safe_float(raw.get('win_rate')),'oos_trade_count':elite._safe_int(raw.get('trade_count')),'min_trade_count':elite._safe_int(raw.get('trade_count'))}
   cid=f'stage3:{ticker}:{h[:12]}'; targets.add(h)
   rows.append({'candidate_id':cid,'stage':'stage3','ticker':ticker,'rulebook_hash':h,'rulebook':rb,'metrics':m,'source_file':str(f.relative_to(ROOT)),'source_row_index':idx,'done_marker':done,'profile_eligible':h in profiles,'period_count':3 if h in profiles else 0,'origin_complete':bool(done and h in profiles),'history_file':str((d/'exit_trades.jsonl').relative_to(ROOT))})
  if targets and (d/'exit_trades.jsonl').is_file():history_sources.append(('stage3',d/'exit_trades.jsonl',targets))
 # unique source candidate only
 uniq={r['candidate_id']:r for r in rows}
 return list(uniq.values()),history_sources

def scan_histories(sources):
 agg=defaultdict(lambda:{'n':0,'sum_pnl':0.0,'wins':0,'sum_atr_pct':0.0,'atr_n':0})
 for stage,path,targets in sources:
  marker='"rulebook_hash": "' if stage=='stage2' else '"final_rulebook_hash": "'
  with path.open('r',encoding='utf-8',errors='ignore') as f:
   for line in f:
    i=line.find(marker)
    if i<0:continue
    h=line[i+len(marker):i+len(marker)+64]
    if h not in targets:continue
    try:r=json.loads(line)
    except:continue
    key=f'{stage}:{path.parent.parent.name if stage=="stage2" else path.parent.parent.name}:{h[:12]}'
    # stage2 path parent is stage2, parent.parent ticker; stage3 same
    a=agg[key]; pnl=float(r.get('pnl_pct') or 0.0); a['n']+=1;a['sum_pnl']+=pnl;a['wins']+=int(pnl>0)
    ep=float(r.get('entry_price') or 0.0)
    if stage=='stage2': atr=float(r.get('entry_atr') or 0.0)
    else:
     sl=float(r.get('stop_loss_atr') or 0.0); sp=float(r.get('stop_price_at_entry') or 0.0); atr=(ep-sp)/sl if ep>0 and sp>0 and sl>0 else 0.0
    if ep>0 and atr>0:a['sum_atr_pct']+=atr/ep*100.0;a['atr_n']+=1
 return agg

def attach_history(rows,agg):
 for r in rows:
  a=agg.get(r['candidate_id'],{}); n=int(a.get('n',0)); r['history_n']=n;r['history_avg_pnl_pct']=a.get('sum_pnl',0.0)/n if n else math.nan;r['history_win_rate_pct']=a.get('wins',0)/n*100 if n else math.nan;r['history_avg_atr_pct']=a.get('sum_atr_pct',0.0)/a.get('atr_n',1) if a.get('atr_n',0) else math.nan
 return rows

def threshold_bundle(rows):
 out={}
 for stage in ('stage2','stage3'):
  x=pd.DataFrame([r for r in rows if r['stage']==stage and r['origin_complete'] and r['history_n']>0])
  counts=x.history_n.to_numpy(); wins=x.history_win_rate_pct.to_numpy(); pnl=x.history_avg_pnl_pct.to_numpy()
  nmin=int(np.quantile(counts,.10,method='higher'))
  sufficient=x[x.history_n>=nmin]
  wcut=float(np.quantile(sufficient.history_win_rate_pct,.10,method='linear'))
  out[stage]={'candidate_n':len(x),'sample_n_p10':nmin,'sample_n_q25':float(np.quantile(counts,.25)),'sample_n_median':float(np.median(counts)),'win_rate_p10_pct':wcut,'win_rate_legacy_45_percentile':float((wins<45.0).mean()*100),'avg_pnl_negative_count':int((pnl<0).sum()),'avg_pnl_q10_pct':float(np.quantile(pnl,.10)),'avg_pnl_median_pct':float(np.median(pnl))}
 ref=pd.read_csv(ROOT/'data/_system/analysis/vol_perstock_mae_mfe_20260707/per_candidate_summary.csv');ref=ref[ref['split'].astype(str).str.upper().eq('OOS')]
 med=ref.groupby('vol_group')['avg_atr14_pct'].median().to_dict(); out['volatility']={'reference_group_median_atr_pct':med,'high_vol_atr_boundary_pct':(float(med['MID_VOL'])+float(med['HIGH_VOL']))/2,'near_zero_weight_abs_max':0.05}
 out['ce']={'ratio_lt':1.25,'top2_share_ge_pct':90.0,'logic':'should_buy AND ratio<1.25 AND top2_share>=90','enforcement_recommendation':'MONITOR_ONLY','reason':'9dd8e02 OOS_DEGRADED'}
 return out

def vol_reference():
 p=ROOT/'data/_system/analysis/vol_perstock_mae_mfe_20260707/per_candidate_summary.csv';d=pd.read_csv(p);d=d[d['split'].astype(str).str.upper().eq('OOS')]
 m={}
 for t,g in d.groupby(d.ticker.astype(str).str.upper()):m[t]=g.vol_group.mode().iloc[0]
 return m

def ce_reference():
 p=AUDIT/'live93_three_symptom_scan.csv';d=pd.read_csv(p);return {str(r.candidate_id):r._asdict() for r in d.itertuples(index=False)}

def apply_checks(rows,thr):
 vmap=vol_reference(); cmap=ce_reference(); hb=thr['volatility']['high_vol_atr_boundary_pct']
 for r in rows:
  fails=[]; holds=[]
  r['check_complete']='PASS' if r['origin_complete'] else 'FAIL';
  if not r['origin_complete']:fails.append('INCOMPLETE_OR_UNVALIDATED')
  t=thr[r['stage']]; n=r['history_n']
  if n<t['sample_n_p10']:r['check_history']='HOLD';holds.append('HISTORY_SAMPLE_LT_P10')
  elif r['history_avg_pnl_pct']<0:r['check_history']='FAIL';fails.append('HISTORY_AVG_PNL_LT_0')
  elif r['history_win_rate_pct']<t['win_rate_p10_pct']:r['check_history']='FAIL';fails.append('HISTORY_WIN_RATE_LT_STAGE_P10')
  else:r['check_history']='PASS'
  vg=vmap.get(r['ticker'])
  if not vg and not math.isnan(r['history_avg_atr_pct']):vg='HIGH_VOL' if r['history_avg_atr_pct']>=hb else 'NON_HIGH_VOL_PROXY'
  r['vol_group']=vg or 'UNKNOWN'; w=abs(float(r['rulebook'].get('weight_volume_surge') or 0.0));r['weight_volume_surge']=float(r['rulebook'].get('weight_volume_surge') or 0.0)
  if r['vol_group']=='UNKNOWN':r['check_boil']='HOLD';holds.append('VOLATILITY_UNKNOWN')
  elif r['vol_group']=='HIGH_VOL' and w<=.05:r['check_boil']='FAIL';fails.append('HIGH_VOL_VOLUME_WEIGHT_NEAR_ZERO')
  else:r['check_boil']='PASS'
  c=cmap.get(r['candidate_id'])
  if c and bool(c.get('eval_ok')) and bool(c.get('should_buy')):
   r['ce_ratio']=float(c.get('ratio'));r['ce_top2_share_pct']=float(c.get('top2_share_pct')) if not pd.isna(c.get('top2_share_pct')) else math.nan
   r['check_ce']='FAIL' if r['ce_ratio']<1.25 and r['ce_top2_share_pct']>=90 else 'PASS'
  else:r['ce_ratio']=math.nan;r['ce_top2_share_pct']=math.nan;r['check_ce']='PENDING'
  r['static_fail_reasons']='|'.join(fails);r['static_hold_reasons']='|'.join(holds);r['static_status']='FAIL' if fails else 'HOLD' if holds else 'PASS'
 return rows

def elite_filter(r):
 m=r['metrics'];rb=r['rulebook'];stage=r['stage'];reason='PASS'
 if stage=='stage2':
  checks=[(m['oos_expectancy_pct']>=2.7,'oos_exp'),(m['oos_fitness']>=70,'fitness'),(m['oos_trade_count']>=15,'trades'),(m['oos_win_rate']>=70,'win'),(m['stress_expectancy_pct']>=.5,'stress'),(m['worst_drawdown_pct']>-18,'dd'),(m['min_trade_count']>=8,'min_trades')]
 else:checks=[(m['oos_expectancy_pct']>=2.7,'exp'),(m['oos_fitness']>=45,'fitness'),(m['oos_win_rate']>=70,'win'),(m['oos_trade_count']>=8,'trades'),(m['worst_drawdown_pct']>-18,'dd')]
 for ok,x in checks:
  if not ok:return False,x
 ok,x=elite._rulebook_passes_anti_pattern_filter(rb,m,stage=stage)
 return ok,x

def rank_and_select(rows):
 deny=elite.load_candidate_denylist(); entries=deny.get('entries') or []
 for r in rows:
  ok,why=elite_filter(r);r['elite_static_pass']=ok;r['elite_filter_reason']=why;r['elite_score']=elite._elite_score(r['metrics'],r['rulebook']) if ok else math.nan
  cand={'candidate_id':r['candidate_id'],'stage':r['stage'],'ticker':r['ticker'],'rulebook_hash':r['rulebook_hash']}
  r['denylisted']=any(elite._denylist_entry_matches(cand,e) for e in entries if bool(e.get('active',True)))
 selected=[]
 for stage,cap in [('stage2',60),('stage3',80)]:
  pool=[r for r in rows if r['stage']==stage and r['static_status']=='PASS' and r['elite_static_pass'] and not r['denylisted']]
  pool.sort(key=lambda r:(r['elite_score'],r['metrics']['oos_fitness'],r['metrics']['oos_expectancy_pct']),reverse=True)
  seen=set()
  for r in pool:
   if r['ticker'] in seen:continue
   seen.add(r['ticker']);r['selected_static']=True;r['selected_stage_rank']=len([x for x in selected if x['stage']==stage])+1;selected.append(r)
   if len([x for x in selected if x['stage']==stage])>=cap:break
 for r in rows:r.setdefault('selected_static',False);r.setdefault('selected_stage_rank',math.nan)
 return selected
