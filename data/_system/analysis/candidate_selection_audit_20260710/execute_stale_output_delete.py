from pathlib import Path
import argparse,csv,json,subprocess
import pandas as pd

R=Path(__file__).resolve().parents[4]
A=R/'data/_system/analysis/candidate_selection_audit_20260710'
SRC=A/'filter_gate_outputs.csv'; LIVE=A/'live_dependency_tree.csv'
T=A/'stale_output_delete_targets.csv'; M=A/'stale_output_delete_manifest.csv'; S=A/'stale_output_delete_skipped.csv'; J=A/'stale_output_delete_result.json'
ER,EF,EB=4,6,4098465
PEX={'.env','.env.backup','data/_system/candidate_denylist.json','data/_system/live_candidate_list_20260707.json','data/_system/live_slots_state.json','data/_system/real_dashboard_buy_candidates.json','scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001'}
PP=('data/_system/analysis/','backup/','tests/','data/_system/ml_sell_omen/','exp_batch_stage123_2009_20260616_full/')
TOK=('rulebooks_all','entry_rulebooks','final_rulebooks','survivors','profile_catalog','validation_results','exit_trades','frozen_oos','oos_reproduce_frozen')
ACTIVE_FILES=('data/_system/ops/live_candidate_slots.py','engine/live/elite_shadow_report.py','scripts/export_real_dashboard_buy_candidates.py','api_server_candidate_only.py','api_server_aftermarket.py','api_server.py')
NAMES=('stage3_live_pool.jsonl','stage3_live_pool_filtered.jsonl','summary.json','summary_filtered.json','rejected_sample.jsonl','rejected_sample_filtered.jsonl')

def save(p,rows,fields):
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def tracked():return set(subprocess.check_output(['git','ls-files'],cwd=R,text=True).splitlines())
def opened():
 try:o=subprocess.run(['lsof'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=30).stdout
 except:return set()
 pre=str(R.resolve())+'/';z=set()
 for line in o.splitlines():
  for x in line.split()[8:]:
   if x.startswith(pre):z.add(x[len(pre):])
 return z
def procs():
 o=subprocess.run(['ps','-eo','pid,ppid,etime,cmd'],stdout=subprocess.PIPE,text=True).stdout
 lines=[x.strip() for x in o.splitlines() if any(k in x for k in ('live_candidate_slots.py','api_server_candidate_only','scripts/run_live.py')) and 'execute_stale_output_delete.py' not in x]
 run=[x for x in lines if 'scripts/run_live.py' in x]; mix=[x for x in run if '--central-stage3-mix' in x and ' on' in x]
 return lines,run,mix
def active_refs():
 hits=[]
 for f in ACTIVE_FILES:
  p=R/f
  if not p.is_file():continue
  text=p.read_text(errors='ignore')
  for n in NAMES:
   if n in text:hits.append(f'{f}->{n}')
 return hits
def globfiles(pat):return sorted(x for x in R.glob(pat) if x.is_file())
def guard(q,live,op):
 low=q.lower()
 if q in PEX:return 'protected_exact'
 if any(q.startswith(x) for x in PP):return 'protected_prefix'
 if any(x in low for x in TOK):return 'protected_token'
 if q in live:return 'live_dependency'
 if q in op:return 'active_open_file'
 return ''

def preflight():
 d=pd.read_csv(SRC);st=d[d.verdict.eq('STALE_OUTPUT')].sort_values('artifact');non=d[d.verdict.isin(['ACTIVE_LIVE','REGEN_OK'])]
 live=set(pd.read_csv(LIVE).path.astype(str));gt=tracked();op=opened();pl,run,mix=procs();refs=active_refs()
 assert len(st)==ER and int(st.path_count.sum())==EF and int(st.size_bytes.sum())==EB and len(live)==148
 protected=set()
 for r in non.itertuples(index=False):protected.update(x.relative_to(R).as_posix() for x in globfiles(str(r.path_pattern)))
 rows=[];files=[];skip=[];seen=set()
 for r in st.itertuples(index=False):
  fs=globfiles(str(r.path_pattern));ac=len(fs);ab=sum(x.stat().st_size for x in fs);rs=[];det=[]
  if ac!=int(r.path_count) or ab!=int(r.size_bytes):rs.append('filesystem_csv_mismatch');det += [f'ac={ac}',f'cc={int(r.path_count)}',f'ab={ab}',f'cb={int(r.size_bytes)}']
  for x in fs:
   q=x.relative_to(R).as_posix()
   if q in seen:rs.append('overlapping_stale_patterns');det.append(q)
   seen.add(q)
   if q in protected:rs.append('active_or_regen_overlap');det.append(q)
   g=guard(q,live,op)
   if g:rs.append(g);det.append(q)
  if mix:rs.append('active_stage3_mix_process');det+=mix
  if refs:rs.append('active_code_reference');det+=refs
  reason='|'.join(sorted(set(rs)));status='SKIP' if reason else 'DELETE'
  rows.append({'artifact':r.artifact,'path_pattern':r.path_pattern,'csv_path_count':int(r.path_count),'actual_path_count':ac,'csv_size_bytes':int(r.size_bytes),'actual_size_bytes':ab,'latest_modified_utc':r.latest_modified_utc,'preflight_status':status,'skip_reason':reason,'skip_details':'|'.join(sorted(set(det))[:80])})
  if reason:skip.append({'artifact':r.artifact,'path_pattern':r.path_pattern,'reason':reason,'details':'|'.join(sorted(set(det))[:80])});continue
  for x in fs:
   q=x.relative_to(R).as_posix();files.append({'file_path':q,'source_artifact':r.artifact,'size_bytes':x.stat().st_size,'modified_ns':x.stat().st_mtime_ns,'git_tracked':q in gt,'deletion_status':'PENDING'})
 save(T,rows,list(rows[0]));save(M,files,list(files[0]));save(S,skip,['artifact','path_pattern','reason','details'])
 out={'mode':'preflight','csv_rows':len(st),'csv_files':int(st.path_count.sum()),'csv_bytes':int(st.size_bytes.sum()),'deletable_rows':sum(x['preflight_status']=='DELETE' for x in rows),'deletable_files':len(files),'deletable_bytes':sum(x['size_bytes'] for x in files),'skipped_rows':len(skip),'git_tracked_files':sum(bool(x['git_tracked']) for x in files),'active_code_references':refs,'run_live_processes':run,'stage3_mix_on_processes':mix,'relevant_processes':pl,'open_target_files':sorted(set(x['file_path'] for x in files)&op)}
 J.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in out.items() if k not in ('relevant_processes','run_live_processes')},ensure_ascii=False,indent=2));return out

def delete():
 out=preflight();m=pd.read_csv(M)
 for r in m.itertuples(index=False):
  p=R/str(r.file_path);assert p.is_file(),r.file_path;p.unlink();print(f'DELETED|{r.file_path}|{int(r.size_bytes)}')
 assert not any((R/str(r.file_path)).exists() for r in m.itertuples(index=False));m['deletion_status']='DELETED';m.to_csv(M,index=False)
 out.update({'mode':'delete','deleted_rows':out['deletable_rows'],'deleted_files':len(m),'deleted_bytes':int(m.size_bytes.sum()),'remaining_files':0,'status':'COMPLETE'});J.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:out[k] for k in ('status','deleted_rows','deleted_files','deleted_bytes','skipped_rows')},ensure_ascii=False,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['preflight','delete']);a=p.parse_args();preflight() if a.mode=='preflight' else delete()
