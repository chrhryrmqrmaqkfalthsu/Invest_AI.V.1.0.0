#!/usr/bin/env python3
import csv,json,math
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean,median

MARKER='PROVISIONAL_PRE_BATCH'
ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/'data/_system/central/stage2_b/entity_turnover_backtest'
OUT=ROOT/'data/_system/central/stage2_b/entity_turnover_stress_diagnosis'
PERIODS=['stress','mid','oos']; CONF='confidence_adjusted'; TURN='turnover_score_mt5'; VARS=[CONF,TURN]
CAP=100000*0.25*0.98

def js(p): return json.load(open(p,encoding='utf-8'))
def f(x,d=0.0):
    try:
        y=float(x); return y if math.isfinite(y) else d
    except Exception: return d
def dt(s): return datetime.strptime(str(s)[:10],'%Y-%m-%d')
def wr(path,rows,fields):
    OUT.mkdir(parents=True,exist_ok=True)
    with open(path,'w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k:r.get(k,'') for k in fields})

def load_comp():
    out={}
    with open(SRC/'comparison.csv',encoding='utf-8') as h:
        for r in csv.DictReader(h):
            if r['variant'] in VARS: out[(r['period'],r['variant'])]=r
    return out

def entity_meta():
    t=js(SRC/'turnover_tag_cache.json')['entities']
    turn={}; conf={}
    for eid,r in t.items():
        hold=f(r.get('avg_holding_days')); turn[eid]=f(r.get('avg_realized_pnl_pct'))/hold if hold>0 else None
    for p in PERIODS:
        sc=js(SRC/f'signal_cache_{p}.json')
        for snaps in sc.values():
            for s in snaps:
                eid=s.get('entity_id')
                if eid and eid not in conf: conf[eid]=f(s.get('confidence'))
    return turn,conf

def deciles(vals):
    good=sorted([(k,v) for k,v in vals.items() if v is not None],key=lambda x:(x[1],x[0])); n=len(good)
    out={k:None for k in vals}
    for i,(k,_) in enumerate(good): out[k]=min(10,int(i*10/n)+1) if n else None
    return out

def load_positions(turn,conf):
    tdec=deciles(turn); cdec=deciles({k:conf.get(k) for k in turn})
    rows=[]
    summ=js(SRC/'summary.json')
    pend={r['period']:r['end'] for r in summ['rows'] if r['variant']==CONF}
    for p in PERIODS:
      for v in VARS:
        res=js(SRC/f'result_{p}_{v}.json')
        sell={tr.get('position_id'):str(tr.get('date'))[:10] for tr in res.get('trades',[]) if tr.get('side')=='sell'}
        led=js(SRC/'ledgers'/f'{p}_{v}'/'ledger_positions.json').get('records',{})
        for pid,pos in led.items():
            eid=pos.get('entity_id',''); ed=str(pos.get('entry_date',''))[:10]
            xd=sell.get(pos.get('position_id') or pid,'') or (str(pos.get('last_updated_at',''))[:10] if pos.get('status')=='closed' else pend.get(p,ed))
            hold=max((dt(xd)-dt(ed)).days,0) if ed and xd else 0
            sh=f(pos.get('opened_shares')); px=f(pos.get('avg_entry_price')); notional=sh*px; pnl=f(pos.get('realized_pnl'))
            ret=pnl/notional*100 if notional>0 else 0.0
            rb=pos.get('rulebook_snapshot') or {}
            rows.append(dict(marker=MARKER,period=p,variant=v,position_id=pos.get('position_id') or pid,entity_id=eid,ticker=pos.get('ticker',''),sector=rb.get('sector_name',''),entry_date=ed,exit_date=xd,status=pos.get('status',''),opened_notional=notional,realized_pnl=pnl,trade_return_pct=ret,holding_days=hold,turnover_score=turn.get(eid),confidence=conf.get(eid),turnover_decile=tdec.get(eid),confidence_decile=cdec.get(eid)))
    return rows

def contribution(pos):
    g=defaultdict(list)
    for r in pos: g[(r['period'],r['variant'],r['entity_id'])].append(r)
    out=[]
    for (p,v,e),rs in g.items():
        rets=[r['trade_return_pct'] for r in rs]; pnl=sum(r['realized_pnl'] for r in rs); holds=[r['holding_days'] for r in rs]
        ords=sorted(rs,key=lambda r:(r['entry_date'],r['position_id'])); gaps=[]; loss_re=0
        for a,b in zip(ords,ords[1:]):
            gaps.append((dt(b['entry_date'])-dt(a['entry_date'])).days)
            if a['realized_pnl']<0: loss_re+=1
        out.append(dict(marker=MARKER,period=p,variant=v,entity_id=e,ticker=rs[0]['ticker'],sector=rs[0]['sector'],position_count=len(rs),realized_pnl=pnl,realized_pnl_pct_sum=sum(rets),avg_trade_pnl_pct=mean(rets),win_rate=sum(1 for r in rs if r['realized_pnl']>0)/len(rs),expectancy_pct=mean(rets),avg_holding_days=mean(holds),median_holding_days=median(holds),max_loss_trade_pct=min(rets),max_win_trade_pct=max(rets),reentry_count=max(len(rs)-1,0),avg_reentry_gap_days=mean(gaps) if gaps else '',loss_then_reentry_count=loss_re,turnover_score=rs[0]['turnover_score'],confidence=rs[0]['confidence']))
    return sorted(out,key=lambda r:(r['period']!='stress',r['variant']!=TURN,r['realized_pnl']))

def hold_dist(pos):
    out=[]
    for p in PERIODS:
      for v in VARS:
        rs=[r for r in pos if r['period']==p and r['variant']==v]; n=len(rs)
        ge=defaultdict(list); gt=defaultdict(list)
        for r in rs: ge[r['entity_id']].append(r); gt[r['ticker']].append(r)
        ent_re=tic_re=loss_re=0; gaps=[]
        for xs in ge.values():
            xs=sorted(xs,key=lambda r:(r['entry_date'],r['position_id'])); ent_re+=max(len(xs)-1,0)
            for a,b in zip(xs,xs[1:]):
                gaps.append((dt(b['entry_date'])-dt(a['entry_date'])).days)
                if a['realized_pnl']<0: loss_re+=1
        for xs in gt.values(): tic_re+=max(len(xs)-1,0)
        out.append(dict(marker=MARKER,period=p,variant=v,position_count=n,holding_1d_count=sum(r['holding_days']<=1 for r in rs),holding_2_3d_count=sum(2<=r['holding_days']<=3 for r in rs),holding_4_7d_count=sum(4<=r['holding_days']<=7 for r in rs),holding_8d_plus_count=sum(r['holding_days']>=8 for r in rs),holding_1d_ratio=sum(r['holding_days']<=1 for r in rs)/n if n else 0,holding_2_3d_ratio=sum(2<=r['holding_days']<=3 for r in rs)/n if n else 0,holding_4_7d_ratio=sum(4<=r['holding_days']<=7 for r in rs)/n if n else 0,holding_8d_plus_ratio=sum(r['holding_days']>=8 for r in rs)/n if n else 0,avg_holding_days=mean([r['holding_days'] for r in rs]) if rs else 0,median_holding_days=median([r['holding_days'] for r in rs]) if rs else 0,same_entity_reentry_count=ent_re,same_ticker_reentry_count=tic_re,avg_reentry_gap_days=mean(gaps) if gaps else '',loss_then_reentry_count=loss_re,loss_then_reentry_ratio=loss_re/ent_re if ent_re else 0))
    return out

def overlap(pos):
    out=[]
    for p in PERIODS:
        a=[r for r in pos if r['period']==p and r['variant']==CONF]; b=[r for r in pos if r['period']==p and r['variant']==TURN]
        ae={r['entity_id'] for r in a}; be={r['entity_id'] for r in b}; at={r['ticker'] for r in a}; bt={r['ticker'] for r in b}
        def top(rs,n):
            c=Counter(r['entity_id'] for r in rs); pnl=defaultdict(float)
            for r in rs: pnl[r['entity_id']]+=r['realized_pnl']
            return set(sorted(c,key=lambda e:(c[e],pnl[e],e),reverse=True)[:n])
        out.append(dict(marker=MARKER,period=p,variant_a=CONF,variant_b=TURN,unique_entities_a=len(ae),unique_entities_b=len(be),intersection=len(ae&be),union=len(ae|be),jaccard=len(ae&be)/len(ae|be) if ae|be else 0,top10_overlap=len(top(a,10)&top(b,10)),top20_overlap=len(top(a,20)&top(b,20)),top50_overlap=len(top(a,50)&top(b,50)),unique_tickers_a=len(at),unique_tickers_b=len(bt),ticker_intersection=len(at&bt),ticker_union=len(at|bt),ticker_jaccard=len(at&bt)/len(at|bt) if at|bt else 0))
    return out

def equity_returns(res):
    out=[]; prev=None
    for p in res.get('equity_curve',[]):
        eq=f(p.get('equity')); ret=0 if prev is None or prev<=0 else (eq/prev-1)*100
        out.append(dict(date=str(p.get('date'))[:10],equity=eq,daily_return_pct=ret,open_position_count=f(p.get('open_position_count')))); prev=eq
    return out

def mdd(eqs):
    peak=None; m=0
    for x in eqs:
        peak=x if peak is None or x>peak else peak
        if peak: m=min(m,(x/peak-1)*100)
    return m

def cap_impact(pos):
    out=[]
    for p in PERIODS:
      for v in VARS:
        res=js(SRC/f'result_{p}_{v}.json'); ds=equity_returns(res); dates=[d['date'] for d in ds]
        rs=[r for r in pos if r['period']==p and r['variant']==v]
        capdays=set()
        for d in dates:
            dd=dt(d); exp=defaultdict(float)
            for r in rs:
                if r['entry_date'] and r['exit_date'] and dt(r['entry_date'])<=dd<=dt(r['exit_date']): exp[r['ticker']]+=r['opened_notional']
            if any(x>=CAP for x in exp.values()): capdays.add(d)
        byexit=defaultdict(list)
        for r in rs: byexit[r['exit_date']].append(r)
        for name,sel in [('cap_pressure_day_proxy',lambda d:d in capdays),('non_cap_pressure_day_proxy',lambda d:d not in capdays)]:
            xs=[d for d in ds if sel(d['date'])]; trs=[r for d in xs for r in byexit.get(d['date'],[])]
            rets=[r['trade_return_pct'] for r in trs]
            diag=res.get('diagnostics',{})
            out.append(dict(marker=MARKER,period=p,variant=v,bucket=name,days=len(xs),day_return_sum=sum(d['daily_return_pct'] for d in xs),day_avg_return=mean([d['daily_return_pct'] for d in xs]) if xs else 0,trade_count=len(trs),trade_expectancy_pct=mean(rets) if rets else 0,trade_win_rate=sum(r['realized_pnl']>0 for r in trs)/len(trs) if trs else 0,actual_cap_hit_events_total=diag.get('ticker_cap_hit_events',''),actual_cap_hit_tickers_total=diag.get('ticker_cap_hit_tickers',''),blocked_candidate_count='not_persisted',blocked_candidate_forward_return_avg='not_persisted'))
    return out

def subperiod(pos):
    labels={'2022-01':'downtrend_high_vol','2022-02':'range_high_vol','2022-03':'rebound_high_vol','2022-04':'downtrend','2022-05':'range_volatile','2022-06':'risk_off_selloff'}; out=[]
    for v in VARS:
        res=js(SRC/f'result_stress_{v}.json'); ds=equity_returns(res); rs=[r for r in pos if r['period']=='stress' and r['variant']==v]
        for mo in labels:
            xs=[d for d in ds if d['date'].startswith(mo)]; trs=[r for r in rs if r['exit_date'].startswith(mo)]
            if not xs: continue
            eqs=[x['equity'] for x in xs]; ret=(eqs[-1]/eqs[0]-1)*100 if eqs[0]>0 else 0; md=mdd(eqs); rets=[r['trade_return_pct'] for r in trs]
            out.append(dict(marker=MARKER,period='stress',variant=v,subperiod=mo,regime_label=labels[mo],start=xs[0]['date'],end=xs[-1]['date'],return_pct=ret,mdd_pct=md,return_mdd=ret/abs(md) if md else '',trade_count=len(trs),expectancy_pct=mean(rets) if rets else 0,win_rate=sum(r['realized_pnl']>0 for r in trs)/len(trs) if trs else 0,avg_holding_days=mean([r['holding_days'] for r in trs]) if trs else 0,avg_open_entity_positions=mean([x['open_position_count'] for x in xs]),max_open_entity_positions=max(x['open_position_count'] for x in xs),cap_hit_events_total_variant=res.get('diagnostics',{}).get('ticker_cap_hit_events','')))
    return out

def decile_perf(pos):
    out=[]
    for p in PERIODS:
      for v in VARS:
        rs=[r for r in pos if r['period']==p and r['variant']==v]
        for td in range(1,11):
            b=[r for r in rs if r['turnover_decile']==td]
            if b:
                rets=[r['trade_return_pct'] for r in b]
                out.append(dict(marker=MARKER,table_type='turnover_decile',period=p,variant=v,turnover_decile=td,confidence_decile='',entity_count=len({r['entity_id'] for r in b}),trade_count=len(b),return_contribution=sum(r['realized_pnl'] for r in b),avg_trade_pnl_pct=mean(rets),expectancy_pct=mean(rets),win_rate=sum(r['realized_pnl']>0 for r in b)/len(b),avg_holding_days=mean([r['holding_days'] for r in b]),avg_confidence=mean([r['confidence'] for r in b if r['confidence'] is not None]) if any(r['confidence'] is not None for r in b) else '',avg_turnover_score=mean([r['turnover_score'] for r in b if r['turnover_score'] is not None]) if any(r['turnover_score'] is not None for r in b) else ''))
        for td in range(1,11):
          for cd in range(1,11):
            b=[r for r in rs if r['turnover_decile']==td and r['confidence_decile']==cd]
            if b:
                rets=[r['trade_return_pct'] for r in b]
                out.append(dict(marker=MARKER,table_type='confidence_turnover_cross',period=p,variant=v,turnover_decile=td,confidence_decile=cd,entity_count=len({r['entity_id'] for r in b}),trade_count=len(b),return_contribution=sum(r['realized_pnl'] for r in b),avg_trade_pnl_pct=mean(rets),expectancy_pct=mean(rets),win_rate=sum(r['realized_pnl']>0 for r in b)/len(b),avg_holding_days=mean([r['holding_days'] for r in b]),avg_confidence=mean([r['confidence'] for r in b if r['confidence'] is not None]) if any(r['confidence'] is not None for r in b) else '',avg_turnover_score=mean([r['turnover_score'] for r in b if r['turnover_score'] is not None]) if any(r['turnover_score'] is not None for r in b) else ''))
    return out

def diagnose(comp,hold,cap,contrib):
    sc=comp[('stress',CONF)]; st=comp[('stress',TURN)]; oc=comp[('oos',CONF)]; ot=comp[('oos',TURN)]
    hc=next(r for r in hold if r['period']=='stress' and r['variant']==CONF); ht=next(r for r in hold if r['period']=='stress' and r['variant']==TURN)
    cc=next(r for r in cap if r['period']=='stress' and r['variant']==CONF and r['bucket']=='cap_pressure_day_proxy'); ct=next(r for r in cap if r['period']=='stress' and r['variant']==TURN and r['bucket']=='cap_pressure_day_proxy')
    wr=[r for r in contrib if r['period']=='stress' and r['variant']==TURN]; neg=sum(r['realized_pnl'] for r in wr if r['realized_pnl']<0); worst=sorted(wr,key=lambda r:r['realized_pnl'])[:5]; worst_loss=sum(r['realized_pnl'] for r in worst); conc=abs(worst_loss)/abs(neg) if neg else 0
    over=int(st['trade_count'])>int(sc['trade_count'])*1.3 and ht['same_entity_reentry_count']>hc['same_entity_reentry_count']
    capfail=f(st['ticker_cap_hit_events'])>f(sc['ticker_cap_hit_events'])*1.1 and f(ct['trade_expectancy_pct'])<f(cc['trade_expectancy_pct'])
    regime=f(st['return_mdd'])<f(sc['return_mdd']) and f(ot['return_mdd'])>f(oc['return_mdd'])
    concfail=conc>=0.35
    primary='regime_mismatch_amplified_by_overtrading' if regime and over else ('overtrading_failure' if over else 'regime_mismatch')
    return dict(primary_suspect=primary,secondary_suspect='cap_interaction',is_overtrading_failure=over,is_cap_interaction_failure=capfail,is_regime_mismatch=regime,is_concentration_failure=concfail,stress_confidence_trade_count=int(sc['trade_count']),stress_turnover_trade_count=int(st['trade_count']),stress_confidence_return_mdd=f(sc['return_mdd']),stress_turnover_return_mdd=f(st['return_mdd']),oos_confidence_return_mdd=f(oc['return_mdd']),oos_turnover_return_mdd=f(ot['return_mdd']),stress_confidence_reentry_count=hc['same_entity_reentry_count'],stress_turnover_reentry_count=ht['same_entity_reentry_count'],stress_confidence_cap_hits=sc['ticker_cap_hit_events'],stress_turnover_cap_hits=st['ticker_cap_hit_events'],stress_turnover_worst5_loss_share_of_negative_pnl=conc,stress_turnover_worst5_entities=[r['entity_id'] for r in worst])

def report(summary):
    d=summary['diagnosis']
    txt=f"""# Entity Turnover Stress Diagnosis ({MARKER})

기존 `entity_turnover_backtest` 산출물만 읽어 만든 잠정 진단입니다. 새 백테스트는 실행하지 않았습니다.

## Executive Summary

- Primary suspect: **{d['primary_suspect']}**
- Secondary suspect: **{d['secondary_suspect']}**
- Overtrading failure: {d['is_overtrading_failure']}
- Cap interaction failure: {d['is_cap_interaction_failure']}
- Regime mismatch: {d['is_regime_mismatch']}
- Concentration failure: {d['is_concentration_failure']}

## Key Evidence

- Stress trades: confidence {d['stress_confidence_trade_count']} vs turnover {d['stress_turnover_trade_count']}
- Stress Return/MDD: confidence {d['stress_confidence_return_mdd']:.2f} vs turnover {d['stress_turnover_return_mdd']:.2f}
- OOS Return/MDD: confidence {d['oos_confidence_return_mdd']:.2f} vs turnover {d['oos_turnover_return_mdd']:.2f}
- Stress re-entry count: confidence {d['stress_confidence_reentry_count']} vs turnover {d['stress_turnover_reentry_count']}
- Stress cap hits: confidence {d['stress_confidence_cap_hits']} vs turnover {d['stress_turnover_cap_hits']}
- Turnover worst-5 loss share of negative PnL: {d['stress_turnover_worst5_loss_share_of_negative_pnl']:.2%}

## Notes on cap-hit analysis

일자별 cap-hit 이벤트와 cap 때문에 탈락한 후보의 forward return은 원본 산출물에 저장되어 있지 않습니다. `cap_hit_impact.csv`는 active ticker exposure가 25% cap에 근접한 날을 `cap_pressure_day_proxy`로 분리했습니다.

## Actionable Next Experiments

1. turnover weight 축소: turnover를 단독 rank가 아니라 confidence 보조 점수로 사용
2. cooldown: 동일 entity/ticker 손실 청산 후 n일 재진입 제한
3. confidence prefilter -> turnover rerank: confidence 상위 안정 집합 안에서만 turnover 사용
4. stress regime gate: risk-off/high-vol regime에서는 turnover 비활성 또는 weight 축소
"""
    (OUT/'report.md').write_text(txt,encoding='utf-8')

def main():
    OUT.mkdir(parents=True,exist_ok=True); turn,conf=entity_meta(); pos=load_positions(turn,conf)
    contrib=contribution(pos); hold=hold_dist(pos); ov=overlap(pos); cap=cap_impact(pos); sub=subperiod(pos); dec=decile_perf(pos); comp=load_comp(); diag=diagnose(comp,hold,cap,contrib)
    wr(OUT/'entity_contribution.csv',contrib,['marker','period','variant','entity_id','ticker','sector','position_count','realized_pnl','realized_pnl_pct_sum','avg_trade_pnl_pct','win_rate','expectancy_pct','avg_holding_days','median_holding_days','max_loss_trade_pct','max_win_trade_pct','reentry_count','avg_reentry_gap_days','loss_then_reentry_count','turnover_score','confidence'])
    wr(OUT/'holding_reentry_distribution.csv',hold,['marker','period','variant','position_count','holding_1d_count','holding_2_3d_count','holding_4_7d_count','holding_8d_plus_count','holding_1d_ratio','holding_2_3d_ratio','holding_4_7d_ratio','holding_8d_plus_ratio','avg_holding_days','median_holding_days','same_entity_reentry_count','same_ticker_reentry_count','avg_reentry_gap_days','loss_then_reentry_count','loss_then_reentry_ratio'])
    wr(OUT/'selection_overlap.csv',ov,['marker','period','variant_a','variant_b','unique_entities_a','unique_entities_b','intersection','union','jaccard','top10_overlap','top20_overlap','top50_overlap','unique_tickers_a','unique_tickers_b','ticker_intersection','ticker_union','ticker_jaccard'])
    wr(OUT/'cap_hit_impact.csv',cap,['marker','period','variant','bucket','days','day_return_sum','day_avg_return','trade_count','trade_expectancy_pct','trade_win_rate','actual_cap_hit_events_total','actual_cap_hit_tickers_total','blocked_candidate_count','blocked_candidate_forward_return_avg'])
    wr(OUT/'stress_subperiod_performance.csv',sub,['marker','period','variant','subperiod','regime_label','start','end','return_pct','mdd_pct','return_mdd','trade_count','expectancy_pct','win_rate','avg_holding_days','avg_open_entity_positions','max_open_entity_positions','cap_hit_events_total_variant'])
    wr(OUT/'turnover_decile_performance.csv',dec,['marker','table_type','period','variant','turnover_decile','confidence_decile','entity_count','trade_count','return_contribution','avg_trade_pnl_pct','expectancy_pct','win_rate','avg_holding_days','avg_confidence','avg_turnover_score'])
    summary=dict(marker=MARKER,source_dir=str(SRC),diagnosis=diag,row_counts=dict(entity_contribution=len(contrib),holding_reentry_distribution=len(hold),selection_overlap=len(ov),cap_hit_impact=len(cap),stress_subperiod_performance=len(sub),turnover_decile_performance=len(dec)),limitations=['Exact cap-hit day and blocked-candidate forward return were not persisted; cap_hit_impact uses cap_pressure_day_proxy.','PROVISIONAL_PRE_BATCH; rerun after batch completion.'])
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); report(summary); print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))
if __name__=='__main__': main()
