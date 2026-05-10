"""Base class every Pebble module must implement."""

from __future__ import annotations
from abc import ABC, abstractmethod


class PebbleModule(ABC):
    name:         str        = ''
    display_name: str        = ''
    description:  str        = ''
    icon:         str        = '🔌'
    # [{key, label, type: 'text'|'path'|'password'}]
    config_fields: list[dict] = []

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def is_ready(self) -> bool:
        """Return True when the module has everything it needs to run."""
        return True

    # ── tool identity ──────────────────────────────────────────────────────────

    @abstractmethod
    def tool_name(self) -> str: ...

    @abstractmethod
    def tool_description(self) -> str: ...

    @abstractmethod
    def tool_parameters(self) -> dict:
        """JSON Schema object describing the tool's parameters."""
        ...

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Run the tool and return a plain-text result."""
        ...

    # ── schema helpers (built from above — modules don't override these) ───────

    def to_openai_schema(self) -> dict:
        return {
            'type': 'function',
            'function': {
                'name':        self.tool_name(),
                'description': self.tool_description(),
                'parameters':  self.tool_parameters(),
            },
        }

    def to_anthropic_schema(self) -> dict:
        return {
            'name':         self.tool_name(),
            'description':  self.tool_description(),
            'input_schema': self.tool_parameters(),
        }

    def to_prompted_line(self) -> str:
        """One-line description used in the ReAct system prompt."""
        props = self.tool_parameters().get('properties', {})
        if props:
            first_key = next(iter(props))
            desc = props[first_key].get('description', first_key)
            return f'  {self.tool_name()}({first_key}) — {self.tool_description()}'
        return f'  {self.tool_name()}() — {self.tool_description()}'
