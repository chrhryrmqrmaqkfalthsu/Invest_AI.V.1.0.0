#!/usr/bin/env python3
"""Inspect recent signal history for an elite-shadow candidate.

무엇을 하는 파일인가:
- 특정 elite-shadow 후보의 최근 N거래일 가격/score/threshold/BUY 여부를 출력한다.
- 신호가 며칠 전부터 이미 유효했는지, 그때 가격 대비 현재가가 얼마나 올랐는지 확인한다.
- broker 주문이나 live 상태는 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.elite_signal_history import build_signal_history


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect elite-shadow signal history")
    parser.add_argument("--candidate-id", default="")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--stage", default="")
    parser.add_argument("--hash", default="", help="rulebook hash short prefix")
    parser.add_argument("--days", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = build_signal_history(
        candidate_id=args.candidate_id or None,
        ticker=args.ticker or None,
        stage=args.stage or None,
        rulebook_hash_short=args.hash or None,
        days=args.days,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    if not payload.get("ok"):
        print("ERROR", payload)
        return 1
    cand = payload["candidate"]
    summary = payload["summary"]
    print(f"[{cand['ticker']}] {cand['stage']} {cand['bucket']} {cand['rulebook_hash_short']}")
    print(
        "summary",
        f"verdict={summary.get('verdict')}",
        f"buy_days={summary.get('buy_day_count')}",
        f"consecutive={summary.get('consecutive_buy_days')}",
        f"first_buy={summary.get('first_buy_date')}@{summary.get('first_buy_price')}",
        f"current={summary.get('current_price')}",
        f"chase={summary.get('chase_from_first_buy_pct')}",
        f"entry_vs_first={summary.get('entry_vs_first_buy_pct')}",
    )
    print("date\tclose\tBUY\tscore\tthr\tratio\treasons")
    for row in payload.get("rows", []):
        if not row.get("ok"):
            print(f"{row.get('date')}\t{row.get('close'):.4f}\tERR\t\t\t\t{row.get('reason')}")
            continue
        reasons = "; ".join((row.get("reasons") or [])[:3])
        print(
            f"{row.get('date')}\t{row.get('close'):.4f}\t{str(bool(row.get('should_buy'))):5s}\t"
            f"{float(row.get('score') or 0):.3f}\t{float(row.get('threshold') or 0):.3f}\t{float(row.get('ratio') or 0):.3f}\t{reasons}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
