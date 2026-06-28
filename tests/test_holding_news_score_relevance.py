from pathlib import Path

from engine.live.holding_news_queue import (
    HOLDING_NEWS_SCORE_LOGIC_VERSION,
    MAX_SCORE_ARTICLE_AGE_DAYS,
    _ticker_news_risk_score,
    _valid_recent_direct_article_rows,
    lookup_holding_news_cache_score,
    save_holding_news_cache_entry,
)


def test_holding_news_score_ignores_peer_article_without_direct_mention():
    feed = [
        {'title':'Apple stock hits all-time high','summary':'Apple shares reached a new high.','time_published':'20260626T150000','ticker_sentiment':[{'ticker':'DELL','relevance_score':'0.61','ticker_sentiment_score':'-0.90'}]},
        {'title':'Dell Technologies shares slip after guidance update','summary':'Dell management discussed softer demand.','time_published':'20260626T140000','ticker_sentiment':[{'ticker':'DELL','relevance_score':'1.0','ticker_sentiment_score':'-0.30'}]},
    ]
    score, count, latest = _ticker_news_risk_score('DELL', feed, now='2026-06-26T16:00:00+00:00')
    assert score == 0.30
    assert count == 1
    assert latest == '2026-06-26T14:00:00+00:00'


def test_holding_news_score_ignores_direct_article_older_than_three_days():
    feed = [
        {'title':'Dell Technologies shares tumble on old concern','summary':'Dell demand was weak.','time_published':'20260620T140000','ticker_sentiment':[{'ticker':'DELL','relevance_score':'1.0','ticker_sentiment_score':'-0.95'}]},
        {'title':'Dell Technologies shares slip after recent guidance update','summary':'Dell management discussed softer demand.','time_published':'20260626T140000','ticker_sentiment':[{'ticker':'DELL','relevance_score':'0.5','ticker_sentiment_score':'-0.20'}]},
    ]
    score, count, latest = _ticker_news_risk_score('DELL', feed, now='2026-06-26T16:00:00+00:00')
    assert score == 0.10
    assert count == 1
    assert latest == '2026-06-26T14:00:00+00:00'


def test_basis_articles_are_sorted_by_risk_before_recency():
    feed = [
        {'title':'Dell Technologies fresh bullish update','summary':'Dell demand improved.','time_published':'20260626T150000','ticker_sentiment':[{'ticker':'DELL','relevance_score':'1.0','ticker_sentiment_score':'0.40'}]},
        {'title':'Dell Technologies older bearish warning','summary':'Dell margin risk increased.','time_published':'20260626T130000','ticker_sentiment':[{'ticker':'DELL','relevance_score':'0.9','ticker_sentiment_score':'-0.50'}]},
    ]
    rows = _valid_recent_direct_article_rows('DELL', feed, now='2026-06-26T16:00:00+00:00')
    assert rows[0]['title'] == 'Dell Technologies older bearish warning'
    assert rows[0]['risk_score'] == 0.45
    score, count, latest = _ticker_news_risk_score('DELL', feed, now='2026-06-26T16:00:00+00:00')
    assert score == 0.45
    assert count == 2
    assert latest == '2026-06-26T15:00:00+00:00'


def test_short_ticker_does_not_match_indefinite_article_word():
    feed = [
        {'title':'A broad market warning hits technology shares','summary':'A weaker demand setup hurt peers.','time_published':'20260626T150000','ticker_sentiment':[{'ticker':'A','relevance_score':'1.0','ticker_sentiment_score':'-0.80'}]},
    ]
    score, count, latest = _ticker_news_risk_score('A', feed, now='2026-06-26T16:00:00+00:00')
    assert score == 0.0
    assert count == 0
    assert latest == ''


def test_short_ticker_accepts_explicit_cashtag_mention():
    feed = [
        {'title':'$A shares fall after guidance warning','summary':'Management lowered its outlook.','time_published':'20260626T150000','ticker_sentiment':[{'ticker':'A','relevance_score':'0.5','ticker_sentiment_score':'-0.80'}]},
    ]
    score, count, latest = _ticker_news_risk_score('A', feed, now='2026-06-26T16:00:00+00:00')
    assert score == 0.4
    assert count == 1
    assert latest == '2026-06-26T15:00:00+00:00'


def test_lookup_rejects_old_score_logic_cache(tmp_path: Path):
    path = tmp_path / 'holding_news_sentiment_cache.json'
    path.write_text('{"entries":{"DELL":{"ticker":"DELL","date":"2026-06-26","score":0.9,"fetched_at":"2026-06-26T19:32:46+00:00","article_count":50,"source":"alphavantage_holding_news"}}}', encoding='utf-8')
    assert lookup_holding_news_cache_score('DELL', path=path) is None


def test_lookup_accepts_recent3d_score_logic_cache(tmp_path: Path):
    path = tmp_path / 'holding_news_sentiment_cache.json'
    save_holding_news_cache_entry('DELL', score=0.2, fetched_at='2026-06-26T19:32:46+00:00', article_count=1, path=path)
    row = lookup_holding_news_cache_score('DELL', path=path)
    assert row and row['score'] == 0.2
    data = path.read_text(encoding='utf-8')
    assert HOLDING_NEWS_SCORE_LOGIC_VERSION in data
    assert f'"max_score_article_age_days": {MAX_SCORE_ARTICLE_AGE_DAYS}' in data
