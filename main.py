#!/usr/bin/env python3
"""Pebble V2 — crab taskbar loader.
Left-click  → (placeholder — chat coming later)
Right-click → Quit
Drag        → reposition along taskbar edge
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import random
import tkinter as tk

import subprocess
import sys

from PIL import Image, ImageTk
from settings_window import SettingsWindow

# Capture unhandled exceptions to ~/.pebble/errors/<date>.jsonl
try:
    import error_reporter
    error_reporter.install_hooks()
except Exception:
    pass

try:
    from modules.gmail import GmailWatcherThread
    _HAS_GMAIL_WATCHER = True
except ImportError:
    _HAS_GMAIL_WATCHER = False

try:
    from modules.slack_module import SlackWatcherThread
    _HAS_SLACK_WATCHER = True
except ImportError:
    _HAS_SLACK_WATCHER = False

try:
    from proactive_engine import ProactiveEngine
    _HAS_PROACTIVE = True
except ImportError:
    _HAS_PROACTIVE = False

from pebble_engine import PebbleEngine

try:
    from pynput import keyboard as _pynput_kb
    _HAS_PYNPUT = True
except ImportError:
    _HAS_PYNPUT = False

from notification_popup import NotificationPopup

try:
    import numpy as np
    _NP = True
except ImportError:
    np  = None  # type: ignore
    _NP = False

# ── DPI awareness ──────────────────────────────────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    RESAMPLE = Image.Resampling.NEAREST
    LANCZOS  = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.NEAREST
    LANCZOS  = Image.LANCZOS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CRAB_DIR   = os.path.join(SCRIPT_DIR, 'crabpics')

KEY_COLOR = '#FF00FF'
CRAB_SIZE = 156
Y_NUDGE   = 46
FRAME_MS  = 33

IDLE_MIN, IDLE_MAX = 25, 70
BLINK_DUR          = 5
DOWN_DUR           = 7

DRAG_THRESHOLD = 8


# ── Win32 helpers ──────────────────────────────────────────────────────────────

def _screen_size():
    return (ctypes.windll.user32.GetSystemMetrics(0),
            ctypes.windll.user32.GetSystemMetrics(1))


def _taskbar_top():
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.bottom


def _cursor_pos():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# ── Sprite loading ─────────────────────────────────────────────────────────────

def _load_sprite(filename: str) -> ImageTk.PhotoImage:
    img = Image.open(os.path.join(CRAB_DIR, filename)).convert('RGBA')
    w, h = img.size
    corners = [img.getpixel((0, 0)), img.getpixel((w - 1, 0)),
               img.getpixel((0, h - 1)), img.getpixel((w - 1, h - 1))]
    bg = [sum(c[i] for c in corners) // 4 for i in range(3)]

    if _NP:
        arr  = np.array(img, dtype=np.int32)
        mask = ((np.abs(arr[:, :, 0] - bg[0]) < 60) &
                (np.abs(arr[:, :, 1] - bg[1]) < 60) &
                (np.abs(arr[:, :, 2] - bg[2]) < 60))
        arr[mask, 3] = 0
        img = Image.fromarray(arr.astype(np.uint8))
    else:
        px = img.load()
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if abs(r - bg[0]) < 60 and abs(g - bg[1]) < 60 and abs(b - bg[2]) < 60:
                    px[x, y] = (0, 0, 0, 0)

    img  = img.resize((CRAB_SIZE, CRAB_SIZE), RESAMPLE)
    base = Image.new('RGB', (CRAB_SIZE, CRAB_SIZE), KEY_COLOR)
    base.paste(img, mask=img.split()[3])
    return ImageTk.PhotoImage(base)


# ── Main class ─────────────────────────────────────────────────────────────────

class CrabPet:
    def __init__(self, root: tk.Tk | None = None):
        self.root = root or tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.wm_attributes('-transparentcolor', KEY_COLOR)

        self.canvas = tk.Canvas(
            self.root, width=CRAB_SIZE, height=CRAB_SIZE,
            bg=KEY_COLOR, highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        self.sprites = {
            'idle':  _load_sprite('crab.png'),
            'blink': _load_sprite('crabeyeclosed.png'),
            'down':  _load_sprite('crabdown.png'),
        }

        self.sw, _   = _screen_size()
        self.fixed_y = _taskbar_top() - CRAB_SIZE + Y_NUDGE

        self.cx          = float(self.sw // 2 - CRAB_SIZE // 2)
        self.state       = 'idle'
        self.frames_left = random.randint(IDLE_MIN, IDLE_MAX)

        self.img_id = self.canvas.create_image(0, 0, anchor='nw',
                                               image=self.sprites['idle'])

        self._is_dragging  = False
        self._drag_start_x = 0
        self._drag_crab_x  = 0.0
        self._chat_proc:  subprocess.Popen | None = None
        self._settings:   SettingsWindow   | None = None
        self._gmail_watcher: 'GmailWatcherThread | None' = None
        self._slack_watchers: list = []   # one SlackWatcherThread per workspace
        self._proactive: 'ProactiveEngine | None' = None
        self._engine: 'PebbleEngine | None' = None
        self._thinking_scheduler = None
        self._vault_for_callbacks = None
        self._hotkey_listener = None

        self.canvas.bind('<Button-1>',        self._mouse_down)
        self.canvas.bind('<B1-Motion>',       self._mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._mouse_up)
        self.canvas.bind('<Button-3>',        self._show_menu)

        # Un-withdraw first (if the wizard hid the root before handing it off),
        # THEN apply our geometry — Tk silently drops geometry calls on a
        # withdrawn window.
        try:
            self.root.deiconify()
        except Exception:
            pass
        self.root.update_idletasks()
        self._place()
        self.root.update_idletasks()
        self.root.lift()
        self._tick()
        self.root.after(2000, self._start_notification_services)
        self._start_hotkey()
        self.root.mainloop()

    # ── positioning ────────────────────────────────────────────────────────────

    def _place(self):
        x = int(max(0, min(self.cx, self.sw - CRAB_SIZE)))
        self.root.geometry(f'{CRAB_SIZE}x{CRAB_SIZE}+{x}+{self.fixed_y}')

    # ── sprite ─────────────────────────────────────────────────────────────────

    def _set_sprite(self, name: str):
        if name != self.state:
            self.state = name
            self.canvas.itemconfig(self.img_id, image=self.sprites[name])

    # ── mouse ──────────────────────────────────────────────────────────────────

    def _mouse_down(self, event):
        self._is_dragging  = False
        self._drag_start_x = event.x_root
        self._drag_crab_x  = self.cx

    def _mouse_drag(self, event):
        if abs(event.x_root - self._drag_start_x) >= DRAG_THRESHOLD:
            self._is_dragging = True
        if self._is_dragging:
            self.cx = self._drag_crab_x + (event.x_root - self._drag_start_x)
            self.cx = max(0.0, min(self.cx, float(self.sw - CRAB_SIZE)))
            self._place()

    def _mouse_up(self, event):
        if not self._is_dragging:
            self._on_click()

    def _on_click(self):
        # If already open, don't spawn a second window
        if self._chat_proc is not None and self._chat_proc.poll() is None:
            return
        sw, sh = _screen_size()
        win_w, win_h = 1100, 740
        x = max(0, (sw - win_w) // 2)
        y = max(0, (sh - win_h) // 2)
        script = os.path.join(SCRIPT_DIR, 'chat_server.py')
        self._chat_proc = subprocess.Popen(
            [sys.executable, script, f'--x={x}', f'--y={y}'],
            cwd=SCRIPT_DIR,
        )

    def _on_settings(self):
        if self._settings is None:
            self._settings = SettingsWindow(self.root)
        self._settings.show()

    def _start_notification_services(self):
        """Start Gmail/Slack watchers and proactive engine if configured."""
        # Cache active modules once; multiple watchers read from this list
        active = []
        try:
            from modules import get_active_modules
            active = get_active_modules()
        except Exception:
            pass

        # ── Gmail watcher ─────────────────────────────────────────────────────
        if _HAS_GMAIL_WATCHER:
            try:
                gmail_mod = next((m for m in active if m.name == 'gmail'), None)
                if gmail_mod is not None:
                    cfg = gmail_mod.cfg
                    senders_raw = cfg.get('important_senders', '')
                    senders = [s.strip() for s in senders_raw.split(',') if s.strip()]
                    if senders:
                        self._gmail_watcher = GmailWatcherThread(
                            important_senders=senders,
                            on_notification=self._on_gmail_notification,
                        )
                        self._gmail_watcher.start()
            except Exception:
                pass

        # ── Slack watchers (one per workspace) ────────────────────────────────
        if _HAS_SLACK_WATCHER:
            try:
                slack_mod = next((m for m in active if m.name == 'slack'), None)
                if slack_mod is not None:
                    cfg = slack_mod.cfg
                    workspaces = cfg.get('workspaces', {}) or {}

                    if workspaces:
                        # Multi-workspace path
                        for ws_name, ws_cfg in workspaces.items():
                            token = (ws_cfg.get('access_token') or '').strip()
                            channels_raw = ws_cfg.get('important_channels', '')
                            channels = [c.strip().lstrip('#')
                                        for c in channels_raw.split(',') if c.strip()]
                            if not (token and channels):
                                continue
                            w = SlackWatcherThread(
                                token=token,
                                channels=channels,
                                on_notification=self._on_slack_notification,
                                workspace_label=ws_name,
                            )
                            w.start()
                            self._slack_watchers.append(w)
                    else:
                        # Backward compat: single-workspace
                        token = cfg.get('bot_token', '').strip()
                        channels_raw = cfg.get('important_channels', '')
                        channels = [c.strip().lstrip('#')
                                    for c in channels_raw.split(',') if c.strip()]
                        if token and channels:
                            w = SlackWatcherThread(
                                token=token,
                                channels=channels,
                                on_notification=self._on_slack_notification,
                            )
                            w.start()
                            self._slack_watchers.append(w)
            except Exception:
                pass

        if _HAS_PROACTIVE:
            try:
                # The dispatcher (owned by the headless engine) is the SOLE
                # notification subscriber — it applies rate-limit / quiet-hours /
                # dedup / focus gating and calls our Tk renderer. ProactiveEngine
                # only publishes events now (P0-2 cutover).
                self._engine = PebbleEngine(notifier=self._render_notification)
                self._engine.start()
                self._proactive = ProactiveEngine()
                self._proactive.start()
            except Exception:
                pass

        # ── Thinking-pass scheduler + reactive challenge (Phase E) ──────────
        try:
            import crab_config
            tp_cfg = crab_config.get('thinking_passes', {}) or {}
            if any((tp_cfg.get(k, {}) or {}).get('enabled')
                   for k in ('close', 'connect', 'drift', 'ideas', 'emerge')):
                from storage import ThinkingScheduler
                self._thinking_scheduler = ThinkingScheduler()
                self._thinking_scheduler.start()

            if (tp_cfg.get('challenge_on_edit', {}) or {}).get('enabled'):
                # Need a long-lived Vault to register the watchdog callback on.
                from storage import Vault, ChallengeOnDecisionEdit
                vault_path = (crab_config.get_module_config('obsidian') or {}).get('vault_path')
                if vault_path:
                    self._vault_for_callbacks = Vault(vault_path, autostart_watcher=True)
                    self._vault_for_callbacks.register_user_edit_callback(
                        ChallengeOnDecisionEdit(cooldown_seconds=300)
                    )
        except Exception:
            pass

    def _start_hotkey(self):
        """Register Alt+Space global hotkey to open Pebble from anywhere."""
        if not _HAS_PYNPUT:
            return
        try:
            _held: set = set()

            def _on_press(key):
                _held.add(key)
                alt   = _pynput_kb.Key.alt   in _held or _pynput_kb.Key.alt_l in _held or _pynput_kb.Key.alt_r in _held
                space = _pynput_kb.Key.space in _held
                if alt and space:
                    self.root.after(0, self._on_click)

            def _on_release(key):
                _held.discard(key)

            self._hotkey_listener = _pynput_kb.Listener(
                on_press=_on_press, on_release=_on_release, daemon=True
            )
            self._hotkey_listener.start()
        except Exception:
            pass

    def _render_notification(self, *, title, body, buttons=None, metadata=None):
        """popup_fn for the NotificationDispatcher. Renders a Tk toast on the main
        thread, translating the dispatcher's headless button action-specs
        ({'label','action','style'}) into real Tk commands. Runs from the
        dispatcher (a worker/bus thread) → must schedule via root.after."""
        def _show():
            resolved = []
            for b in (buttons or []):
                action  = b.get('action')
                command = self._on_click if action == 'open_chat' else (lambda: None)
                resolved.append({'label':   b.get('label', 'OK'),
                                 'command': command,
                                 'style':   b.get('style', 'default')})
            if not resolved:
                resolved = [{'label': 'Dismiss', 'command': lambda: None, 'style': 'default'}]
            # auto_dismiss_ms is carried per-notification by the dispatcher
            # (0 = persist until dismissed, e.g. reminders / focus-end).
            auto_dismiss = (metadata or {}).get('auto_dismiss_ms', 15000)
            try:
                NotificationPopup(self.root, title=title, body=body,
                                  buttons=resolved, auto_dismiss_ms=auto_dismiss).show()
            except Exception:
                pass
        try:
            self.root.after(0, _show)
        except Exception:
            pass

    def _on_slack_notification(self, info: dict):
        """Slack-watcher callback; runs in worker thread → schedule on Tk thread."""
        def _show():
            ws    = info.get('workspace', '')
            ch    = info.get('channel_name', '?')
            who   = info.get('user_name', 'someone')
            text  = (info.get('text') or '')[:80]
            if not text:
                text = '(message has no text — maybe attachment/file)'

            title = f'💬  {ws}  ·  #{ch}  ·  {who}' if ws else f'💬  #{ch}  ·  {who}'
            popup = NotificationPopup(
                self.root,
                title=title,
                body=text,
                buttons=[
                    {'label': 'Open Pebble', 'command': self._on_click, 'style': 'primary'},
                    {'label': 'Ignore',      'command': lambda: None,    'style': 'default'},
                ],
                auto_dismiss_ms=15000,
            )
            popup.show()
        self.root.after(0, _show)

    def _on_gmail_notification(self, info: dict):
        """Called from background thread — must schedule on tkinter thread."""
        def _show():
            sender = info.get('sender', 'Unknown')
            subject = info.get('subject', '(no subject)')
            if len(subject) > 55:
                subject = subject[:52] + '...'

            popup = NotificationPopup(
                self.root,
                title=f'📧 {sender}',
                body=subject,
                buttons=[
                    {'label': 'Ask Pebble', 'command': self._on_click, 'style': 'primary'},
                    {'label': 'Ignore', 'command': lambda: None, 'style': 'default'},
                ],
                auto_dismiss_ms=10000,
            )
            popup.show()

        self.root.after(0, _show)

    # ── right-click menu ───────────────────────────────────────────────────────

    def _show_menu(self, event):
        menu = tk.Menu(
            self.root, tearoff=0,
            bg='#1e1e2e', fg='#cdd6f4',
            activebackground='#534AB7', activeforeground='#ffffff',
            font=('Segoe UI', 10), bd=0, relief='flat',
        )
        def defer(cmd):
            return lambda: self.root.after(10, cmd)

        menu.add_command(label='  Ask Pebble', command=defer(self._on_click))
        menu.add_command(label='  Settings',   command=defer(self._on_settings))
        menu.add_separator()
        def _quit():
            if self._gmail_watcher:
                self._gmail_watcher.stop()
            for w in self._slack_watchers:
                try: w.stop()
                except Exception: pass
            if self._proactive:
                self._proactive.stop()
            if self._engine:
                try: self._engine.stop()
                except Exception: pass
            if self._thinking_scheduler:
                try: self._thinking_scheduler.stop()
                except Exception: pass
            if self._vault_for_callbacks:
                try: self._vault_for_callbacks.stop()
                except Exception: pass
            if self._hotkey_listener:
                try:
                    self._hotkey_listener.stop()
                except Exception:
                    pass
            self.root.destroy()
        menu.add_command(label='  Quit', command=defer(_quit))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ── main loop ──────────────────────────────────────────────────────────────

    def _tick(self):
        # sprite animation
        self.frames_left -= 1
        if self.frames_left <= 0:
            if self.state == 'idle':
                nxt = random.choice(['blink', 'down'])
                self._set_sprite(nxt)
                self.frames_left = BLINK_DUR if nxt == 'blink' else DOWN_DUR
            else:
                self._set_sprite('idle')
                self.frames_left = random.randint(IDLE_MIN, IDLE_MAX)

        self.root.after(FRAME_MS, self._tick)


if __name__ == '__main__':
    import crab_config
    root = None
    if not crab_config.get('setup_complete'):
        from setup_wizard import run_setup
        root = run_setup()          # returns the Tk root after wizard exits
    if crab_config.get('setup_complete'):
        CrabPet(root)               # reuses wizard root — no second Tk()
