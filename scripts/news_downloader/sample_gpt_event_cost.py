#!/usr/bin/env python3
"""
EVT-2C4-COST: 이벤트 GPT 해석 단가/안정성 샘플 측정

- 후보 10~20건만 실제 GPT 호출한다.
- market_history_v2.csv는 절대 쓰지 않는다.
- llm_news_cache.json은 --write-cache일 때만, 호출 1건 성공마다 즉시 저장한다.
- 사용량(response.usage), 소요시간, cache hit 재실행 여부를 기록한다.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean, median

from openai import OpenAI

from engine.market.colab_v32 import CONFIG
from scripts.news_downloader.dry_run_event_candidate_reduction import (
    VARIANTS,
    load_cache,
    load_feed_for_day,
    candidates_for_feed,
    reduce_candidates,
    cache_has_interpretation,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_FILE = ROOT / CONFIG["LLM_CACHE_FILE"]
DEFAULT_OUT = ROOT / "data/_system/research/evt2c4cost_20260607/evt2c4cost_sample_metrics.json"
DEFAULT_CANDIDATE_DATES = [date(2025, 10, 14), date(2026, 2, 27), date(2026, 6, 5)]

# 2026-06-07 OpenAI official pricing page snapshot used by report.
# Current live pricing should still be checked before full run.
FALLBACK_MODEL_PRICES_PER_MILLION = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


def article_cache_key(article: dict) -> str:
    url = article.get("url", "")
    return url if url else article.get("title", "")[:100]


def build_messages(article: dict, matched_events: list[str]) -> list[dict]:
    title = article.get('title', '')
    desc = article.get('description', '') or ''
    source = article.get('source', {}).get('name', 'Unknown')

    system_msg = """당신은 미국 주식 시장 분석 전문가입니다. 
주어진 뉴스가 S&P500 전체에 미치는 실제 영향을 객관적으로 분석합니다.
반드시 매크로 경제 상식에 기반해서 판단하며, JSON 형식으로만 응답합니다."""

    user_msg = f"""[뉴스]
제목: {title}
요약: {desc}
출처: {source}
키워드 매칭된 이벤트 후보: {', '.join(matched_events)}

[필수 매크로 경제 상식]
1. 금리 인상 → 할인율↑ → 미래현금흐름가치↓ → **S&P500 악재 (-5 ~ -8)**
2. 금리 인하 → 유동성↑ → 위험자산 선호 → **S&P500 호재 (+5 ~ +8)**
3. 관세 부과 → 마진↓ → 시장 악재
4. 전쟁 발발 → 불확실성↑ → 시장 악재 (방산주만 별도 호재)
5. 수출규제 (반도체) → 해당 섹터 큰 악재
6. 유가 급등 → 인플레 압력↑ → 시장 악재 (에너지주만 호재)
7. 지정학적 긴장 → 불확실성↑ → 시장 악재

[신뢰도 기준]
- 확정: Fed 공식 발표, 실제 발생, 정부 결정
- 예상: 시장 컨센서스, 트레이더 베팅, 주요 매체 다수 보도
- 추측: 단일 애널리스트 의견, 추측성 기사
- 루머: 미확인 정보

[시점 기준]
- 즉시: 오늘~며칠 내 영향
- 단기: 1~2주 영향
- 중기: 1~3개월 영향
- 장기: 6개월 이상 영향

[주의사항]
- impact_score는 S&P500 전체 영향 기준 (-10 ~ +10)
- 부정 문맥("rate hike unlikely", "war averted") → is_real_event=false
- 과거 회고 기사 → is_real_event=false
- 비유적 표현("trade war between siblings") → is_real_event=false
- 트레이더 베팅 단계 → confidence=예상
- affected_sectors는 한국어로: ["반도체","기술","방산","에너지","금융","헬스케어","소비재"] 중 해당되는 것

다음 JSON 형식으로만 응답:

{{
  "is_real_event": true 또는 false,
  "event_type": "전쟁|수출규제|관세|금리정책_인상|금리정책_인하|지정학_긴장|유가급등|실적쇼크|은행위기|인플레이션|연준발언|기타|해당없음",
  "market_impact": "강한_호재|호재|중립|악재|강한_악재",
  "impact_score": -10에서 +10 사이 정수,
  "confidence": "확정|예상|추측|루머",
  "timeframe": "즉시|단기|중기|장기",
  "affected_sectors": ["..."],
  "reasoning": "한 문장 근거 (S&P500 기준)"
}}"""
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def choose_sample_candidates(cache: dict, sample_size: int, max_new_per_event: int, max_new_per_day: int, max_cached_per_event: int) -> list[dict]:
    pool: list[dict] = []
    seen_keys = set()
    # 이벤트 다양성과 폭주일/최신일을 섞기 위해 C3 대표 샘플 날짜를 먼저 사용한다.
    for d in DEFAULT_CANDIDATE_DATES:
        feed, _ = load_feed_for_day(d, VARIANTS)
        raw_candidates, _, _ = candidates_for_feed(feed)
        reduced = reduce_candidates(
            raw_candidates,
            cache,
            max_new_per_event=max_new_per_event,
            max_new_per_day=max_new_per_day,
            max_cached_per_event=max_cached_per_event,
        )
        for cand in reduced.selected:
            key = article_cache_key(cand["article"])
            if key in seen_keys:
                continue
            if cache_has_interpretation(cache, cand["article"]):
                continue
            pool.append(cand)
            seen_keys.add(key)
            if len(pool) >= sample_size:
                return pool
    return pool[:sample_size]


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int, explicit_input_price: float | None, explicit_output_price: float | None) -> float | None:
    if explicit_input_price is not None and explicit_output_price is not None:
        input_price = explicit_input_price
        output_price = explicit_output_price
    else:
        price = FALLBACK_MODEL_PRICES_PER_MILLION.get(model)
        if not price:
            return None
        input_price = price["input"]
        output_price = price["output"]
    return (prompt_tokens / 1_000_000) * input_price + (completion_tokens / 1_000_000) * output_price


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="EVT-2C4-COST GPT event interpretation sample")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--max-new-per-event", type=int, default=3)
    parser.add_argument("--max-new-per-day", type=int, default=40)
    parser.add_argument("--max-cached-per-event", type=int, default=3)
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--write-cache", action="store_true")
    parser.add_argument("--sleep-sec", type=float, default=0.2)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--input-price-per-million", type=float, default=None)
    parser.add_argument("--output-price-per-million", type=float, default=None)
    args = parser.parse_args()

    if args.sample_size <= 0 or args.sample_size > 20:
        raise SystemExit("❌ --sample-size는 1~20 범위만 허용합니다")

    cache = load_cache(args.cache_file)
    cache_before = len(cache)
    model = CONFIG["LLM_MODEL"]
    client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])
    samples = choose_sample_candidates(cache, args.sample_size, args.max_new_per_event, args.max_new_per_day, args.max_cached_per_event)

    results = []
    counters = Counter()
    started = time.time()

    for idx, cand in enumerate(samples, start=1):
        article = cand["article"]
        matched = cand.get("matched_event_types", [])
        key = article_cache_key(article)
        if key in cache and cache[key].get("interpretation"):
            counters["cache_hit_initial"] += 1
            continue
        messages = build_messages(article, matched)
        attempt = 0
        last_error = None
        while attempt <= args.max_retries:
            attempt += 1
            t0 = time.time()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=500,
                    temperature=0.2,
                    timeout=CONFIG["LLM_TIMEOUT"],
                )
                elapsed = time.time() - t0
                content = response.choices[0].message.content.strip()
                interp = json.loads(content)
                usage = response.usage
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
                cost = estimate_cost(model, prompt_tokens, completion_tokens, args.input_price_per_million, args.output_price_per_million)
                if args.write_cache:
                    cache[key] = {
                        "cached_at": __import__("datetime").datetime.now().isoformat(),
                        "interpretation": interp,
                    }
                    save_cache(args.cache_file, cache)
                results.append({
                    "idx": idx,
                    "attempts": attempt,
                    "elapsed_sec": elapsed,
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "estimated_cost_usd": cost,
                    "cache_key": key[:160],
                    "title": article.get("title", "")[:240],
                    "source": article.get("source", {}).get("name", ""),
                    "matched_event_types": matched,
                    "interpretation": interp,
                })
                counters["success"] += 1
                break
            except Exception as exc:
                elapsed = time.time() - t0
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                counters["retry_or_fail"] += 1
                if attempt > args.max_retries:
                    counters["failed"] += 1
                    results.append({
                        "idx": idx,
                        "attempts": attempt,
                        "elapsed_sec": elapsed,
                        "model": model,
                        "error": last_error,
                        "title": article.get("title", "")[:240],
                        "source": article.get("source", {}).get("name", ""),
                        "matched_event_types": matched,
                    })
                    break
                time.sleep(2 * attempt)
        time.sleep(args.sleep_sec)

    cache_after = len(load_cache(args.cache_file))
    successes = [r for r in results if "error" not in r]
    prompt_tokens = [r["prompt_tokens"] for r in successes]
    completion_tokens = [r["completion_tokens"] for r in successes]
    total_tokens = [r["total_tokens"] for r in successes]
    elapsed_sec = [r["elapsed_sec"] for r in successes]
    costs = [r["estimated_cost_usd"] for r in successes if r.get("estimated_cost_usd") is not None]

    per_call_avg_cost = mean(costs) if costs else None
    estimated_full_calls = 4911
    estimated_full_cost = per_call_avg_cost * estimated_full_calls if per_call_avg_cost is not None else None
    estimated_full_time_min = (median(elapsed_sec) * estimated_full_calls / 60.0) if elapsed_sec else None

    summary = {
        "model": model,
        "sample_requested": args.sample_size,
        "sample_candidates_available": len(samples),
        "success": counters["success"],
        "failed": counters["failed"],
        "cache_before": cache_before,
        "cache_after": cache_after,
        "cache_added": cache_after - cache_before,
        "write_cache": args.write_cache,
        "elapsed_total_sec": time.time() - started,
        "avg_prompt_tokens": mean(prompt_tokens) if prompt_tokens else None,
        "median_prompt_tokens": median(prompt_tokens) if prompt_tokens else None,
        "avg_completion_tokens": mean(completion_tokens) if completion_tokens else None,
        "median_completion_tokens": median(completion_tokens) if completion_tokens else None,
        "avg_total_tokens": mean(total_tokens) if total_tokens else None,
        "median_total_tokens": median(total_tokens) if total_tokens else None,
        "avg_elapsed_sec": mean(elapsed_sec) if elapsed_sec else None,
        "median_elapsed_sec": median(elapsed_sec) if elapsed_sec else None,
        "avg_estimated_cost_usd": per_call_avg_cost,
        "estimated_full_calls": estimated_full_calls,
        "estimated_full_cost_usd": estimated_full_cost,
        "estimated_full_time_min_median_latency": estimated_full_time_min,
        "price_source": "explicit args if provided, else fallback gpt-4o-mini $0.15/M input and $0.60/M output",
    }
    payload = {"summary": summary, "results": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== EVT-2C4-COST sample ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"metrics_out={args.out}")
    print("=== END SAMPLE ===")
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
