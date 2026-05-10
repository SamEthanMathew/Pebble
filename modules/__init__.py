"""Module registry — add new PebbleModule subclasses here to register them."""

from __future__ import annotations

import crab_config
from .base import PebbleModule
from .system_context import SystemContextModule
from .obsidian import ObsidianModule
from .clipboard import ClipboardModule

try:
    from .tasks import TaskModule
    _HAS_TASKS = True
except ImportError:
    _HAS_TASKS = False

try:
    from .journal import JournalModule
    _HAS_JOURNAL = True
except ImportError:
    _HAS_JOURNAL = False

try:
    from .notion import NotionModule
    _HAS_NOTION = True
except ImportError:
    _HAS_NOTION = False

try:
    from .gmail import GmailModule
    _HAS_GMAIL = True
except ImportError:
    _HAS_GMAIL = False

try:
    from .gcal import GCalModule
    _HAS_GCAL = True
except ImportError:
    _HAS_GCAL = False

try:
    from .brave_search import BraveSearchModule
    _HAS_BRAVE = True
except ImportError:
    _HAS_BRAVE = False

try:
    from .weather import WeatherModule
    _HAS_WEATHER = True
except ImportError:
    _HAS_WEATHER = False

try:
    from .alpaca_market import AlpacaMarketModule
    _HAS_ALPACA = True
except ImportError:
    _HAS_ALPACA = False

try:
    from .stt import STTModule
    _HAS_STT = True
except ImportError:
    _HAS_STT = False

try:
    from .screenshot import ScreenshotModule
    _HAS_SCREENSHOT = True
except ImportError:
    _HAS_SCREENSHOT = False

try:
    from .spotify import SpotifyModule
    _HAS_SPOTIFY = True
except ImportError:
    _HAS_SPOTIFY = False

try:
    from .github_module import GitHubModule
    _HAS_GITHUB = True
except ImportError:
    _HAS_GITHUB = False

try:
    from .slack_module import SlackModule
    _HAS_SLACK = True
except ImportError:
    _HAS_SLACK = False

try:
    from .todoist import TodoistModule
    _HAS_TODOIST = True
except ImportError:
    _HAS_TODOIST = False

try:
    from .news_feed import NewsFeedModule
    _HAS_NEWS = True
except ImportError:
    _HAS_NEWS = False

try:
    from .focus_timer import FocusTimerModule
    _HAS_FOCUS = True
except ImportError:
    _HAS_FOCUS = False

try:
    from .file_search import FileSearchModule
    _HAS_FILES = True
except ImportError:
    _HAS_FILES = False

try:
    from .reminders import RemindersModule
    _HAS_REMINDERS = True
except ImportError:
    _HAS_REMINDERS = False

try:
    from .canvas import CanvasModule
    _HAS_CANVAS = True
except ImportError:
    _HAS_CANVAS = False

try:
    from .kalshi import KalshiModule
    _HAS_KALSHI = True
except ImportError:
    _HAS_KALSHI = False

try:
    from .crypto import CryptoModule
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

try:
    from .discord_module import DiscordModule
    _HAS_DISCORD = True
except ImportError:
    _HAS_DISCORD = False

try:
    from .memory import MemoryModule
    _HAS_MEMORY = True
except ImportError:
    _HAS_MEMORY = False

ALL_MODULES: list[type[PebbleModule]] = [m for m in [
    SystemContextModule,
    ClipboardModule,
    ObsidianModule,
    TaskModule          if _HAS_TASKS      else None,
    JournalModule       if _HAS_JOURNAL    else None,
    RemindersModule     if _HAS_REMINDERS  else None,
    FocusTimerModule    if _HAS_FOCUS      else None,
    FileSearchModule    if _HAS_FILES      else None,
    NotionModule        if _HAS_NOTION     else None,
    GmailModule         if _HAS_GMAIL      else None,
    GCalModule          if _HAS_GCAL       else None,
    SpotifyModule       if _HAS_SPOTIFY    else None,
    SlackModule         if _HAS_SLACK      else None,
    GitHubModule        if _HAS_GITHUB     else None,
    TodoistModule       if _HAS_TODOIST    else None,
    NewsFeedModule      if _HAS_NEWS       else None,
    BraveSearchModule   if _HAS_BRAVE      else None,
    WeatherModule       if _HAS_WEATHER    else None,
    AlpacaMarketModule  if _HAS_ALPACA     else None,
    CanvasModule        if _HAS_CANVAS     else None,
    KalshiModule        if _HAS_KALSHI     else None,
    CryptoModule        if _HAS_CRYPTO     else None,
    DiscordModule       if _HAS_DISCORD    else None,
    MemoryModule        if _HAS_MEMORY     else None,
    STTModule           if _HAS_STT        else None,
    ScreenshotModule    if _HAS_SCREENSHOT else None,
] if m is not None]

_REGISTRY: dict[str, type[PebbleModule]] = {cls.name: cls for cls in ALL_MODULES}


def get_active_modules() -> list[PebbleModule]:
    """Instantiate and return every module that is enabled and ready."""
    cfgs   = crab_config.get_all_module_configs()
    active = []
    for cls in ALL_MODULES:
        entry = cfgs.get(cls.name, {})
        default_on = cls.name in ('system_context', 'clipboard', 'tasks', 'journal',
                                   'reminders', 'memory', 'focus_timer', 'file_search',
                                   'news_feed', 'screenshot', 'stt', 'crypto')
        if not entry.get('enabled', default_on):
            continue
        instance = cls(entry)
        if instance.is_ready():
            active.append(instance)
    return active
