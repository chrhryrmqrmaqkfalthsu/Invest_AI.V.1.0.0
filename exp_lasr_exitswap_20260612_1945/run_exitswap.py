from __future__ import annotations

import copy
import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone

from engine.core.metadata import compute_rulebook_hash
from engine.strategies.rulebook import Rulebook
from scripts.research.run_honest_stage2_full_ga_4fold import (
    context_from_cache,
    DEFAULT_OHLCV_CACHE,
    run_backtest_cc,
    result_metrics,
    ENTRY_EXECUTION_MODE,
    EXIT_EXECUTION_MODE,
    FOLD_EXIT_POLICY,
    FITNESS_MODE,
)

OUT = Path("exp_lasr_exitswap_20260612_1945")
TICKER = "LASR"
LIVE_HASH_PREFIX = "42088d4e"
SET_A_HASH_PREFIX = "2820575b"
SURVIVOR_PREFIXES = ["0707c5f2", "2820575b", "28291859", "89908043", "cd2d26c4", "de9eb672"]
PERIODS = [
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025H2", "2025-06-01", None),
]
EXIT_FIELDS = [
    "exit_strategy",
    "stop_loss_atr",
    "stop_loss_atr_bear",
    "take_profit_atr",
    "take_profit_atr_bull",
    "trailing_atr",
    "trailing_atr_volatile",
    "trailing_activation_profit_pct",
    "breakeven_enabled",
    "breakeven_trigger_profit_pct",
    "breakeven_floor_profit_pct",
    "max_holding_days",
    "sell_omen_enabled",
    "sell_omen_threshold",
]
STRICT_GENERAL_EXP = 1.0
STRICT_STRESS_EXP = 0.0
STRICT_MIN_TRADES = 5
STRICT_MIN_MEMBER = 10.0


def f0(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def load_live_rulebook() -> Rulebook:
    with open("data/symbols/LASR/parameters.json", encoding="utf-8") as f:
        payload = json.load(f)
    return Rulebook.from_dict(payload["rulebook"])


def load_survivor_params() -> dict[str, dict]:
    out = {}
    with open("exp_lasr_reverse_20260612_1856/rulebooks_topn.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            prefix = row["hash"][:8]
            if prefix in SURVIVOR_PREFIXES:
                out[prefix] = row["params"]
    missing = sorted(set(SURVIVOR_PREFIXES) - set(out))
    if missing:
        raise RuntimeError(f"missing survivor params: {missing}")
    return out


def median_numeric(values):
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return float(statistics.median(nums))


def majority(values):
    c = Counter(values)
    return c.most_common(1)[0][0]


def synthesize_set_b(params_by_prefix: dict[str, dict]) -> dict:
    vals = {k: [params_by_prefix[p][k] for p in SURVIVOR_PREFIXES] for k in EXIT_FIELDS}
    out = {}
    for k, seq in vals.items():
        first = seq[0]
        if isinstance(first, bool):
            out[k] = bool(majority(seq))
        elif isinstance(first, str):
            out[k] = str(majority(seq))
        elif k == "max_holding_days":
            out[k] = int(round(median_numeric(seq)))
        else:
            out[k] = median_numeric(seq)
    return out


def apply_exit_params(base: Rulebook, exit_params: dict) -> Rulebook:
    rb = copy.deepcopy(base)
    for k in EXIT_FIELDS:
        setattr(rb, k, exit_params[k])
    return rb


def only_exit_fields_changed(base: Rulebook, modified: Rulebook) -> tuple[bool, list[str]]:
    base_d = base.to_dict()
    mod_d = modified.to_dict()
    changed = [k for k in sorted(set(base_d) | set(mod_d)) if base_d.get(k) != mod_d.get(k)]
    return all(k in EXIT_FIELDS for k in changed), changed


def exit_dist(result) -> dict:
    return dict(sorted(Counter(str(t.get("exit_reason", "")) for t in (getattr(result, "trades", []) or [])).items()))


def run_variant(name: str, rb: Rulebook, ctx: dict, data_end: str) -> tuple[list[dict], list[dict]]:
    rows = []
    trades_rows = []
    for label, start, end in PERIODS:
        end_date = data_end if end is None else end
        result = run_backtest_cc(rb, ctx, start_date=start, end_date=end_date)
        m = result_metrics(result)
        dist = exit_dist(result)
        row = {
            "variant": name,
            "label": label,
            "rulebook_hash": compute_rulebook_hash(rb),
            "expectancy_pct": f0(m.get("expectancy_pct")),
            "max_drawdown_pct": f0(m.get("max_drawdown_pct")),
            "trade_count": int(m.get("trade_count", 0) or 0),
            "win_rate": f0(m.get("win_rate")),
            "profit_factor": f0(m.get("profit_factor")),
            "fitness": f0(m.get("fitness")),
            "exit_dist": json.dumps(dist, ensure_ascii=False, sort_keys=True),
        }
        req = STRICT_STRESS_EXP if label == "2025H2" else STRICT_GENERAL_EXP
        row["strict_pass"] = bool(row["expectancy_pct"] >= req and row["trade_count"] >= STRICT_MIN_TRADES)
        rows.append(row)
        trades_rows.append({
            "variant": name,
            "label": label,
            "exit_dist": dist,
            "trades": list(getattr(result, "trades", []) or []),
        })
    return rows, trades_rows


def fmt(x):
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        return f"{x:.3f}"
    return str(x)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ctx = context_from_cache(TICKER, DEFAULT_OHLCV_CACHE)
    data_end = str(ctx.get("data_end") or ctx.get("data_max") or "2026-06-09")
    live_rb = load_live_rulebook()
    survivor_params = load_survivor_params()
    set_a_params = {k: survivor_params[SET_A_HASH_PREFIX][k] for k in EXIT_FIELDS}
    set_b_params = synthesize_set_b(survivor_params)
    rb_a = apply_exit_params(live_rb, set_a_params)
    rb_b = apply_exit_params(live_rb, set_b_params)
    a_ok, a_changed = only_exit_fields_changed(live_rb, rb_a)
    b_ok, b_changed = only_exit_fields_changed(live_rb, rb_b)
    if not a_ok or not b_ok:
        raise RuntimeError(f"non-exit fields changed: A={a_changed}, B={b_changed}")

    variants = [
        ("baseline_live_42088d4e", live_rb),
        ("setA_2820575b_exit_only", rb_a),
        ("setB_6survivor_median_exit_only", rb_b),
    ]
    all_rows = []
    all_trades = []
    for name, rb in variants:
        rows, trades = run_variant(name, rb, ctx, data_end)
        all_rows.extend(rows)
        all_trades.extend(trades)

    with (OUT / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "variant", "label", "rulebook_hash", "expectancy_pct", "max_drawdown_pct", "trade_count",
            "win_rate", "profit_factor", "fitness", "strict_pass", "exit_dist",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)
    with (OUT / "trades.jsonl").open("w", encoding="utf-8") as f:
        for r in all_trades:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    (OUT / "exit_params.json").write_text(json.dumps({
        "baseline_live_42088d4e": {k: getattr(live_rb, k) for k in EXIT_FIELDS},
        "setA_2820575b_exit_only": set_a_params,
        "setB_6survivor_median_exit_only": set_b_params,
        "setB_synthesis": "numeric median across six survivors; bool/string majority; max_holding_days rounded median",
        "entry_fixed_check": {"A_only_exit_fields_changed": a_ok, "A_changed_fields": a_changed, "B_only_exit_fields_changed": b_ok, "B_changed_fields": b_changed},
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    by = {(r["variant"], r["label"]): r for r in all_rows}
    base = "baseline_live_42088d4e"
    lines = []
    lines.append("# LASR exit-only swap experiment\n\n")
    lines.append("진입 규칙은 live `42088d4e` rulebook 그대로 유지하고, 청산 필드만 교체했다. GA 없음. 라이브 파일 수정 없음.\n\n")
    lines.append("## Settings\n\n")
    lines.append(f"- ticker={TICKER}\n- data_end={data_end}\n- entry={ENTRY_EXECUTION_MODE}\n- exit={EXIT_EXECUTION_MODE}\n- fold_exit_policy={FOLD_EXIT_POLICY}\n- live_hard_stop_guard=True\n- fitness_mode={FITNESS_MODE}\n")
    lines.append("\n## Entry fixed check\n\n")
    lines.append(f"- Set A changed fields: {', '.join(a_changed)}\n")
    lines.append(f"- Set B changed fields: {', '.join(b_changed)}\n")
    lines.append("- 두 세트 모두 변경 필드는 청산 필드 목록 안에만 있음.\n")
    lines.append("\n## Exit parameter sets\n\n")
    lines.append("| field | baseline live | Set A 2820575b | Set B six-survivor median/majority |\n|---|---:|---:|---:|\n")
    for k in EXIT_FIELDS:
        lines.append(f"| {k} | {fmt(getattr(live_rb, k))} | {fmt(set_a_params[k])} | {fmt(set_b_params[k])} |\n")
    lines.append("\n## 3-way metrics\n\n")
    lines.append("| period | variant | expectancy% | Δ vs base | max DD% | ΔDD vs base | trades | Δtrades | exits |\n|---|---|---:|---:|---:|---:|---:|---:|---|\n")
    for label, _, _ in PERIODS:
        b = by[(base, label)]
        for variant, _ in variants:
            r = by[(variant, label)]
            lines.append(
                f"| {label} | {variant} | {r['expectancy_pct']:.3f} | {r['expectancy_pct'] - b['expectancy_pct']:+.3f} | "
                f"{r['max_drawdown_pct']:.3f} | {r['max_drawdown_pct'] - b['max_drawdown_pct']:+.3f} | "
                f"{r['trade_count']} | {r['trade_count'] - b['trade_count']:+d} | `{r['exit_dist']}` |\n"
            )
    lines.append("\n## Phase 4 판정 재료\n\n")
    b2022 = by[(base, "2022")]
    a2022 = by[("setA_2820575b_exit_only", "2022")]
    c2022 = by[("setB_6survivor_median_exit_only", "2022")]
    b25 = by[(base, "2025H2")]
    a25 = by[("setA_2820575b_exit_only", "2025H2")]
    c25 = by[("setB_6survivor_median_exit_only", "2025H2")]
    lines.append(f"- 2022 expectancy: baseline {b2022['expectancy_pct']:.3f}, Set A {a2022['expectancy_pct']:.3f}, Set B {c2022['expectancy_pct']:.3f}\n")
    lines.append(f"- 2022 maxDD: baseline {b2022['max_drawdown_pct']:.3f}, Set A {a2022['max_drawdown_pct']:.3f}, Set B {c2022['max_drawdown_pct']:.3f}\n")
    lines.append(f"- 2025H2 expectancy: baseline {b25['expectancy_pct']:.3f}, Set A {a25['expectancy_pct']:.3f}, Set B {c25['expectancy_pct']:.3f}\n")
    lines.append(f"- 2025H2 maxDD: baseline {b25['max_drawdown_pct']:.3f}, Set A {a25['max_drawdown_pct']:.3f}, Set B {c25['max_drawdown_pct']:.3f}\n")
    lines.append("\n## One-line conclusion placeholder\n\n")
    lines.append("숫자 기준 판정은 최종 응답에서 요약한다.\n")
    (OUT / "REPORT.md").write_text("".join(lines), encoding="utf-8")
    print(json.dumps({
        "out": str(OUT),
        "rows": len(all_rows),
        "baseline_2022_exp": by[(base, "2022")]["expectancy_pct"],
        "setA_2022_exp": by[("setA_2820575b_exit_only", "2022")]["expectancy_pct"],
        "setB_2022_exp": by[("setB_6survivor_median_exit_only", "2022")]["expectancy_pct"],
        "baseline_2025H2_exp": by[(base, "2025H2")]["expectancy_pct"],
        "setA_2025H2_exp": by[("setA_2820575b_exit_only", "2025H2")]["expectancy_pct"],
        "setB_2025H2_exp": by[("setB_6survivor_median_exit_only", "2025H2")]["expectancy_pct"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
