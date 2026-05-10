"""Cache + scraper tests."""

from __future__ import annotations


def test_cache_set_get_roundtrip(pebble_home):
    import cache
    cache.set('news', 'https://example.com/x', {'title': 'X', 'text': 'body'})
    out = cache.get('news', 'https://example.com/x')
    assert out == {'title': 'X', 'text': 'body'}


def test_cache_returns_none_when_missing(pebble_home):
    import cache
    assert cache.get('news', 'https://nope') is None


def test_cache_expires_after_ttl(pebble_home, monkeypatch):
    import cache
    cache.set('news', 'k', {'v': 1}, ttl_hours=0.0001)  # ~0.36s
    # Force "now" to be in the future
    import datetime as dt
    real_now = cache._now
    fake = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)
    monkeypatch.setattr(cache, '_now', lambda: fake)
    assert cache.get('news', 'k') is None


def test_cache_per_source_ttl_from_config(pebble_home):
    import cache
    import crab_config
    crab_config.set_value('scraping', {'cache_ttl_hours': {'mysource': 100}})
    # The internal lookup should pick that up
    assert cache._ttl_hours_for('mysource') == 100


def test_cache_invalidate(pebble_home):
    import cache
    cache.set('news', 'k', {'v': 1})
    assert cache.get('news', 'k') is not None
    assert cache.invalidate('news', 'k') is True
    assert cache.get('news', 'k') is None


def test_cache_clear_source(pebble_home):
    import cache
    cache.set('news', 'a', {'a': 1})
    cache.set('news', 'b', {'b': 2})
    cache.set('weather', 'w', {'w': 1})
    n = cache.clear_source('news')
    assert n == 2
    assert cache.get('news', 'a') is None
    assert cache.get('weather', 'w') is not None


def test_scraper_uses_cache(pebble_home, monkeypatch):
    """Second fetch should NOT re-call requests when cache has the url."""
    import scraper

    call_count = [0]

    class FakeResp:
        text = '<html><head><title>Hi</title></head><body><p>Hello</p></body></html>'
        def raise_for_status(self): pass

    def fake_get(url, **kw):
        call_count[0] += 1
        return FakeResp()

    monkeypatch.setattr(scraper.requests, 'get', fake_get)

    a = scraper.fetch('https://example.com/x', source='generic')
    b = scraper.fetch('https://example.com/x', source='generic')
    assert call_count[0] == 1  # second was served from cache
    assert a == b
    assert a['url'] == 'https://example.com/x'


def test_scraper_force_refresh_bypasses_cache(pebble_home, monkeypatch):
    import scraper

    call_count = [0]

    class FakeResp:
        text = '<html><body>x</body></html>'
        def raise_for_status(self): pass

    def fake_get(url, **kw):
        call_count[0] += 1
        return FakeResp()

    monkeypatch.setattr(scraper.requests, 'get', fake_get)

    scraper.fetch('https://example.com/x', source='generic')
    scraper.fetch('https://example.com/x', source='generic', force_refresh=True)
    assert call_count[0] == 2


def test_scraper_failure_returns_none_audits(pebble_home, monkeypatch):
    import scraper, audit

    def fake_get(url, **kw):
        raise scraper.requests.ConnectionError('boom')
    monkeypatch.setattr(scraper.requests, 'get', fake_get)

    out = scraper.fetch('https://broken.example', source='generic')
    assert out is None
    rows = audit.tail(10)
    assert any(r.get('action') == 'fetch_failed' for r in rows)


def test_scraper_minimal_html_fallback(pebble_home, monkeypatch):
    """Without trafilatura, the regex fallback still produces title + text."""
    import scraper

    monkeypatch.setattr(scraper, '_HAS_TRAFILATURA', False)

    class FakeResp:
        text = '''<html><head><title>Demo Page</title></head><body>
                  <script>alert("x")</script>
                  <p>Hello <b>world</b>.</p></body></html>'''
        def raise_for_status(self): pass

    monkeypatch.setattr(scraper.requests, 'get', lambda url, **kw: FakeResp())

    out = scraper.fetch('https://example.com/y', source='generic')
    assert out is not None
    assert 'Demo Page' in out['title']
    assert 'Hello' in out['text']
    assert 'alert' not in out['text']  # script block stripped
