from __future__ import annotations
import json, os, pathlib, shutil, subprocess, sys, time, socket
ROOT=pathlib.Path(r"C:\kingmaker_nulltest_v8")
STAGE=ROOT/"scripts/research/stage23_rework_20260713"
OUT_ROOT=ROOT/"data/_system/analysis/stage3_feature_nulltest_v8_20260715"
BASELINE=ROOT/"data/_system/analysis/stage3_aap_tradecount_factor_v3_20260715/AAP/NOTEBOOK_MAX"
RUNS=[{'feature': 'trend_chop20', 'variant': 'REAL', 'shuffle_seed': 2026071501}, {'feature': 'trend_chop20', 'variant': 'SHUFFLED', 'shuffle_seed': 2026071501}, {'feature': 'atr14_pct', 'variant': 'REAL', 'shuffle_seed': 2026071501}, {'feature': 'atr14_pct', 'variant': 'SHUFFLED', 'shuffle_seed': 2026071501}, {'feature': 'range_pct_rank60', 'variant': 'REAL', 'shuffle_seed': 2026071501}, {'feature': 'range_pct_rank60', 'variant': 'SHUFFLED', 'shuffle_seed': 2026071501}, {'feature': 'rs_peer3_ret20', 'variant': 'REAL', 'shuffle_seed': 2026071501}, {'feature': 'rs_peer3_ret20', 'variant': 'SHUFFLED', 'shuffle_seed': 2026071501}]
PROTECTED={'.env': 'da8173082d40ef3f3f800568b29d1cc7139a1c06fe7979d32ead6cdb5579f1ce', 'data/_system/market_history.csv': '35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38', 'data/_system/market_history_v2.csv': 'b7db98bd5b17b7a95cc852cde6f6b44643ff450ebf6dbb86c6347548e9f4c611'}
DAEMON={'cmdline': '/home/g3000kkw/kingmaker/venv/bin/python /home/g3000kkw/kingmaker/data/_system/ops/live_candidate_slots.py daemon --interval 60', 'pid': 494330, 'snapshot_source': 'VM_snapshot_for_notebook_staging_run', 'starttime_ticks': '36014393', 'state': 'Sl'}
SOURCE_COMMIT="ca1154d"
ENV=os.environ.copy(); ENV.update({"PYTHONPATH":str(STAGE)+r";C:\kingmaker;C:\kingmaker\vendor","KINGMAKER_MARKET_CUTOFF_DATE":"2026-07-10","PYTHONUTF8":"1","PYTHONIOENCODING":"utf-8","PYTHONUNBUFFERED":"1","OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1","NUMEXPR_NUM_THREADS":"1"})
STATUS_PATH=OUT_ROOT/"driver_status.json"
def write_status(payload):
 payload=dict(payload); payload["updated_at"]=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
write_status({"host":socket.gethostname(),"pid":os.getpid(),"state":"starting","runs":RUNS})
results=[]
for idx, run in enumerate(RUNS,1):
 feature=run["feature"]; variant=run["variant"]; seed=str(run["shuffle_seed"]); out_dir=OUT_ROOT/feature/variant
 if out_dir.exists(): shutil.rmtree(out_dir)
 out_dir.parent.mkdir(parents=True, exist_ok=True); log_path=out_dir.parent/(variant+"_run_stdout.log")
 cmd=[sys.executable, str(STAGE/"scripts/research/run_stage3_aap_feature_nulltest_v8_fixed_host.py"), "--extra-feature", feature, "--feature-variant", variant, "--shuffle-seed", seed, "--baseline-dir", str(BASELINE), "--out-dir", str(out_dir), "--seed-base", "2026071401", "--workers", "28", "--host-role", "notebook", "--market-cutoff-date", "2026-07-10", "--protected-snapshot-json", json.dumps(PROTECTED, ensure_ascii=False), "--daemon-snapshot-json", json.dumps(DAEMON, ensure_ascii=False), "--source-git-commit", SOURCE_COMMIT]
 launch={"index":idx,"feature":feature,"variant":variant,"shuffle_seed":int(seed),"host":socket.gethostname(),"python":sys.executable,"cwd":str(STAGE),"cmd":cmd,"log_path":str(log_path),"env_subset":{k:ENV.get(k) for k in ["PYTHONPATH","KINGMAKER_MARKET_CUTOFF_DATE","PYTHONUTF8","PYTHONIOENCODING","PYTHONUNBUFFERED","OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS"]}}
 (out_dir.parent/(variant+"_launch_driver.json")).write_text(json.dumps(launch, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
 write_status({"host":socket.gethostname(),"pid":os.getpid(),"state":"running","current":launch,"completed":results})
 with log_path.open("w", encoding="utf-8", errors="replace") as log:
  started=time.time(); proc=subprocess.run(cmd,cwd=str(STAGE),env=ENV,stdout=log,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace"); elapsed=time.time()-started
 result={"index":idx,"feature":feature,"variant":variant,"returncode":proc.returncode,"elapsed_seconds":elapsed,"out_dir":str(out_dir),"log_path":str(log_path)}
 results.append(result); write_status({"host":socket.gethostname(),"pid":os.getpid(),"state":"completed_one" if proc.returncode==0 else "failed","current":result,"completed":results})
 if proc.returncode!=0: raise SystemExit(proc.returncode)
write_status({"host":socket.gethostname(),"pid":os.getpid(),"state":"finished","completed":results})
