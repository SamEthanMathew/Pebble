"""Entity store: SQLite roundtrip, lookup precedence, module surface."""

from __future__ import annotations

import pytest


def test_init_is_idempotent(pebble_home):
    import entity_store
    entity_store.init()
    entity_store.init()  # second call must not raise


def test_add_and_lookup_by_name(pebble_home):
    import entity_store
    e = entity_store.add('course', '15-122 Principles of Imperative Computation',
                          aliases=['15-122', 'pic'],
                          payload={'professor': 'Saquib', 'semester': 'F25'})
    assert e.id and e.type == 'course'

    hit = entity_store.lookup('15-122 Principles of Imperative Computation')
    assert hit is not None
    assert hit.payload['professor'] == 'Saquib'


def test_lookup_precedence_exact_alias_over_substring(pebble_home):
    import entity_store
    a = entity_store.add('course', '15-122', aliases=['pic'])
    b = entity_store.add('course', '21-122', aliases=['calc'])

    # exact alias 'pic' resolves to a, even though 'pic' is a substring of nothing
    hit = entity_store.lookup('pic')
    assert hit and hit.name == '15-122'


def test_lookup_substring_fallback(pebble_home):
    import entity_store
    entity_store.add('person', 'Pranav Sharma', aliases=['pranav'])
    hit = entity_store.lookup('shar')
    assert hit and hit.name == 'Pranav Sharma'


def test_lookup_type_filter(pebble_home):
    import entity_store
    entity_store.add('course', 'Pranav 101', aliases=[])
    entity_store.add('person', 'Pranav Sharma', aliases=[])

    course_hit = entity_store.lookup('Pranav', type='course')
    person_hit = entity_store.lookup('Pranav', type='person')
    assert course_hit and course_hit.type == 'course'
    assert person_hit and person_hit.type == 'person'


def test_lookup_unknown_returns_none(pebble_home):
    import entity_store
    assert entity_store.lookup('does not exist') is None


def test_list_entities(pebble_home):
    import entity_store
    entity_store.add('course', 'A')
    entity_store.add('course', 'B')
    entity_store.add('person', 'C')
    assert len(entity_store.list_entities()) == 3
    assert len(entity_store.list_entities(type='course')) == 2


def test_update(pebble_home):
    import entity_store
    e = entity_store.add('person', 'Old Name')
    updated = entity_store.update(e.id, {'name': 'New Name'})
    assert updated and updated.name == 'New Name'
    # Lookup by new name works; old name doesn't
    assert entity_store.lookup('New Name')
    assert entity_store.lookup('Old Name') is None


def test_delete(pebble_home):
    import entity_store
    e = entity_store.add('person', 'Doomed')
    assert entity_store.delete(e.id) is True
    assert entity_store.lookup('Doomed') is None
    assert entity_store.delete(e.id) is False  # already gone


def test_invalid_type_rejected(pebble_home):
    import entity_store
    with pytest.raises(ValueError):
        entity_store.add('not-a-real-type', 'X')


def test_module_lookup_action(pebble_home):
    import entity_store
    from modules.entity_module import EntityModule

    entity_store.add('course', '15-122', aliases=['pic'])
    m = EntityModule({'enabled': True})
    out = m.execute(action='lookup', query='15-122')
    assert '15-122' in out


def test_module_add_action(pebble_home):
    from modules.entity_module import EntityModule
    m = EntityModule({'enabled': True})
    out = m.execute(action='add', entity_type='course', name='21-127',
                    aliases=['math foundations'], payload={'professor': 'X'})
    assert '21-127' in out
    assert 'Added' in out


def test_module_default_tiers(pebble_home):
    from modules.entity_module import EntityModule
    from modules.base import ActionTier
    m = EntityModule({})
    assert m.action_tier('lookup') == ActionTier.AUTO
    assert m.action_tier('list')   == ActionTier.AUTO
    assert m.action_tier('add')    == ActionTier.NOTIFY
    assert m.action_tier('delete') == ActionTier.ASK
