"""상위 N개 Rulebook을 독립 전략으로 병렬 운용하는 앙상블 백테스트.
- Raw: 멤버 거래를 그대로 합쳐 _summarize -> 신호 품질(거래수/승률/expectancy)
- Portfolio: 멤버별 pnl_pct 합 * weight -> 자본배분 관점 참고
"""
import copy
from engine.learning.backtest import run_backtest, _summarize


def run_ensemble_backtest(rulebooks, df, weights=None, **kwargs):
    if not rulebooks:
        raise ValueError("rulebooks 비어있음")
    n = len(rulebooks)
    if weights is None:
        weights = [1.0 / n] * n
    if len(weights) != n:
        raise ValueError(f"weights({len(weights)}) != rulebooks({n})")

    member_results = []
    for rb in rulebooks:
        member_results.append(run_backtest(rb, df, **kwargs))

    ensemble_trades = []
    for r in member_results:
        ensemble_trades.extend(r.trades)
    ensemble_rb = copy.deepcopy(rulebooks[0])
    raw_result = _summarize(ensemble_rb, ensemble_trades)

    portfolio_total_pnl_pct = 0.0
    member_pnl = []
    for w, r in zip(weights, member_results):
        s = sum(t.get("pnl_pct", 0.0) for t in r.trades)
        member_pnl.append(s)
        portfolio_total_pnl_pct += s * w

    # 진입일 중복률 (다양성 지표)
    entry_dates = [t.get("entry_date") for r in member_results for t in r.trades]
    total = len(entry_dates)
    uniq = len(set(entry_dates))
    overlap_ratio = (1 - uniq / total) if total else 0.0

    return {
        "raw": raw_result,
        "portfolio_total_pnl_pct": portfolio_total_pnl_pct,
        "member_results": member_results,
        "member_pnl_pct_sum": member_pnl,
        "overlap_ratio": overlap_ratio,
        "n_entries": total,
        "n_unique_entry_dates": uniq,
        "weights": weights,
        "n_members": n,
    }
