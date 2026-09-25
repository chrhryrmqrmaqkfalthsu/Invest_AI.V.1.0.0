from pathlib import Path
import json,math,pandas as pd
R=Path(__file__).resolve().parents[4];A=R/'data/_system/analysis/candidate_selection_audit_20260710'
HIGH=A/'high_vol_volume_blind_all_high_vol.csv';STRICT=A/'high_vol_volume_activity_stage2_strict.csv';RELAX=A/'high_vol_volume_activity_stage2_relaxed.csv';PERF=A/'high_vol_volume_activity_stage2_performance.csv';SUM=A/'high_vol_volume_activity_stage2_summary.json'
def flag(s):return s if s.dtype==bool else s.astype(str).str.lower().isin({'true','1','yes'})
def stats(q,policy,group,scope):
 base=q[q.base_avg_pnl_pct.notna()];hold=q[q.holdout_n.fillna(0)>0];comp=q[q.origin_complete_bool];ch=comp[comp.holdout_n.fillna(0)>0]
 def weighted(x):return (x.holdout_avg_pnl_pct*x.holdout_n).sum()/x.holdout_n.sum() if len(x) and x.holdout_n.sum() else math.nan
 return {'policy':policy,'group':group,'scope':scope,'candidate_count':len(q),'complete_candidate_count':len(comp),'base_metric_candidate_count':len(base),'base_avg_pnl_candidate_mean_pct':base.base_avg_pnl_pct.mean(),'base_win_rate_candidate_mean_pct':base.base_win_rate_pct.mean(),'holdout_candidate_count':len(hold),'holdout_trade_count':int(hold.holdout_n.sum()) if len(hold) else 0,'holdout_avg_pnl_candidate_mean_pct':hold.holdout_avg_pnl_pct.mean(),'holdout_win_rate_candidate_mean_pct':hold.holdout_win_rate_pct.mean(),'holdout_avg_pnl_trade_weighted_pct':weighted(hold),'complete_holdout_candidate_count':len(ch),'complete_holdout_trade_count':int(ch.holdout_n.sum()) if len(ch) else 0,'complete_holdout_avg_pnl_candidate_mean_pct':ch.holdout_avg_pnl_pct.mean(),'complete_holdout_win_rate_candidate_mean_pct':ch.holdout_win_rate_pct.mean(),'complete_holdout_avg_pnl_trade_weighted_pct':weighted(ch)}
def main():
 h=pd.read_csv(HIGH,low_memory=False);h['origin_complete_bool']=flag(h.origin_complete_bool);sid=set(pd.read_csv(STRICT,usecols=['candidate_id']).candidate_id);rid=set(pd.read_csv(RELAX,usecols=['candidate_id']).candidate_id);h['strict_risk']=h.candidate_id.isin(sid);h['relaxed_risk']=h.candidate_id.isin(rid);rows=[]
 for policy,col in [('STRICT','strict_risk'),('RELAXED','relaxed_risk')]:
  for scope,q in [('ALL_HIGH_VOL',h),('COMPLETE_HIGH_VOL',h[h.origin_complete_bool]),('STAGE2_HIGH_VOL',h[h.stage=='stage2']),('STAGE3_HIGH_VOL',h[h.stage=='stage3'])]:
   rows.append(stats(q[q[col]],policy,'RISK',scope));rows.append(stats(q[~q[col]],policy,'HIGH_VOL_REMAINDER',scope))
 out=pd.DataFrame(rows);out.to_csv(PERF,index=False)
 s=json.loads(SUM.read_text());cmp={}
 for policy in ['STRICT','RELAXED']:
  q=out[(out.policy==policy)&(out.scope=='COMPLETE_HIGH_VOL')].set_index('group');cmp[policy]={'risk_candidates':int(q.loc['RISK','candidate_count']),'remainder_candidates':int(q.loc['HIGH_VOL_REMAINDER','candidate_count']),'risk_holdout_candidates':int(q.loc['RISK','holdout_candidate_count']),'remainder_holdout_candidates':int(q.loc['HIGH_VOL_REMAINDER','holdout_candidate_count']),'risk_holdout_trades':int(q.loc['RISK','holdout_trade_count']),'remainder_holdout_trades':int(q.loc['HIGH_VOL_REMAINDER','holdout_trade_count']),'risk_holdout_avg_pnl_pct':float(q.loc['RISK','holdout_avg_pnl_candidate_mean_pct']),'remainder_holdout_avg_pnl_pct':float(q.loc['HIGH_VOL_REMAINDER','holdout_avg_pnl_candidate_mean_pct']),'risk_holdout_win_rate_pct':float(q.loc['RISK','holdout_win_rate_candidate_mean_pct']),'remainder_holdout_win_rate_pct':float(q.loc['HIGH_VOL_REMAINDER','holdout_win_rate_candidate_mean_pct'])}
 s['performance_reference_complete_high_vol']=cmp;s['performance_note']='descriptive only; blocking is structural; candidate selection/training overlap may remain';SUM.write_text(json.dumps(s,ensure_ascii=False,indent=2));print(json.dumps(cmp,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
