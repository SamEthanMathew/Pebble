"""Memory v1.5: word-overlap + recency-weighted recall."""

from __future__ import annotations


def test_remember_then_list(pebble_home):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='I prefer Python over Java', category='preference')
    out = m.execute(action='list')
    assert 'Python' in out


def test_recall_returns_overlap_match(pebble_home):
    from modules.memory import MemoryModule, search
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='Sarah Chen leads the data team',  category='person')
    m.execute(action='remember', text='I prefer dark mode in everything', category='preference')

    hits = search('Who is Sarah?')
    assert hits
    assert hits[0]['text'].startswith('Sarah Chen')


def test_recall_recency_breaks_overlap_tie(pebble_home, monkeypatch):
    """Two entries with identical overlap — newer wins."""
    import datetime
    from modules import memory as mem_module

    # Insert two entries, one old one new, with identical query overlap
    items = [
        {'id': 1, 'text': 'pebble color blue', 'category': 'fact',
         'created': '2024-01-01', 'last_accessed': '2024-01-01'},
        {'id': 2, 'text': 'pebble color green', 'category': 'fact',
         'created': datetime.date.today().isoformat(),
         'last_accessed': datetime.date.today().isoformat()},
    ]
    mem_module._save(items)

    hits = mem_module.search('pebble color')
    assert hits
    assert hits[0]['text'].endswith('green')


def test_recall_touches_last_accessed(pebble_home):
    """Returning an entry refreshes last_accessed so it stays warm."""
    import datetime
    from modules import memory as mem_module

    today = datetime.date.today().isoformat()
    items = [
        {'id': 1, 'text': 'tag X', 'category': 'fact',
         'created': '2024-01-01', 'last_accessed': '2024-01-01'},
    ]
    mem_module._save(items)
    hits = mem_module.search('tag')
    assert hits
    saved = mem_module._load()
    assert saved[0]['last_accessed'] == today


def test_no_overlap_returns_no_results(pebble_home):
    from modules.memory import MemoryModule, search
    MemoryModule({'enabled': True}).execute(
        action='remember', text='taco truck on Forbes is great', category='place')
    assert search('quantum chromodynamics') == []


def test_migration_adds_last_accessed(pebble_home):
    """Old entries without last_accessed get one on first load."""
    from atomic_io import write_json
    import modules.memory as mem_module
    write_json(mem_module._MEMORY_PATH,
               [{'id': 1, 'text': 'old', 'category': 'fact', 'created': '2023-01-01'}])
    items = mem_module._load()
    assert items[0]['last_accessed'] == '2023-01-01'


def test_forget_removes_matching(pebble_home):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='delete me')
    m.execute(action='remember', text='keep me')
    out = m.execute(action='forget', text='delete')
    assert 'Removed 1' in out
    out = m.execute(action='list')
    assert 'keep me' in out
    assert 'delete me' not in out


def test_default_tiers(pebble_home):
    from modules.base import ActionTier
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    assert m.action_tier('recall')   == ActionTier.AUTO
    assert m.action_tier('remember') == ActionTier.NOTIFY
