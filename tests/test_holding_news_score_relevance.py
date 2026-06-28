from engine.live.holding_news_queue import _ticker_news_risk_score


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
