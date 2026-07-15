
from __future__ import annotations
import argparse, hashlib, importlib.util, json, math, os, sys, traceback
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
STAGE = ROOT / "scripts/research/stage23_rework_20260713"
V5_PATH = STAGE / "scripts/research/run_stage3_aap_eec_penalty_v5_host.py"
CACHE_ROOT = ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
SELL_OMEN = ROOT / "data/_system/ml_sell_omen/sell_omen_scores.csv"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def load_v5():
    spec = importlib.util.spec_from_file_location('run_stage3_aap_eec_penalty_v5_host', V5_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load v5: {V5_PATH}')
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def force_v5_eec_params(v5: Any) -> None:
    os.environ['KINGMAKER_ENTRY_EEC_TARGET'] = '6'
    os.environ['KINGMAKER_ENTRY_EEC_FLOOR'] = '0.5'
    os.environ['KINGMAKER_ENTRY_EEC_CLUSTER_GAP_TRADING_DAYS'] = '8'
    if hasattr(v5, 'eec_v5'):
        v5.eec_v5.ENTRY_FITNESS_EEC_TARGET = 6.0
        v5.eec_v5.ENTRY_FITNESS_EEC_FLOOR = 0.5
    if hasattr(v5, 'execution_bt'):
        v5.execution_bt.ENTRY_FITNESS_EEC_TARGET = 6.0
        v5.execution_bt.ENTRY_FITNESS_EEC_FLOOR = 0.5


def patch_for_ticker(v5: Any, ticker: str) -> None:
    ticker = ticker.upper().strip()
    support = v5.runner.support
    from engine.learning.learner import _detect_sector_name

    def load_cache_context(requested: str, market_history_df: pd.DataFrame):
        requested = ticker
        path = CACHE_ROOT / f"{requested}.pkl"
        if not path.is_file():
            raise FileNotFoundError(f"OHLCV cache missing: {path}")
        src = pd.read_pickle(path).copy()
        src.index = pd.to_datetime(src.index, errors='coerce')
        if src.index.isna().any():
            raise RuntimeError(f"invalid OHLCV cache index dates: {requested}")
        src = src.sort_index()
        required = ['Open','High','Low','Close','Volume']
        missing = [c for c in required if c not in src.columns]
        if missing:
            raise RuntimeError(f"OHLCV cache missing columns for {requested}: {missing}")
        raw = src[required].copy()
        for col in required:
            raw[col] = pd.to_numeric(raw[col], errors='coerce')
            if not np.isfinite(raw[col].to_numpy(dtype=float)).all():
                raise RuntimeError(f"OHLCV NaN/Inf: {requested}:{col}")
        df = support.calc_indicators(raw)
        df, sell_omen_info = support.attach_sell_omen_scores(df, requested, score_table_path=SELL_OMEN)
        adapter = support.mod._pipeline_context.get_adapter(requested)
        meta = adapter.meta
        sector_name = _detect_sector_name(meta.name)
        base_rulebook = support.default_rulebook(requested, asset_type=meta.asset_type, direction=meta.direction)
        base_rulebook.sector_name = sector_name
        data_start = str(pd.Timestamp(df.index.min()).date())
        data_end = str(pd.Timestamp(df.index.max()).date())
        context = {
            'ticker': requested,
            'adapter': adapter,
            'meta': meta,
            'df': df,
            'rows': int(len(df)),
            'data_min': data_start,
            'data_max': data_end,
            'data_start': data_start,
            'data_end': data_end,
            'market_history_df': market_history_df.copy(),
            'ticker_sentiment': None,
            'sector_name': sector_name,
            'base_rulebook': base_rulebook,
            'sell_omen_info': sell_omen_info,
        }
        metadata = {
            'path': str(path.resolve()),
            'sha256': sha256_file(path),
            'rows': int(len(df)),
            'first_date': data_start,
            'last_date': data_end,
            'external_fetch': False,
            'auto_regenerate': False,
            'source': 'stage0_ohlcv_cache_pkl_runtime_loader',
            'sell_omen_score_table': str(SELL_OMEN.resolve()),
            'sell_omen_info': sell_omen_info,
        }
        return context, metadata

    # patch the shared runner module globals only; source files are unchanged.
    modules = []
    for obj in [v5, getattr(v5, 'runner', None), getattr(v5, 'base', None), getattr(v5, 'v3', None), getattr(v5, 'v4', None)]:
        if obj is not None:
            modules.append(obj)
            r = getattr(obj, 'runner', None)
            if r is not None:
                modules.append(r)
    seen = set()
    for mod in modules:
        if id(mod) in seen:
            continue
        seen.add(id(mod))
        if hasattr(mod, 'TICKER'):
            setattr(mod, 'TICKER', ticker)
    support._load_snapshot_context = load_cache_context


def install_qualify_only_stub(v5: Any, ticker: str) -> None:
    def _run_entry_qualify_only(out_dir: Path, ctx: dict[str, Any], seed_base: int, call_index: int):
        summary = {
            'ticker': ticker,
            'stage': 'entry',
            'skipped_by_multiticker_v5_probe': True,
            'skip_reason': 'qualify_only_aap_idiosyncrasy_probe_all3_metric',
            'selected_count': 0,
            'pool_count': 0,
            'seed_base': int(seed_base),
            'call_index': int(call_index),
        }
        (out_dir / 'entry_rulebooks.jsonl').write_text('', encoding='utf-8')
        (out_dir / 'entry_result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str)+'\n', encoding='utf-8')
        return summary, []
    v5.runner._run_entry = _run_entry_qualify_only
    v5.runner.MULTITICKER_V5_PROBE_QUALIFY_ONLY = True


def verify(ticker: str) -> dict[str, Any]:
    v5 = load_v5()
    force_v5_eec_params(v5)
    patch_for_ticker(v5, ticker)
    if hasattr(v5.base, '_patch_market_cutoff'):
        v5.base._patch_market_cutoff(date.fromisoformat('2026-07-10'))
    market_frame, market_metadata = v5.runner.support._preflight_market_snapshot()
    ctx, ohlcv = v5.runner.support._load_snapshot_context(ticker, market_frame)
    df = ctx['df']
    folds = {'train_1':('2022-07-01','2023-06-30'), 'train_2':('2023-07-01','2024-06-30'), 'train_3':('2024-07-01','2025-06-30')}
    features = {}
    for col in ['MA5','MA20','MA60','MACD_hist','RSI','BB_lower','BB_upper','Volume_ratio']:
        features[col] = int(pd.to_numeric(df[col], errors='coerce').notna().sum()) if col in df.columns else -1
    return {'ticker': ticker, 'market_auto_fetch': market_metadata.get('auto_fetch_enabled'), 'market_auto_regenerate': market_metadata.get('auto_regenerate_enabled'), 'ohlcv': ohlcv, 'rows': len(df), 'features_present': features, 'fold_rows': {k: int(len(df.loc[(df.index>=pd.Timestamp(a)) & (df.index<=pd.Timestamp(b))])) for k,(a,b) in folds.items()}, 'base_rulebook_ticker': getattr(ctx['base_rulebook'], 'ticker', None)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--out-dir')
    parser.add_argument('--baseline-dir')
    parser.add_argument('--seed-base', type=int, default=2026071401)
    parser.add_argument('--workers', type=int, default=28)
    parser.add_argument('--host-role', default='notebook')
    parser.add_argument('--market-cutoff-date', default='2026-07-10')
    parser.add_argument('--protected-snapshot-json', default='{}')
    parser.add_argument('--daemon-snapshot-json', default='{}')
    parser.add_argument('--source-git-commit', default='unknown')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args(argv)
    if args.verify_only:
        print(json.dumps(verify(args.ticker), ensure_ascii=False, indent=2, default=str), flush=True)
        return 0
    if not args.out_dir or not args.baseline_dir:
        raise SystemExit('--out-dir and --baseline-dir are required for run')
    v5 = load_v5()
    force_v5_eec_params(v5)
    patch_for_ticker(v5, args.ticker)
    install_qualify_only_stub(v5, args.ticker.upper().strip())
    run_argv = [
        '--baseline-dir', args.baseline_dir,
        '--out-dir', args.out_dir,
        '--seed-base', str(args.seed_base),
        '--workers', str(args.workers),
        '--host-role', args.host_role,
        '--market-cutoff-date', args.market_cutoff_date,
        '--protected-snapshot-json', args.protected_snapshot_json,
        '--daemon-snapshot-json', args.daemon_snapshot_json,
        '--source-git-commit', args.source_git_commit,
    ]
    try:
        return int(v5.main(run_argv))
    except Exception:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out/'driver_failure.json').write_text(json.dumps({'ticker':args.ticker,'traceback':traceback.format_exc()}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        raise

if __name__ == '__main__':
    raise SystemExit(main())
