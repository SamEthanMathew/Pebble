"""Headless Pebble engine — Milestone B / P0-1.

Today Pebble's lifecycle (watcher supervision, notification routing, autonomy,
scheduling) is fused into the Tk taskbar class in main.py, so there is nothing a
web/mac/phone client could talk to. This is the seam that fixes that: all
business logic moves here, with **zero GUI/OS toolkit imports**, and each
platform shell becomes a thin client that injects its own ports (a `notifier`
today; an `IdleSource`/`HotkeyRegistrar`/`TrayShell` as B proceeds).

Invariant (enforced by tests/test_engine_headless.py): importing this module must
never pull in tkinter / pywebview / pynput. Keep GUI-touching imports out — the
shell owns those and passes callables in.

This is deliberately additive right now: it centralizes the notification-routing
seam (the dispatcher as the sole subscriber, popup_fn injected) so P0-2's cutover
and P0-3's planner→autonomy funnel can land here, tested, before main.py is
reduced to a thin Windows shell.
"""

from __future__ import annotations

from typing import Any, Callable


class PebbleEngine:
    """Owns Pebble's headless lifecycle. Construct with a `notifier` callable that
    actually shows a notification (signature: notifier(title=, body=, buttons=,
    metadata=)); the Windows shell passes a Tk renderer, a future web client posts
    to the browser. With no notifier, notifications are logged (dispatcher stub)."""

    def __init__(self, *, notifier: Callable[..., None] | None = None) -> None:
        self._notifier = notifier
        self._dispatcher = None
        self._started = False

    # ── module registry ─────────────────────────────────────────────────────────

    def modules(self) -> list:
        """The active integration modules (read from config)."""
        from modules import get_active_modules
        return get_active_modules()

    # ── autonomy ────────────────────────────────────────────────────────────────

    def make_autonomy(self, **kwargs: Any):
        """Construct an Autonomy router (the only thing allowed to fire writes).
        kwargs forwarded (approval_handler, modules)."""
        from autonomy import Autonomy
        return Autonomy(**kwargs)

    # ── notifications (P0-2 seam) ───────────────────────────────────────────────

    @property
    def notifications(self):
        """The NotificationDispatcher — the single thing that decides popup/no-popup
        and now/later. Built lazily with the injected popup_fn so it stays headless."""
        if self._dispatcher is None:
            from planners.dispatcher import NotificationDispatcher
            self._dispatcher = NotificationDispatcher(popup_fn=self._notifier)
        return self._dispatcher

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Wire the dispatcher as the notification consumer (subscribes to the bus).
        Idempotent-ish: only subscribes once."""
        if self._started:
            return
        self.notifications.start()
        self._started = True

    def stop(self) -> None:
        self._started = False
