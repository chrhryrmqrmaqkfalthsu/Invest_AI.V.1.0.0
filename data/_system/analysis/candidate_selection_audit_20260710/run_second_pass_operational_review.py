from __future__ import annotations

import json, re, subprocess, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[4]
AUDIT=ROOT/'data/_system/analysis/candidate_selection_audit_20260710'
SRC=AUDIT/'orphan_file_candidates.csv'
OUT=AUDIT/'operational_unused_second_pass.csv'
SUMMARY=AUDIT/'operational_unused_second_pass_summary.json'
NOW=datetime.now(timezone.utc)
DELETED={'data/_system/pipeline','data/_system/condition_db_sell_omen_clean','data/_system/condition_db_sell_omen_lr8d85','data/_system/logs'}
CURRENT='exp_batch_stage123_2009_20260616_full'
ONEOFF=('smoke','dryrun','verify','audit','proto','failure','diagnostic','baseline','sweep','gatecheck','reverse','multiyear','exitswap','exitga','sequence','classifier','noleak','range_predictor','payoff','lockbox','stage25','event_decay','retry')
ROTATED=('legacy','rollback','smoke','launcher','qualify','shard','stage2_','stage3_','honest_','baseline','verify','audit','replay','detached','wrapper','resume','semiauto','next_open')

def text(cmd):
 try:return subprocess.run(cmd,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=30).stdout
 except:return ''

def open_files():
 pref=str(ROOT.resolve())+'/'
 out=set()
 for line in text(['lsof']).splitlines():
  for tok in line.split()[8:]:
   if tok.startswith(pref): out.add(tok[len(pref):])
 return out

def graph():
 sys.path.insert(0,str(AUDIT.resolve()))
 import run_orphan_file_audit_safe as safe
 a=safe.audit; src=a.source_files(); edges,rev,errs=a.build_import_graph(src); alive,lits=a.active_closure(src,edges)
 alive={p.relative_to(ROOT).as_posix() for p in alive}
 refs={}
 for target,sources in lits.items():
  try:r=target.relative_to(ROOT).as_posix()
  except:continue
  refs[r]={s.relative_to(ROOT).as_posix() for s in sources if s in alive}
 return alive,refs,len(errs)

def live_ref(path,alive,lits):
 refs=set()
 if path in alive: refs.add(path)
 for target,sources in lits.items():
  if target==path or target.startswith(path.rstrip('/')+'/') or path.startswith(target.rstrip('/')+'/'): refs.update(sources)
 return bool(refs),sorted(refs)

def exp_root(path):
 x=path.split('/',1)[0]
 return x if x.startswith('exp_') else ''

def exp_complete(root):
 p=ROOT/root
 if not p.is_dir(): return False,'root missing'
 if (p/'summary.json').exists(): return True,'summary.json exists'
 if (p/'batch_summary.json').exists(): return True,'batch_summary.json exists'
 if (p/'run_status.json').exists():
  try:
   raw=json.dumps(json.loads((p/'run_status.json').read_text()),ensure_ascii=False).lower()
   if 'complete' in raw or 'completed' in raw: return True,'run_status completed'
  except: pass
 if any(t in root.lower() for t in ONEOFF): return True,'one-off experiment name'
 return False,'completion marker absent'

def stats(path,row):
 if path.is_dir():
  fs=[x for x in path.rglob('*') if x.is_file()]; size=sum(x.stat().st_size for x in fs); count=len(fs); ts=max((x.stat().st_mtime for x in fs),default=path.stat().st_mtime)
 else:size=path.stat().st_size; count=1; ts=path.stat().st_mtime
 dt=datetime.fromtimestamp(ts,timezone.utc)
 return size,count,pd.Timestamp(dt).tz_convert('Asia/Seoul').isoformat(),max(0,(NOW-dt).total_seconds()/86400)

def classify(row,alive,lits,opened,ps):
 ptxt=str(row.path); p=ROOT/ptxt
 if ptxt in DELETED or not p.exists(): return None
 reachable,refs=live_ref(ptxt,alive,lits); opened_here=ptxt in opened or any(x.startswith(ptxt.rstrip('/')+'/') for x in opened)
 reachable=reachable or opened_here
 size,count,mtime,age=stats(p,row); root=exp_root(ptxt); typ=str(row.type); name=p.name.lower()
 cat,verdict,why='UNCLASSIFIED_REVIEW','PARTIAL','운영 비도달이나 수동 사용·복구·연구 가치 불명확'
 if ptxt.startswith('backup/') or ptxt=='backup': cat,verdict,why='BACKUP_SNAPSHOT','KEEP','이번 지시에서 backup 전체 절대 보존'
 elif ptxt.startswith('data/backups/') or ptxt=='data/backups' or 'backup' in name: cat,verdict,why='BACKUP_SNAPSHOT','KEEP','백업 스냅샷 별도 취급'
 elif ptxt.startswith('tests/'): cat,verdict,why='TEST_CODE','KEEP','테스트 코드는 별도 취급'
 elif reachable: cat,verdict,why='ACTIVE_RUNTIME_DEPENDENCY','KEEP','활성 그래프·구체 경로 리터럴 또는 열린 핸들 도달'
 elif typ=='OPERATIONAL_STATE_BACKUP': cat,verdict,why='OPERATIONAL_STATE_BACKUP','PARTIAL','장애 복구 가치 개별 확인 필요'
 elif typ=='LOCK_OR_PID_ARTIFACT' or p.suffix in {'.pid','.lock'}:
  running=False
  if p.suffix=='.pid':
   try: running=Path('/proc/'+p.read_text().strip()).exists()
   except: pass
  if not running and not opened_here and age>=1: cat,verdict,why='STALE_LOCK_OR_PID','DELETE_OK','실행 PID·열린 핸들·활성 참조 없음'
  else: cat,verdict,why='LOCK_OR_PID','PARTIAL','프로세스 조정 파일 가능성'
 elif typ=='LOG_ARTIFACT' or p.suffix in {'.log','.out'}:
  if opened_here: cat,verdict,why='ACTIVE_LOG','KEEP','현재 프로세스가 열린 핸들 보유'
  elif CURRENT in ptxt: cat,verdict,why='PRODUCTION_BATCH_HISTORY_LOG','PARTIAL','현재 생산 배치 재개·장애 이력 가능'
  elif re.search(r'20\d{6}',name) or any(t in name for t in ROTATED) or root: cat,verdict,why='ROTATED_OR_FINISHED_LOG','DELETE_OK','열린 핸들·활성 참조 없는 회전/실험 로그'
  else: cat,verdict,why='UNREFERENCED_LOG','PARTIAL','회전 잔여 여부 불명확'
 elif root and root!=CURRENT:
  done,reason=exp_complete(root); running=root in ps; cat='TERMINATED_EXPERIMENT_DATA'
  if done and not running: verdict,why='DELETE_OK',f'운영 비도달·프로세스 없음·{reason}; 보호 룰풀은 KEEP 분리'
  else: verdict,why='PARTIAL',f'운영 비도달이나 {reason}; 연구 재현 여부 확인 필요'
 elif typ in {'ONE_OFF_OR_RESEARCH_SCRIPT','DEAD_OR_MANUAL_SCRIPT','UNREACHED_ENGINE_MODULE'} or p.suffix=='.py': cat,verdict,why='ONE_OFF_OR_LEGACY_CODE','PARTIAL','수동 실행·복구 사용 가능성'
 elif typ=='DOCUMENTATION_OR_NOTE' or p.suffix=='.md' or ptxt.startswith('docs/'): cat,verdict,why='UNREFERENCED_DOCUMENT_NOTE','PARTIAL','설계·장애 이력 가치 불명확'
 elif typ=='EXACT_DUPLICATE_REVIEW': cat,verdict,why='EXACT_DUPLICATE','PARTIAL','경로 의미·수동 소비 여부 확인 필요'
 elif ptxt.startswith(('data/_system/bulk_diagnostic','data/_system/clean_spread_trainonly','data/_system/swing_trainonly','data/_system/true_wf_logs','data/_system/condition_db')): cat,verdict,why='TERMINATED_EXPERIMENT_DATA','DELETE_OK','활성 참조 0건인 구 진단/train-only/condition DB 산출물'
 elif ptxt.startswith(('data/_system/code_backups','data/_system/ga_dump_backup','data/_system/backups')): cat,verdict,why='BACKUP_SNAPSHOT','KEEP','백업 스냅샷 별도 취급'
 elif ptxt.startswith('data/_system/liquidation_snapshots'): cat,verdict,why='HISTORICAL_SNAPSHOT','PARTIAL','청산 검증·감사 재현 가치 가능'
 elif ptxt.startswith('data/_system/'): cat,verdict,why='UNREFERENCED_SYSTEM_DATA','PARTIAL','동적 파일명 접근 가능성'
 return {'path':ptxt,'path_kind':row.path_kind,'original_type':typ,'second_pass_type':cat,'operational_reachable':'O' if reachable else 'X','open_file_handle':'O' if opened_here else 'X','active_reference_count':len(refs),'active_reference_trace':'|'.join(refs[:10]),'size_bytes':size,'size_mib':round(size/1048576,6),'file_count':count,'last_modified_kst':mtime,'days_since_modified':round(age,3),'safety_verdict':verdict,'evidence':why}

def main():
 src=pd.read_csv(SRC); review=src[src.risk.eq('REVIEW')]; alive,lits,errors=graph(); opened=open_files(); ps=text(['ps','-eo','pid,ppid,etime,cmd'])
 rows=[x for r in review.itertuples(index=False) if (x:=classify(r,alive,lits,opened,ps))]
 out=pd.DataFrame(rows).sort_values(['safety_verdict','second_pass_type','size_bytes','path'],ascending=[True,True,False,True]); out.to_csv(OUT,index=False)
 vs=out.groupby('safety_verdict').agg(rows=('path','size'),represented_files=('file_count','sum'),bytes=('size_bytes','sum')).reset_index()
 ts=out.groupby(['safety_verdict','second_pass_type']).agg(rows=('path','size'),represented_files=('file_count','sum'),bytes=('size_bytes','sum')).reset_index()
 summary={'created_at':NOW.isoformat(),'source_review_rows':len(review),'deleted_step1':sorted(DELETED),'remaining_rows':len(out),'active_dependency_files':len(alive),'literal_reference_targets':len(lits),'parse_errors':errors,'open_project_files':sorted(opened),'verdict_summary':vs.to_dict('records'),'type_summary':ts.to_dict('records'),'largest_delete_ok':out[out.safety_verdict.eq('DELETE_OK')].nlargest(50,'size_bytes').to_dict('records')}
 SUMMARY.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'remaining_rows':len(out),'active_files':len(alive),'parse_errors':errors,'verdicts':dict(Counter(out.safety_verdict)),'delete_ok_bytes':int(out.loc[out.safety_verdict.eq('DELETE_OK'),'size_bytes'].sum())},ensure_ascii=False,indent=2))
 return 0
if __name__=='__main__': raise SystemExit(main())
