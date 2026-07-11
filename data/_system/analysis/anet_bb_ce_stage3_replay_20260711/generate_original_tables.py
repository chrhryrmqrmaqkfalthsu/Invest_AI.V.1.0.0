import json
from pathlib import Path
from replay_common import BASE,SPECS,csvtxt,f,many,one,period_map,stats
OUT=Path(__file__).resolve().parent

def avg(xs):return sum(xs)/len(xs) if xs else None
ce_rows=[];rule_rows=[];source_rows=[]
commits={'ANET':'8390e6b96d4b391c392cfb4c00cb6163cf4150a0','BB':'54f1b999a9ded22792d2a28e81658083690268e8','CE':'378554410a2ff3ddc9697fbceb7dfb320ab7e7ff'}
for t,pfx in SPECS.items():
 sd=BASE/t/'stage3';rows=many(sd/'rl_replay_trades.jsonl',pfx)
 for period in ['stress_pre_2022h1','train_1','train_2','recent_1y']:
  pr=[x for x in rows if x.get('period_label')==period]
  for flag,name in [(True,'ce_type'),(False,'non_ce')]:
   g=[x for x in pr if stats(x)['ce_like_flag']==flag];pn=[f(x.get('pnl_pct')) for x in g if f(x.get('pnl_pct')) is not None];cs=[stats(x)['top2_concentration'] for x in g if stats(x)['top2_concentration'] is not None];rs=[stats(x)['ratio'] for x in g if stats(x)['ratio'] is not None]
   ce_rows.append({'ticker':t,'period':period,'group':name,'trade_count':len(g),'avg_pnl_pct':avg(pn),'win_rate_pct':100*sum(x>0 for x in pn)/len(pn) if pn else None,'avg_top2_concentration':avg(cs),'avg_ratio':avg(rs)})
 ce=[x for x in rows if stats(x)['ce_like_flag']];non=[x for x in rows if not stats(x)['ce_like_flag']]
 def group(g):
  pn=[f(x.get('pnl_pct')) for x in g if f(x.get('pnl_pct')) is not None]
  return len(g),avg(pn),100*sum(x>0 for x in pn)/len(pn) if pn else None
 cn,ca,cw=group(ce);nn,na,nw=group(non);conc=[stats(x)['top2_concentration'] for x in rows];rat=[stats(x)['ratio'] for x in rows];pos=[stats(x)['positive_component_count'] for x in rows]
 rule_rows.append({'ticker':t,'rule_id':f'stage3:{t}:{pfx}','trade_count':len(rows),'component_available_count':sum(bool(x.get('entry_signal_components')) for x in rows),'ce_type_count':cn,'ce_type_ratio_pct':100*cn/len(rows),'ce_avg_pnl_pct':ca,'non_ce_avg_pnl_pct':na,'ce_minus_non_ce_avg_pnl_pct':ca-na,'ce_win_rate_pct':cw,'non_ce_win_rate_pct':nw,'avg_top2_concentration':avg(conc),'avg_score_threshold_ratio':avg(rat),'avg_positive_component_count':avg(pos)})
 v=one(sd/'validation_results.jsonl',pfx);m=json.loads((sd/'manifest.json').read_text())
 for label,p in period_map(v).items():
  source_rows.append({'ticker':t,'rule_id':f'stage3:{t}:{pfx}','period_label':label,'period_role':p['role'],'start':p.get('start') or 'DATA_START','end':p.get('end'),'manifest_updated_at':m['updated_at'],'original_code_cutoff_commit':commits[t],'rulebook_source':str(sd/'final_rulebooks.jsonl'),'original_trade_source':str(sd/'exit_trades.jsonl'),'original_component_source':str(sd/'rl_replay_trades.jsonl'),'rerun_ohlcv_source':'yfinance via load_ohlcv; manifest date as exclusive end','non_ohlcv_frozen_snapshot':'unavailable'})
(OUT/'ce_type_performance.csv').write_text(csvtxt(ce_rows),encoding='utf-8')
(OUT/'rule_comparison.csv').write_text(csvtxt(rule_rows),encoding='utf-8')
(OUT/'periods_and_sources.csv').write_text(csvtxt(source_rows),encoding='utf-8')
print(json.dumps({'ce_rows':len(ce_rows),'rule_rows':len(rule_rows),'source_rows':len(source_rows)},ensure_ascii=False))
