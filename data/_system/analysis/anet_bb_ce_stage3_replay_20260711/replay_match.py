from __future__ import annotations
from typing import Any
from replay_common import d,f,vals,csvtxt

def choose(o:dict[str,Any],rr:list[dict[str,Any]],used:set[int])->int|None:
    c=[i for i,x in enumerate(rr) if i not in used and d(x.get('entry_date'))==d(o.get('entry_date'))]
    if not c:return None
    op=f(o.get('entry_price'))
    if op is None:return c[0]
    return min(c,key=lambda i:abs((f(rr[i].get('entry_price')) or 0)-op))

def build(ticker:str,prefix:str,rule_hash:str,p:dict[str,Any],orig:list[dict[str,Any]],rr:list[dict[str,Any]]):
    used:set[int]=set();rows=[];exact=0;entry=0
    for n,o in enumerate(orig,1):
        j=choose(o,rr,used);x=rr[j] if j is not None else None
        if j is not None:used.add(j);entry+=1
        op=f(o.get('entry_price'));rp=f(x.get('entry_price')) if x else None
        on=f(o.get('pnl_pct'));rn=f(x.get('pnl_pct')) if x else None
        ep=abs(rp-op) if op is not None and rp is not None else None
        pd=abs(rn-on) if on is not None and rn is not None else None
        em=bool(x is not None and d(o.get('exit_date'))==d(x.get('exit_date')))
        ex=bool(x is not None and ep is not None and pd is not None and ep<=1e-8 and pd<=1e-8 and em);exact+=int(ex)
        row={'ticker':ticker,'rule_id':f'stage3:{ticker}:{prefix}','rulebook_hash':rule_hash,'period_label':p['label'],
        'period_role':p['role'],'period_start':p.get('start') or 'DATA_START','period_end':p.get('end'),
        'row_origin':'original_snapshot','original_trade_no':n,'rerun_trade_no':j+1 if j is not None else None,
        'match_status':'exact' if ex else ('entry_date_match_but_values_differ' if x is not None else 'missing_in_rerun'),
        'entry_price_abs_diff':ep,'pnl_abs_diff':pd,'exit_date_match':em}
        row.update(vals('original',o));row.update(vals('rerun',x));rows.append(row)
    for j,x in enumerate(rr):
        if j in used:continue
        row={'ticker':ticker,'rule_id':f'stage3:{ticker}:{prefix}','rulebook_hash':rule_hash,'period_label':p['label'],
        'period_role':p['role'],'period_start':p.get('start') or 'DATA_START','period_end':p.get('end'),
        'row_origin':'rerun_only','original_trade_no':None,'rerun_trade_no':j+1,'match_status':'not_in_original',
        'entry_price_abs_diff':None,'pnl_abs_diff':None,'exit_date_match':False}
        row.update(vals('original',None));row.update(vals('rerun',x));rows.append(row)
    return csvtxt(rows),{'entry_date_matches':entry,'exact_matches':exact,'rerun_used':len(used)}
