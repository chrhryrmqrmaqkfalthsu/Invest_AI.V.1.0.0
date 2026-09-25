"""
Market Analyzer v3.2 - 봇 통합 버전
- 콜랩 원본 로직 그대로 유지
- 차이점: API 키를 .env에서 로드, 출력 경로를 봇 디렉토리로
- 5년 시계열 빌드와 실시간 분석 양쪽에서 함수들을 재사용 가능
"""
import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

from dotenv import load_dotenv
load_dotenv()

try:
    from openai import OpenAI
except ImportError:
    print("📦 openai SDK 설치 중...")
    os.system("pip install openai --quiet")
    from openai import OpenAI

# ============================================================
# CONFIG
# ============================================================
CONFIG = {
    "NEWS_API_KEY": os.getenv("NEWSAPI_KEY", ""),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
    
    "OUTPUT_FILE": "data/_system/market_state.json",
    "LLM_CACHE_FILE": "data/_system/llm_news_cache.json",
    
    "LLM_MODEL": "gpt-4o-mini",
    "LLM_TIMEOUT": 30,
    "MAX_LLM_CALLS_PER_RUN": 15,
    "LLM_CACHE_HOURS": 24,
    
    "EVENT_IMPACT_MIN": -30,
    "EVENT_IMPACT_MAX": 20,
    
    "TRUSTED_SOURCES": {
        'reuters', 'bloomberg', 'wall street journal', 'wsj',
        'financial times', 'cnbc', 'associated press', 'marketwatch',
        'barron', 'the economist', 'business insider', 'forbes', 'fortune',
        # AlphaVantage 흔한 소스 추가
        'seeking alpha', 'zacks', 'motley fool', 'benzinga',
    },
    
    "NEGATION_PATTERNS": [
        'unlikely', 'denies', 'denied', 'rules out', 'ruled out',
        'averted', 'avoided', 'no longer', 'ended', 'lifted',
        'resolved', 'cancelled', 'cancels', 'reject', 'rejected',
        'no plans', 'not planning', 'dismissed',
        # 추가: 잘못된 매칭 방지
        'easing concerns', 'easing of restrictions', 'easing tensions',
        'releasing', 'increasing', 'decreasing',
        'no rate', 'no hike', 'no cut',
        'rules against', 'avoid', 'avoids',
    ],
    
    "CONFIDENCE_WEIGHT": {
        "확정": 1.0, "예상": 0.6, "추측": 0.3, "루머": 0.1
    },
    
    "TIMEFRAME_WEIGHT": {
        "즉시": 1.0, "단기": 0.7, "중기": 0.4, "장기": 0.2,
    },
    
    "CONFLICT_PAIRS": [
        ("금리정책_인상", "금리정책_인하"),
    ],
    "CONFLICT_PENALTY": -2,
    
    "SUSPICIOUS_PATTERNS": {
        "금리정책_인상": ("호재(+)", lambda s: s > 0),
        "금리정책_인하": ("악재(-)", lambda s: s < 0),
        "관세": ("호재(+2 이상)", lambda s: s > 2),
        "전쟁": ("강한_호재(+5 이상)", lambda s: s >= 5),
        "수출규제": ("호재(+)", lambda s: s > 0),
        "유가급등": ("강한_호재(+5 이상)", lambda s: s >= 5),
        "지정학_긴장": ("호재(+2 이상)", lambda s: s > 2),
        "은행위기": ("호재(+)", lambda s: s > 0),
        "인플레이션": ("호재(+2 이상)", lambda s: s > 2),
    },
    
    "EVENT_TO_AFFECTED_SECTORS": {
        "수출규제": ["반도체", "기술"],
        "관세": ["기술", "반도체", "소비재"],
        "금리정책_인상": ["기술", "반도체"],
        "금리정책_인하": ["기술", "반도체", "금융"],
        "전쟁": ["기술", "소비재"],
        "지정학_긴장": ["반도체", "기술"],
        "유가급등": ["소비재"],
        "은행위기": ["금융"],
        "인플레이션": ["기술", "소비재"],
        "연준발언": ["기술", "금융"],
    },
    
    "SECTOR_IMPACT_MULTIPLIER": 3.0,
}

# ============================================================
# 키워드 사전
# ============================================================
CRITICAL_EVENTS = {
    "전쟁": {
        "keywords": [
            "war breaks out", "invasion", "military strike", "missile attack",
            "military conflict", "armed conflict", "troops deployed", "airstrike",
            "ground assault", "war declared",
            "hamas attack", "israel strikes", "ukraine war", "russian forces",
            "military operation", "rocket attack", "drone strike",
        ],
    },
    "수출규제": {
        "keywords": [
            "export ban", "export control", "export restriction", "chip restriction",
            "semiconductor sanctions", "technology ban", "trade sanctions",
            "chip export ban", "semiconductor export",
            "chip ban", "tech restriction", "export curb",
            "chip sanctions", "advanced chip", "huawei sanctions",
        ],
    },
    "관세": {
        "keywords": [
            "new tariff", "tariff hike", "trade war", "import duty",
            "retaliatory tariff", "tariff announced", "tariff imposed",
            "tariff increase", "tariff threat",
            "tariffs on china", "china tariff", "trade dispute",
            "import tax", "trade tension", "trade barrier",
        ],
    },
    "금리정책_인상": {
        "keywords": [
            "rate hike", "rate increase", "rates higher",
            "fed raises", "fed hikes", "fed lifts",
            "hawkish fed", "hawkish stance", "hawkish powell",
            "interest rate hike", "monetary tightening", "quantitative tightening",
            "basis points hike", "bps hike",
            "75 basis points", "50 basis points", "25 basis points",
            "fed tightening", "rate increase by",
            "powell hawkish", "fomc hike", "borrowing costs rise",
            "fed boosts rates", "fed lifts rates",
        ],
    },
    "금리정책_인하": {
        "keywords": [
            "rate cut", "rate decrease", "rates lower",
            "fed cuts", "fed lowers", "fed reduces",
            "dovish fed", "dovish stance", "dovish powell",
            "interest rate cut", "monetary easing", "quantitative easing",
            "basis points cut", "bps cut",
            "fed pivots", "fed pivot", "fed pause",
            "rate reduction", "borrowing costs fall",
            "fomc cut", "fed eases policy",
        ],
    },
    "지정학_긴장": {
        "keywords": [
            "taiwan strait", "north korea threat", "middle east tension",
            "south china sea", "iran tensions", "geopolitical risk",
            "nuclear threat", "diplomatic crisis",
            "china taiwan", "russia nato", "korea missile",
            "israel iran", "houthi attack", "red sea attack",
            "geopolitical tension",
        ],
    },
    "유가급등": {
        "keywords": [
            "oil prices surge", "crude oil spike", "opec production cut",
            "oil supply shock", "oil price jumps", "crude surge",
            "oil rallies",
            "oil soars", "crude jumps", "brent surges",
            "oil supply cut", "energy crisis",
        ],
    },
    "실적쇼크": {
        "keywords": [
            "guidance cut", "earnings miss", "profit warning",
            "downgrade outlook", "weak quarterly results",
            "missed estimates", "lowered guidance", "slashed forecast",
            "revenue miss", "earnings disappoint", "profit slumps",
            "guidance lowered", "outlook cut",
        ],
    },
    "은행위기": {
        "keywords": [
            "silicon valley bank", "svb collapse", "svb fails",
            "first republic", "signature bank", "credit suisse",
            "bank run", "bank collapse", "bank failure",
            "banking crisis", "regional bank crisis", "bank contagion",
            "fdic seizure", "bank rescue", "deposit flight",
            "bank shares plunge", "banking turmoil",
        ],
    },
    "인플레이션": {
        "keywords": [
            "cpi rises", "cpi jumps", "inflation surges", "inflation jumps",
            "consumer prices rise", "hot inflation", "sticky inflation",
            "pce inflation", "core cpi", "core pce",
            "inflation data", "inflation report", "inflation print",
            "price pressures", "inflation persists",
            "cpi report", "ppi rises", "wholesale prices",
        ],
    },
    "연준발언": {
        "keywords": [
            "powell speech", "powell testimony", "powell remarks",
            "fomc minutes", "fed minutes", "fed officials",
            "jackson hole", "fed chair", "fed statement",
            "fomc statement", "fed press conference",
            "fed policy", "fed outlook",
        ],
    },
}
# ============================================================
# 뉴스 처리 함수들 (콜랩 원본)
# ============================================================
def deduplicate_articles(articles):
    seen_urls, seen_titles, unique = set(), set(), []
    for art in articles:
        url = art.get('url', '') or ''
        title = (art.get('title', '') or '').strip().lower()
        if not title:
            continue
        key = url if url else title
        if key in seen_urls or title in seen_titles:
            continue
        seen_urls.add(key)
        seen_titles.add(title)
        unique.append(art)
    return unique

def filter_trusted(articles):
    trusted, others = [], []
    for art in articles:
        source = (art.get('source', {}).get('name', '') or '').lower()
        if any(t in source for t in CONFIG["TRUSTED_SOURCES"]):
            trusted.append(art)
        else:
            others.append(art)
    return trusted, others

def has_negation_near(text, keyword):
    text_lower = text.lower()
    kw_lower = keyword.lower()
    idx = text_lower.find(kw_lower)
    if idx == -1:
        return False
    start = max(0, idx - 30)
    end = min(len(text_lower), idx + len(kw_lower) + 20)
    context = text_lower[start:end]
    return any(neg in context for neg in CONFIG["NEGATION_PATTERNS"])

def keyword_filter(articles):
    candidates = []
    filtered_out_negation = []
    
    for art in articles:
        title = (art.get("title") or "").lower()
        desc = (art.get("description") or "").lower()
        text = f"{title} {desc}"
        matched_events = []
        
        for event_name, conf in CRITICAL_EVENTS.items():
            for kw in conf["keywords"]:
                if kw.lower() in text:
                    if has_negation_near(text, kw):
                        filtered_out_negation.append({
                            "title": art.get("title", ""),
                            "matched_keyword": kw,
                            "reason": "negation_detected"
                        })
                        continue
                    matched_events.append(event_name)
                    break
        
        if matched_events:
            candidates.append({
                "article": art,
                "matched_event_types": list(set(matched_events))
            })
    
    return candidates, filtered_out_negation

# ============================================================
# LLM 캐시
# ============================================================
def load_llm_cache():
    p = CONFIG["LLM_CACHE_FILE"]
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_llm_cache(cache):
    p = CONFIG["LLM_CACHE_FILE"]
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def interpret_news_with_gpt(client, article, matched_events):
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

    try:
        response = client.chat.completions.create(
            model=CONFIG["LLM_MODEL"],
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg}
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.2,
            timeout=CONFIG["LLM_TIMEOUT"]
        )
        text = response.choices[0].message.content.strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        return None
    except Exception as e:
        return None

# ============================================================
# 집계 (콜랩 원본)
# ============================================================
def sanity_check(event_type, impact_score):
    patterns = CONFIG["SUSPICIOUS_PATTERNS"]
    if event_type in patterns:
        label, condition = patterns[event_type]
        if condition(impact_score):
            return False, f"⚠ {event_type}인데 {label} 판정 (매크로 상식 불일치)"
    return True, ""

def detect_conflicting_events(active_events):
    extra_penalty = 0
    conflicts = []
    for a, b in CONFIG["CONFLICT_PAIRS"]:
        if a in active_events and b in active_events:
            extra_penalty += CONFIG["CONFLICT_PENALTY"]
            conflicts.append(f"{a} ↔ {b}")
    return extra_penalty, conflicts

def aggregate_events(interpreted, verbose=True):
    active_events = {}
    total_impact = 0
    rejected = []
    
    conf_w = CONFIG["CONFIDENCE_WEIGHT"]
    time_w = CONFIG["TIMEFRAME_WEIGHT"]
    
    for item in interpreted:
        interp = item['interpretation']
        if not interp.get('is_real_event', False):
            continue
        event_type = interp.get('event_type', '기타')
        if event_type in ['해당없음', '기타']:
            continue
        
        score = interp.get('impact_score', 0)
        title = item['article'].get('title', '')
        
        is_valid, warning = sanity_check(event_type, score)
        if not is_valid:
            if verbose:
                print(f"  🚫 {warning}")
                print(f"     기사: {title[:60]}")
                print(f"     → 자동 부호 반전: {score} → {-score}")
            rejected.append({
                "title": title,
                "event_type": event_type,
                "original_score": score,
                "corrected_score": -score,
                "warning": warning,
                "gpt_reasoning": interp.get('reasoning', '')
            })
            score = -score
        
        conf = interp.get('confidence', '추측')
        timeframe = interp.get('timeframe', '단기')
        
        conf_mult = conf_w.get(conf, 0.3)
        time_mult = time_w.get(timeframe, 0.7)
        weighted = score * conf_mult * time_mult
        total_impact += weighted
        
        gpt_sectors = interp.get('affected_sectors', [])
        default_sectors = CONFIG["EVENT_TO_AFFECTED_SECTORS"].get(event_type, [])
        all_sectors = list(set(gpt_sectors + default_sectors))
        
        if event_type not in active_events:
            active_events[event_type] = {
                "match_count": 0,
                "total_impact_score": 0,
                "market_impact": interp.get('market_impact'),
                "affected_sectors": all_sectors,
                "articles": []
            }
        active_events[event_type]["match_count"] += 1
        active_events[event_type]["total_impact_score"] = round(
            active_events[event_type]["total_impact_score"] + weighted, 2)
        active_events[event_type]["affected_sectors"] = list(set(
            active_events[event_type]["affected_sectors"] + all_sectors
        ))
        active_events[event_type]["articles"].append({
            "title": title,
            "source": item['article'].get('source', {}).get('name', ''),
            "impact_score": score,
            "confidence": conf,
            "timeframe": timeframe,
            "weighted_score": round(weighted, 2),
            "reasoning": interp.get('reasoning', ''),
            "url": item['article'].get('url', ''),
            "sanity_corrected": not is_valid
        })
    
    conflict_penalty, conflicts = detect_conflicting_events(active_events)
    total_impact += conflict_penalty
    
    total_impact = round(max(min(total_impact, CONFIG["EVENT_IMPACT_MAX"]),
                             CONFIG["EVENT_IMPACT_MIN"]), 1)
    return active_events, total_impact, rejected, conflicts, conflict_penalty


# ============================================================
# 자가 검증
# ============================================================
if __name__ == "__main__":
    print("[OK] colab_v32 모듈 로드 정상")
    print(f"  NEWS_API_KEY: {'설정됨' if CONFIG['NEWS_API_KEY'] else '❌ 없음'}")
    print(f"  OPENAI_API_KEY: {'설정됨' if CONFIG['OPENAI_API_KEY'] else '❌ 없음'}")
    print(f"  CRITICAL_EVENTS: {len(CRITICAL_EVENTS)}개 카테고리")
    
    # 부정어 필터 테스트
    test_articles = [
        {"title": "Fed announces rate hike", "description": "0.25% increase"},
        {"title": "Rate hike unlikely this month", "description": "analyst says"},
        {"title": "War breaks out in region", "description": "conflict escalates"},
        {"title": "War averted by diplomacy", "description": "peace deal"},
    ]
    candidates, neg = keyword_filter(test_articles)
    print(f"\n키워드 필터 테스트:")
    print(f"  입력 4건 → 통과 {len(candidates)}건, 부정어 차단 {len(neg)}건")
    for c in candidates:
        print(f"    ✅ {c['article']['title']} [{c['matched_event_types']}]")
    for n in neg:
        print(f"    🚫 {n['title']} [{n['matched_keyword']}]")
