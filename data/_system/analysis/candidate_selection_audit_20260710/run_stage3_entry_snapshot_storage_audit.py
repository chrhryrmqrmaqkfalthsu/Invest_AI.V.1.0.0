from __future__ import annotations
import csv, glob, json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'data/_system/analysis/candidate_selection_audit_20260710'
BASE=OUT/'integrated_gate_candidate_dryrun.csv'

def scan_files(names):
    fields=Counter(); rows=0; files=[]
    for name in sorted({str(x) for x in names}):
        p=Path(name)
        if not p.exists(): continue
        files.append(str(p))
        with p.open(encoding='utf-8',errors='ignore') as f:
            for line in f:
                if not line.strip(): continue
                try:o=json.loads(line)
                except:continue
                if not isinstance(o,dict):continue
                rows+=1; fields.update(o.keys())
    return files,rows,fields

def write_csv(path,rows,cols):
    with open(path,'w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)

def main():
    s3_names=glob.glob(str(ROOT/'exp_batch_stage123_2009_20260616_full/tickers/*/stage3/exit_trades.jsonl'))
    base=pd.read_csv(BASE,usecols=['stage','source_file'],low_memory=False)
    s2_names=[]
    for source in base.loc[base.stage.eq('stage2'),'source_file'].dropna().astype(str).unique():
        s2_names.append(str((ROOT/source).parent/'trades.jsonl'))
    s3files,s3n,s3=scan_files(s3_names)
    s2files,s2n,s2=scan_files(s2_names)
    entry_tokens=('entry_signal','entry_market','entry_news','entry_topic','entry_event','raw_score','components','threshold','ratio')
    schema=[]
    for stage,fields,total in [('stage3_exit_trades',s3,s3n),('stage2_trades',s2,s2n)]:
        for k in sorted(fields):
            schema.append({'dataset':stage,'field':k,'row_count_with_field':fields[k],'total_rows':total,'coverage_pct':fields[k]/total*100 if total else 0,'entry_signal_related':k.startswith('entry_') or any(t in k.lower() for t in entry_tokens)})
    write_csv(OUT/'stage3_entry_snapshot_schema_fields.csv',schema,['dataset','field','row_count_with_field','total_rows','coverage_pct','entry_signal_related'])

    state_specs=[
      ('live_slots_state','data/_system/live_slots_state.json'),('pending_orders','data/_system/pending_orders.json'),('live_slots_events','data/_system/live_slots_events.jsonl'),('live_auto_events','data/_system/live_auto_events.jsonl'),('elite_shadow_state','data/_system/elite_shadow_state.json'),('elite_shadow_trades','data/_system/elite_shadow_trades.jsonl'),('s2_auto_state','data/_system/s2_auto_state.json'),('s2_auto_order_intents','data/_system/s2_auto_order_intents.jsonl')]
    state_rows=[]
    for label,rel in state_specs:
        p=ROOT/rel
        if not p.exists():
            state_rows.append({'source':label,'path':rel,'exists':False,'top_or_union_fields':'','signal_fields_found':'','note':'missing'})
            continue
        objs=[]
        try:
            if p.suffix=='.jsonl':
                for line in p.open(encoding='utf-8',errors='ignore'):
                    if line.strip():
                        try:o=json.loads(line)
                        except:continue
                        if isinstance(o,dict):objs.append(o)
            else:
                o=json.loads(p.read_text(encoding='utf-8'))
                objs=[o] if isinstance(o,dict) else []
        except Exception: objs=[]
        union=set()
        def walk(x):
            if isinstance(x,dict):
                union.update(x.keys())
                for v in x.values():walk(v)
            elif isinstance(x,list):
                for v in x:walk(v)
        for o in objs:walk(o)
        signal=[k for k in sorted(union) if any(t in k.lower() for t in ['score','threshold','ratio','component','market_adjustment','raw_score'])]
        state_rows.append({'source':label,'path':rel,'exists':True,'top_or_union_fields':'|'.join(sorted(union)),'signal_fields_found':'|'.join(signal),'note':''})
    write_csv(OUT/'stage3_entry_snapshot_state_log_fields.csv',state_rows,['source','path','exists','top_or_union_fields','signal_fields_found','note'])

    summary={
      'verdict':'PARTIAL',
      'stage3_exit_trade_files':len(s3files),'stage3_exit_trade_rows':s3n,'stage3_exit_unique_fields':len(s3),
      'stage3_exit_fields':sorted(s3),'stage3_exit_entry_signal_fields':[k for k in sorted(s3) if k.startswith('entry_signal') or k in {'entry_market_adjustment','entry_news_sentiment','entry_topic_features','entry_event_flags'}],
      'stage2_trade_files':len(s2files),'stage2_trade_rows':s2n,'stage2_entry_signal_fields':[k for k in sorted(s2) if k.startswith('entry_signal') or k in {'entry_market_adjustment','entry_news_sentiment','entry_topic_features','entry_event_flags'}],
      'finding':'Live Stage3 path persists score/raw_score/threshold/ratio in candidate state, but components and market_adjustment are dropped; shadow persists score/threshold/ratio/reasons but not components. Stage3 canonical exit_trades currently contains no entry signal fields.',
      'no_code_change':True
    }
    (OUT/'stage3_entry_snapshot_storage_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
