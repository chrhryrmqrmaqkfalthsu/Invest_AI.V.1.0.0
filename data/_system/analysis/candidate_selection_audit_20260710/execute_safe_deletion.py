from pathlib import Path
import json, shutil
import pandas as pd

ROOT=Path(__file__).resolve().parents[4]
A=ROOT/'data/_system/analysis/candidate_selection_audit_20260710'
C=A/'orphan_file_candidates.csv'
L=A/'live_dependency_tree.csv'
EXP_ROWS,EXP_FILES,EXP_BYTES=45,343,5150150
ALLOWED={'REGENERABLE_CACHE_DIRECTORY','EXPLICIT_BACKUP_COPY','STALE_ATOMIC_TEMP'}
PROTECTED_EXACT={'.env','.env.backup','data/_system/candidate_denylist.json','scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001'}
PROTECTED_NAMES={'rulebooks_all.jsonl','entry_rulebooks.jsonl','final_rulebooks.jsonl','survivors.jsonl','profile_catalog.jsonl','validation_results.jsonl','exit_trades.jsonl'}

def files(p): return [p] if p.is_file() else sorted(x for x in p.rglob('*') if x.is_file())

def guard(s):
 p=Path(s); low=s.lower(); cache=p.suffix in {'.pyc','.pyo'} and '__pycache__' in p.parts
 assert s not in PROTECTED_EXACT
 assert not s.startswith(('data/_system/analysis/','exp_batch_stage123_2009_20260616_full/'))
 assert p.name not in PROTECTED_NAMES
 assert 'oos_reproduce_frozen' not in low and 'frozen_oos' not in low
 if not cache: assert not any(x in p.name.lower() for x in ('rulebook','survivor','profile_catalog'))

def main():
 d=pd.read_csv(C); s=d[d.risk.eq('SAFE_TO_DELETE')].sort_values('path'); live=set(pd.read_csv(L).path.astype(str))
 assert len(s)==EXP_ROWS and int(s.file_count.sum())==EXP_FILES and int(s.size_bytes.sum())==EXP_BYTES
 assert set(s.type)==ALLOWED and not d[d.risk.isin(['REVIEW','KEEP'])].path.isin(s.path).any()
 expanded=[]
 for r in s.itertuples(index=False):
  t=ROOT/str(r.path); assert t.exists(); fs=files(t)
  assert len(fs)==int(r.file_count) and sum(x.stat().st_size for x in fs)==int(r.size_bytes)
  guard(str(r.path))
  for x in fs:
   q=x.relative_to(ROOT).as_posix(); guard(q); assert q not in live; expanded.append(q)
  pref=str(r.path).rstrip('/')+'/'; assert not any(x==str(r.path) or x.startswith(pref) for x in live)
 assert len(expanded)==len(set(expanded))==EXP_FILES
 print('DELETE_VALIDATION_OK',json.dumps({'safe_rows':len(s),'expanded_files':len(expanded),'bytes':EXP_BYTES,'live_overlap':0,'protected_overlap':0},sort_keys=True))
 for r in s.itertuples(index=False):
  t=ROOT/str(r.path); shutil.rmtree(t) if t.is_dir() else t.unlink()
  print(f'DELETED|{r.path}|{r.path_kind}|{int(r.file_count)}|{int(r.size_bytes)}')
 assert not any((ROOT/str(r.path)).exists() for r in s.itertuples(index=False))
 print(json.dumps({'status':'DELETION_COMPLETE','deleted_rows':len(s),'deleted_files':EXP_FILES,'deleted_bytes':EXP_BYTES,'remaining_targets':0},sort_keys=True))
 return 0

if __name__=='__main__': raise SystemExit(main())
