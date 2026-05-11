"""Base class every Pebble module must implement."""

from __future__ import annotations
from abc import ABC, abstractmethod
from enum import Enum


class ActionTier(Enum):
    """Per docs/contracts.md §1.

    AUTO   — act silently (reads, internal state, user-initiated timers)
    NOTIFY — act, then tell the user (drafts, task changes, journal appends)
    ASK    — propose, wait for approval (outbound sends, calendar writes, deletes)
    """
    AUTO   = 'auto'
    NOTIFY = 'notify'
    ASK    = 'ask'


class PebbleModule(ABC):
    name:         str        = ''
    display_name: str        = ''
    description:  str        = ''
    icon:         str        = '🔌'
    # [{key, label, type: 'text'|'path'|'password'}]
    config_fields: list[dict] = []

    # Subclasses override with per-action defaults. Anything not listed defaults to ASK.
    # Example:  _default_tiers = {'search': ActionTier.AUTO, 'send': ActionTier.ASK}
    _default_tiers: dict[str, ActionTier] = {}

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

    # ── action tier (autonomy layer) ───────────────────────────────────────────

    def action_tier(self, action_name: str) -> ActionTier:
        """Resolve the tier for a given action.

        Precedence: config override (config.tiers.<tool_name>.<action_name>) →
        self._default_tiers → ActionTier.ASK.
        """
        try:
            import crab_config
            overrides = crab_config.get('tiers', {}) or {}
            mod_overrides = overrides.get(self.tool_name(), {})
            raw = mod_overrides.get(action_name)
            if raw:
                return ActionTier(raw)
        except Exception:
            pass
        return self._default_tiers.get(action_name, ActionTier.ASK)

    def outbound_target_id(self, action_name: str, args: dict) -> str | None:
        """For outbound actions, the canonical identifier of the target
        (e.g. recipient email, channel id). Used by the first-time-action
        ledger to ask once per new target. Default: None (non-outbound).

        Subclasses override for actions like gmail.send, slack.send.
        """
        return None

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
            return f'  {self.tool_name()}({first_key}) — {self.tool_description()}'
        return f'  {self.tool_name()}() — {self.tool_description()}'
