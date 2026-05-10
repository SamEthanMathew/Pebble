"""Pebble module wrapping the entity store so the LLM can resolve, add, and list entities as a tool."""

from __future__ import annotations

import json

import entity_store
from .base import PebbleModule, ActionTier


class EntityModule(PebbleModule):
    name         = 'entities'
    display_name = 'Entity Store'
    description  = 'Resolve and manage structured entities (courses, people, projects).'
    icon         = '🗂️'
    config_fields: list[dict] = []

    _default_tiers = {
        'lookup':       ActionTier.AUTO,
        'list':         ActionTier.AUTO,
        'add':          ActionTier.NOTIFY,
        'update':       ActionTier.NOTIFY,
        'delete':       ActionTier.ASK,
    }

    def is_ready(self) -> bool:
        entity_store.init()
        return True

    def tool_name(self) -> str:
        return 'entities'

    def tool_description(self) -> str:
        return ('Resolve a string (like "15-122" or "Pranav") to a structured entity, '
                'or manage the entity store. Use action="lookup" to resolve, '
                '"list" to enumerate, "add" to create a new entity (course/person/project/recurring/account).')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['lookup', 'list', 'add', 'update', 'delete'],
                    'description': 'What to do',
                },
                'query':       {'type': 'string', 'description': 'For lookup: the string to resolve'},
                'entity_type': {'type': 'string', 'enum': list(entity_store.ENTITY_TYPES),
                                'description': 'Entity type. Required for add; optional filter for lookup/list'},
                'name':        {'type': 'string', 'description': 'For add: canonical display name'},
                'aliases':     {'type': 'array', 'items': {'type': 'string'},
                                'description': 'For add: alternative names'},
                'payload':     {'type': 'object',
                                'description': 'For add: type-specific fields (e.g. course → code, professor, url)'},
                'id':          {'type': 'string', 'description': 'For update/delete: entity id'},
                'fields':      {'type': 'object', 'description': 'For update: fields to change'},
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'list', **kwargs) -> str:
        try:
            if action == 'lookup':
                query = kwargs.get('query', '').strip()
                if not query:
                    return 'lookup requires a `query`.'
                ent = entity_store.lookup(query, type=kwargs.get('entity_type'))
                if not ent:
                    return f'No entity matches "{query}".'
                return json.dumps(ent.to_dict(), indent=2, default=str)

            if action == 'list':
                ents = entity_store.list_entities(type=kwargs.get('entity_type'))
                if not ents:
                    return 'No entities stored yet.'
                lines = [f'{len(ents)} entit{"y" if len(ents)==1 else "ies"}:']
                for e in ents:
                    aliases = f' (aka {", ".join(e.aliases)})' if e.aliases else ''
                    lines.append(f'  [{e.type}] {e.name}{aliases}')
                return '\n'.join(lines)

            if action == 'add':
                entity_type = kwargs.get('entity_type')
                name        = kwargs.get('name', '').strip()
                if not entity_type or not name:
                    return 'add requires `entity_type` and `name`.'
                ent = entity_store.add(
                    type    = entity_type,
                    name    = name,
                    aliases = kwargs.get('aliases', []),
                    payload = kwargs.get('payload', {}),
                )
                return f'Added {ent.type}: {ent.name} (id: {ent.id}).'

            if action == 'update':
                ent_id = kwargs.get('id')
                fields = kwargs.get('fields', {})
                if not ent_id or not fields:
                    return 'update requires `id` and `fields`.'
                ent = entity_store.update(ent_id, fields)
                if not ent:
                    return f'No entity with id {ent_id}.'
                return f'Updated {ent.type}: {ent.name}.'

            if action == 'delete':
                ent_id = kwargs.get('id')
                if not ent_id:
                    return 'delete requires `id`.'
                ok = entity_store.delete(ent_id)
                return f'Deleted entity {ent_id}.' if ok else f'No entity with id {ent_id}.'

            return f'Unknown action: {action}'
        except Exception as e:
            return f'Entity store error: {e}'
