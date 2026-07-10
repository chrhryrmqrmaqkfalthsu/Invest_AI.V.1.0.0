from pathlib import Path
import argparse,csv,json,shutil,subprocess
import pandas as pd

R=Path(__file__).resolve().parents[4]; A=R/'data/_system/analysis/candidate_selection_audit_20260710'
SRC=A/'operational_unused_second_pass.csv'; LIVE=A/'live_dependency_tree.csv'
T=A/'second_pass_delete_targets.csv'; E=A/'second_pass_delete_expanded_manifest.csv'; S=A/'second_pass_delete_skipped.csv'; J=A/'second_pass_delete_result.json'
ER,EF,EB=1082,3507,1795116467
PEX={'.env','.env.backup','data/_system/candidate_denylist.json','scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001','data/logs/kingmaker.log','data/logs/error.log','data/logs/trades.log'}
PP=('data/_system/analysis/','backup/','tests/','data/_system/ml_sell_omen/','exp_batch_stage123_2009_20260616_full/tickers/')
PN={'rulebooks_all.jsonl','entry_rulebooks.jsonl','final_rulebooks.jsonl','survivors.jsonl','validation_results.jsonl','exit_trades.jsonl','candidate_denylist.json','.env','.env.backup'}
TOK=('profile_catalog','frozen_oos','oos_reproduce_frozen')

def tracked(): return set(subprocess.check_output(['git','ls-files'],cwd=R,text=True).splitlines())
def opened():
 try:o=subprocess.run(['lsof'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=30).stdout
 except:return set()
 pre=str(R.resolve())+'/'; z=set()
 for line in o.splitlines():
  for x in line.split()[8:]:
   if x.startswith(pre): z.add(x[len(pre):])
 return z
def under(p): return [p] if p.is_file() else sorted(x for x in p.rglob('*') if x.is_file())
def guard(q,live,op):
 p=Path(q); l=q.lower()
 if q in PEX:return 'protected_exact'
 if any(q.startswith(x.rstrip('/')) for x in PP):return 'protected_prefix'
 if p.name in PN:return 'protected_basename'
 if any(x in l for x in TOK):return 'protected_pattern'
 if q in live:return 'live_dependency'
 if q in op:return 'active_open_file'
 return ''
def save(path,rows,fields):
 with path.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def preflight():
 d=pd.read_csv(SRC); x=d[d.safety_verdict.eq('DELETE_OK')].sort_values('path'); n=d[~d.safety_verdict.eq('DELETE_OK')]
 live=set(pd.read_csv(LIVE).path.astype(str)); op=opened(); gt=tracked(); assert len(x)==ER and int(x.file_count.sum())==EF and int(x.size_bytes.sum())==EB and len(live)==148
 nr=n.path.astype(str).tolist(); rows=[]; exp=[]; skip=[]; seen=set()
 for r in x.itertuples(index=False):
  q=str(r.path); p=R/q
  if not p.exists(): skip.append({'path':q,'reason':'missing','details':''}); continue
  fs=under(p); ac=len(fs); ab=sum(z.stat().st_size for z in fs); reasons=[]; details=[]; pref=q.rstrip('/')+'/'
  for o in nr:
   if o==q or o.startswith(pref) or q.startswith(o.rstrip('/')+'/'): reasons.append('partial_keep_overlap');details.append(o)
  if q in live or any(z.startswith(pref) for z in live): reasons.append('live_row_overlap')
  if q in op or any(z.startswith(pref) for z in op): reasons.append('open_row_overlap')
  for z in fs:
   rr=z.relative_to(R).as_posix(); g=guard(rr,live,op)
   if g: reasons.append(g);details.append(rr)
  if ac!=int(r.file_count) or ab!=int(r.size_bytes): reasons.append('filesystem_csv_mismatch');details += [f'ac={ac}',f'cc={int(r.file_count)}',f'ab={ab}',f'cb={int(r.size_bytes)}']
  reason='|'.join(sorted(set(reasons))); status='SKIP' if reason else 'DELETE'
  rows.append({'path':q,'path_kind':r.path_kind,'second_pass_type':r.second_pass_type,'csv_file_count':int(r.file_count),'actual_file_count':ac,'csv_size_bytes':int(r.size_bytes),'actual_size_bytes':ab,'last_modified_kst':r.last_modified_kst,'preflight_status':status,'skip_reason':reason,'skip_details':'|'.join(sorted(set(details))[:50])})
  if reason: skip.append({'path':q,'reason':reason,'details':'|'.join(sorted(set(details))[:100])}); continue
  for z in fs:
   rr=z.relative_to(R).as_posix(); assert rr not in seen,rr; seen.add(rr); exp.append({'file_path':rr,'source_row_path':q,'size_bytes':z.stat().st_size,'git_tracked':rr in gt,'deletion_status':'PENDING'})
 save(T,rows,list(rows[0])); save(E,exp,list(exp[0])); save(S,skip,['path','reason','details'])
 out={'mode':'preflight','csv_rows':len(x),'csv_files':int(x.file_count.sum()),'csv_bytes':int(x.size_bytes.sum()),'deletable_rows':sum(r['preflight_status']=='DELETE' for r in rows),'deletable_files':len(exp),'deletable_bytes':sum(r['size_bytes'] for r in exp),'skipped_rows':len(skip),'git_tracked_files':sum(bool(r['git_tracked']) for r in exp)};J.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2));return out

def delete():
 out=preflight(); t=pd.read_csv(T); e=pd.read_csv(E); z=t[t.preflight_status.eq('DELETE')].copy();z['depth']=z.path.str.count('/');z=z.sort_values('depth',ascending=False)
 for r in z.itertuples(index=False):
  p=R/str(r.path)
  if p.is_dir():shutil.rmtree(p)
  else:p.unlink()
  print(f'DELETED|{r.path}|{r.path_kind}|{int(r.actual_file_count)}|{int(r.actual_size_bytes)}')
 assert not any((R/str(r.path)).exists() for r in z.itertuples(index=False));e['deletion_status']='DELETED';e.to_csv(E,index=False)
 out.update({'mode':'delete','deleted_rows':len(z),'deleted_files':len(e),'deleted_bytes':int(e.size_bytes.sum()),'status':'COMPLETE'});J.write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':
 m=argparse.ArgumentParser();m.add_argument('mode',choices=['preflight','delete']);a=m.parse_args();preflight() if a.mode=='preflight' else delete()
