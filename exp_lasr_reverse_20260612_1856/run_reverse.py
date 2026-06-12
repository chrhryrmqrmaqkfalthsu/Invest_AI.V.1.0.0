from __future__ import annotations
import csv, json, math, time, collections
from pathlib import Path
from datetime import datetime, timezone

from engine.core.metadata import compute_rulebook_hash
from engine.learning.genetic import GAConfig, run_ga, collect_top_rulebooks
from engine.pipeline.topn_survivor import score_topn_validation_periods
from scripts.research.run_honest_stage2_full_ga_4fold import (
    context_from_cache, DEFAULT_OHLCV_CACHE, run_backtest_cc, result_metrics,
    ENTRY_EXECUTION_MODE, EXIT_EXECUTION_MODE, FOLD_EXIT_POLICY, FITNESS_MODE,
    POSITION_LIMIT_KRW, COMMISSION_RATE, WARMUP,
)

OUT = Path('exp_lasr_reverse_20260612_1856')
TICKER = 'LASR'
POP = 100
GEN = 40
SEED = 21264911  # original LR8D full-universe order: shard0 local_idx=43, split_idx=4(2025H2)
TOP_N = 100
MIN_TRADES = 5
MIN_MEMBER_SCORE = 10.0
MIN_GENERAL_EXP = 1.0
MIN_STRESS_EXP = 0.0
PARAM_FIELDS = [
    'exit_strategy','stop_loss_atr','stop_loss_atr_bear','take_profit_atr','take_profit_atr_bull',
    'trailing_atr','trailing_atr_volatile','trailing_activation_profit_pct','breakeven_enabled',
    'breakeven_trigger_profit_pct','breakeven_floor_profit_pct','max_holding_days',
    'sell_omen_enabled','sell_omen_threshold'
]

def f0(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0

def i0(x):
    try:
        return int(float(x or 0))
    except Exception:
        return 0

def rb_params(rb):
    return {k: getattr(rb, k, None) for k in PARAM_FIELDS}

def exit_dist(result):
    return dict(sorted(collections.Counter(str(t.get('exit_reason','')) for t in (getattr(result,'trades',[]) or [])).items()))

def metrics_row(label, year, is_stress, rank, rb, result, split):
    h = compute_rulebook_hash(rb)
    m = result_metrics(result)
    return {
        'ticker': TICKER,
        'year': year,
        'label': label,
        'is_stress': bool(is_stress),
        'rank_is': int(rank),
        'rulebook_hash': h,
        'train_fitness': f0(getattr(rb, 'fitness', 0.0)),
        'train_period': [split['train_start'], split['train_end']],
        'test_period': [split['test_start'], split['test_end']],
        'oos': {k: m[k] for k in ['trade_count','win_rate','expectancy_pct','profit_factor','max_drawdown_pct']},
        'fitness': m['fitness'],
    }

def load_2022_comparison():
    p = Path('exp_lasr_multiyear_20260612_1845/survival_summary.csv')
    if not p.exists():
        return None
    rows = list(csv.DictReader(p.open(encoding='utf-8')))
    return {
        'candidate_count': len(rows),
        'pass_2022': sum(1 for r in rows if r.get('pass_2022') == 'True'),
        'general_pass_3': sum(1 for r in rows if str(r.get('general_pass_count')) == '3'),
        'stress_pass': sum(1 for r in rows if r.get('pass_2025H2') == 'True'),
        'all4': sum(1 for r in rows if r.get('survives_all4') == 'True'),
    }

def main():
    started_all = time.time()
    ctx = context_from_cache(TICKER, DEFAULT_OHLCV_CACHE)
    data_start = str(ctx.get('data_start') or ctx.get('data_min') or '2020-01-01')
    data_end = str(ctx.get('data_end') or ctx.get('data_max') or '2026-06-09')
    train_start = data_start
    train_end = '2025-05-31'
    eval_splits = [
        {'label':'2022','year':2022,'train_start':train_start,'train_end':train_end,'test_start':'2022-01-01','test_end':'2022-12-31','is_stress':False},
        {'label':'2023','year':2023,'train_start':train_start,'train_end':train_end,'test_start':'2023-01-01','test_end':'2023-12-31','is_stress':False},
        {'label':'2024','year':2024,'train_start':train_start,'train_end':train_end,'test_start':'2024-01-01','test_end':'2024-12-31','is_stress':False},
        {'label':'2025H2','year':'2025H2','train_start':train_start,'train_end':train_end,'test_start':'2025-06-01','test_end':data_end,'is_stress':True},
    ]
    cfg = GAConfig(
        population=POP, generations=GEN, elite_ratio=0.2, mutation_rate=0.15, mutation_strength=0.2,
        tournament_size=3, seed_pattern_ratio=0.33, early_stop_no_improve=GEN, random_seed=SEED,
    )
    print('RUN_REVERSE_START', json.dumps({'ticker':TICKER,'seed':SEED,'population':POP,'generations':GEN,'train':[train_start,train_end]}, ensure_ascii=False), flush=True)
    def evaluate_fn(rb):
        res = run_backtest_cc(rb, ctx, start_date=train_start, end_date=train_end)
        return f0(getattr(res, 'fitness', -1_000_000.0))
    ga_started = time.time()
    ga = run_ga(base_rulebook=ctx['base_rulebook'], evaluate_fn=evaluate_fn, ga_config=cfg)
    ga_sec = time.time() - ga_started
    candidates = collect_top_rulebooks(ga, TOP_N)
    print('RUN_REVERSE_GA_DONE', json.dumps({'seconds':round(ga_sec,3),'candidates':len(candidates),'best_train_fitness':f0(getattr(getattr(ga,'best',None),'fitness',0.0))}, ensure_ascii=False), flush=True)

    periods = []
    raw_by_hash = {}
    trade_rows = []
    for split in eval_splits:
        print('RUN_REVERSE_EVAL', split['label'], flush=True)
        cand_rows = []
        for rank, rb in enumerate(candidates, 1):
            res = run_backtest_cc(rb, ctx, start_date=split['test_start'], end_date=split['test_end'])
            row = metrics_row(split['label'], split['year'], split['is_stress'], rank, rb, res, split)
            h = row['rulebook_hash']
            if h not in raw_by_hash:
                raw_by_hash[h] = {
                    'hash': h,
                    'rank_train': rank,
                    'train_fitness': f0(getattr(rb, 'fitness', 0.0)),
                    'rulebook': rb.to_dict(),
                    'params': rb_params(rb),
                }
            raw_by_hash[h].setdefault('periods_raw', {})[split['label']] = {**row['oos'], 'fitness': row['fitness'], 'exit_dist': exit_dist(res)}
            trade_rows.append({'hash':h,'label':split['label'],'rank_train':rank,'trade_count':len(getattr(res,'trades',[]) or []),'exit_dist':exit_dist(res),'trades':list(getattr(res,'trades',[]) or [])})
            cand_rows.append(row)
        periods.append({'ticker':TICKER,'year':split['year'],'label':split['label'],'is_stress':split['is_stress'],'train_period':[train_start,train_end],'test_period':[split['test_start'],split['test_end']],'candidate_count':len(cand_rows),'candidates':cand_rows})

    scored = score_topn_validation_periods({'periods':periods}, general_years=(2022,2023,2024), stress_labels=('2025H2',))
    for bucket in ['general_periods','stress_periods']:
        for p in scored.get(bucket, []):
            label = p.get('label')
            for c in p.get('candidates', []):
                h = c.get('rulebook_hash')
                m = c.get('oos_metrics') or {}
                raw_by_hash[h].setdefault('periods_scored', {})[label] = {
                    'trade_count': i0(m.get('trade_count')),
                    'win_rate': f0(m.get('win_rate')),
                    'expectancy_pct': f0(m.get('expectancy_pct')),
                    'profit_factor': f0(m.get('profit_factor')),
                    'max_drawdown_pct': f0(m.get('max_drawdown_pct')),
                    'oos_member_score': f0(c.get('oos_member_score')),
                    'rank_is': i0(c.get('rank_is')),
                }

    summary_rows = []
    survivors = []
    pass_2022_hashes = []
    for h, item in raw_by_hash.items():
        ps = item.get('periods_scored', {})
        passes = {}
        for label in ['2022','2023','2024']:
            p = ps.get(label, {})
            passes[label] = bool(i0(p.get('trade_count')) >= MIN_TRADES and f0(p.get('oos_member_score')) >= MIN_MEMBER_SCORE and f0(p.get('expectancy_pct')) >= MIN_GENERAL_EXP)
        p = ps.get('2025H2', {})
        passes['2025H2'] = bool(i0(p.get('trade_count')) >= MIN_TRADES and f0(p.get('oos_member_score')) >= MIN_MEMBER_SCORE and f0(p.get('expectancy_pct')) >= MIN_STRESS_EXP)
        general_pass_count = sum(1 for k in ['2022','2023','2024'] if passes[k])
        all4 = bool(general_pass_count >= 3 and passes['2025H2'])
        row = {'hash':h,'rank_train':item['rank_train'],'train_fitness':item['train_fitness'],'pass_2022':passes['2022'],'pass_2023':passes['2023'],'pass_2024':passes['2024'],'pass_2025H2':passes['2025H2'],'general_pass_count':general_pass_count,'survives_all4':all4}
        for label in ['2022','2023','2024','2025H2']:
            p = ps.get(label, {})
            row[f'{label}_exp'] = f0(p.get('expectancy_pct'))
            row[f'{label}_dd'] = f0(p.get('max_drawdown_pct'))
            row[f'{label}_trades'] = i0(p.get('trade_count'))
            row[f'{label}_member'] = f0(p.get('oos_member_score'))
        summary_rows.append(row)
        if passes['2022']:
            pass_2022_hashes.append(h)
        if all4:
            survivors.append({**row,'params':item['params'],'rulebook':item['rulebook'],'periods':ps})
    summary_rows.sort(key=lambda r:(not r['survives_all4'],-r['general_pass_count'],not r['pass_2025H2'],not r['pass_2022'],r['rank_train']))
    survivors.sort(key=lambda r:r['rank_train'])

    OUT.mkdir(parents=True, exist_ok=True)
    config = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'ticker': TICKER,
        'direction_note': 'reverse diagnostic only: trained through 2025-05-31 and applied backward to 2022/2023/2024; not chronological OOS',
        'population': POP,
        'generations': GEN,
        'seed': SEED,
        'top_n': TOP_N,
        'strict_k3_individual_criteria': {'general_years':['2022','2023','2024'],'stress_label':'2025H2','survivor_k':3,'min_trades':MIN_TRADES,'min_member_score':MIN_MEMBER_SCORE,'min_general_expectancy_pct':MIN_GENERAL_EXP,'min_stress_expectancy_pct':MIN_STRESS_EXP},
        'execution_settings': {'entry_execution_mode':ENTRY_EXECUTION_MODE,'exit_execution_mode':EXIT_EXECUTION_MODE,'fold_exit_policy':FOLD_EXIT_POLICY,'live_hard_stop_guard':True,'fitness_mode':FITNESS_MODE,'position_limit_krw':POSITION_LIMIT_KRW,'commission_rate':COMMISSION_RATE,'warmup':WARMUP},
        'train_period':[train_start,train_end],
        'eval_periods': eval_splits,
        'ga_seconds': ga_sec,
        'total_seconds': time.time()-started_all,
        'generations_run': getattr(ga,'generations_run',None),
        'candidate_count': len(candidates),
        'best_train_fitness': f0(getattr(getattr(ga,'best',None),'fitness',0.0)),
    }
    (OUT/'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    with (OUT/'period_metrics.csv').open('w', newline='', encoding='utf-8') as f:
        fields=['hash','rank_train','label','trade_count','win_rate','expectancy_pct','profit_factor','max_drawdown_pct','oos_member_score','exit_dist']
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for h,item in sorted(raw_by_hash.items(), key=lambda kv:kv[1]['rank_train']):
            for label in ['2022','2023','2024','2025H2']:
                p=item.get('periods_scored',{}).get(label,{})
                raw=item.get('periods_raw',{}).get(label,{})
                w.writerow({'hash':h,'rank_train':item['rank_train'],'label':label,'trade_count':i0(p.get('trade_count')),'win_rate':f0(p.get('win_rate')),'expectancy_pct':f0(p.get('expectancy_pct')),'profit_factor':f0(p.get('profit_factor')),'max_drawdown_pct':f0(p.get('max_drawdown_pct')),'oos_member_score':f0(p.get('oos_member_score')),'exit_dist':json.dumps(raw.get('exit_dist',{}),ensure_ascii=False,sort_keys=True)})
    with (OUT/'survival_summary.csv').open('w', newline='', encoding='utf-8') as f:
        fields=list(summary_rows[0].keys()) if summary_rows else []
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(summary_rows)
    (OUT/'survivors.json').write_text(json.dumps(survivors, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    with (OUT/'rulebooks_topn.jsonl').open('w', encoding='utf-8') as f:
        for h,item in sorted(raw_by_hash.items(), key=lambda kv:kv[1]['rank_train']):
            f.write(json.dumps({'hash':h,'rank_train':item['rank_train'],'train_fitness':item['train_fitness'],'params':item['params'],'rulebook':item['rulebook']}, ensure_ascii=False, sort_keys=True)+'\n')
    with (OUT/'trades.jsonl').open('w', encoding='utf-8') as f:
        for tr in trade_rows:
            f.write(json.dumps(tr, ensure_ascii=False, sort_keys=True)+'\n')

    live_params = json.load(open('data/symbols/LASR/parameters.json', encoding='utf-8'))['rulebook']
    comp22 = load_2022_comparison()
    lines = []
    lines.append('# LASR 2025H2-trained individual reverse multi-year diagnostic\n')
    lines.append('주의: 이 실험은 2025H2까지 학습한 개체를 과거 2022~2024에 적용하는 역방향 진단이며, 시간순 OOS가 아니다. 실거래 방법으로 직접 사용할 수 없다.\n')
    lines.append('\n## Phase 0 — strict_k3 criteria\n')
    lines.append(f'- survivor_k=3 general years: 2022/2023/2024\n- min_trades={MIN_TRADES}\n- min_member_score={MIN_MEMBER_SCORE}\n- general expectancy >= {MIN_GENERAL_EXP}\n- stress 2025H2 expectancy >= {MIN_STRESS_EXP}\n')
    lines.append('\n## Execution settings\n')
    lines.append(f'- entry={ENTRY_EXECUTION_MODE}, exit={EXIT_EXECUTION_MODE}, fold_exit_policy={FOLD_EXIT_POLICY}, live_hard_stop_guard=True\n')
    lines.append('\n## Phase 1 — GA\n')
    lines.append(f'- population={POP}, generations={GEN}, seed={SEED}, candidates={len(candidates)}, ga_seconds={ga_sec:.3f}, best_train_fitness={config["best_train_fitness"]:.6f}\n')
    lines.append('\n## Phase 3 — pass-rate summary\n')
    lines.append(f'- 2022 pass count={sum(1 for r in summary_rows if r["pass_2022"])}\n')
    lines.append(f'- general 3-year pass count={sum(1 for r in summary_rows if r["general_pass_count"]>=3)}\n')
    lines.append(f'- stress pass count={sum(1 for r in summary_rows if r["pass_2025H2"])}\n')
    lines.append(f'- all4 survivor count={len(survivors)}\n')
    dist=collections.Counter(str(r['general_pass_count']) for r in summary_rows)
    lines.append(f'- general_pass_count distribution={dict(sorted(dist.items()))}\n')
    lines.append('\n## Comparison with 2022-trained experiment\n')
    if comp22:
        lines.append('| experiment | candidates | 2022 pass | general3 pass | stress pass | all4 pass |\n|---|---:|---:|---:|---:|---:|\n')
        lines.append(f"| 2022-trained forward | {comp22['candidate_count']} | {comp22['pass_2022']} | {comp22['general_pass_3']} | {comp22['stress_pass']} | {comp22['all4']} |\n")
        lines.append(f"| 2025H2-trained reverse | {len(summary_rows)} | {sum(1 for r in summary_rows if r['pass_2022'])} | {sum(1 for r in summary_rows if r['general_pass_count']>=3)} | {sum(1 for r in summary_rows if r['pass_2025H2'])} | {len(survivors)} |\n")
    else:
        lines.append('- previous 2022-trained result not found.\n')
    lines.append('\n## All4 survivors\n')
    if survivors:
        lines.append('| rank | hash | 2022 exp/dd/t/member | 2023 | 2024 | 2025H2 |\n|---:|---|---|---|---|---|\n')
        for s in survivors[:30]:
            def cell(label): return f"{s[label+'_exp']:.3f}/{s[label+'_dd']:.3f}/{s[label+'_trades']}/{s[label+'_member']:.2f}"
            lines.append(f"| {s['rank_train']} | {s['hash'][:8]} | {cell('2022')} | {cell('2023')} | {cell('2024')} | {cell('2025H2')} |\n")
    else:
        lines.append('- no 2025H2-trained candidate passed all four periods under strict_k3 individual criteria.\n')
    lines.append('\n## Top 20 candidate survival summary\n')
    lines.append('| rank | hash | pass count | stress pass | 2022 exp/dd/t/member | 2023 | 2024 | 2025H2 |\n|---:|---|---:|---:|---|---|---|---|\n')
    for r in summary_rows[:20]:
        def c(label): return f"{r[label+'_exp']:.2f}/{r[label+'_dd']:.2f}/{r[label+'_trades']}/{r[label+'_member']:.1f}"
        lines.append(f"| {r['rank_train']} | {r['hash'][:8]} | {r['general_pass_count']} | {r['pass_2025H2']} | {c('2022')} | {c('2023')} | {c('2024')} | {c('2025H2')} |\n")
    lines.append('\n## Phase 4 — exit parameter comparison\n')
    compare_pool = survivors
    pool_label = 'all4 survivors'
    if not compare_pool:
        pass2022 = [r for r in summary_rows if r['pass_2022']]
        compare_pool = []
        ph = set(r['hash'] for r in pass2022)
        for h,item in raw_by_hash.items():
            if h in ph:
                compare_pool.append({'hash':h,'rank_train':item['rank_train'],'params':item['params']})
        pool_label = '2022-pass candidates because all4 survivors=0'
    lines.append(f'- comparison pool: {pool_label}, count={len(compare_pool)}\n')
    if compare_pool:
        lines.append('| param | live_42088d4e | pool_min | pool_max |\n|---|---:|---:|---:|\n')
        for k in PARAM_FIELDS:
            vals=[s['params'].get(k) for s in compare_pool]
            if all(isinstance(v,(int,float,bool)) for v in vals if v is not None) and isinstance(live_params.get(k),(int,float,bool)):
                nums=[float(v) for v in vals if v is not None]
                lines.append(f"| {k} | {live_params.get(k)} | {min(nums) if nums else ''} | {max(nums) if nums else ''} |\n")
            else:
                lines.append(f"| {k} | {live_params.get(k)} | {sorted(set(str(v) for v in vals))} |  |\n")
    else:
        lines.append('- no comparison pool.\n')
    (OUT/'REPORT.md').write_text(''.join(lines), encoding='utf-8')

    print('RUN_REVERSE_RESULT', json.dumps({
        'out': str(OUT),
        'candidate_count': len(summary_rows),
        'pass_2022': sum(1 for r in summary_rows if r['pass_2022']),
        'general3': sum(1 for r in summary_rows if r['general_pass_count']>=3),
        'stress_pass': sum(1 for r in summary_rows if r['pass_2025H2']),
        'all4': len(survivors),
        'general_pass_dist': dict(sorted(collections.Counter(str(r['general_pass_count']) for r in summary_rows).items())),
        'total_seconds': round(time.time()-started_all, 3),
    }, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
