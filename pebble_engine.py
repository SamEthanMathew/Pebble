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

import threading
from typing import Any, Callable


class PebbleEngine:
    """Owns Pebble's headless lifecycle. Construct with a `notifier` callable that
    actually shows a notification (signature: notifier(title=, body=, buttons=,
    metadata=)); the Windows shell passes a Tk renderer, a future web client posts
    to the browser. With no notifier, notifications are logged (dispatcher stub)."""

    FLUSH_INTERVAL = 30  # seconds — drain the dispatcher's rate-limit queue

    def __init__(self, *, notifier: Callable[..., None] | None = None) -> None:
        self._notifier = notifier
        self._dispatcher = None
        self._autonomy = None
        self._approvals = None
        self._started = False
        self._subscribed = False
        self._flush_interval = self.FLUSH_INTERVAL
        self._flush_stop = threading.Event()
        self._flush_thread: threading.Thread | None = None
        self._watchers: list = []   # Gmail/Slack watcher threads
        self._proactive = None      # ProactiveEngine (publish-only)

    # ── module registry ─────────────────────────────────────────────────────────

    def modules(self) -> list:
        """The active integration modules (read from config)."""
        from modules import get_active_modules
        return get_active_modules()

    # ── autonomy ────────────────────────────────────────────────────────────────

    def make_autonomy(self, **kwargs: Any):
        """Construct a fresh Autonomy router (the only thing allowed to fire
        writes). kwargs forwarded (approval_handler, modules). Prefer `.autonomy`
        for the shared production funnel; use this only for isolated/one-off use."""
        from autonomy import Autonomy
        return Autonomy(**kwargs)

    @property
    def autonomy(self):
        """The single production Autonomy funnel — owns pending ASK proposals so
        the approvals inbox has one source of truth (P0-3)."""
        if self._autonomy is None:
            from autonomy import Autonomy
            self._autonomy = Autonomy()
        return self._autonomy

    @property
    def approvals(self):
        """The single send-with-delay queue for user-approved outbound actions."""
        if self._approvals is None:
            from approval_queue import ApprovalQueue
            self._approvals = ApprovalQueue()
        return self._approvals

    # ── approvals-inbox API (backs the UI's Approvals panel in a later B step) ──

    def pending_approvals(self) -> dict:
        """ASK-tier proposals awaiting the user's decision."""
        return self.autonomy.pending_proposals()

    def approve(self, proposal_id: str):
        return self.autonomy.approve_pending(proposal_id)

    def deny(self, proposal_id: str):
        return self.autonomy.deny_pending(proposal_id)

    def queued_sends(self) -> list:
        """Approved outbound actions counting down in the send-with-delay window."""
        return self.approvals.pending()

    def cancel_send(self, item_id: str) -> bool:
        return self.approvals.cancel(item_id)

    def send_now(self, item_id: str) -> bool:
        return self.approvals.fire_now(item_id)

    # ── notifications (P0-2 seam) ───────────────────────────────────────────────

    @property
    def notifications(self):
        """The NotificationDispatcher — the single thing that decides popup/no-popup
        and now/later. Built lazily with the injected popup_fn (stays headless), and
        with rate-limit + quiet-hours read from config."""
        if self._dispatcher is None:
            from planners.dispatcher import NotificationDispatcher
            kw: dict = {'popup_fn': self._notifier}
            try:
                import crab_config
                notif_cfg = crab_config.get('notifications', {}) or {}
                sched_cfg = crab_config.get('schedule', {}) or {}
                if notif_cfg.get('max_per_10min'):
                    kw['max_per_10min'] = int(notif_cfg['max_per_10min'])
                if sched_cfg.get('quiet_hours_start'):
                    kw['quiet_hours_start'] = sched_cfg['quiet_hours_start']
                if sched_cfg.get('quiet_hours_end'):
                    kw['quiet_hours_end'] = sched_cfg['quiet_hours_end']
            except Exception:
                pass
            self._dispatcher = NotificationDispatcher(**kw)
        return self._dispatcher

    # ── lifecycle ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe the dispatcher to the bus and start draining its rate-limit
        queue so queued (non-critical) notifications eventually fire — otherwise
        everything past the first per-10-min slot would be silently dropped."""
        if self._started:
            return
        if not self._subscribed:
            self.notifications.start()   # subscribe exactly once (survives stop/start)
            self._subscribed = True
        self._flush_stop.clear()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name='pebble-notif-flush')
        self._flush_thread.start()
        self._started = True

    def _flush_loop(self) -> None:
        while not self._flush_stop.wait(timeout=self._flush_interval):
            try:
                self.notifications.flush_queue()
            except Exception:
                pass

    def stop(self) -> None:
        self._flush_stop.set()
        for w in self._watchers:
            try:
                w.stop()
            except Exception:
                pass
        if self._proactive is not None:
            try:
                self._proactive.stop()
            except Exception:
                pass
        self._started = False

    # ── background services (headless watcher supervision) ──────────────────────

    def start_services(self) -> None:
        """Start the dispatcher + Gmail/Slack watchers + proactive engine. Watchers
        publish to the bus; the dispatcher renders via the injected notifier. A
        headless client gets the same background services — the Windows shell only
        supplies the notifier."""
        self.start()
        self._start_watchers()
        self._start_proactive()

    def _active_modules(self) -> list:
        try:
            from modules import get_active_modules
            return get_active_modules()
        except Exception:
            return []

    def _start_watchers(self) -> None:
        active = self._active_modules()
        self._start_gmail_watcher(active)
        self._start_slack_watchers(active)

    def _start_gmail_watcher(self, active: list) -> None:
        try:
            from modules.gmail import GmailWatcherThread
            from events import bus, EMAIL_RECEIVED_IMPORTANT
        except Exception:
            return
        try:
            mod = next((m for m in active if getattr(m, 'name', '') == 'gmail'), None)
            if mod is None:
                return
            senders = [s.strip() for s in (mod.cfg.get('important_senders', '') or '').split(',') if s.strip()]
            if not senders:
                return
            w = GmailWatcherThread(
                important_senders=senders,
                on_notification=lambda info: bus.publish(EMAIL_RECEIVED_IMPORTANT, info),
            )
            w.start()
            self._watchers.append(w)
        except Exception:
            pass

    def _start_slack_watchers(self, active: list) -> None:
        try:
            from modules.slack_module import SlackWatcherThread
            from events import bus, SLACK_MESSAGE_IMPORTANT
        except Exception:
            return
        try:
            mod = next((m for m in active if getattr(m, 'name', '') == 'slack'), None)
            if mod is None:
                return
            cfg = mod.cfg
            publish = lambda info: bus.publish(SLACK_MESSAGE_IMPORTANT, info)  # noqa: E731

            def _channels(raw):
                return [c.strip().lstrip('#') for c in (raw or '').split(',') if c.strip()]

            workspaces = cfg.get('workspaces', {}) or {}
            if workspaces:
                for ws_name, ws_cfg in workspaces.items():
                    token = (ws_cfg.get('access_token') or '').strip()
                    channels = _channels(ws_cfg.get('important_channels', ''))
                    if not (token and channels):
                        continue
                    w = SlackWatcherThread(token=token, channels=channels,
                                           on_notification=publish, workspace_label=ws_name)
                    w.start()
                    self._watchers.append(w)
            else:
                token = (cfg.get('bot_token', '') or '').strip()
                channels = _channels(cfg.get('important_channels', ''))
                if token and channels:
                    w = SlackWatcherThread(token=token, channels=channels, on_notification=publish)
                    w.start()
                    self._watchers.append(w)
        except Exception:
            pass

    def _start_proactive(self) -> None:
        try:
            from proactive_engine import ProactiveEngine
            self._proactive = ProactiveEngine()
            self._proactive.start()
        except Exception:
            pass
