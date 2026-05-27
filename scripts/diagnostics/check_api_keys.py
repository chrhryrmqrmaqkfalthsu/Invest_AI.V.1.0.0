import os
from dotenv import load_dotenv
load_dotenv()
import requests

key = os.getenv("NEWSAPI_KEY")
print(f"[NewsAPI] key={key[:8] if key else 'NONE'}...")
r = requests.get(f"https://newsapi.org/v2/top-headlines?country=us&category=business&pageSize=3&apiKey={key}")
j = r.json()
print(f"  status={j.get('status')}, total={j.get('totalResults', 0)}")
if j.get('articles'):
    print(f"  샘플: {j['articles'][0]['title'][:75]}")

av_key = os.getenv("ALPHA_VANTAGE_KEY")
print(f"\n[AlphaVantage] key={av_key[:8] if av_key else 'NONE'}...")
r = requests.get(f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL&limit=3&apikey={av_key}")
j = r.json()
items = j.get('feed', [])
print(f"  현재 feed={len(items)}")
if items:
    print(f"  샘플: {items[0].get('title','')[:75]}")
    print(f"  sentiment={items[0].get('overall_sentiment_score','N/A')}")
else:
    print(f"  응답: {str(j)[:200]}")

r = requests.get(f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL&time_from=20220301T0000&time_to=20220305T0000&limit=3&apikey={av_key}")
j = r.json()
items = j.get('feed', [])
print(f"\n[AlphaVantage 2022-03] feed={len(items)}")
if items:
    print(f"  날짜={items[0].get('time_published','')[:8]}")
    print(f"  제목={items[0].get('title','')[:75]}")
else:
    print(f"  응답: {str(j)[:200]}")

from openai import OpenAI
oa = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
resp = oa.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "say only: ok"}],
    max_tokens=5
)
print(f"\n[OpenAI] {resp.choices[0].message.content}")
print("\n완료")
