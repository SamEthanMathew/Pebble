"""Entity auto-suggest: ledger increments, threshold, accept/dismiss."""

from __future__ import annotations


def test_observe_increments_count(pebble_home):
    import entity_suggest
    msgs = [
        {'from_email': 'a@x.com', 'from_name': 'A'},
        {'from_email': 'a@x.com', 'from_name': 'A'},
        {'from_email': 'b@y.com', 'from_name': 'B'},
    ]
    led = entity_suggest.observe(msgs)
    assert led['a@x.com']['count'] == 2
    assert led['b@y.com']['count'] == 1


def test_known_senders_skipped(pebble_home):
    import entity_store, entity_suggest
    entity_store.add('person', 'Known Person', aliases=['known@x.com'])
    led = entity_suggest.observe([
        {'from_email': 'known@x.com', 'from_name': 'Known Person'},
        {'from_email': 'unknown@y.com', 'from_name': 'New'},
    ])
    assert 'known@x.com' not in led
    assert 'unknown@y.com' in led


def test_threshold_filters_suggestions(pebble_home):
    import entity_suggest
    entity_suggest.observe([{'from_email': 'a@x.com'}] * 2)
    entity_suggest.observe([{'from_email': 'b@y.com'}] * 4)
    out = entity_suggest.find_suggestions(threshold=3)
    emails = {s['email'] for s in out}
    assert 'b@y.com' in emails
    assert 'a@x.com' not in emails


def test_accept_adds_entity_and_clears_ledger(pebble_home):
    import entity_store, entity_suggest
    entity_suggest.observe([{'from_email': 'sarah@cmu.edu', 'from_name': 'Sarah'}] * 3)
    assert entity_suggest.accept('sarah@cmu.edu') is True
    # Now in entity store
    e = entity_store.lookup('sarah@cmu.edu', type='person')
    assert e is not None and e.name == 'Sarah'
    # Cleared from ledger
    assert 'sarah@cmu.edu' not in entity_suggest._load_ledger()


def test_dismiss_clears_ledger_no_entity_added(pebble_home):
    import entity_store, entity_suggest
    entity_suggest.observe([{'from_email': 'spam@x.com'}] * 5)
    assert entity_suggest.dismiss('spam@x.com') is True
    assert 'spam@x.com' not in entity_suggest._load_ledger()
    assert entity_store.lookup('spam@x.com', type='person') is None


def test_observe_drops_when_user_adds_entity(pebble_home):
    """If user manually added the entity between scans, ledger drops the email."""
    import entity_store, entity_suggest
    entity_suggest.observe([{'from_email': 'pal@x.com', 'from_name': 'Pal'}] * 3)
    assert 'pal@x.com' in entity_suggest._load_ledger()

    entity_store.add('person', 'Pal', aliases=['pal@x.com'])
    # Next observation drops the email since it's now known
    entity_suggest.observe([{'from_email': 'pal@x.com', 'from_name': 'Pal'}])
    assert 'pal@x.com' not in entity_suggest._load_ledger()
