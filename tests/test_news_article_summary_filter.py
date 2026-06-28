import gzip, json
from pathlib import Path
from engine.live import news_article_summary as mod


def write_cache(path: Path, articles):
    path.parent.mkdir(parents=True)
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump({'feed': articles}, f)


def setup(tmp_path, monkeypatch, articles):
    cache=tmp_path/'cache'; syms=tmp_path/'symbols'
    (syms/'DELL').mkdir(parents=True)
    (syms/'DELL'/'parameters.json').write_text(json.dumps({'asset_meta':{'name':'Dell Technologies Inc - Class C'}}), encoding='utf-8')
    write_cache(cache/'DELL'/'av_DELL_202606.json.gz', articles)
    monkeypatch.setattr(mod, 'CACHE', cache)
    monkeypatch.setattr(mod, 'SYMS', syms)
    monkeypatch.setenv('NEWS_TRANSLATION_ENABLED','0')


def test_hides_peer_article_without_direct_mention(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [
        {'title':'Apple stock hits all-time high at 315.02 USD','summary':'Apple shares reached a new high.','url':'https://x/apple','time_published':'20260626T150000','source':'X','ticker_sentiment':[{'ticker':'DELL','relevance_score':'0.61','ticker_sentiment_score':'-0.10'}]},
        {'title':'Dell Technologies shares slip after guidance update','summary':'Dell management discussed softer demand.','url':'https://x/dell','time_published':'20260626T140000','source':'X','ticker_sentiment':[{'ticker':'DELL','relevance_score':'1.0','ticker_sentiment_score':'-0.30'}]},
    ])
    rows=mod.articles_for_ticker('DELL', limit=2)
    assert len(rows)==1
    assert rows[0]['title_en']=='Dell Technologies shares slip after guidance update'


def test_keeps_old_direct_article_as_display_reference(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, [
        {'title':'Dell Technologies shares slip after guidance update','summary':'Dell management discussed softer demand.','url':'https://x/dell-old','time_published':'20260602T140000','source':'X','ticker_sentiment':[{'ticker':'DELL','relevance_score':'1.0','ticker_sentiment_score':'-0.30'}]},
    ])
    rows=mod.articles_for_ticker('DELL', limit=2)
    assert len(rows)==1
    assert rows[0]['basis']=='display_direct_reference_with_negative_risk'
