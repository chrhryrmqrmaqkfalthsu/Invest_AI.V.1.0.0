"""LR-8C-FIX 본 실행 진행상황 텔레그램 알림 (read-only monitor)."""
import time
from pathlib import Path
from engine.live.telegram.notifier import TelegramNotifier

OUT = Path("data/_system/research/lr8c_run2_20260607")
TOPN = OUT / "lr8c_run2_topn.jsonl"
RB = OUT / "lr8c_run2_topn_rulebooks.jsonl"
TRADES = OUT / "lr8c_run2_trades.jsonl"
TOTAL = 340
INTERVAL_SEC = 1800
START = time.time()


def count_lines(path: Path) -> int:
    try:
        with path.open() as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def main():
    nt = TelegramNotifier()
    nt.send("🚀 LR-8C-FIX 본 실행 모니터링 시작 (룰북+거래로그 저장 버전)")
    while True:
        tp = count_lines(TOPN)
        rb = count_lines(RB)
        tr = count_lines(TRADES)
        elapsed = (time.time() - START) / 3600.0
        pct = tp / TOTAL * 100.0
        msg = (
            f"📊 진행: {tp}/{TOTAL} ({pct:.1f}%)\n"
            f"📦 룰북: {rb}줄\n"
            f"🧾 거래로그: {tr}줄\n"
            f"⏱ 경과: {elapsed:.1f}h"
        )
        if tp > 0 and rb == 0:
            msg += "\n⚠️ 경고: 룰북이 저장되지 않고 있음!"
        if tp > 0 and tr == 0:
            msg += "\n⚠️ 경고: 거래로그가 저장되지 않고 있음!"
        nt.send(msg)
        if tp >= TOTAL:
            nt.send(f"✅ LR-8C-FIX 완료! topn={tp}, 룰북={rb}줄, 거래로그={tr}줄")
            break
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
