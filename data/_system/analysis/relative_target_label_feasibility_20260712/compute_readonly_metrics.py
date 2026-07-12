#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_classif

ROOT=Path(__file__).resolve().parents[4]
PILOT=ROOT/'data/_system/analysis/stage2_3_rediscovery_pilot_20260712'
FP=PILOT/'feature_set.csv'; SP=PILOT/'symbol_list.csv'
FEATURES=['pullback_from_high5_pct','fade_after_surge_score','inv_close_pos5','inv_ret_d1_pct','single_up_day5_pct','atr14_pct','realized_vol20_pct','bb_width20_pct','true_range_d1_pct','range_vs_atr14','range_vs_range20','volume_ratio5_prior','volume_ratio20_prior','volume_chg1_pct']
LABELS={
'L0_FIXED_3PCT':'max(High[D+1],High[D+2]) >= Open[D0]*1.03',
'L1_ATR14_1_5X':'max(High[D+1],High[D+2]) >= Open[D0] + 1.5*ATR14[D-1]',
'L2_RV20_1_0X_2D':'max(High[D+1],High[D+2]) >= Open[D0]*(1+sqrt(2)*RV20_pct[D-1]/100)'}

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()

def ohlcv(p):
 d=pd.read_csv(p); d['Date']=pd.to_datetime(d['Date'],errors='coerce').dt.tz_localize(None)
 d=d.dropna(subset=['Date']).set_index('Date').sort_index(); d.index=pd.DatetimeIndex(d.index).normalize(); d=d[~d.index.duplicated(keep='last')]
 for c in ['Open','High','Low','Close','Volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
 return d

def expanded(d):
 c=d.Close.astype(float); h=d.High.astype(float); l=d.Low.astype(float); v=d.Volume.astype(float); pc=c.shift(1)
 tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1); atr=tr.ewm(alpha=1/14,adjust=False).mean(); ret=c.pct_change()*100
 rv=ret.rolling(20,min_periods=20).std(ddof=1); mid=c.rolling(20,min_periods=1).mean(); sd=c.rolling(20,min_periods=1).std(ddof=1); rp=100*(h-l)/c.replace(0,np.nan)
 x=pd.DataFrame({'atr14_abs_d1':atr,'atr14_pct':100*atr/c.replace(0,np.nan),'realized_vol20_pct':rv,'bb_width20_pct':400*sd/mid.replace(0,np.nan),'true_range_d1_pct':100*tr/c.replace(0,np.nan),'range_vs_atr14':(h-l)/atr.replace(0,np.nan),'range_vs_range20':rp/rp.shift(1).rolling(20,min_periods=20).mean().replace(0,np.nan),'volume_ratio5_prior':v/v.shift(1).rolling(5,min_periods=5).mean().replace(0,np.nan),'volume_ratio20_prior':v/v.shift(1).rolling(20,min_periods=20).mean().replace(0,np.nan),'volume_chg1_pct':100*(v/v.shift(1).replace(0,np.nan)-1)},index=d.index).shift(1)
 x.index.name='date'; return x.reset_index()

def corr(x,y):
 return 0.0 if len(x)<3 or np.std(x)<=1e-15 or np.std(y)<=1e-15 else float(np.corrcoef(x,y)[0,1])

def mib(x,y):
 return float(mutual_info_classif(x.reshape(-1,1),y,discrete_features=False,n_neighbors=5,random_state=20260712)[0]/math.log(2))

def ent(p):
 return 0.0 if p<=0 or p>=1 else -(p*math.log2(p)+(1-p)*math.log2(1-p))

def clean(v):
 if isinstance(v,dict): return {str(k):clean(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)): return [clean(x) for x in v]
 if isinstance(v,np.generic): return clean(v.item())
 if isinstance(v,float) and not math.isfinite(v): return None
 return v

def main():
 base=pd.read_csv(FP); base['date']=pd.to_datetime(base.date,errors='coerce').dt.tz_localize(None); syms=pd.read_csv(SP).sort_values('selection_order')
 parts=[]; checks=[]
 for r in syms.to_dict('records'):
  p=ROOT/str(r['source_path']); actual=sha(p)
  if actual!=str(r['source_sha256']): raise RuntimeError(f"source SHA mismatch {r['ticker']}")
  d=ohlcv(p); x=expanded(d); x.insert(0,'ticker',str(r['ticker'])); parts.append(x); checks.append({'ticker':r['ticker'],'source_path':r['source_path'],'source_sha256':actual,'sha_match':True,'ohlcv_rows':len(d)})
 m=base.merge(pd.concat(parts,ignore_index=True),on=['ticker','date'],how='left',validate='many_to_one'); m['inv_close_pos5']=1-m.close_pos5; m['inv_ret_d1_pct']=-m.ret_d1_pct
 fh=m[['future_high_d1','future_high_d2']].max(axis=1); op=m.entry_open_d0.astype(float)
 m['L0_FIXED_3PCT']=(fh>=op*1.03).astype(int); m['L1_ATR14_1_5X']=(fh>=op+1.5*m.atr14_abs_d1).astype(int); m['L2_RV20_1_0X_2D']=(fh>=op*(1+math.sqrt(2)*m.realized_vol20_pct/100)).astype(int)
 mismatch=int((m.L0_FIXED_3PCT!=m.label_2d3pct.astype(int)).sum())
 if mismatch: raise RuntimeError(f'L0 mismatch {mismatch}')
 valid=m.dropna(subset=FEATURES+['atr14_abs_d1']+list(LABELS)).copy(); valid=valid[np.isfinite(valid[FEATURES+['atr14_abs_d1']].to_numpy(float)).all(axis=1)].reset_index(drop=True)
 pre_counts=valid.groupby('ticker').size().to_dict(); date_counts=valid.groupby('date').ticker.nunique(); common_dates=set(date_counts[date_counts==50].index); common=valid[valid.date.isin(common_dates)].copy().reset_index(drop=True)
 counts=common.groupby('ticker').size()
 if counts.nunique()!=1 or len(counts)!=50: raise RuntimeError(str(counts.to_dict()))
 rates=[]
 for t,g in common.groupby('ticker',sort=True):
  r={'ticker':t,'sample_count_common':len(g)}
  for lab in LABELS: r[f'{lab}_positive_count']=int(g[lab].sum()); r[f'{lab}_positive_rate']=float(g[lab].mean())
  rates.append(r)
 rdf=pd.DataFrame(rates); fairness=[]
 for lab,definition in LABELS.items():
  a=rdf[f'{lab}_positive_rate'].to_numpy(float); mean=float(a.mean()); sd=float(a.std(ddof=0))
  fairness.append({'label':lab,'definition':definition,'ticker_count':len(a),'common_rows_total':len(common),'positive_rate_mean':mean,'positive_rate_variance_population':float(a.var(ddof=0)),'positive_rate_std_population':sd,'positive_rate_cv':sd/mean,'positive_rate_min':float(a.min()),'positive_rate_max':float(a.max()),'positive_rate_range':float(a.max()-a.min()),'positive_rate_p10':float(np.quantile(a,.1)),'positive_rate_p90':float(np.quantile(a,.9)),'positive_rate_iqr':float(np.quantile(a,.75)-np.quantile(a,.25))})
 ranks=common.groupby('ticker',sort=False)[FEATURES].rank(pct=True,method='average'); info=[]; summaries=[]
 for lab,definition in LABELS.items():
  y=common[lab].to_numpy(int); rate=float(y.mean()); entropy=ent(rate); rows=[]
  for f in FEATURES:
   x=common[f].to_numpy(float); xr=ranks[f].to_numpy(float); mi=mib(x,y); r={'feature':f,'label':lab,'label_definition':definition,'analysis_scope':'50_TICKER_COMMON_DATE_INTERSECTION','sample_count':len(common),'label_positive_rate':rate,'pearson_corr':corr(x,y),'mutual_information_bits':mi,'label_entropy_bits':entropy,'mi_entropy_fraction':mi/entropy if entropy else None,'within_ticker_rank_pearson_corr':corr(xr,y),'within_ticker_rank_mi_bits':mib(xr,y)}; rows.append(r); info.append(r)
  top=sorted(rows,key=lambda z:z['mutual_information_bits'],reverse=True); tw=sorted(rows,key=lambda z:z['within_ticker_rank_mi_bits'],reverse=True)
  summaries.append({'label':lab,'positive_rate_all_common':rate,'label_entropy_bits':entropy,'top_n':5,'top_feature_mi_bits_sum':float(sum(z['mutual_information_bits'] for z in top[:5])),'top_feature_mi_entropy_fraction_sum':float(sum(z['mi_entropy_fraction'] for z in top[:5])),'top_features_by_mi':[z['feature'] for z in top[:5]],'max_single_feature_mi_bits':float(top[0]['mutual_information_bits']),'abs_corr_ge_0_10_feature_count':int(sum(abs(z['pearson_corr'])>=.1 for z in rows)),'top_feature_abs_corr':float(max(abs(z['pearson_corr']) for z in rows)),'within_ticker_top_feature_mi_bits_sum':float(sum(z['within_ticker_rank_mi_bits'] for z in tw[:5])),'within_ticker_top_features_by_mi':[z['feature'] for z in tw[:5]],'within_ticker_abs_corr_ge_0_10_feature_count':int(sum(abs(z['within_ticker_rank_pearson_corr'])>=.1 for z in rows))})
 out={'metadata':{'feature_source':str(FP.relative_to(ROOT)),'symbol_source':str(SP.relative_to(ROOT)),'feature_source_sha256':sha(FP),'symbol_source_sha256':sha(SP),'input_rows':len(base),'input_tickers':int(base.ticker.nunique()),'valid_rows_before_common_date_intersection':len(valid),'valid_rows_by_ticker_before_intersection':pre_counts,'common_rows':len(common),'common_rows_per_ticker':int(counts.iloc[0]),'common_date_count':len(common_dates),'common_first_date':str(common.date.min().date()),'common_last_date':str(common.date.max().date()),'l0_mismatch_against_preserved_label':mismatch,'mi_estimator':'sklearn.feature_selection.mutual_info_classif','mi_neighbors':5,'mi_random_state':20260712,'mi_unit':'bits','l2_two_day_scaling':'sqrt(2)*daily_realized_vol20_pct','training_or_ga_run':False},'label_definitions':LABELS,'label_positive_rates':rates,'label_fairness_comparison':fairness,'feature_label_information':info,'label_information_summary':summaries,'source_checks':checks}
 print(json.dumps(clean(out),ensure_ascii=False,sort_keys=True))
if __name__=='__main__': main()
