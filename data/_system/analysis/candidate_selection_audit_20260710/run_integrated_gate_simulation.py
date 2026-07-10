from __future__ import annotations

import json, math, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE=Path(__file__).resolve().parent; ROOT=Path(__file__).resolve().parents[4]
for p in (HERE,ROOT):
 if str(p) not in sys.path:sys.path.insert(0,str(p))
import integrated_gate_sim_core as core
from integrated_gate_holdout import scan_histories_split,attach_histories

OUT=HERE
CAND=OUT/'integrated_gate_candidate_dryrun.csv'; PASS=OUT/'integrated_gate_pass_candidates.csv'; COND=OUT/'integrated_gate_condition_summary.csv'; SCEN=OUT/'integrated_gate_scenario_summary.csv'; THR=OUT/'integrated_gate_thresholds.json'; ARCH=OUT/'integrated_gate_architecture.json'; SNAP=OUT/'integrated_gate_simulation_summary.json'

def flat(r):
 m=r['metrics']; rb=r['rulebook']
 return {'candidate_id':r['candidate_id'],'stage':r['stage'],'ticker':r['ticker'],'rulebook_hash':r['rulebook_hash'],'source_file':r['source_file'],'source_row_index':r['source_row_index'],'done_marker':r['done_marker'],'profile_eligible':r['profile_eligible'],'origin_complete':r['origin_complete'],'period_count':r['period_count'],'all_history_n':r.get('all_history_n'),'all_history_avg_pnl_pct':r.get('all_history_avg_pnl_pct'),'all_history_win_rate_pct':r.get('all_history_win_rate_pct'),'base_n':r.get('history_n'),'base_avg_pnl_pct':r.get('history_avg_pnl_pct'),'base_win_rate_pct':r.get('history_win_rate_pct'),'holdout_n':r.get('holdout_n'),'holdout_avg_pnl_pct':r.get('holdout_avg_pnl_pct'),'holdout_win_rate_pct':r.get('holdout_win_rate_pct'),'history_avg_atr_pct':r.get('history_avg_atr_pct'),'vol_group':r.get('vol_group'),'weight_volume_surge':r.get('weight_volume_surge'),'check_complete':r.get('check_complete'),'check_history':r.get('check_history'),'check_boil':r.get('check_boil'),'check_ce':r.get('check_ce'),'ce_ratio':r.get('ce_ratio'),'ce_top2_share_pct':r.get('ce_top2_share_pct'),'static_status':r.get('static_status'),'static_fail_reasons':r.get('static_fail_reasons'),'static_hold_reasons':r.get('static_hold_reasons'),'elite_static_pass':r.get('elite_static_pass'),'elite_filter_reason':r.get('elite_filter_reason'),'elite_score':r.get('elite_score'),'denylisted':r.get('denylisted'),'selected_static':r.get('selected_static'),'selected_stage_rank':r.get('selected_stage_rank'),'oos_expectancy_pct':m.get('oos_expectancy_pct'),'oos_fitness':m.get('oos_fitness'),'oos_win_rate':m.get('oos_win_rate'),'oos_trade_count':m.get('oos_trade_count'),'worst_drawdown_pct':m.get('worst_drawdown_pct'),'signal_threshold':rb.get('signal_threshold'),'volume_surge_ratio':rb.get('volume_surge_ratio')}

def group_stats(rows,label):
 x=pd.DataFrame(rows)
 if x.empty:return {'group':label,'candidate_n':0}
 h=x[x.holdout_n>0]
 return {'group':label,'candidate_n':len(x),'holdout_candidate_n':len(h),'holdout_trade_n':int(h.holdout_n.sum()) if len(h) else 0,'holdout_candidate_equal_avg_pnl_pct':float(h.holdout_avg_pnl_pct.mean()) if len(h) else None,'holdout_trade_weighted_avg_pnl_pct':float((h.holdout_avg_pnl_pct*h.holdout_n).sum()/h.holdout_n.sum()) if len(h) and h.holdout_n.sum() else None,'holdout_candidate_equal_win_rate_pct':float(h.holdout_win_rate_pct.mean()) if len(h) else None}

def select_current_style(rows):
 selected=[]
 for stage,cap in [('stage2',60),('stage3',80)]:
  pool=[r for r in rows if r['static_status']=='PASS' and r['elite_static_pass'] and r['stage']==stage]
  pool.sort(key=lambda r:(r['elite_score'],r['metrics']['oos_fitness'],r['metrics']['oos_expectancy_pct']),reverse=True);seen=set()
  for r in pool:
   if r['ticker'] in seen:continue
   seen.add(r['ticker']);selected.append(r)
   if len([x for x in selected if x['stage']==stage])>=cap:break
 return [r for r in selected if not r['denylisted']]

def architecture(thr):
 return {'design_status':'DESIGN_ONLY_NOT_IMPLEMENTED','policy_version_proposal':'integrated-gate-v1','overall_semantics':{'blocking':'any enabled checker FAIL => FAIL','hold':'no FAIL and any HOLD/ERROR => HOLD; not published','pass':'all enabled blockers PASS/NOT_APPLICABLE','monitor_only':'recorded but does not affect overall'},'checker_interface':{'protocol':'CandidateGateChecker','fields':['name','version','phase(STATIC|DYNAMIC)','enforcement(BLOCK|MONITOR)','required_inputs','evaluate(context)->GateCheckResult'],'result_schema':['status(PASS|FAIL|HOLD|NOT_APPLICABLE|ERROR)','reason_codes[]','evidence{}','source_fingerprints{}','evaluated_at','policy_version']},'checkers':[{'name':'artifact_completeness','phase':'STATIC','enforcement':'BLOCK','criteria':'Stage2 _stage2_done + five survivor periods; Stage3 _stage3_done + rule hash in stage3_profile_catalog','source':['survivors.jsonl','final_rulebooks.jsonl','stage3_profile_catalog.jsonl','_stage2_done.json','_stage3_done.json']},{'name':'exit_history_quality','phase':'STATIC','enforcement':'BLOCK','criteria':{'sample_min':'stage-specific development-history count P10','avg_pnl':'<0 FAIL','win_rate':'<stage-specific P10 FAIL'},'thresholds':{'stage2':thr['stage2'],'stage3':thr['stage3']},'holdout':'Stage2 oos_2025h2; Stage3 recent_1y reserved for diagnostic validation'},{'name':'high_vol_volume_weight_zero','phase':'STATIC','enforcement':'BLOCK','criteria':{'high_vol':'reference OOS vol_group; unseen ticker uses ATR-proxy boundary from MID/HIGH medians','abs_weight_volume_surge_lte':0.05},'source':'high_vol_volume_weight_zero_*'},{'name':'ce_margin_concentration','phase':'DYNAMIC','enforcement':'MONITOR','criteria':{'should_buy':True,'ratio_lt':1.25,'top2_core_share_ge_pct':90.0},'source':'live93_three_symptom_scan / 9dd8e02','note':'interface included, but blocking disabled because prior frozen OOS result was OOS_DEGRADED'}],'flow':['scan immutable origins','stable-file snapshot and incremental parse','static checkers','persist static gate catalog','existing elite metrics and score','denylist before per-stage ticker dedup; fallback to next candidate','stage caps','evaluate signal lazily by ranked ticker candidates','dynamic CE checker','should_buy','publish live candidates atomically'],'continuous_update':{'cadence':'live_candidate_slots daemon 60 seconds','fingerprint':'path,size,mtime_ns,tail_hash; per-file byte offset for append-only JSONL','incremental':'process only new/changed candidate hashes and new trade rows','unstable_write_guard':'size/mtime before and after read must match; otherwise defer','full_rebuild_triggers':['policy/checker version hash changed','source shrank or inode changed','profile/done marker changed','state schema changed','manual --full-rebuild'],'state_proposal':'data/_system/integrated_candidate_gate/gate_state.json','atomic_outputs':['data/_system/integrated_candidate_gate/static_catalog.jsonl','data/_system/integrated_candidate_gate/ranked_candidates.jsonl','data/_system/integrated_candidate_gate/live_candidates.json'],'origin_mutation':False},'live_connection_points':[{'file':'data/_system/ops/live_candidate_slots.py','point':'refresh_slots before build_elite_shadow_report/load_gate_list','change':'refresh/read integrated gate catalog; remove fixed 93-ID join'},{'file':'engine/live/elite_shadow_report.py','point':'collectors before ticker dedup','change':'consume gated candidates or shared gate evaluator; apply denylist before dedup'},{'file':'scripts/export_real_dashboard_buy_candidates.py','point':'export validation','change':'require matching policy_version and gate PASS; reuse shared dynamic result'},{'file':'engine/live/central_control.py','point':'DEFAULT_STAGE3_POOL dead path','change':'remove or explicitly rebuild-on-enable; current deleted pool must not be silently expected'}],'candidate_file_schema':{'candidate_id':'stage:ticker:hash12','origin':{'stage':'','ticker':'','rulebook_hash':'','source_file':'','source_row_index':0,'source_fingerprint':''},'checks':{'checker_name':{'status':'','reasons':[],'evidence':{},'version':''}},'overall_static_status':'PASS|FAIL|HOLD','fail_reasons':[],'history':{'n':0,'avg_pnl_pct':0,'win_rate_pct':0,'holdout_diagnostic':{}},'volatility':{'group':'','method':'reference|atr_proxy','avg_atr_pct':0},'ranking':{'elite_score':0,'stage_rank':0},'policy_version':'','evaluated_at':''}}

def main():
 rows,sources=core.load_origins(); agg=scan_histories_split(sources); attach_histories(rows,agg)
 for r in rows:
  r['all_history_n']=r['history_n'];r['all_history_avg_pnl_pct']=r['history_avg_pnl_pct'];r['all_history_win_rate_pct']=r['history_win_rate_pct']
  r['history_n']=r['base_n'];r['history_avg_pnl_pct']=r['base_avg_pnl_pct'];r['history_win_rate_pct']=r['base_win_rate_pct']
 thr=core.threshold_bundle(rows);core.apply_checks(rows,thr);selected=core.rank_and_select(rows);current_style=select_current_style(rows)
 df=pd.DataFrame([flat(r) for r in rows]);df.to_csv(CAND,index=False)
 ps=pd.DataFrame([flat(r) for r in selected]);ps['recommended_pass']=True;ps['strict_ce_known_pass']=ps.check_ce.ne('FAIL');ps['strict_ce_fail_closed_pass']=ps.check_ce.eq('PASS');ps.to_csv(PASS,index=False)
 cond=[]
 for stage in ['ALL','stage2','stage3']:
  q=df if stage=='ALL' else df[df.stage.eq(stage)]
  for checker,col in [('completeness','check_complete'),('history','check_history'),('boil','check_boil'),('ce_dynamic','check_ce'),('static_or','static_status')]:
   for status,n in q[col].value_counts(dropna=False).items():cond.append({'scope':stage,'checker':checker,'status':status,'count':int(n),'total':len(q),'rate_pct':round(n/len(q)*100,6) if len(q) else 0})
 pd.DataFrame(cond).to_csv(COND,index=False)
 scenarios=[{'scenario':'origin_total','candidate_count':len(df),'stage2_count':int((df.stage=='stage2').sum()),'stage3_count':int((df.stage=='stage3').sum()),'zero_risk':False},{'scenario':'static_gate_pass_origin','candidate_count':int((df.static_status=='PASS').sum()),'stage2_count':int(((df.static_status=='PASS')&(df.stage=='stage2')).sum()),'stage3_count':int(((df.static_status=='PASS')&(df.stage=='stage3')).sum()),'zero_risk':int((df.static_status=='PASS').sum())==0},{'scenario':'recommended_ranked_ce_monitor','candidate_count':len(ps),'stage2_count':int((ps.stage=='stage2').sum()),'stage3_count':int((ps.stage=='stage3').sum()),'zero_risk':len(ps)==0},{'scenario':'strict_ce_known_only','candidate_count':int(ps.strict_ce_known_pass.sum()),'stage2_count':int(ps[ps.strict_ce_known_pass].stage.eq('stage2').sum()),'stage3_count':int(ps[ps.strict_ce_known_pass].stage.eq('stage3').sum()),'zero_risk':int(ps.strict_ce_known_pass.sum())==0},{'scenario':'strict_ce_fail_closed','candidate_count':int(ps.strict_ce_fail_closed_pass.sum()),'stage2_count':int(ps[ps.strict_ce_fail_closed_pass].stage.eq('stage2').sum()),'stage3_count':int(ps[ps.strict_ce_fail_closed_pass].stage.eq('stage3').sum()),'zero_risk':int(ps.strict_ce_fail_closed_pass.sum())==0},{'scenario':'current_order_deny_after_dedup','candidate_count':len(current_style),'stage2_count':sum(r['stage']=='stage2' for r in current_style),'stage3_count':sum(r['stage']=='stage3' for r in current_style),'zero_risk':len(current_style)==0}]
 pd.DataFrame(scenarios).to_csv(SCEN,index=False)
 history_validation=[]
 for stage in ['stage2','stage3']:
  sr=[r for r in rows if r['stage']==stage and r['origin_complete']]
  history_validation.append(group_stats([flat(r) for r in sr if r['check_history']=='FAIL'],stage+'_A2_FAIL'))
  history_validation.append(group_stats([flat(r) for r in sr if r['check_history']=='PASS'],stage+'_A2_PASS'))
 thr['history_holdout_validation']=history_validation;THR.write_text(json.dumps(thr,ensure_ascii=False,indent=2),encoding='utf-8')
 ARCH.write_text(json.dumps(architecture(thr),ensure_ascii=False,indent=2),encoding='utf-8')
 summary={'created_at':datetime.now(timezone.utc).isoformat(),'origin_counts':df.stage.value_counts().to_dict(),'static_status_counts':df.static_status.value_counts().to_dict(),'selected_recommended':len(ps),'selected_stage_counts':ps.stage.value_counts().to_dict(),'ce_selected_counts':ps.check_ce.value_counts().to_dict(),'deny_before_dedup_count':len(ps),'deny_after_dedup_count':len(current_style),'fallback_recovered_count':len(ps)-len(current_style),'condition_fail_reasons':Counter(x for s in df.static_fail_reasons.fillna('') for x in str(s).split('|') if x),'condition_hold_reasons':Counter(x for s in df.static_hold_reasons.fillna('') for x in str(s).split('|') if x),'history_holdout_validation':history_validation,'scenarios':scenarios,'notes':['CE dynamic data is inherited from live93 snapshot, not re-evaluated against current market.','CE blocking remains monitor-only recommendation because commit 9dd8e02 found OOS degradation.','No order, retraining, production candidate file or source mutation was performed.']}
 SNAP.write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=dict),encoding='utf-8')
 print(json.dumps({'origin':summary['origin_counts'],'static':summary['static_status_counts'],'selected':summary['selected_stage_counts'],'ce':summary['ce_selected_counts'],'fallback_recovered':summary['fallback_recovered_count'],'thresholds':{k:thr[k] for k in ['stage2','stage3','volatility','ce']},'history_holdout':history_validation},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
