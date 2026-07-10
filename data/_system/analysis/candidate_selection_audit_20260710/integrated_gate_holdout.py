from __future__ import annotations

import json
from collections import defaultdict


def scan_histories_split(sources):
    agg=defaultdict(lambda:{'n':0,'sum_pnl':0.0,'wins':0,'sum_atr_pct':0.0,'atr_n':0,'base_n':0,'base_sum':0.0,'base_wins':0,'holdout_n':0,'holdout_sum':0.0,'holdout_wins':0})
    for stage,path,targets in sources:
        marker='"rulebook_hash": "' if stage=='stage2' else '"final_rulebook_hash": "'
        ticker=path.parent.parent.name
        holdout_label='oos_2025h2' if stage=='stage2' else 'recent_1y'
        with path.open('r',encoding='utf-8',errors='ignore') as handle:
            for line in handle:
                pos=line.find(marker)
                if pos<0: continue
                rule_hash=line[pos+len(marker):pos+len(marker)+64]
                if rule_hash not in targets: continue
                try: row=json.loads(line)
                except Exception: continue
                key=f'{stage}:{ticker}:{rule_hash[:12]}'; pnl=float(row.get('pnl_pct') or 0.0)
                prefix='holdout' if str(row.get('period_label') or '')==holdout_label else 'base'; a=agg[key]
                a['n']+=1; a['sum_pnl']+=pnl; a['wins']+=int(pnl>0)
                a[prefix+'_n']+=1; a[prefix+'_sum']+=pnl; a[prefix+'_wins']+=int(pnl>0)
                ep=float(row.get('entry_price') or 0.0)
                if stage=='stage2': atr=float(row.get('entry_atr') or 0.0)
                else:
                    sl=float(row.get('stop_loss_atr') or 0.0); sp=float(row.get('stop_price_at_entry') or 0.0)
                    atr=(ep-sp)/sl if ep>0 and sp>0 and sl>0 else 0.0
                if ep>0 and atr>0: a['sum_atr_pct']+=atr/ep*100.0; a['atr_n']+=1
    return agg


def attach_histories(rows,agg):
    for row in rows:
        a=agg.get(row['candidate_id'],{}); n=int(a.get('n',0))
        row['history_n']=n; row['history_avg_pnl_pct']=a.get('sum_pnl',0.0)/n if n else float('nan')
        row['history_win_rate_pct']=a.get('wins',0)/n*100.0 if n else float('nan')
        row['history_avg_atr_pct']=a.get('sum_atr_pct',0.0)/a.get('atr_n',1) if a.get('atr_n',0) else float('nan')
        for prefix in ('base','holdout'):
            pn=int(a.get(prefix+'_n',0)); row[prefix+'_n']=pn
            row[prefix+'_avg_pnl_pct']=a.get(prefix+'_sum',0.0)/pn if pn else float('nan')
            row[prefix+'_win_rate_pct']=a.get(prefix+'_wins',0)/pn*100.0 if pn else float('nan')
    return rows
