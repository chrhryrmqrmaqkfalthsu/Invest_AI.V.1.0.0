"""ticker_sentiment v6: 토픽별 감성 분리 집계."""
import gzip
import json
import argparse
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import statistics

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data/_system/ticker_news_cache"
OUTPUT_DIR = ROOT / "data/_system/ticker_sentiment"

AV_TOPICS = [
    "blockchain", "earnings", "ipo", "mergers_and_acquisitions",
    "financial_markets", "economy_fiscal", "economy_monetary",
    "economy_macro", "energy_transportation", "finance",
    "life_sciences", "manufacturing", "real_estate",
    "retail_wholesale", "technology",
]
TOPIC_SET = set(AV_TOPICS)
SENT_COLS = [f"sent_{t}" for t in AV_TOPICS]
CNT_COLS = [f"cnt_{t}" for t in AV_TOPICS]
BASE_COLS = [
    "date", "news_count", "sentiment_avg", "sentiment_std",
    "bullish_ratio", "bearish_ratio", "relevance_avg", "high_rel_count",
]
ALL_COLS = BASE_COLS + SENT_COLS + CNT_COLS


def parse_av_date(time_published: str):
    try:
        return datetime.strptime(time_published[:8], "%Y%m%d").date()
    except Exception:
        return None


def extract_ticker_data(item: dict, ticker: str):
    found = None
    for ts in item.get("ticker_sentiment", []):
        if ts.get("ticker") == ticker:
            try:
                s = float(ts.get("ticker_sentiment_score", 0))
                r = float(ts.get("relevance_score", 0))
                label = ts.get("ticker_sentiment_label", "")
            except (ValueError, TypeError):
                return None
            found = (s, r, label)
            break
    if found is None:
        return None
    topics = []
    for tp in item.get("topics", []):
        name = tp.get("topic", "")
        if name not in TOPIC_SET:
            continue
        try:
            tr = float(tp.get("relevance_score", 0))
        except (ValueError, TypeError):
            tr = 0.0
        topics.append((name, tr))
    s, r, label = found
    return s, r, label, topics


def aggregate_ticker(ticker: str, verbose: bool = False):
    ticker_dir = CACHE_DIR / ticker
    if not ticker_dir.exists():
        if verbose:
            print(f"  X {ticker}: 캐시 디렉토리 없음")
        return None
    monthly_files = sorted(ticker_dir.glob(f"av_{ticker}_*.json.gz"))
    if not monthly_files:
        if verbose:
            print(f"  X {ticker}: 캐시 파일 없음")
        return None

    daily = defaultdict(list)
    for mf in monthly_files:
        try:
            with gzip.open(mf, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            if verbose:
                print(f"  ! {mf.name} 로드 실패: {e}")
            continue
        for item in data.get("feed", []):
            d = parse_av_date(item.get("time_published", ""))
            if d is None:
                continue
            tdata = extract_ticker_data(item, ticker)
            if tdata is None:
                continue
            daily[d].append(tdata)

    if not daily:
        if verbose:
            print(f"  ! {ticker}: 집계 가능한 데이터 없음")
        return []

    rows = []
    for d in sorted(daily.keys()):
        entries = daily[d]
        n = len(entries)
        total_weight = sum(r for _, r, _, _ in entries)
        if total_weight > 0:
            sent_avg = sum(s * r for s, r, _, _ in entries) / total_weight
        else:
            sent_avg = sum(s for s, _, _, _ in entries) / n
        sents = [s for s, _, _, _ in entries]
        sent_std = statistics.stdev(sents) if n > 1 else 0.0
        bullish = sum(1 for _, _, lbl, _ in entries if "Bullish" in lbl)
        bearish = sum(1 for _, _, lbl, _ in entries if "Bearish" in lbl)
        rels = [r for _, r, _, _ in entries]
        rel_avg = sum(rels) / n
        high_rel = sum(1 for r in rels if r >= 0.5)

        topic_num = {t: 0.0 for t in AV_TOPICS}
        topic_den = {t: 0.0 for t in AV_TOPICS}
        topic_cnt = {t: 0 for t in AV_TOPICS}
        for s, r, _, topics in entries:
            for name, tr in topics:
                w = r * tr
                if w <= 0:
                    continue
                topic_num[name] += s * w
                topic_den[name] += w
                topic_cnt[name] += 1

        row = {
            "date": d.strftime("%Y-%m-%d"),
            "news_count": n,
            "sentiment_avg": round(sent_avg, 4),
            "sentiment_std": round(sent_std, 4),
            "bullish_ratio": round(bullish / n, 3),
            "bearish_ratio": round(bearish / n, 3),
            "relevance_avg": round(rel_avg, 3),
            "high_rel_count": high_rel,
        }
        for t in AV_TOPICS:
            row[f"sent_{t}"] = round(topic_num[t] / topic_den[t], 4) if topic_den[t] > 0 else 0.0
            row[f"cnt_{t}"] = topic_cnt[t]
        rows.append(row)
    return rows


def save_csv(ticker: str, rows: list):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{ticker}_daily.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(ALL_COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in ALL_COLS) + "\n")
    return path


def load_csv(ticker: str):
    path = OUTPUT_DIR / f"{ticker}_daily.csv"
    if not path.exists():
        return None
    result = {}
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) != len(header):
                continue
            row = dict(zip(header, parts))
            d = row.get("date")
            if not d:
                continue
            rec = {}
            for c in header:
                if c == "date":
                    continue
                v = row.get(c, "")
                try:
                    if c.startswith("cnt_") or c in ("news_count", "high_rel_count"):
                        rec[c] = int(v)
                    else:
                        rec[c] = float(v)
                except (ValueError, TypeError):
                    rec[c] = 0
            result[d] = rec
    return result


def process_all(tickers=None, resume=True, verbose=False):
    if tickers is None:
        tickers = sorted([d.name for d in CACHE_DIR.iterdir() if d.is_dir()])
    print(f"=== sentiment 집계 v6: {len(tickers)}종 ===")
    done = skip = empty = failed = 0
    started = datetime.now()
    for i, t in enumerate(tickers, 1):
        out = OUTPUT_DIR / f"{t}_daily.csv"
        if resume and out.exists() and out.stat().st_size > 50:
            skip += 1
            continue
        try:
            rows = aggregate_ticker(t, verbose=verbose)
            if rows is None:
                failed += 1
                continue
            save_csv(t, rows)
            if rows:
                done += 1
                if verbose or i % 50 == 0 or i == len(tickers):
                    print(f"  [{i}/{len(tickers)}] {t}: {len(rows)}일 | done={done} skip={skip}")
            else:
                empty += 1
        except Exception as e:
            print(f"  X {t}: {type(e).__name__}: {e}")
            failed += 1
    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n=== 완료: 처리 {done}, skip {skip}, 빈데이터 {empty}, 실패 {failed} ({elapsed/60:.1f}분) ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.ticker:
        print(f"=== {args.ticker} 집계 v6 ===")
        rows = aggregate_ticker(args.ticker, verbose=True)
        if rows is None:
            print("실패: 캐시 없음")
            sys.exit(1)
        path = save_csv(args.ticker, rows)
        print(f"OK {path}  ({len(rows)}일)")
        if rows:
            print(f"  기간: {rows[0]['date']} ~ {rows[-1]['date']}")
            tot = {t: 0 for t in AV_TOPICS}
            for r in rows:
                for t in AV_TOPICS:
                    tot[t] += r[f"cnt_{t}"]
            print("  토픽별 누적 기사수:")
            for t in sorted(AV_TOPICS, key=lambda x: -tot[x]):
                if tot[t] > 0:
                    print(f"    {t:28s} {tot[t]:6d}")
            print("  샘플 (최근 3일):")
            for r in rows[-3:]:
                tops = [(t, r[f'sent_{t}'], r[f'cnt_{t}']) for t in AV_TOPICS if r[f'cnt_{t}'] > 0]
                tops = sorted(tops, key=lambda x: -x[2])[:4]
                ttxt = " ".join(f"{t}={s:+.2f}({c})" for t, s, c in tops)
                print(f"    {r['date']} | {r['news_count']:3d}건 | avg={r['sentiment_avg']:+.3f} | {ttxt}")
    elif args.all:
        process_all(resume=not args.force, verbose=args.verbose)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
