"""Clipboard module — read current clipboard text. No config required."""

from __future__ import annotations
import ctypes
import ctypes.wintypes

from .base import ActionTier, PebbleModule


def _read_win32_clipboard() -> str:
    CF_UNICODETEXT = 13
    if not ctypes.windll.user32.OpenClipboard(0):
        return ''
    try:
        handle = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ''
        ptr = ctypes.windll.kernel32.GlobalLock(handle)
        if not ptr:
            return ''
        try:
            return ctypes.wstring_at(ptr)
        finally:
            ctypes.windll.kernel32.GlobalUnlock(handle)
    finally:
        ctypes.windll.user32.CloseClipboard()


class ClipboardModule(PebbleModule):
    name         = 'clipboard'
    display_name = 'Clipboard'
    description  = 'Lets Pebble read what you currently have copied'
    icon         = '📋'

    _default_tiers = {
        'get': ActionTier.AUTO,
    }

    config_fields = []

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'get_clipboard'

    def tool_description(self) -> str:
        return (
            'Get the current text content of the clipboard. '
            'Use when the user says "this", "that", refers to something they copied, '
            'or pastes content and asks about it.'
        )

    def tool_parameters(self) -> dict:
        return {'type': 'object', 'properties': {}, 'required': []}

    def execute(self, **_) -> str:
        try:
            text = _read_win32_clipboard()
        except Exception as e:
            return f'Could not read clipboard: {e}'
        if not text or not text.strip():
            return 'Clipboard is empty.'
        if len(text) > 2000:
            text = text[:2000] + '\n… [truncated]'
        return f'Clipboard content:\n{text}'
