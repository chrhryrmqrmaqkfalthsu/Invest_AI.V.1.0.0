"""
종목별 뉴스 sentiment 일별 집계
- 입력: data/_system/ticker_news_cache/{TICKER}/av_{TICKER}_YYYYMM.json.gz
- 출력: data/_system/ticker_sentiment/{TICKER}_daily.csv

집계 컬럼:
  date            : YYYY-MM-DD
  news_count      : 그 날 해당 종목 관련 기사 수
  sentiment_avg   : ticker_sentiment_score 가중 평균 (-1 ~ +1)
  sentiment_std   : 표준편차
  bullish_ratio   : Bullish + Somewhat-Bullish 비율 (0 ~ 1)
  bearish_ratio   : Bearish + Somewhat-Bearish 비율
  relevance_avg   : relevance_score 평균 (얼마나 그 종목과 관련 깊은지)
  high_rel_count  : relevance >= 0.5 기사 수

사용:
  python3 -m engine.market.ticker_sentiment --ticker NVDA          # 한 종목
  python3 -m engine.market.ticker_sentiment --all                  # 다운로드된 전 종목
  python3 -m engine.market.ticker_sentiment --resume               # 이어하기
"""
import gzip
import json
import argparse
import sys
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict
import statistics

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data/_system/ticker_news_cache"
OUTPUT_DIR = ROOT / "data/_system/ticker_sentiment"


def parse_av_date(time_published: str):
    """20240602T143000 → date(2024,6,2)"""
    try:
        return datetime.strptime(time_published[:8], "%Y%m%d").date()
    except Exception:
        return None


def extract_ticker_data(item: dict, ticker: str):
    """
    한 기사에서 특정 ticker에 대한 sentiment + relevance 추출
    Returns: (sentiment_score, relevance_score, label) or None
    """
    for ts in item.get("ticker_sentiment", []):
        if ts.get("ticker") == ticker:
            try:
                s = float(ts.get("ticker_sentiment_score", 0))
                r = float(ts.get("relevance_score", 0))
                label = ts.get("ticker_sentiment_label", "")
                return s, r, label
            except (ValueError, TypeError):
                return None
    return None


def aggregate_ticker(ticker: str, verbose: bool = False):
    """
    한 종목의 모든 월별 캐시 파일 → 일별 집계 CSV
    Returns: rows (list of dicts) or None if no data
    """
    ticker_dir = CACHE_DIR / ticker
    if not ticker_dir.exists():
        if verbose:
            print(f"  ❌ {ticker}: 캐시 디렉토리 없음")
        return None

    monthly_files = sorted(ticker_dir.glob(f"av_{ticker}_*.json.gz"))
    if not monthly_files:
        if verbose:
            print(f"  ❌ {ticker}: 캐시 파일 없음")
        return None

    # 일별로 그룹화
    daily = defaultdict(list)  # date -> [(sentiment, relevance, label), ...]

    for mf in monthly_files:
        try:
            with gzip.open(mf, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            if verbose:
                print(f"  ⚠️ {mf.name} 로드 실패: {e}")
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
            print(f"  ⚠️ {ticker}: 집계 가능한 데이터 없음")
        return []

    # 일별 통계 계산
    rows = []
    for d in sorted(daily.keys()):
        entries = daily[d]
        n = len(entries)

        # relevance 가중 평균 sentiment (relevance가 높을수록 영향력 큼)
        total_weight = sum(r for _, r, _ in entries)
        if total_weight > 0:
            sent_avg = sum(s * r for s, r, _ in entries) / total_weight
        else:
            sent_avg = sum(s for s, _, _ in entries) / n

        # 표준편차 (sentiment만)
        sents = [s for s, _, _ in entries]
        sent_std = statistics.stdev(sents) if n > 1 else 0.0

        # bullish/bearish 비율
        bullish = sum(1 for _, _, lbl in entries if "Bullish" in lbl)
        bearish = sum(1 for _, _, lbl in entries if "Bearish" in lbl)

        # relevance
        rels = [r for _, r, _ in entries]
        rel_avg = sum(rels) / n
        high_rel = sum(1 for r in rels if r >= 0.5)

        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "news_count": n,
            "sentiment_avg": round(sent_avg, 4),
            "sentiment_std": round(sent_std, 4),
            "bullish_ratio": round(bullish / n, 3),
            "bearish_ratio": round(bearish / n, 3),
            "relevance_avg": round(rel_avg, 3),
            "high_rel_count": high_rel,
        })

    return rows


def save_csv(ticker: str, rows: list):
    """일별 행을 CSV로 저장 (pandas 없이, 메모리 절약)"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{ticker}_daily.csv"

    if not rows:
        # 빈 파일이라도 생성 (재처리 방지)
        path.write_text("date,news_count,sentiment_avg,sentiment_std,bullish_ratio,bearish_ratio,relevance_avg,high_rel_count\n")
        return path

    cols = ["date", "news_count", "sentiment_avg", "sentiment_std",
            "bullish_ratio", "bearish_ratio", "relevance_avg", "high_rel_count"]
    with open(path, "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in cols) + "\n")
    return path


def load_csv(ticker: str):
    """저장된 CSV를 dict로 로드 (backtest.py에서 사용)
    Returns: {date_str: {sentiment_avg, news_count, ...}} or None
    """
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
            d = row["date"]
            # 숫자 변환
            try:
                result[d] = {
                    "news_count": int(row["news_count"]),
                    "sentiment_avg": float(row["sentiment_avg"]),
                    "sentiment_std": float(row["sentiment_std"]),
                    "bullish_ratio": float(row["bullish_ratio"]),
                    "bearish_ratio": float(row["bearish_ratio"]),
                    "relevance_avg": float(row["relevance_avg"]),
                    "high_rel_count": int(row["high_rel_count"]),
                }
            except (ValueError, KeyError):
                continue
    return result


def process_all(tickers=None, resume=True, verbose=False):
    """다수 종목 일괄 처리"""
    if tickers is None:
        # 다운로드된 종목 자동 발견
        tickers = sorted([d.name for d in CACHE_DIR.iterdir() if d.is_dir()])

    print(f"=== 종목별 sentiment 집계: {len(tickers)}종 ===")

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
                if verbose or i % 100 == 0 or i == len(tickers):
                    elapsed = (datetime.now() - started).total_seconds()
                    rate = (done + skip) / max(elapsed, 1)
                    eta = (len(tickers) - i) / max(rate, 0.1)
                    print(f"  [{i}/{len(tickers)}] {t}: {len(rows)}일 | done={done} skip={skip} | ETA={eta/60:.1f}분")
            else:
                empty += 1
        except Exception as e:
            print(f"  ❌ {t}: {type(e).__name__}: {e}")
            failed += 1

    elapsed = (datetime.now() - started).total_seconds()
    print(f"\n=== 완료: 처리 {done}, skip {skip}, 빈 데이터 {empty}, 실패 {failed} ===")
    print(f"소요 시간: {elapsed/60:.1f}분")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", help="단일 종목 처리")
    ap.add_argument("--all", action="store_true", help="다운로드된 전 종목")
    ap.add_argument("--resume", action="store_true", help="기존 CSV는 skip")
    ap.add_argument("--force", action="store_true", help="기존 CSV 무시하고 재처리")
    ap.add_argument("--verbose", action="store_true", help="상세 로그")
    args = ap.parse_args()

    if args.ticker:
        print(f"=== {args.ticker} 집계 ===")
        rows = aggregate_ticker(args.ticker, verbose=True)
        if rows is None:
            print("실패")
            sys.exit(1)
        path = save_csv(args.ticker, rows)
        print(f"✅ {path}")
        print(f"  행수: {len(rows)}")
        if rows:
            print(f"  기간: {rows[0]['date']} ~ {rows[-1]['date']}")
            sents = [r["sentiment_avg"] for r in rows]
            ncounts = [r["news_count"] for r in rows]
            print(f"  sentiment 범위: {min(sents):.3f} ~ {max(sents):.3f}, 평균 {sum(sents)/len(sents):.3f}")
            print(f"  뉴스/일: 평균 {sum(ncounts)/len(ncounts):.1f}, 최대 {max(ncounts)}")
            print(f"\n  샘플 (최근 5일):")
            for r in rows[-5:]:
                print(f"    {r['date']} | {r['news_count']:3d}건 | sent={r['sentiment_avg']:+.3f} | bull={r['bullish_ratio']:.2f} bear={r['bearish_ratio']:.2f}")
    elif args.all:
        process_all(resume=not args.force, verbose=args.verbose)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
