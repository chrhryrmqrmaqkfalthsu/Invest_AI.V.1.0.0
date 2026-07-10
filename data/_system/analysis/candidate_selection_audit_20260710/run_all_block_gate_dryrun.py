from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import json,pandas as pd
R=Path(__file__).resolve().parents[4];A=R/'data/_system/analysis/candidate_selection_audit_20260710'
SRC=A/'integrated_gate_candidate_dryrun.csv';WIN={'stage2':58.52738150023009,'stage3':50.0};NREF={'stage2':35,'stage3':24};CAP={'stage2':60,'stage3':80}
def bs(s):return s if s.dtype==bool else s.astype(str).str.lower().isin(['true','1','yes'])
def select(d,statuses):
 out=[]
 for st,cap in CAP.items():
  q=d[(d.stage==st)&d.final_status.isin(statuses)&d.elite_ok&~d.deny].sort_values(['elite_score','oos_fitness','oos_expectancy_pct','rulebook_hash'],ascending=[False,False,False,True]);seen=set();rank=0
  for _,r in q.iterrows():
   if r.ticker in seen:continue
   seen.add(r.ticker);rank+=1;r=r.copy();r['selected_stage_rank']=rank;r['selection_policy']='denylist_before_ticker_dedup';out.append(r)
   if rank>=cap:break
 return pd.DataFrame(out)
def sr(name,q,note):
 c=q.stage.value_counts().to_dict() if len(q) else {};return {'scenario':name,'candidate_count':len(q),'stage2_count':int(c.get('stage2',0)),'stage3_count':int(c.get('stage3',0)),'confirmed_pass_count':int(q.final_status.eq('PASS').sum()) if len(q) else 0,'unjudged_count':int(q.final_status.eq('UNJUDGED').sum()) if len(q) else 0,'zero_candidate_risk':len(q)==0,'note':note}
def main():
 d=pd.read_csv(SRC);assert len(d)==17071 and d.candidate_id.nunique()==17071
 d['complete']=bs(d.origin_complete);d['deny']=bs(d.denylisted);d['elite_ok']=bs(d.elite_static_pass);d['win_cutoff_pct']=d.stage.map(WIN);d['sample_reference_p10']=d.stage.map(NREF);d['sample_insufficient_warning']=d.base_n.fillna(0)<d.sample_reference_p10
 d['fail_completeness']=~d.complete;d['fail_avg_pnl_negative']=d.base_avg_pnl_pct.notna()&d.base_avg_pnl_pct.lt(0);d['fail_win_rate']=d.base_win_rate_pct.notna()&d.base_win_rate_pct.lt(d.win_cutoff_pct);d['fail_boil']=d.check_boil.astype(str).eq('FAIL');d['fail_ce']=d.check_ce.astype(str).eq('FAIL');d['ce_judgment']=d.check_ce.astype(str).map({'PASS':'PASS','FAIL':'FAIL','PENDING':'UNJUDGED'}).fillna('UNJUDGED')
 checks=[('COMPLETENESS','fail_completeness'),('AVG_PNL_LT_0','fail_avg_pnl_negative'),('WIN_RATE_LT_STAGE_CUTOFF','fail_win_rate'),('HIGH_VOL_VOLUME_WEIGHT_NEAR_ZERO','fail_boil'),('CE_RATIO_LT_1_25_AND_TOP2_GE_90','fail_ce')]
 d['block_reasons']=d.apply(lambda r:'|'.join(n for n,c in checks if bool(r[c])),axis=1);d['block_reason_count']=d.block_reasons.str.count(r'\|')+d.block_reasons.ne('').astype(int);d['final_status']='PASS';d.loc[d.block_reason_count.gt(0),'final_status']='FAIL';d.loc[d.block_reason_count.eq(0)&d.ce_judgment.eq('UNJUDGED'),'final_status']='UNJUDGED';d['primary_block_reason']=d.block_reasons.str.split('|').str[0]
 cols=['candidate_id','stage','ticker','rulebook_hash','source_file','source_row_index','final_status','block_reasons','block_reason_count','primary_block_reason','complete','profile_eligible','done_marker','period_count','base_n','sample_reference_p10','sample_insufficient_warning','base_avg_pnl_pct','base_win_rate_pct','win_cutoff_pct','vol_group','history_avg_atr_pct','weight_volume_surge','check_boil','ce_judgment','ce_ratio','ce_top2_share_pct','check_ce','fail_completeness','fail_avg_pnl_negative','fail_win_rate','fail_boil','fail_ce','elite_ok','elite_filter_reason','elite_score','oos_expectancy_pct','oos_fitness','oos_win_rate','oos_trade_count','worst_drawdown_pct','deny']
 d[cols].sort_values(['final_status','stage','ticker','candidate_id']).to_csv(A/'all_block_candidate_decisions.csv',index=False)
 hits=[]
 for n,c in checks:
  for r in d[d[c]].itertuples(index=False):hits.append({'condition':n,'candidate_id':r.candidate_id,'stage':r.stage,'ticker':r.ticker,'final_status':r.final_status,'all_block_reasons':r.block_reasons,'base_n':r.base_n,'base_avg_pnl_pct':r.base_avg_pnl_pct,'base_win_rate_pct':r.base_win_rate_pct,'win_cutoff_pct':r.win_cutoff_pct,'vol_group':r.vol_group,'history_avg_atr_pct':r.history_avg_atr_pct,'weight_volume_surge':r.weight_volume_surge,'ce_judgment':r.ce_judgment,'ce_ratio':r.ce_ratio,'ce_top2_share_pct':r.ce_top2_share_pct})
 pd.DataFrame(hits).sort_values(['condition','stage','ticker','candidate_id']).to_csv(A/'all_block_condition_hits.csv',index=False)
 sums=[]
 for scope in ['ALL','stage2','stage3']:
  q=d if scope=='ALL' else d[d.stage==scope]
  for n,c in checks:sums.append({'scope':scope,'condition':n,'status':'FAIL','count':int(q[c].sum()),'total':len(q),'rate_pct':round(q[c].mean()*100,6)})
  for st,n in q.final_status.value_counts().items():sums.append({'scope':scope,'condition':'OR_FINAL_STATUS','status':st,'count':int(n),'total':len(q),'rate_pct':round(n/len(q)*100,6)})
  for st in ['PASS','FAIL','UNJUDGED']:sums.append({'scope':scope,'condition':'CE_COVERAGE','status':st,'count':int(q.ce_judgment.eq(st).sum()),'total':len(q),'rate_pct':round(q.ce_judgment.eq(st).mean()*100,6)})
  sums.append({'scope':scope,'condition':'SAMPLE_REFERENCE_WARNING','status':'BELOW_P10_NOT_BLOCKED','count':int(q.sample_insufficient_warning.sum()),'total':len(q),'rate_pct':round(q.sample_insufficient_warning.mean()*100,6)})
 pd.DataFrame(sums).to_csv(A/'all_block_condition_summary.csv',index=False)
 cov=select(d,{'PASS','UNJUDGED'});fc=select(d,{'PASS'});jud=select(d[d.ce_judgment.isin(['PASS','FAIL'])],{'PASS'});fcols=cols+['selected_stage_rank','selection_policy'];cov[fcols].to_csv(A/'all_block_final_candidates_coverage_aware.csv',index=False);fc[fcols].to_csv(A/'all_block_final_candidates_fail_closed.csv',index=False);jud[fcols].to_csv(A/'all_block_final_candidates_judged_only.csv',index=False)
 scenarios=[sr('origin_all',d,'all origin candidates'),sr('origin_confirmed_pass',d[d.final_status=='PASS'],'all five conditions judged and passed'),sr('origin_unjudged',d[d.final_status=='UNJUDGED'],'no known fail; CE unavailable'),sr('origin_fail',d[d.final_status=='FAIL'],'one or more BLOCK conditions failed'),sr('ce_judged_origin',d[d.ce_judgment.isin(['PASS','FAIL'])],'historical CE coverage'),sr('coverage_aware_ranked',cov,'known FAIL removed; PASS+UNJUDGED ranked'),sr('judged_only_ranked',jud,'only CE-judged confirmed PASS'),sr('unjudged_fail_closed_ranked',fc,'only confirmed PASS published')]
 ds=d.copy();ds.loc[ds.sample_insufficient_warning,'final_status']='FAIL';scenarios.append(sr('sample_below_p10_also_fail_closed_origin',ds[ds.final_status=='PASS'],'sensitivity only; not listed as BLOCK'))
 pd.DataFrame(scenarios).to_csv(A/'all_block_scenario_summary.csv',index=False)
 th={'created_at':datetime.now(timezone.utc).isoformat(),'policy':'all-causal-conditions-block-dryrun-v1','or_semantics':'any FAIL=>FAIL; no FAIL+CE unavailable=>UNJUDGED; all judged pass=>PASS','conditions':{'completeness':'origin_complete true; Stage3 profile eligible','avg_pnl':'base average PnL <0% FAIL','win_rate':{'stage2_lt_pct':WIN['stage2'],'stage3_lt_pct':WIN['stage3']},'boil':'HIGH_VOL and abs(weight_volume_surge)<=0.05 FAIL','ce':'ratio<1.25 and Top2>=90% FAIL; absent snapshot UNJUDGED'},'sample_reference_not_blocker':{'stage2_p10':35,'stage3_p10':24},'selection_order':['BLOCK checks','elite filter/score','denylist','ticker dedup fallback','stage caps 60/80'],'ce_source':'historical live93 snapshot; no live evaluation'};(A/'all_block_thresholds.json').write_text(json.dumps(th,ensure_ascii=False,indent=2))
 res={'created_at':datetime.now(timezone.utc).isoformat(),'source_rows':len(d),'source_stage_counts':d.stage.value_counts().to_dict(),'condition_fail_counts':{n:int(d[c].sum()) for n,c in checks},'condition_hit_rows':len(hits),'final_status_counts':d.final_status.value_counts().to_dict(),'ce_coverage_counts':d.ce_judgment.value_counts().to_dict(),'sample_warning_counts':{'all':int(d.sample_insufficient_warning.sum()),'stage2':int(d[d.stage=='stage2'].sample_insufficient_warning.sum()),'stage3':int(d[d.stage=='stage3'].sample_insufficient_warning.sum())},'coverage_aware_final_count':len(cov),'coverage_aware_final_status_counts':cov.final_status.value_counts().to_dict(),'coverage_aware_stage_counts':cov.stage.value_counts().to_dict(),'judged_only_final_count':len(jud),'judged_only_stage_counts':jud.stage.value_counts().to_dict(),'fail_closed_final_count':len(fc),'fail_closed_stage_counts':fc.stage.value_counts().to_dict(),'fail_closed_candidate_ids':fc.candidate_id.tolist(),'scenarios':scenarios,'no_source_mutation':True,'no_live_candidate_file_change':True,'no_order':True};(A/'all_block_dryrun_summary.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
 print(json.dumps({'fails':res['condition_fail_counts'],'status':res['final_status_counts'],'ce':res['ce_coverage_counts'],'coverage_final':{'n':len(cov),'status':cov.final_status.value_counts().to_dict(),'stage':cov.stage.value_counts().to_dict()},'fail_closed':{'n':len(fc),'stage':fc.stage.value_counts().to_dict(),'ids':fc.candidate_id.tolist()}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
