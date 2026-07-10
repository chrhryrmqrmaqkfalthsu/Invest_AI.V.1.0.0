from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[4]
A=ROOT/'data/_system/analysis/candidate_selection_audit_20260710'
C=A/'integrated_gate_candidate_dryrun.csv';P=A/'integrated_gate_pass_candidates.csv';COND=A/'integrated_gate_condition_summary.csv';SCEN=A/'integrated_gate_scenario_summary.csv';THR=A/'integrated_gate_thresholds.json';ARCH=A/'integrated_gate_architecture.json';SNAP=A/'integrated_gate_simulation_summary.json';VAL=A/'integrated_gate_history_validation.csv';EVID=A/'integrated_gate_checker_evidence.csv'
NMIN={'stage2':35,'stage3':24};WCUT={'stage2':58.52738150023009,'stage3':50.0}
EXTRA_CHECKERS={'recommended_static','history_win_monitor','boil_monitor','ce_monitor'}

def summarize(q,label):
 h=q[q.holdout_n>0]
 return {'group':label,'candidate_n':len(q),'holdout_candidate_n':len(h),'holdout_trade_n':int(h.holdout_n.sum()) if len(h) else 0,'holdout_candidate_equal_avg_pnl_pct':h.holdout_avg_pnl_pct.mean() if len(h) else None,'holdout_trade_weighted_avg_pnl_pct':((h.holdout_avg_pnl_pct*h.holdout_n).sum()/h.holdout_n.sum()) if len(h) and h.holdout_n.sum() else None,'holdout_candidate_equal_win_rate_pct':h.holdout_win_rate_pct.mean() if len(h) else None}
def select(d,deny_before=True):
 out=[]
 for stage,cap in [('stage2',60),('stage3',80)]:
  q=d[(d.stage==stage)&(d.recommended_static_status=='PASS')&d.elite_static_pass].sort_values(['elite_score','oos_fitness','oos_expectancy_pct'],ascending=False)
  if deny_before:q=q[~q.denylisted]
  seen=set()
  for _,r in q.iterrows():
   if r.ticker in seen:continue
   seen.add(r.ticker);out.append(r)
   if sum(x.stage==stage for x in out)>=cap:break
 if not deny_before:out=[r for r in out if not r.denylisted]
 return pd.DataFrame(out)
def main():
 d=pd.read_csv(C)
 sample_low=d.apply(lambda r:r.base_n<NMIN[str(r.stage)],axis=1)
 d['recommended_static_status']='PASS'
 d.loc[sample_low & d.origin_complete,'recommended_static_status']='HOLD'
 d.loc[~d.origin_complete,'recommended_static_status']='FAIL'
 d.loc[d.base_avg_pnl_pct<0,'recommended_static_status']='FAIL'
 d['history_win_monitor']=d.apply(lambda r:r.base_win_rate_pct<WCUT[str(r.stage)] if pd.notna(r.base_win_rate_pct) else False,axis=1)
 d['boil_monitor']=d.check_boil.eq('FAIL');d['ce_monitor']=d.check_ce.eq('FAIL')
 d.to_csv(C,index=False)
 sel=select(d,True);cur=select(d,False);sel['recommended_pass']=True;sel['strict_ce_known_pass']=sel.check_ce.ne('FAIL');sel['strict_ce_fail_closed_pass']=sel.check_ce.eq('PASS');sel.to_csv(P,index=False)
 old=pd.read_csv(COND);old=old[~old.checker.isin(EXTRA_CHECKERS)];extra=[]
 for scope in ['ALL','stage2','stage3']:
  q=d if scope=='ALL' else d[d.stage==scope]
  for col,name in [('recommended_static_status','recommended_static'),('history_win_monitor','history_win_monitor'),('boil_monitor','boil_monitor'),('ce_monitor','ce_monitor')]:
   for status,n in q[col].value_counts(dropna=False).items():extra.append({'scope':scope,'checker':name,'status':status,'count':int(n),'total':len(q),'rate_pct':round(n/len(q)*100,6)})
 pd.concat([old,pd.DataFrame(extra)],ignore_index=True).to_csv(COND,index=False)
 scenarios=pd.DataFrame([{'scenario':'origin_total','candidate_count':len(d),'stage2_count':int((d.stage=='stage2').sum()),'stage3_count':int((d.stage=='stage3').sum()),'zero_risk':False},{'scenario':'strict_static_all_requested','candidate_count':int((d.static_status=='PASS').sum()),'stage2_count':int(((d.static_status=='PASS')&(d.stage=='stage2')).sum()),'stage3_count':int(((d.static_status=='PASS')&(d.stage=='stage3')).sum()),'zero_risk':False},{'scenario':'evidence_safe_static_pass_origin','candidate_count':int((d.recommended_static_status=='PASS').sum()),'stage2_count':int(((d.recommended_static_status=='PASS')&(d.stage=='stage2')).sum()),'stage3_count':int(((d.recommended_static_status=='PASS')&(d.stage=='stage3')).sum()),'zero_risk':False},{'scenario':'evidence_safe_ranked_ce_monitor','candidate_count':len(sel),'stage2_count':int((sel.stage=='stage2').sum()),'stage3_count':int((sel.stage=='stage3').sum()),'zero_risk':len(sel)==0},{'scenario':'strict_ce_known_only','candidate_count':int(sel.strict_ce_known_pass.sum()),'stage2_count':int(sel[sel.strict_ce_known_pass].stage.eq('stage2').sum()),'stage3_count':int(sel[sel.strict_ce_known_pass].stage.eq('stage3').sum()),'zero_risk':int(sel.strict_ce_known_pass.sum())==0},{'scenario':'strict_ce_fail_closed','candidate_count':int(sel.strict_ce_fail_closed_pass.sum()),'stage2_count':int(sel[sel.strict_ce_fail_closed_pass].stage.eq('stage2').sum()),'stage3_count':int(sel[sel.strict_ce_fail_closed_pass].stage.eq('stage3').sum()),'zero_risk':int(sel.strict_ce_fail_closed_pass.sum())==0},{'scenario':'deny_after_dedup_current_order','candidate_count':len(cur),'stage2_count':int((cur.stage=='stage2').sum()),'stage3_count':int((cur.stage=='stage3').sum()),'zero_risk':len(cur)==0}]);scenarios.to_csv(SCEN,index=False)
 vals=[]
 for stage in ['stage2','stage3']:
  q=d[(d.stage==stage)&d.origin_complete]
  rules={'SAMPLE_LT_P10':q.base_n<NMIN[stage],'AVG_PNL_LT_0':q.base_avg_pnl_pct<0,'WIN_LT_P10':q.base_win_rate_pct<WCUT[stage],'WIN_LT_45':q.base_win_rate_pct<45,'BOIL_PATTERN':q.boil_monitor}
  for name,mask in rules.items():vals.append({'stage':stage,**summarize(q[mask],name)})
  vals.append({'stage':stage,**summarize(q[~rules['WIN_LT_P10']],'WIN_P10_PASS')})
 pd.DataFrame(vals).to_csv(VAL,index=False)
 evidence=[{'checker':'artifact_completeness','requested_mode':'BLOCK','recommended_mode':'BLOCK','evidence':'Stage3 profile membership removes validate bypass; deterministic completeness condition'},{'checker':'history_sample','requested_mode':'BLOCK/HOLD','recommended_mode':'HOLD','evidence':'P10 thresholds derived from development trade-count distribution; reliability guard, not return claim'},{'checker':'history_avg_pnl_negative','requested_mode':'BLOCK','recommended_mode':'BLOCK','evidence':'economic break-even 0%; current complete pool has zero hits'},{'checker':'history_low_win','requested_mode':'BLOCK','recommended_mode':'MONITOR','evidence':'Stage3 low-win group holdout PnL 6.32% vs pass 3.43%; blocking not validated'},{'checker':'boil_high_vol_zero_weight','requested_mode':'BLOCK','recommended_mode':'MONITOR','evidence':'live93 linkage negative, but full-origin proxy cohort holdout direction conflicts; need exact volatility coverage/OOS validation'},{'checker':'ce_ratio_top2','requested_mode':'BLOCK','recommended_mode':'MONITOR','evidence':'9dd8e02 Gate A/B and combination OOS_DEGRADED or neutral'}];pd.DataFrame(evidence).to_csv(EVID,index=False)
 thr=json.loads(THR.read_text());thr['recommended_enforcement']={'completeness':'BLOCK','sample_lt_p10':'HOLD','avg_pnl_lt_0':'BLOCK','win_lt_p10':'MONITOR','boil':'MONITOR','ce':'MONITOR'};thr['history_validation_table']=str(VAL.relative_to(ROOT));THR.write_text(json.dumps(thr,ensure_ascii=False,indent=2),encoding='utf-8')
 arch=json.loads(ARCH.read_text());modes={'artifact_completeness':'BLOCK','exit_history_quality':'BLOCK_WITH_WIN_MONITOR','high_vol_volume_weight_zero':'MONITOR','ce_margin_concentration':'MONITOR'}
 for x in arch['checkers']:
  x['enforcement']=modes[x['name']]
  if x['name']=='exit_history_quality':x['criteria']['win_rate']='<stage P10 WARN only; holdout did not validate blocking';x['note']='sample shortage HOLD; avg PnL<0 FAIL; win lower-tail recorded only'
 arch['policy_version_proposal']='integrated-gate-v1-evidence-safe';arch['strict_requested_policy']='all four checker FAIL values block; provided only as sensitivity scenario, not recommended';ARCH.write_text(json.dumps(arch,ensure_ascii=False,indent=2),encoding='utf-8')
 snap=json.loads(SNAP.read_text());snap['recommended_static_status_counts']=d.recommended_static_status.value_counts().to_dict();snap['recommended_selected_stage_counts']=sel.stage.value_counts().to_dict();snap['recommended_selected_count']=len(sel);snap['deny_after_dedup_count']=len(cur);snap['fallback_recovered_count']=len(sel)-len(cur);snap['fallback_recovered_candidates']=sel[~sel.candidate_id.isin(set(cur.candidate_id))][['candidate_id','ticker','stage']].to_dict('records');snap['selected_monitor_hits']={'history_win':int(sel.history_win_monitor.sum()),'boil':int(sel.boil_monitor.sum()),'ce':int(sel.ce_monitor.sum()),'ce_pending':int(sel.check_ce.eq('PENDING').sum())};snap['scenarios']=scenarios.to_dict('records');SNAP.write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'recommended_static':snap['recommended_static_status_counts'],'selected':snap['recommended_selected_stage_counts'],'fallback':snap['fallback_recovered_candidates'],'monitor_hits':snap['selected_monitor_hits']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
