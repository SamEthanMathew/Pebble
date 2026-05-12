"""System context module — current date, time, platform. No config required."""

from __future__ import annotations
import datetime
import platform

from .base import ActionTier, PebbleModule


class SystemContextModule(PebbleModule):
    name         = 'system_context'
    display_name = 'System Context'
    description  = 'Gives Pebble access to the current date, time, and system info'
    icon         = '🕐'

    # No named actions today (single endpoint returns date/time/OS), but
    # declaring an AUTO default keeps the autonomy layer from gating reads.
    _default_tiers = {
        'cpu':       ActionTier.AUTO,
        'memory':    ActionTier.AUTO,
        'processes': ActionTier.AUTO,
    }

    config_fields = []

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'get_system_info'

    def tool_description(self) -> str:
        return 'Get the current date, time, day of the week, and OS information.'

    def tool_parameters(self) -> dict:
        return {'type': 'object', 'properties': {}, 'required': []}

    def execute(self, **_) -> str:
        now = datetime.datetime.now()
        tz  = datetime.datetime.now(datetime.timezone.utc).astimezone().tzname() or 'local'
        return (
            f"Date: {now.strftime('%A, %B %d, %Y')}\n"
            f"Time: {now.strftime('%I:%M %p')} ({tz})\n"
            f"OS:   {platform.system()} {platform.release()}"
        )
