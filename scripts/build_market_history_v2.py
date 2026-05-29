"""
21개월 알파밴티지 캐시 → 일별 시장 분석 시계열 빌드
- 각 날짜에 콜랩 v3.2 Layer 2~4 적용
- 결과: data/_system/market_history_v2.csv
- GPT 호출 결과 영구 캐시 (재실행 시 비용 0)
"""
import sys
sys.path.insert(0, '.')

import json
import time
import pandas as pd
from pathlib import Path
from datetime import datetime
from openai import OpenAI

from engine.market.av_adapter import load_articles_by_date, list_available_dates, load_articles_for_date
from engine.market.colab_v32 import (
    CONFIG, keyword_filter, deduplicate_articles, filter_trusted,
    interpret_news_with_gpt, aggregate_events,
    load_llm_cache, save_llm_cache,
)

OUTPUT_CSV = Path("data/_system/market_history_v2.csv")
CHECKPOINT = Path("data/_system/market_history_v2_progress.json")


def daily_av_aggregate(articles):
    """AlphaVantage 원본 점수 집계 (GPT 없이도 사용 가능한 baseline)"""
    if not articles:
        return {
            "av_sentiment_avg": 0.0,
            "av_sentiment_std": 0.0,
            "av_bullish_ratio": 0.0,
            "av_bearish_ratio": 0.0,
            "news_count": 0,
        }
    
    scores = []
    bullish = bearish = neutral = 0
    for art in articles:
        s = art.get("_av_sentiment_score")
        if s is not None:
            try:
                scores.append(float(s))
            except:
                pass
        label = (art.get("_av_sentiment_label") or "").lower()
        if "bullish" in label:
            bullish += 1
        elif "bearish" in label:
            bearish += 1
        else:
            neutral += 1
    
    total = bullish + bearish + neutral
    s_series = pd.Series(scores) if scores else pd.Series([0.0])
    
    return {
        "av_sentiment_avg": round(s_series.mean(), 4),
        "av_sentiment_std": round(s_series.std() if len(scores) > 1 else 0, 4),
        "av_bullish_ratio": round(bullish / max(1, total), 3),
        "av_bearish_ratio": round(bearish / max(1, total), 3),
        "news_count": len(articles),
    }


def process_day(date_obj, articles, client, gpt_cache):
    """하루치 처리: 콜랩 v3.2 Layer 2~4 실행"""
    # AlphaVantage 원본 집계
    av_stats = daily_av_aggregate(articles)
    
    # Layer 2: 키워드 + 부정어 필터
    deduped = deduplicate_articles(articles)
    candidates, neg_filtered = keyword_filter(deduped)
    
    # Layer 3: GPT 해석 (캐시 활용)
    interpreted = []
    gpt_calls = 0
    
    for cand in candidates:
        article = cand['article']
        url = article.get('url', '')
        cache_key = url if url else article.get('title', '')[:100]
        
        # 캐시 체크 (영구 캐시, 시간 만료 없음 — 과거 뉴스 분석이라)
        if cache_key in gpt_cache:
            cached = gpt_cache[cache_key]
            interp = cached.get('interpretation')
            if interp:
                interpreted.append({
                    "article": article,
                    "matched_event_types": cand['matched_event_types'],
                    "interpretation": interp,
                })
            continue
        
        # GPT 호출
        interp = interpret_news_with_gpt(client, article, cand['matched_event_types'])
        gpt_calls += 1
        
        if interp:
            interpreted.append({
                "article": article,
                "matched_event_types": cand['matched_event_types'],
                "interpretation": interp,
            })
            gpt_cache[cache_key] = {
                "cached_at": datetime.now().isoformat(),
                "interpretation": interp,
            }
        
        time.sleep(0.1)  # OpenAI rate limit 여유
    
    # Layer 4: 집계
    active_events, event_adj, rejected, conflicts, conflict_penalty = aggregate_events(
        interpreted, verbose=False
    )
    
    # 결과 행
    row = {
        "date": date_obj.strftime("%Y-%m-%d"),
        # AlphaVantage 원본
        **av_stats,
        # 콜랩 v3.2 분석
        "candidates_count": len(candidates),
        "negation_filtered": len(neg_filtered),
        "gpt_interpreted": len(interpreted),
        "gpt_calls_new": gpt_calls,
        "active_events_count": len(active_events),
        "event_adjustment": event_adj,
        "conflicts_count": len(conflicts),
        "conflict_penalty": conflict_penalty,
        "sanity_corrections": len(rejected),
        # 활성 이벤트 (쉼표 구분)
        "active_events": ",".join(active_events.keys()),
        # 강한 이벤트 플래그 (11개 카테고리)
        "has_war": int("전쟁" in active_events),
        "has_rate_hike": int("금리정책_인상" in active_events),
        "has_rate_cut": int("금리정책_인하" in active_events),
        "has_rate_event": int(("금리정책_인상" in active_events) or ("금리정책_인하" in active_events)),
        "has_geopolitical": int("지정학_긴장" in active_events),
        "has_tariff": int("관세" in active_events),
        "has_export_ban": int("수출규제" in active_events),
        "has_earnings_shock": int("실적쇼크" in active_events),
        "has_oil_surge": int("유가급등" in active_events),
        "has_banking_crisis": int("은행위기" in active_events),
        "has_inflation": int("인플레이션" in active_events),
        "has_fed_statement": int("연준발언" in active_events),
    }
    return row, gpt_calls


def main():
    print("=" * 70)
    print("Market History v2 Builder (콜랩 v3.2 → 21개월 시계열)")
    print(f"실행 시각: {datetime.now()}")
    print("=" * 70)
    
    # 데이터 로드 (스트리밍: 파일명만 스캔, article은 그때그때)
    print("\n[1/4] AlphaVantage 캐시 스캔...", flush=True)
    sorted_dates = list_available_dates()
    if not sorted_dates:
        print("❌ 캐시 파일 없음")
        return
    print(f"  커버 일수: {len(sorted_dates)}", flush=True)
    print(f"  기간: {sorted_dates[0]} ~ {sorted_dates[-1]}", flush=True)
    
    # GPT 캐시 로드
    print("\n[2/4] GPT 캐시 로드...")
    gpt_cache = load_llm_cache()
    print(f"  캐시된 해석: {len(gpt_cache):,}건")
    
    # OpenAI 클라이언트
    client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])
    
    # 체크포인트 (이어하기)
    done_dates = set()
    rows = []
    if OUTPUT_CSV.exists():
        existing = pd.read_csv(OUTPUT_CSV)
        done_dates = set(existing["date"].tolist())
        rows = existing.to_dict('records')
        print(f"  이전 진행: {len(done_dates)}일 완료, 이어서 시작")
    
    # 일별 처리
    print(f"\n[3/4] 일별 처리 시작 ({len(sorted_dates)}일)...")
    total_gpt_calls = 0
    started_at = time.time()
    
    for i, d in enumerate(sorted_dates):
        d_str = d.strftime("%Y-%m-%d")
        if d_str in done_dates:
            continue
        
        articles = load_articles_for_date(d)
        row, gpt_calls = process_day(d, articles, client, gpt_cache)
        del articles  # 메모리 즉시 해제
        rows.append(row)
        total_gpt_calls += gpt_calls
        
        # 진행 출력
        elapsed = time.time() - started_at
        done_count = len(rows) - len(done_dates) if done_dates else len(rows)
        avg_per_day = elapsed / max(1, done_count)
        remaining = (len(sorted_dates) - i - 1) * avg_per_day
        
        print(f"  [{i+1}/{len(sorted_dates)}] {d_str}: "
              f"art={row['news_count']:3d}, 후보={row['candidates_count']:2d}, "
              f"GPT신규={gpt_calls:2d}, 이벤트={row['active_events_count']}, "
              f"adj={row['event_adjustment']:+.1f} "
              f"| 누적GPT={total_gpt_calls}, ETA={remaining/60:.1f}분", flush=True)
        
        # 주기적 저장 (50일마다)
        if (i + 1) % 50 == 0:
            pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False)
            save_llm_cache(gpt_cache)
            print(f"  💾 중간 저장 완료 ({len(rows)}일)")
    
    # 최종 저장
    print("\n[4/4] 최종 저장...")
    df = pd.DataFrame(rows)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(OUTPUT_CSV, index=False)
    save_llm_cache(gpt_cache)
    
    # 결과 요약
    print(f"\n{'=' * 70}")
    print(f"✅ 완료: {OUTPUT_CSV}")
    print(f"  총 일수: {len(df)}")
    print(f"  GPT 호출(신규): {total_gpt_calls:,}건")
    print(f"  GPT 캐시 총량: {len(gpt_cache):,}건")
    print(f"  소요시간: {(time.time() - started_at) / 60:.1f}분")
    
    # 통계
    print(f"\n[이벤트 발생 통계]")
    for col in ["has_war", "has_rate_hike", "has_rate_cut", "has_rate_event",
                "has_geopolitical", "has_tariff", "has_export_ban",
                "has_earnings_shock", "has_oil_surge", "has_banking_crisis",
                "has_inflation", "has_fed_statement"]:
        pct = df[col].sum() * 100 / len(df)
        print(f"  {col}: {df[col].sum()}일 ({pct:.1f}%)")
    
    print(f"\n[event_adjustment 분포]")
    print(f"  평균: {df['event_adjustment'].mean():+.2f}")
    print(f"  최대: {df['event_adjustment'].max():+.2f}")
    print(f"  최소: {df['event_adjustment'].min():+.2f}")
    print(f"  std:  {df['event_adjustment'].std():.2f}")
    
    print(f"\n[샘플 - event_adjustment 가장 강한 날 Top 5]")
    print(df.nlargest(5, 'event_adjustment')[['date', 'event_adjustment', 'active_events']].to_string(index=False))
    print(f"\n[샘플 - event_adjustment 가장 부정적인 날 Top 5]")
    print(df.nsmallest(5, 'event_adjustment')[['date', 'event_adjustment', 'active_events']].to_string(index=False))
    
    print("=" * 70)


if __name__ == "__main__":
    main()
