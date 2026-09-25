from __future__ import annotations

import csv, json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'data/_system/analysis/candidate_selection_audit_20260710'
CSV=OUT/'stage23_artifact_lineage.csv'
SNAP=OUT/'stage23_live_dependency_snapshot.json'
S2='exp_batch_stage123_2009_20260616_full/tickers/*/stage2*/'
S3='exp_batch_stage123_2009_20260616_full/tickers/*/stage3*/'
COUNT={'rulebooks_all.jsonl','survivors.jsonl','entry_rulebooks.jsonl','final_rulebooks.jsonl','validation_results.jsonl','stage3_profile_catalog.jsonl','stage3_ineligible.jsonl','central_index.jsonl','stage3_live_pool.jsonl'}

def iso(t): return datetime.fromtimestamp(t,timezone.utc).isoformat() if t else ''
def lines(p):
 n=0
 with p.open('rb') as f:
  for x in f:
   if x.strip(): n+=1
 return n

def stats(pattern, exact=False):
 ps=[ROOT/pattern] if exact else sorted(ROOT.glob(pattern)); ps=[p for p in ps if p.is_file()]
 mt=[p.stat().st_mtime for p in ps]; name=Path(pattern).name
 return {'file_count':len(ps),'size_bytes':sum(p.stat().st_size for p in ps),'records':sum(lines(p) for p in ps) if name in COUNT else '', 'oldest_modified_utc':iso(min(mt)) if mt else '', 'latest_modified_utc':iso(max(mt)) if mt else '', 'sample_paths':'|'.join(p.relative_to(ROOT).as_posix() for p in ps[:3])}

def row(stage,artifact,path,lineage,retrain,regen,code,live,brk,decision,reason,exact=False):
 return {'stage':stage,'artifact':artifact,'path_pattern':path,'lineage':lineage,'retraining_required':retrain,'regeneration':regen,'regeneration_code':code,'live_reference':live,'immediate_break_if_deleted':brk,'decision':decision,'decision_reason':reason,**stats(path,exact)}

def loadj(p):
 try:return json.loads((ROOT/p).read_text())
 except:return None

def main():
 defs=[
 ('Stage2','config.json',S2+'config.json','ORIGIN','O(exact)','Stage2 full rerun','run_stage2.py::build_config/run_stage2','none','X','KEEP_ORIGIN','seed/GA/data/code provenance'),
 ('Stage2','rulebooks_all.jsonl',S2+'rulebooks_all.jsonl','ORIGIN','O','rerun Stage2 GA','run_stage2.py::run_training','not direct; re-gate source','future re-gate impossible','KEEP_ORIGIN','direct Stage2 GA individual pool'),
 ('Stage2','ga_history.csv',S2+'ga_history.csv','ORIGIN','O(exact)','rerun Stage2 GA','run_stage2.py::run_training','none','X','KEEP_ORIGIN','direct GA telemetry'),
 ('Stage2','period_metrics_all.csv',S2+'period_metrics_all.csv','DERIVED','X','re-evaluate rulebooks_all','run_stage2.py::evaluate_periods','not direct','X','SAFE_TO_REGEN_DELETE','deterministic evaluation output'),
 ('Stage2','early_cut_log.csv',S2+'early_cut_log.csv','DERIVED','X','re-evaluate rulebooks_all with gate','run_stage2.py::evaluate_periods','none','X','SAFE_TO_REGEN_DELETE','gate trace derived from origin'),
 ('Stage2','survivors.jsonl',S2+'survivors.jsonl','DERIVED','X','filter/re-evaluate rulebooks_all','run_stage2.py::evaluate_periods; standalone wrapper needed','DIRECT central_index/elite/central-control','O','KEEP_ORIGIN','derived but live dereferences it'),
 ('Stage2','trades.jsonl',S2+'trades.jsonl','DERIVED','X','backtest retained rulebooks','run_stage2.py::evaluate_periods','OPTIONAL elite report include_trades','partial dashboard','REVIEW','regenerable but report API reads it'),
 ('Stage2','rl_replay_trades.jsonl',S2+'rl_replay_trades.jsonl','DERIVED','X','backtest and emit replay schema','run_stage2.py::_rl_replay_trade','none current','X','SAFE_TO_REGEN_DELETE','replay intermediate'),
 ('Stage2','summary.json',S2+'summary.json','DERIVED','X(core)/O(exact)','summarize outputs','run_stage2.py::run_stage2','none','X','REVIEW','runtime telemetry not exactly reconstructable'),
 ('Stage2','_stage2_done.json',S2+'_stage2_done.json','DERIVED','X','validate outputs/rewrite marker','run_stage23_batch.py::make_stage2_marker','batch resume','resume break','KEEP_ORIGIN','operational marker'),
 ('Stage3 qualify','qualify_result.json',S3+'qualify_result.json','ORIGIN','O','rerun qualify GA','run_stage3_aggressive.py::run_qualify','entry prerequisite','future entry resume break','KEEP_ORIGIN','qualify individuals are intentionally discarded'),
 ('Stage3 entry','entry_rulebooks.jsonl',S3+'entry_rulebooks.jsonl','ORIGIN','O','rerun entry GA','run_stage3_aggressive.py::run_entry_ga','exit GA prerequisite','exit retrain impossible','KEEP_ORIGIN','direct entry-GA output'),
 ('Stage3 entry','entry_rejected_overlap.json',S3+'entry_rejected_overlap.json','ORIGIN','O(exact)','rerun entry GA/selection','run_stage3_aggressive.py::_select_diverse_entry_rows','none','X','KEEP_ORIGIN','rejected pool not recoverable from selected entries'),
 ('Stage3 entry','entry_result.json',S3+'entry_result.json','ORIGIN','O(exact)','rerun entry GA','run_stage3_aggressive.py::run_entry_ga','none','X','KEEP_ORIGIN','GA pool/runtime provenance'),
 ('Stage3 exit','final_rulebooks.jsonl',S3+'final_rulebooks.jsonl','ORIGIN','O','rerun exit-gene GA from entries','run_stage3_aggressive.py::run_exit_ga','DIRECT elite report/evaluate_candidate','O','KEEP_ORIGIN','direct exit-GA output, not filter result'),
 ('Stage3 exit','exit_result.json',S3+'exit_result.json','ORIGIN','O(exact)','rerun exit GA','run_stage3_aggressive.py::run_exit_ga','none','X','KEEP_ORIGIN','exit-GA weights/best/runtime provenance'),
 ('Stage3 validate','validation_results.jsonl',S3+'validation_results.jsonl','DERIVED','X','validate final_rulebooks','run_stage3_aggressive.py::run_validate','not current elite direct','X','SAFE_TO_REGEN_DELETE','deterministic validation output'),
 ('Stage3 validate','stage3_profile_catalog.jsonl',S3+'stage3_profile_catalog.jsonl','DERIVED','X','validate final_rulebooks in clean temp dir','run_stage3_aggressive.py::run_validate','source for stage3_live_pool; not active elite direct','future pool rebuild blocked','SAFE_TO_REGEN_DELETE','profile/filter output from final origin'),
 ('Stage3 validate','stage3_ineligible.jsonl',S3+'stage3_ineligible.jsonl','DERIVED','X','validate final_rulebooks','run_stage3_aggressive.py::run_validate','none','X','SAFE_TO_REGEN_DELETE','failed-gate rows derived from final'),
 ('Stage3 validate','exit_trades.jsonl',S3+'exit_trades.jsonl','DERIVED','X','validate/backtest final_rulebooks','run_stage3_aggressive.py::run_validate','OPTIONAL elite report include_trades','partial report','KEEP_ORIGIN','absolute preservation overrides regenerability'),
 ('Stage3 validate','rl_replay_trades.jsonl',S3+'rl_replay_trades.jsonl','DERIVED','X','validate/backtest final_rulebooks','run_stage3_aggressive.py::run_validate','none current','X','SAFE_TO_REGEN_DELETE','replay intermediate'),
 ('Stage3 validate','validate_result.json',S3+'validate_result.json','DERIVED','X','summarize validation','run_stage3_aggressive.py::run_validate','wrapper required output','future validation check fails','REVIEW','regenerable but operational validator requires it'),
 ('Stage3','last_run_summary.json',S3+'last_run_summary.json','DERIVED','X(core)','aggregate summaries','run_stage3_aggressive.py::main','wrapper required output','future validation check fails','REVIEW','derived operational summary'),
 ('Stage3','_stage3_done.json',S3+'_stage3_done.json','DERIVED','X','validate outputs/rewrite marker','run_stage23_batch.py::make_stage3_marker','batch resume','resume break','KEEP_ORIGIN','operational marker'),
 ('Batch index','central_index.jsonl','exp_batch_stage123_2009_20260616_full/central_index.jsonl','DERIVED','X','re-index retained outputs','run_stage23_batch.py::build_*_central_index_rows','DIRECT Stage2 elite/central-control','O','KEEP_ORIGIN','derived but live directly reads it'),
 ('Live derived','stage3_live_pool.jsonl','data/_system/central/stage3_live_pool/stage3_live_pool.jsonl','DERIVED','X','filter profile catalogs','build_stage3_live_pool.py','DIRECT only with --central-stage3-mix on; default off','conditional','REVIEW','regenerable supported execution input'),
 ('Live derived','live_slots_state.json','data/_system/live_slots_state.json','DERIVED','X','daemon refresh','live_candidate_slots.py::refresh_slots','DIRECT active daemon/dashboard','O until refresh','KEEP_ORIGIN','active operational state'),
 ('Live derived','real_dashboard_buy_candidates.json','data/_system/real_dashboard_buy_candidates.json','DERIVED','X','export live slots','export_real_dashboard_buy_candidates.py','DIRECT dashboard/manual buy','O until export','KEEP_ORIGIN','active handoff'),
 ('Audit derived','historical live93 candidate scan','data/_system/analysis/candidate_selection_audit_20260710/live93_*','DERIVED','X(historical exact may differ)','rerun historical report/audit','analysis readouts','none','X','KEEP_ORIGIN','analysis absolute preservation'),
 ]
 rows=[]
 for d in defs:
  exact=d[2] in {'exp_batch_stage123_2009_20260616_full/central_index.jsonl','data/_system/central/stage3_live_pool/stage3_live_pool.jsonl','data/_system/live_slots_state.json','data/_system/real_dashboard_buy_candidates.json'}
  rows.append(row(*d,exact=exact))
 fields=list(rows[0])
 with CSV.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 snap={'created_at':datetime.now(timezone.utc).isoformat(),'lineage_counts':dict(Counter(r['lineage'] for r in rows)),'decision_counts':dict(Counter(r['decision'] for r in rows))}
 try:
  from engine.live.elite_shadow_report import build_elite_shadow_report
  snap['elite_shadow_report']=build_elite_shadow_report(stage2_limit=60,stage3_limit=80,include_trades=False).get('summary') or {}
 except Exception as e:snap['elite_shadow_report_error']=f'{type(e).__name__}:{e}'
 for key,p in {'live_slots':'data/_system/live_slots_state.json','dashboard_candidates':'data/_system/real_dashboard_buy_candidates.json','elite_shadow_state':'data/_system/elite_shadow_state.json','broker_positions':'data/_system/positions.json'}.items():
  x=loadj(p); q=ROOT/p; z={'path':p,'exists':q.exists(),'size_bytes':q.stat().st_size if q.exists() else 0,'modified_utc':iso(q.stat().st_mtime) if q.exists() else ''}
  if isinstance(x,dict):
   if key=='live_slots':z.update(candidate_pool=len(x.get('candidate_pool') or []),slots=len(x.get('slots') or []),waitlist=len(x.get('waitlist') or []),updated_at=x.get('updated_at'))
   elif key=='dashboard_candidates':z.update(candidates=len(x.get('candidates') or {}),updated_at=x.get('updated_at'))
   elif key=='elite_shadow_state':z.update(open_positions=len(x.get('open_positions') or {}),summary=x.get('summary'),updated_at=x.get('updated_at'))
   else:z.update(positions=len(x),tickers=sorted(x))
  snap[key]=z
 snap['critical_findings']=['rulebooks_all=Stage2 GA origin','survivors=derived but live direct','entry_rulebooks=entry GA origin','final_rulebooks=exit GA origin','qualify individuals discarded; qualify re-gate requires GA rerun','validation/profile/ineligible derived from final_rulebooks']
 SNAP.write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'rows':len(rows),'lineage':snap['lineage_counts'],'decisions':snap['decision_counts'],'elite_candidates':snap.get('elite_shadow_report',{}).get('candidate_count'),'live_slots':snap['live_slots'],'shadow_open':snap['elite_shadow_state'].get('open_positions'),'broker_positions':snap['broker_positions'].get('positions')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
