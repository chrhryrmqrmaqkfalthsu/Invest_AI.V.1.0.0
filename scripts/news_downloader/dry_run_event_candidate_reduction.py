#!/usr/bin/env python3
"""
EVT-2C3: event-focused 후보 축약 dry-run

- 원본 daily + daily_event_focus variant를 merge/dedup한다.
- keyword_filter 후보를 event_type별 대표 후보로 축약한다.
- GPT/API 호출은 절대 하지 않는다.
- llm_news_cache.json은 읽기만 한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Iterable

from engine.market.av_adapter import av_item_to_article
from engine.market.colab_v32 import CONFIG, deduplicate_articles, keyword_filter

ROOT = Path(__file__).resolve().parents[2]
DAILY_DIR = ROOT / "data/_system/news_cache/daily"
EVENT_FOCUS_DIR = ROOT / "data/_system/news_cache/daily_event_focus"
DEFAULT_CAPPED_LIST = ROOT / "data/_system/research/evt2b_20260607/evt2b_capped_1000_dates.txt"
DEFAULT_CACHE_FILE = ROOT / CONFIG["LLM_CACHE_FILE"]
DEFAULT_REPORT_DIR = ROOT / "data/_system/research/evt2c3_20260607"

VARIANTS = [
    "relevance",
    "topic_earnings",
    "topic_financial_markets",
    "topic_economy_monetary",
    "topic_economy_macro",
    "topic_economy_fiscal",
]
MONETARY_FISCAL_VARIANTS = {"topic_economy_monetary", "topic_economy_fiscal"}
TRUSTED_SOURCE_PATTERNS = tuple(sorted(CONFIG.get("TRUSTED_SOURCES", [])))


@dataclass
class ReducedResult:
    selected: list[dict]
    selected_keys: set[str]
    cached_selected: int
    new_selected: int
    per_event_selected: Counter


def parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def read_dates(path: Path) -> list[date]:
    out: list[date] = []
    seen: set[date] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        d = parse_ymd(line)
        if d not in seen:
            out.append(d)
            seen.add(d)
    return sorted(out)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def av_item_key(item: dict) -> str:
    url = str(item.get("url", "") or "").strip()
    if url:
        return "url:" + url
    title = normalize_title(str(item.get("title", "") or ""))
    ts = str(item.get("time_published", "") or "")
    return "title:" + title + "|" + ts


def article_key(article: dict) -> str:
    url = str(article.get("url", "") or "").strip()
    if url:
        return url
    return normalize_title(str(article.get("title", "") or ""))


def normalize_title(title: str) -> str:
    text = title.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9가-힣\s]", " ", text)
    text = re.sub(r"\b[a-z]{1,5}\d?\b", " ", text)  # ticker-like short tokens 제거
    text = re.sub(r"\d+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def source_name(article: dict) -> str:
    src = article.get("source", {})
    if isinstance(src, dict):
        return str(src.get("name", "") or "")
    return str(src or "")


def is_trusted(article: dict) -> bool:
    src = source_name(article).lower()
    return any(pattern in src for pattern in TRUSTED_SOURCE_PATTERNS)


def article_hour_bucket(article: dict) -> str:
    raw = str(article.get("_time_published", "") or "")
    try:
        hour = int(raw[9:11])
    except Exception:
        return "unknown"
    if hour < 6:
        return "00-06"
    if hour < 12:
        return "06-12"
    if hour < 18:
        return "12-18"
    return "18-24"


def desc_len(article: dict) -> int:
    return len(str(article.get("description", "") or ""))


def cache_has_interpretation(cache: dict, article: dict) -> bool:
    key = article_key(article)
    item = cache.get(key)
    return isinstance(item, dict) and bool(item.get("interpretation"))


def load_feed_for_day(day: date, include_variants: Iterable[str] = VARIANTS) -> tuple[list[dict], list[str]]:
    feeds: list[dict] = []
    used_files: list[str] = []
    daily_path = DAILY_DIR / f"av_market_{day:%Y%m%d}.json"
    if daily_path.exists():
        data = load_json(daily_path)
        feed = data.get("feed", []) if isinstance(data.get("feed", []), list) else []
        feeds.extend(feed)
        used_files.append(str(daily_path.relative_to(ROOT)))
    for variant in include_variants:
        p = EVENT_FOCUS_DIR / f"av_market_{day:%Y%m%d}_{variant}.json"
        if not p.exists():
            continue
        data = load_json(p)
        feed = data.get("feed", []) if isinstance(data.get("feed", []), list) else []
        feeds.extend(feed)
        used_files.append(str(p.relative_to(ROOT)))
    by_key: dict[str, dict] = {}
    for item in feeds:
        by_key[av_item_key(item)] = item
    return list(by_key.values()), used_files


def candidates_for_feed(feed: list[dict]) -> tuple[list[dict], list[dict], int]:
    articles = [av_item_to_article(item) for item in feed]
    deduped = deduplicate_articles(articles)
    candidates, neg = keyword_filter(deduped)
    # 같은 URL/title이 여러 번 들어오면 event type union으로 병합
    merged: dict[str, dict] = {}
    for cand in candidates:
        art = cand["article"]
        key = article_key(art)
        if key not in merged:
            merged[key] = {"article": art, "matched_event_types": sorted(set(cand.get("matched_event_types", [])))}
        else:
            evs = set(merged[key]["matched_event_types"])
            evs.update(cand.get("matched_event_types", []))
            merged[key]["matched_event_types"] = sorted(evs)
    return list(merged.values()), neg, len(deduped)


def event_set(candidates: list[dict]) -> set[str]:
    out: set[str] = set()
    for cand in candidates:
        out.update(cand.get("matched_event_types", []))
    return out


def event_counts(candidates: list[dict]) -> Counter:
    cnt = Counter()
    for cand in candidates:
        for event in cand.get("matched_event_types", []):
            cnt[event] += 1
    return cnt


def candidate_score(cand: dict, cache: dict, source_counts: Counter, bucket_counts: Counter, title_cluster_counts: Counter) -> float:
    art = cand["article"]
    score = 0.0
    if cache_has_interpretation(cache, art):
        score += 6.0
    if is_trusted(art):
        score += 3.0
    score += min(len(cand.get("matched_event_types", [])), 3) * 1.0
    score += min(desc_len(art) / 350.0, 1.5)
    src = source_name(art).lower()
    bucket = article_hour_bucket(art)
    cluster = normalize_title(str(art.get("title", "") or ""))[:80]
    if source_counts[src] == 0:
        score += 1.0
    elif source_counts[src] == 1:
        score += 0.3
    else:
        score -= 1.0 * (source_counts[src] - 1)
    if bucket_counts[bucket] == 0:
        score += 0.8
    if title_cluster_counts[cluster] > 0:
        score -= 2.0 * title_cluster_counts[cluster]
    return score


def reduce_candidates(
    candidates: list[dict],
    cache: dict,
    max_new_per_event: int = 5,
    max_new_per_day: int = 60,
    max_cached_per_event: int = 5,
) -> ReducedResult:
    selected: list[dict] = []
    selected_keys: set[str] = set()
    per_event_new = Counter()
    per_event_cached = Counter()
    per_event_selected = Counter()
    source_counts = Counter()
    bucket_counts = Counter()
    title_cluster_counts = Counter()
    cached_selected = 0
    new_selected = 0

    def add(cand: dict, cached: bool) -> bool:
        nonlocal cached_selected, new_selected
        key = article_key(cand["article"])
        if key in selected_keys:
            return False
        selected.append(cand)
        selected_keys.add(key)
        src = source_name(cand["article"]).lower()
        source_counts[src] += 1
        bucket_counts[article_hour_bucket(cand["article"])] += 1
        title_cluster_counts[normalize_title(str(cand["article"].get("title", "") or ""))[:80]] += 1
        if cached:
            cached_selected += 1
            for event in cand.get("matched_event_types", []):
                per_event_cached[event] += 1
                per_event_selected[event] += 1
        else:
            new_selected += 1
            for event in cand.get("matched_event_types", []):
                per_event_new[event] += 1
                per_event_selected[event] += 1
        return True

    raw_events = event_set(candidates)
    cached = [c for c in candidates if cache_has_interpretation(cache, c["article"])]
    uncached = [c for c in candidates if not cache_has_interpretation(cache, c["article"])]

    # 1) 캐시 후보를 event별 quota 안에서 먼저 선택한다. 비용 0이지만 aggregation 폭증 방지를 위해 event별 cap은 둔다.
    for event in sorted(raw_events):
        pool = [c for c in cached if event in c.get("matched_event_types", [])]
        pool = sorted(pool, key=lambda c: candidate_score(c, cache, source_counts, bucket_counts, title_cluster_counts), reverse=True)
        for cand in pool:
            if per_event_cached[event] >= max_cached_per_event:
                break
            add(cand, cached=True)

    # 2) 신규 GPT 후보는 event별 quota와 day hard cap을 지킨다.
    for event in sorted(raw_events):
        pool = [c for c in uncached if event in c.get("matched_event_types", [])]
        while per_event_new[event] < max_new_per_event and new_selected < max_new_per_day:
            remaining = [c for c in pool if article_key(c["article"]) not in selected_keys]
            if not remaining:
                break
            best = max(
                remaining,
                key=lambda c: candidate_score(c, cache, source_counts, bucket_counts, title_cluster_counts),
            )
            add(best, cached=False)

    # 3) day cap이 남았고 아직 raw event category가 빠졌다면, 빠진 category당 최소 1개를 강제로 보강한다.
    reduced_events = event_set(selected)
    missing_events = sorted(raw_events - reduced_events)
    for event in missing_events:
        if new_selected >= max_new_per_day:
            break
        pool = [c for c in uncached if event in c.get("matched_event_types", []) and article_key(c["article"]) not in selected_keys]
        if not pool:
            continue
        best = max(pool, key=lambda c: candidate_score(c, cache, source_counts, bucket_counts, title_cluster_counts))
        add(best, cached=False)

    return ReducedResult(
        selected=selected,
        selected_keys=selected_keys,
        cached_selected=cached_selected,
        new_selected=new_selected,
        per_event_selected=per_event_selected,
    )


def pct(num: float, den: float) -> float:
    return 0.0 if den == 0 else (num / den * 100.0)


def estimate_cost(new_calls: int, input_tokens: int, output_tokens: int, input_per_million: float, output_per_million: float) -> float:
    return new_calls * ((input_tokens / 1_000_000) * input_per_million + (output_tokens / 1_000_000) * output_per_million)


def main() -> int:
    parser = argparse.ArgumentParser(description="EVT-2C3 candidate reduction dry-run")
    parser.add_argument("--capped-list", type=Path, default=DEFAULT_CAPPED_LIST)
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--max-new-per-event", type=int, default=5)
    parser.add_argument("--max-new-per-day", type=int, default=60)
    parser.add_argument("--max-cached-per-event", type=int, default=5)
    parser.add_argument("--write-reports", action="store_true")
    parser.add_argument("--input-tokens", type=int, default=900)
    parser.add_argument("--output-tokens", type=int, default=250)
    parser.add_argument("--input-price-per-million", type=float, default=0.15)
    parser.add_argument("--output-price-per-million", type=float, default=0.60)
    args = parser.parse_args()

    dates = read_dates(args.capped_list)
    cache = load_cache(args.cache_file)
    rows = []
    recall_loss_rows = []
    monetary_fiscal_rows = []

    totals = Counter()
    event_raw_total = Counter()
    event_reduced_total = Counter()
    sample_days = {date(2025, 10, 14), date(2026, 2, 27), date(2026, 6, 5)}
    sample_summaries = []

    for day in dates:
        feed_all, used_files = load_feed_for_day(day, VARIANTS)
        raw_candidates, neg, deduped_count = candidates_for_feed(feed_all)
        raw_events = event_set(raw_candidates)
        raw_counts = event_counts(raw_candidates)

        # monetary/fiscal contribution: 이 두 variant를 제거했을 때 사라지는 event category 측정
        feed_without_mf, _ = load_feed_for_day(day, [v for v in VARIANTS if v not in MONETARY_FISCAL_VARIANTS])
        cand_without_mf, _, _ = candidates_for_feed(feed_without_mf)
        events_without_mf = event_set(cand_without_mf)
        mf_added_events = raw_events - events_without_mf
        if mf_added_events:
            monetary_fiscal_rows.append((day, sorted(mf_added_events)))

        result = reduce_candidates(
            raw_candidates,
            cache,
            max_new_per_event=args.max_new_per_event,
            max_new_per_day=args.max_new_per_day,
            max_cached_per_event=args.max_cached_per_event,
        )
        reduced_events = event_set(result.selected)
        reduced_counts = event_counts(result.selected)
        lost_events = sorted(raw_events - reduced_events)
        if lost_events:
            recall_loss_rows.append((day, lost_events, sorted(raw_events), sorted(reduced_events)))

        cached_candidates_total = sum(1 for c in raw_candidates if cache_has_interpretation(cache, c["article"]))
        new_candidates_total = len(raw_candidates) - cached_candidates_total

        rows.append({
            "date": day,
            "feed_unique": len(feed_all),
            "deduped_articles": deduped_count,
            "negation_filtered": len(neg),
            "raw_candidates": len(raw_candidates),
            "raw_events_count": len(raw_events),
            "reduced_candidates": len(result.selected),
            "reduced_events_count": len(reduced_events),
            "cached_candidates_total": cached_candidates_total,
            "new_candidates_total": new_candidates_total,
            "cached_selected": result.cached_selected,
            "new_selected": result.new_selected,
            "reduction_pct": pct(len(raw_candidates) - len(result.selected), len(raw_candidates)),
            "new_reduction_pct": pct(new_candidates_total - result.new_selected, new_candidates_total),
            "lost_events": ",".join(lost_events),
            "raw_events": ",".join(sorted(raw_events)),
            "reduced_events": ",".join(sorted(reduced_events)),
            "mf_added_events": ",".join(sorted(mf_added_events)),
            "used_files": len(used_files),
        })

        totals["feed_unique"] += len(feed_all)
        totals["deduped_articles"] += deduped_count
        totals["raw_candidates"] += len(raw_candidates)
        totals["reduced_candidates"] += len(result.selected)
        totals["cached_candidates_total"] += cached_candidates_total
        totals["new_candidates_total"] += new_candidates_total
        totals["cached_selected"] += result.cached_selected
        totals["new_selected"] += result.new_selected
        totals["negation_filtered"] += len(neg)
        for event, count in raw_counts.items():
            event_raw_total[event] += count
        for event, count in reduced_counts.items():
            event_reduced_total[event] += count

        if day in sample_days:
            sample_summaries.append({
                "date": day,
                "feed_unique": len(feed_all),
                "deduped_articles": deduped_count,
                "raw_candidates": len(raw_candidates),
                "reduced_candidates": len(result.selected),
                "cached_selected": result.cached_selected,
                "new_selected": result.new_selected,
                "raw_events": raw_counts,
                "reduced_events": reduced_counts,
                "lost_events": lost_events,
                "mf_added_events": sorted(mf_added_events),
            })

    total_raw = totals["raw_candidates"]
    total_reduced = totals["reduced_candidates"]
    total_new = totals["new_candidates_total"]
    total_new_selected = totals["new_selected"]
    estimated_cost = estimate_cost(
        total_new_selected,
        args.input_tokens,
        args.output_tokens,
        args.input_price_per_million,
        args.output_price_per_million,
    )

    print("=== EVT-2C3 candidate reduction dry-run ===")
    print(f"dates={len(dates)}")
    print(f"cache_file={args.cache_file}")
    print(f"cache_entries={len(cache)}")
    print(f"max_new_per_event={args.max_new_per_event}")
    print(f"max_new_per_day={args.max_new_per_day}")
    print(f"max_cached_per_event={args.max_cached_per_event}")
    print(f"total_feed_unique={totals['feed_unique']}")
    print(f"total_deduped_articles={totals['deduped_articles']}")
    print(f"total_raw_candidates={total_raw}")
    print(f"total_reduced_candidates={total_reduced}")
    print(f"overall_reduction_pct={pct(total_raw-total_reduced,total_raw):.2f}")
    print(f"total_cached_candidates={totals['cached_candidates_total']}")
    print(f"total_new_candidates={total_new}")
    print(f"selected_cached_candidates={totals['cached_selected']}")
    print(f"estimated_new_gpt_calls={total_new_selected}")
    print(f"new_gpt_reduction_pct={pct(total_new-total_new_selected,total_new):.2f}")
    print(f"recall_loss_days={len(recall_loss_rows)}")
    print(f"monetary_fiscal_added_category_days={len(monetary_fiscal_rows)}")
    print(f"estimated_cost_usd_model_assumption={estimated_cost:.4f}")
    print("event_raw_total:")
    for event, count in event_raw_total.most_common():
        print(f"  {event}: {count}")
    print("event_reduced_total:")
    for event, count in event_reduced_total.most_common():
        print(f"  {event}: {count}")
    print("sample_days:")
    for sample in sample_summaries:
        print(
            f"  {sample['date']} raw={sample['raw_candidates']} reduced={sample['reduced_candidates']} "
            f"cached_selected={sample['cached_selected']} new_selected={sample['new_selected']} "
            f"lost={sample['lost_events']} mf_added={sample['mf_added_events']}"
        )
        print(f"    raw_events={sample['raw_events'].most_common()}")
        print(f"    reduced_events={sample['reduced_events'].most_common()}")
    print("=== END DRY RUN ===")

    if args.write_reports:
        args.report_dir.mkdir(parents=True, exist_ok=True)
        per_day = args.report_dir / "evt2c3_reduction_per_day.txt"
        recall = args.report_dir / "evt2c3_recall_loss_days.txt"
        per_day.write_text(
            "date\tfeed_unique\tdeduped_articles\tnegation_filtered\traw_candidates\treduced_candidates\t"
            "raw_events_count\treduced_events_count\tcached_candidates_total\tnew_candidates_total\t"
            "cached_selected\tnew_selected\treduction_pct\tnew_reduction_pct\tlost_events\tmf_added_events\traw_events\treduced_events\n"
            + "\n".join(
                "\t".join([
                    r["date"].isoformat(),
                    str(r["feed_unique"]),
                    str(r["deduped_articles"]),
                    str(r["negation_filtered"]),
                    str(r["raw_candidates"]),
                    str(r["reduced_candidates"]),
                    str(r["raw_events_count"]),
                    str(r["reduced_events_count"]),
                    str(r["cached_candidates_total"]),
                    str(r["new_candidates_total"]),
                    str(r["cached_selected"]),
                    str(r["new_selected"]),
                    f"{r['reduction_pct']:.2f}",
                    f"{r['new_reduction_pct']:.2f}",
                    r["lost_events"],
                    r["mf_added_events"],
                    r["raw_events"],
                    r["reduced_events"],
                ])
                for r in rows
            )
            + "\n",
            encoding="utf-8",
        )
        recall.write_text(
            "date\tlost_events\traw_events\treduced_events\n"
            + "\n".join(
                f"{day.isoformat()}\t{','.join(lost)}\t{','.join(raw)}\t{','.join(red)}"
                for day, lost, raw, red in recall_loss_rows
            )
            + ("\n" if recall_loss_rows else ""),
            encoding="utf-8",
        )
        summary = {
            "dates": len(dates),
            "cache_entries": len(cache),
            "total_feed_unique": totals["feed_unique"],
            "total_deduped_articles": totals["deduped_articles"],
            "total_raw_candidates": total_raw,
            "total_reduced_candidates": total_reduced,
            "overall_reduction_pct": pct(total_raw-total_reduced,total_raw),
            "total_cached_candidates": totals["cached_candidates_total"],
            "total_new_candidates": total_new,
            "selected_cached_candidates": totals["cached_selected"],
            "estimated_new_gpt_calls": total_new_selected,
            "new_gpt_reduction_pct": pct(total_new-total_new_selected,total_new),
            "recall_loss_days": len(recall_loss_rows),
            "monetary_fiscal_added_category_days": len(monetary_fiscal_rows),
            "estimated_cost_usd_model_assumption": estimated_cost,
            "event_raw_total": dict(event_raw_total),
            "event_reduced_total": dict(event_reduced_total),
            "sample_summaries": [
                {
                    "date": s["date"].isoformat(),
                    "feed_unique": s["feed_unique"],
                    "deduped_articles": s["deduped_articles"],
                    "raw_candidates": s["raw_candidates"],
                    "reduced_candidates": s["reduced_candidates"],
                    "cached_selected": s["cached_selected"],
                    "new_selected": s["new_selected"],
                    "raw_events": dict(s["raw_events"]),
                    "reduced_events": dict(s["reduced_events"]),
                    "lost_events": s["lost_events"],
                    "mf_added_events": s["mf_added_events"],
                }
                for s in sample_summaries
            ],
        }
        (args.report_dir / "evt2c3_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
