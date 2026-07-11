import csv,io,json
from pathlib import Path
from replay_common import SPECS,csvtxt
from replay_job import run
OUT=Path(__file__).resolve().parent
ENT=OUT/'entries';ENT.mkdir(exist_ok=True)
periods=['stress_pre_2022h1','train_1','train_2','recent_1y']
summaries=[];trade_rows=[]
keep=['ticker','rule_id','period_label','row_origin','original_trade_no','rerun_trade_no','match_status','original_entry_date','rerun_entry_date','original_entry_price','rerun_entry_price','original_exit_date','rerun_exit_date','original_pnl_pct','rerun_pnl_pct','entry_price_abs_diff','pnl_abs_diff','exit_date_match']
for ticker,prefix in SPECS.items():
 for period in periods:
  result=run(ticker,period);summaries.append(result['summary'])
  name=f'{ticker}_{prefix}_{period}.csv';(ENT/name).write_text(result['detail_csv'],encoding='utf-8')
  for row in csv.DictReader(io.StringIO(result['detail_csv'])):
   trade_rows.append({k:row.get(k,'') for k in keep})
summary_csv=csvtxt(summaries)
(OUT/'reproduction_period_summary.csv').write_text(summary_csv,encoding='utf-8')
(OUT/'reproduction_check.csv').write_text(summary_csv,encoding='utf-8')
(OUT/'reproduction_trade_comparison.csv').write_text(csvtxt(trade_rows),encoding='utf-8')
gaps=[]
for s in summaries:
 if not s['exact_period_reproduction']:
  gaps.append({'ticker':s['ticker'],'period_label':s['period_label'],'issue':'REPRODUCTION_MISMATCH','detail':f"original={s['original_snapshot_trades']}; rerun={s['rerun_trades']}; entry_date_match={s['entry_date_match_count']}; exact={s['exact_match_count']}; missing={s['missing_in_rerun_count']}; rerun_only={s['rerun_only_count']}",'impact':'현재 재실행 거래를 원래 Stage3 거래와 동일시할 수 없음'})
for scope,detail in [('all','원래 실행 시점 frozen market_history/market_history_v2 snapshot 부재'),('all','원래 실행 시점 frozen ticker_sentiment snapshot 부재'),('all','원래 실행 시점 frozen sell_omen_scores snapshot 부재'),('all','OHLC는 원래 manifest 날짜를 exclusive end로 yfinance 재조회; 공급자 과거 수정 가능')]:
 gaps.append({'ticker':scope,'period_label':'all','issue':'FROZEN_INPUT_UNAVAILABLE','detail':detail,'impact':'완전 재현 제한'})
(OUT/'data_gaps_and_mismatches.csv').write_text(csvtxt(gaps),encoding='utf-8')
for p in [OUT/'write_probe.tmp']:
 if p.exists():p.unlink()
print(json.dumps({'periods':len(summaries),'detail_files':len(list(ENT.glob('*.csv'))),'exact_periods':sum(bool(x['exact_period_reproduction']) for x in summaries),'component_original':sum(x['original_component_available_count'] for x in summaries),'component_rerun':sum(x['rerun_component_available_count'] for x in summaries)},ensure_ascii=False))
