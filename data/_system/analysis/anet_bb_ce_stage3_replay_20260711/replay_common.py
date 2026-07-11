from __future__ import annotations
import csv,io,json,math
from pathlib import Path
from typing import Any
PROJECT=Path(__file__).resolve().parents[4]
BASE=PROJECT/'exp_batch_stage123_2009_20260616_full'/'tickers'
SPECS={'ANET':'fe220620802b','BB':'f1bdfe7f8ad9','CE':'998b0b638c66'}
TOP2_MIN=.70
RATIO_MAX=1.15

def one(path:Path,prefix:str)->dict[str,Any]:
    for line in path.open(encoding='utf-8'):
        if prefix in line:return json.loads(line)
    raise RuntimeError(f'not found {prefix} in {path}')

def many(path:Path,prefix:str)->list[dict[str,Any]]:
    return [json.loads(x) for x in path.open(encoding='utf-8') if prefix in x]

def f(x:Any)->float|None:
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except Exception:return None

def d(x:Any)->str:return str(x)[:10] if x is not None else ''

def stats(t:dict[str,Any])->dict[str,Any]:
    c={str(k):f(v) for k,v in (t.get('entry_signal_components') or {}).items() if f(v) is not None}
    p=sorted([(k,v) for k,v in c.items() if v>0],key=lambda z:(-z[1],z[0]))
    ps=sum(v for _,v in p);t2=sum(v for _,v in p[:2])
    s=f(t.get('entry_signal_score'));raw=f(t.get('entry_signal_raw_score'));th=f(t.get('entry_signal_threshold'))
    ma=f(t.get('entry_market_adjustment'))
    if ma is None:ma=f(t.get('entry_signal_market_adjustment'))
    ratio=s/th if s is not None and th not in (None,0) else None
    conc=t2/ps if ps>0 else None
    return {'score':s,'raw_score':raw,'threshold':th,'market_adjustment':ma,'ratio':ratio,
      'positive_component_count':len(p),'top1_component':p[0][0] if p else '',
      'top1_contribution':p[0][1] if p else None,'top2_component':p[1][0] if len(p)>1 else '',
      'top2_contribution':p[1][1] if len(p)>1 else None,'positive_sum':ps,'top2_sum':t2,
      'top2_concentration':conc,'ce_like_flag':bool(conc is not None and ratio is not None and conc>=TOP2_MIN and ratio<=RATIO_MAX),
      'components_json':json.dumps(c,ensure_ascii=False,sort_keys=True,separators=(',',':'))}

def vals(prefix:str,t:dict[str,Any]|None)->dict[str,Any]:
    if t is None:
        num=['entry_price','exit_price','pnl_pct','holding_days','score','raw_score','threshold','market_adjustment','ratio','positive_component_count','top1_contribution','top2_contribution','positive_sum','top2_sum','top2_concentration','ce_like_flag']
        txt=['entry_signal_date','entry_date','exit_date','exit_reason','top1_component','top2_component','components_json']
        return {**{f'{prefix}_{k}':None for k in num},**{f'{prefix}_{k}':'' for k in txt}}
    z={f'{prefix}_entry_signal_date':d(t.get('entry_signal_date')),f'{prefix}_entry_date':d(t.get('entry_date')),
       f'{prefix}_entry_price':f(t.get('entry_price')),f'{prefix}_exit_date':d(t.get('exit_date')),
       f'{prefix}_exit_price':f(t.get('exit_price')),f'{prefix}_pnl_pct':f(t.get('pnl_pct')),
       f'{prefix}_exit_reason':str(t.get('exit_reason') or ''),f'{prefix}_holding_days':t.get('holding_days')}
    z.update({f'{prefix}_{k}':v for k,v in stats(t).items()})
    return z

def csvtxt(rows:list[dict[str,Any]])->str:
    if not rows:return ''
    s=io.StringIO(newline='');w=csv.DictWriter(s,fieldnames=list(rows[0]),lineterminator='\n')
    w.writeheader();w.writerows(rows);return s.getvalue()

def period_map(v:dict[str,Any])->dict[str,dict[str,Any]]:
    out={};x=dict(v['exit_check_period']);x['role']='stress_exit_check';out[x['label']]=x
    for q in v['pure_oos_validation_periods']:
        x=dict(q);x['role']='pure_oos';out[x['label']]=x
    return out
