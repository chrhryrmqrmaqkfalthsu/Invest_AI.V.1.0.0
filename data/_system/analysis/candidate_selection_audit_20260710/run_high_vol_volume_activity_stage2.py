from pathlib import Path
from datetime import datetime,timezone
import json,sys,pandas as pd
R=Path(__file__).resolve().parents[4];A=R/'data/_system/analysis/candidate_selection_audit_20260710'
for p in (R,A):
 if str(p) not in sys.path:sys.path.insert(0,str(p))
import run_unvalidated_gene_analysis as lib
S1=A/'high_vol_volume_blind_risk_candidates.csv';ACT=A/'unvalidated_gene_rule_activity_full.csv.gz'
ALL=A/'high_vol_volume_activity_stage2_all_stage1.csv';STRICT=A/'high_vol_volume_activity_stage2_strict.csv';RELAX=A/'high_vol_volume_activity_stage2_relaxed.csv';PAR=A/'high_vol_volume_activity_stage2_boil_parity.csv';SCOPE=A/'high_vol_volume_activity_stage2_scope_summary.csv';SUM=A/'high_vol_volume_activity_stage2_summary.json'
def flag(x):return x if isinstance(x,bool) else str(x).lower() in {'true','1','yes'}
def row_at(p,n,c):
 if p not in c:c[p]=[json.loads(x) for x in p.open(errors='ignore') if x.strip()]
 return c[p][n-1]
def label(n,d):return 'NEVER_FIRED' if n==0 else 'RARELY_ACTIVE' if n<5 or (n/d if d else 0)<.01 else 'ACTIVE'
def main():
 risk=pd.read_csv(S1,low_memory=False);assert len(risk)==3036
 use=['pool_scope','ticker','rulebook_hash','source_file','source_line','train_label','train_start','train_end','direction','eligible_days','volume_surge_weight','volume_surge_fired_count','volume_surge_fired_rate','volume_surge_activity_label','volume_surge_validation_state']
 act=pd.read_csv(ACT,usecols=use,low_memory=False);act['k']=act.pool_scope.astype(str)+'|'+act.ticker.astype(str)+'|'+act.rulebook_hash.astype(str);dup=act[act.k.duplicated(False)];payload=[x for x in use if x not in {'source_file','source_line'}];assert all(g[payload].drop_duplicates().shape[0]==1 for _,g in dup.groupby('k'));duplicate_rows=len(act)-act.k.nunique();act=act.sort_values(['k','source_file','source_line']).drop_duplicates('k');am=act.set_index('k').to_dict('index')
 src={};bars={};prep={};rows=[];reused=recalc=0
 for x in risk.itertuples(index=False):
  p=R/str(x.source_file);o=row_at(p,int(x.source_row_index),src);rb=o.get('rulebook') or {}
  if x.stage=='stage3': ah=str(o.get('entry_rulebook_hash') or '');scope='STAGE3_ENTRY'
  else: ah=str(x.rulebook_hash);scope='STAGE2_UPSTREAM'
  z=am.get(f'{scope}|{x.ticker}|{ah}')
  if z is not None:z=dict(z);source='REUSED_A73118D';reused+=1
  else:
   origins=o.get('origins') or [];assert x.stage=='stage2' and len(origins)==1
   g=origins[0];ts,te,tl=str(g['train_start']),str(g['train_end']),str(g['train_label']);direction=str(rb.get('direction','long'));ticker=str(x.ticker)
   if ticker not in bars:bars[ticker]=lib.load_bars(ticker)
   key=(ticker,ts,te,direction)
   if key not in prep:prep[key]=lib.prepare_window(bars[ticker],ts,te,direction)
   e=lib.evaluate_activity(rb,prep[key]);z={'source_file':str(p.relative_to(R)),'source_line':int(x.source_row_index),'train_label':tl,'train_start':ts,'train_end':te,'direction':direction,'eligible_days':int(e['eligible_days']),'volume_surge_weight':float(e['volume_surge_weight']),'volume_surge_fired_count':int(e['volume_surge_fired_count']),'volume_surge_fired_rate':float(e['volume_surge_fired_rate']),'volume_surge_activity_label':str(e['volume_surge_activity_label']),'volume_surge_validation_state':str(e['volume_surge_validation_state'])};source='RECOMPUTED_RETRY_SOURCE';recalc+=1
  n=int(z['volume_surge_fired_count']);days=int(z['eligible_days']);lab=label(n,days);assert lab==str(z['volume_surge_activity_label']);w=float(rb.get('weight_volume_surge'));wd=abs(w-float(z['volume_surge_weight']));assert wd<1e-9
  q=x._asdict();q.update({'activity_rule_hash':ah,'activity_source':source,'activity_source_file':z['source_file'],'activity_source_line':int(z['source_line']),'train_label':z['train_label'],'train_start':z['train_start'],'train_end':z['train_end'],'direction':z['direction'],'eligible_days':days,'volume_surge_ratio':float(rb.get('volume_surge_ratio')),'activity_volume_surge_weight':float(z['volume_surge_weight']),'volume_weight_abs_diff':wd,'volume_surge_fired_count':n,'volume_surge_fired_rate':float(z['volume_surge_fired_rate']),'volume_surge_fired_rate_pct':float(z['volume_surge_fired_rate'])*100,'volume_surge_activity_label':lab,'volume_surge_validation_state':z['volume_surge_validation_state'],'stage2_strict_risk':lab=='NEVER_FIRED','stage2_relaxed_risk':lab in {'NEVER_FIRED','RARELY_ACTIVE'}});rows.append(q)
 out=pd.DataFrame(rows).sort_values(['volume_surge_activity_label','stage','ticker','candidate_id']);assert len(out)==3036 and reused==3033 and recalc==3
 strict=out[out.stage2_strict_risk];relax=out[out.stage2_relaxed_risk];out.to_csv(ALL,index=False);strict.to_csv(STRICT,index=False);relax.to_csv(RELAX,index=False)
 boil=out[out.boil_existing_bool.map(flag)].copy();boil['strict_captured']=boil.stage2_strict_risk;boil['relaxed_captured']=boil.stage2_relaxed_risk;boil['is_named_boil_9044']=boil.candidate_id.eq('stage3:BOIL:9044dc2c67a3');boil.to_csv(PAR,index=False)
 high=pd.read_csv(A/'high_vol_volume_blind_all_high_vol.csv',low_memory=False);sc=[]
 for name,base in [('HIGH_VOL',high),('STAGE1_RISK',out),('COMPLETE_HIGH_VOL',high[high.origin_complete_bool.map(flag)]),('COMPLETE_STAGE1_RISK',out[out.origin_complete_bool.map(flag)])]:
  ids=set(base.candidate_id);den=len(base)
  for metric,n in [('BASE_COUNT',den),('STRICT_NEVER_FIRED',len(set(strict.candidate_id)&ids)),('RELAXED_NEVER_OR_RARE',len(set(relax.candidate_id)&ids))]:sc.append({'scope':name,'metric':metric,'count':n,'denominator':den,'rate_pct':n/den*100 if den else 0})
 for lab,n in out.volume_surge_activity_label.value_counts().items():sc.append({'scope':'STAGE1_RISK_LABEL','metric':lab,'count':int(n),'denominator':len(out),'rate_pct':n/len(out)*100})
 pd.DataFrame(sc).to_csv(SCOPE,index=False)
 named=boil[boil.is_named_boil_9044].iloc[0];summary={'created_at':datetime.now(timezone.utc).isoformat(),'stage1_risk_count':len(out),'high_vol_count':len(high),'origin_count':17071,'activity_data':{'reused_a73118d':reused,'recomputed_missing_retry_sources':recalc,'deduplicated_identical_source_rows':duplicate_rows,'unjudged':0,'label_counts':out.volume_surge_activity_label.value_counts().to_dict(),'definition':{'NEVER_FIRED':'count==0','RARELY_ACTIVE':'count 1..4 OR rate<1%','ACTIVE':'count>=5 AND rate>=1%'}},'strict':{'count':len(strict),'pct_stage1':len(strict)/len(out)*100,'pct_high_vol':len(strict)/len(high)*100,'pct_origin':len(strict)/17071*100,'stage_counts':strict.stage.value_counts().to_dict()},'relaxed':{'count':len(relax),'pct_stage1':len(relax)/len(out)*100,'pct_high_vol':len(relax)/len(high)*100,'pct_origin':len(relax)/17071*100,'stage_counts':relax.stage.value_counts().to_dict()},'boil_parity':{'high_vol_boil_count':len(boil),'strict_captured':int(boil.strict_captured.sum()),'relaxed_captured':int(boil.relaxed_captured.sum()),'named_boil':{'candidate_id':named.candidate_id,'eligible_days':int(named.eligible_days),'fired_count':int(named.volume_surge_fired_count),'fired_rate_pct':float(named.volume_surge_fired_rate_pct),'label':named.volume_surge_activity_label,'strict_captured':bool(named.strict_captured),'relaxed_captured':bool(named.relaxed_captured)}},'no_source_mutation':True,'no_live_change':True,'no_training':True,'no_order':True,'no_delete':True};SUM.write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
