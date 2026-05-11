"""Pebble Settings window — opened from the right-click tray menu.

Contains a Modules tab where users can enable/disable integrations and
enter any required configuration (vault paths, API keys, etc.).
"""

from __future__ import annotations
import ctypes
import os
import tkinter as tk
from tkinter import filedialog

import crab_config
from modules import ALL_MODULES
from modules.base import PebbleModule

C = {
    'bg':      '#0d0d1a',
    'panel':   '#141427',
    'card':    '#1a1a30',
    'card_hl': '#20203a',
    'border':  '#272745',
    'accent':  '#534AB7',
    'accent2': '#6560c8',
    'teal':    '#1D9E75',
    'text':    '#e2e2f0',
    'dim':     '#62628a',
    'muted':   '#2e2e48',
    'err':     '#e05c7a',
    'pill':    '#232340',
}

W, H = 480, 620

ALWAYS_ACTIVE = {'system_context', 'clipboard'}

_GOOGLE_TOKEN = os.path.expanduser('~/.pebble/google_token.json')


class SettingsWindow:
    def __init__(self, parent: tk.Tk):
        self._parent  = parent
        self._visible = False
        self._field_vars: dict[str, dict[str, tk.StringVar]] = {}

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(False)
        self.win.title('Pebble Settings')
        self.win.configure(bg=C['bg'])
        self.win.resizable(False, False)
        self.win.withdraw()
        self.win.protocol('WM_DELETE_WINDOW', self.hide)
        _dark_titlebar(self.win)
        self._build()

    # ── show / hide ────────────────────────────────────────────────────────────

    def toggle(self):
        if self._visible:
            self.hide()
        else:
            self.show()

    def show(self):
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.geometry(f'{W}x{H}+{(sw - W)//2}+{(sh - H)//2}')
        self._refresh()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self._visible = True

    def hide(self):
        self.win.withdraw()
        self._visible = False

    # ── build ──────────────────────────────────────────────────────────────────

    def _build(self):
        # title bar
        hdr = tk.Frame(self.win, bg=C['accent'], height=46)
        hdr.pack(fill='x')
        hdr.pack_propagate(False)
        tk.Label(hdr, text='⚙  Settings', bg=C['accent'], fg='white',
                 font=('Segoe UI', 12, 'bold')).pack(side='left', padx=16)
        tk.Button(
            hdr, text='✕', command=self.hide,
            bg=C['accent'], fg='#ccccdd',
            activebackground=C['err'], activeforeground='white',
            font=('Segoe UI', 10), relief='flat', bd=0,
            padx=14, cursor='hand2',
        ).pack(side='right')

        # ── Behavior section (Dry Run toggle) ────────────────────────────────
        tk.Label(self.win, text='Behavior', bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 13, 'bold')).pack(anchor='w', padx=20, pady=(18, 4))

        behavior_outer = tk.Frame(self.win, bg=C['border'], padx=1, pady=1)
        behavior_outer.pack(fill='x', padx=20, pady=(0, 6))
        behavior_card = tk.Frame(behavior_outer, bg=C['card'])
        behavior_card.pack(fill='x', ipadx=12, ipady=10)

        dry_row = tk.Frame(behavior_card, bg=C['card'])
        dry_row.pack(fill='x', padx=4, pady=2)
        tk.Label(dry_row, text='🧪  Dry Run Mode', bg=C['card'], fg=C['text'],
                 font=('Segoe UI', 10, 'bold')).pack(side='left')
        self._dry_run_var = tk.BooleanVar(value=bool(crab_config.get('dry_run', False)))

        def _toggle_dry_run():
            crab_config.set_value('dry_run', bool(self._dry_run_var.get()))

        tk.Checkbutton(
            dry_row, variable=self._dry_run_var, command=_toggle_dry_run,
            bg=C['card'], fg=C['text'], selectcolor=C['accent'],
            activebackground=C['card'], bd=0, highlightthickness=0,
        ).pack(side='right')

        tk.Label(
            behavior_card,
            text=("When on, Pebble logs every action it WOULD take to "
                  "~/.pebble/dry_run_previews/ instead of calling external APIs. "
                  "Review with /review-drafts in chat."),
            bg=C['card'], fg=C['dim'], font=('Segoe UI', 9),
            wraplength=W - 80, justify='left',
        ).pack(anchor='w', padx=4, pady=(4, 0))

        # ── Modules section ─────────────────────────────────────────────────
        tk.Label(self.win, text='Modules', bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 13, 'bold')).pack(anchor='w', padx=20, pady=(18, 4))
        tk.Label(
            self.win,
            text='Enable integrations to give Pebble access to your apps and data.',
            bg=C['bg'], fg=C['dim'], font=('Segoe UI', 9),
        ).pack(anchor='w', padx=20, pady=(0, 10))

        # scrollable module list
        outer = tk.Frame(self.win, bg=C['border'], padx=1, pady=1)
        outer.pack(fill='both', expand=True, padx=20, pady=(0, 20))

        canvas = tk.Canvas(outer, bg=C['card'], highlightthickness=0, bd=0)
        sb     = tk.Scrollbar(outer, orient='vertical', command=canvas.yview,
                               bg=C['panel'], troughcolor=C['bg'],
                               relief='flat', bd=0)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        self._scroll_frame = tk.Frame(canvas, bg=C['bg'])
        self._scroll_win   = canvas.create_window((0, 0), window=self._scroll_frame,
                                                   anchor='nw')

        def _on_resize(e):
            canvas.itemconfig(self._scroll_win, width=e.width)
        canvas.bind('<Configure>', _on_resize)

        def _on_frame_resize(e):
            canvas.configure(scrollregion=canvas.bbox('all'))
        self._scroll_frame.bind('<Configure>', _on_frame_resize)

        canvas.bind_all('<MouseWheel>',
                        lambda e: canvas.yview_scroll(-1 * (e.delta // 120), 'units'))

        self._canvas = canvas
        self._build_module_cards()

    def _build_module_cards(self):
        for widget in self._scroll_frame.winfo_children():
            widget.destroy()
        self._field_vars = {}

        cfgs = crab_config.get_all_module_configs()

        active_modules    = []
        available_modules = []

        for cls in ALL_MODULES:
            entry   = cfgs.get(cls.name, {})
            default = cls.name in ('system_context', 'clipboard', 'tasks', 'journal')
            enabled = entry.get('enabled', default)
            if enabled:
                active_modules.append((cls, entry))
            else:
                available_modules.append((cls, entry))

        # Sort: ALWAYS_ACTIVE pinned last in active section
        active_modules.sort(key=lambda x: (x[0].name in ALWAYS_ACTIVE, x[0].display_name))
        available_modules.sort(key=lambda x: x[0].display_name)

        if active_modules:
            self._section_label('Active Integrations')
            for cls, entry in active_modules:
                self._add_active_card(cls, entry)

        if available_modules:
            self._section_label('Available Integrations')
            for cls, entry in available_modules:
                self._add_available_card(cls, entry)

    def _section_label(self, text: str):
        frame = tk.Frame(self._scroll_frame, bg=C['bg'])
        frame.pack(fill='x', padx=20, pady=(16, 6))
        tk.Label(frame, text=text, bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 11, 'bold')).pack(side='left')
        tk.Frame(frame, bg=C['border'], height=1).pack(
            side='left', fill='x', expand=True, padx=(10, 0), pady=10)

    def _add_active_card(self, cls: type[PebbleModule], saved_cfg: dict):
        always_active = cls.name in ALWAYS_ACTIVE

        outer = tk.Frame(self._scroll_frame, bg=C['bg'])
        outer.pack(fill='x', padx=20, pady=(0, 8))

        frame = tk.Frame(outer, bg=C['card'], relief='flat', bd=0)
        frame.pack(fill='x')

        # colored left accent bar
        accent_bar = tk.Frame(frame, bg=C['accent'], width=3)
        accent_bar.pack(side='left', fill='y')

        body = tk.Frame(frame, bg=C['card'])
        body.pack(side='left', fill='x', expand=True, padx=14, pady=12)

        # header row
        hrow = tk.Frame(body, bg=C['card'])
        hrow.pack(fill='x')

        tk.Label(hrow, text=cls.icon, bg=C['card'],
                 font=('Segoe UI', 14)).pack(side='left')
        tk.Label(hrow, text=f'  {cls.display_name}', bg=C['card'], fg=C['text'],
                 font=('Segoe UI', 10, 'bold')).pack(side='left')

        if always_active:
            # Padlock — cannot be removed
            tk.Label(hrow, text='🔒', bg=C['card'], fg=C['dim'],
                     font=('Segoe UI', 9)).pack(side='right')
        else:
            remove_btn = tk.Button(
                hrow, text='✕  Remove',
                bg=C['err'], fg='white',
                activebackground='#c04060', activeforeground='white',
                font=('Segoe UI', 8), relief='flat', bd=0,
                padx=12, pady=3, cursor='hand2',
            )
            remove_btn.pack(side='right')

            def _remove(c=cls):
                # Save current field values before disabling
                cfg_now = crab_config.get_module_config(c.name)
                for fld in c.config_fields:
                    var = self._field_vars.get(c.name, {}).get(fld['key'])
                    if var:
                        cfg_now[fld['key']] = var.get()
                cfg_now['enabled'] = False
                crab_config.set_module_config(c.name, cfg_now)
                self._build_module_cards()

            remove_btn.config(command=_remove)

        tk.Label(body, text=cls.description, bg=C['card'], fg=C['dim'],
                 font=('Segoe UI', 9), wraplength=360, justify='left',
                 ).pack(anchor='w', pady=(3, 0))

        # config fields — always shown for active modules
        self._field_vars[cls.name] = {}
        if cls.config_fields:
            fields_frame = tk.Frame(body, bg=C['card'])
            fields_frame.pack(fill='x', pady=(8, 0))
            for field in cls.config_fields:
                self._add_field(fields_frame, cls.name, field, saved_cfg)

        # ready indicator
        instance = cls(saved_cfg)
        if not instance.is_ready() and cls.config_fields:
            tk.Label(body, text='⚠  Not configured — fill in fields above',
                     bg=C['card'], fg=C['err'],
                     font=('Segoe UI', 8, 'italic')).pack(anchor='w', pady=(4, 0))

        # bottom border
        tk.Frame(outer, bg=C['border'], height=1).pack(fill='x')

    def _add_available_card(self, cls: type[PebbleModule], saved_cfg: dict):
        outer = tk.Frame(self._scroll_frame, bg=C['bg'])
        outer.pack(fill='x', padx=20, pady=(0, 4))

        frame = tk.Frame(outer, bg=C['panel'], relief='flat', bd=0)
        frame.pack(fill='x')

        body = tk.Frame(frame, bg=C['panel'])
        body.pack(fill='x', padx=12, pady=8)

        # left side: icon + name + description
        left = tk.Frame(body, bg=C['panel'])
        left.pack(side='left', fill='x', expand=True)

        tk.Label(left, text=cls.icon, bg=C['panel'],
                 font=('Segoe UI', 14)).pack(side='left')

        text_col = tk.Frame(left, bg=C['panel'])
        text_col.pack(side='left', padx=(6, 0))

        tk.Label(text_col, text=cls.display_name, bg=C['panel'], fg=C['text'],
                 font=('Segoe UI', 10, 'bold')).pack(anchor='w')

        desc = cls.description
        if len(desc) > 60:
            desc = desc[:57] + '...'
        tk.Label(text_col, text=desc, bg=C['panel'], fg=C['dim'],
                 font=('Segoe UI', 9)).pack(anchor='w')

        # right side: Add button
        add_btn = tk.Button(
            body, text='+ Add',
            bg=C['accent'], fg='white',
            activebackground=C['accent2'], activeforeground='white',
            font=('Segoe UI', 9), relief='flat', bd=0,
            padx=12, pady=4, cursor='hand2',
        )
        add_btn.pack(side='right')

        def _add(c=cls):
            cfg_now = crab_config.get_module_config(c.name)
            cfg_now['enabled'] = True
            crab_config.set_module_config(c.name, cfg_now)
            self._build_module_cards()

        add_btn.config(command=_add)

        # bottom border
        tk.Frame(outer, bg=C['border'], height=1).pack(fill='x')

    def _add_field(self, parent: tk.Frame, mod_name: str,
                   field: dict, saved_cfg: dict):
        row = tk.Frame(parent, bg=C['card'])
        row.pack(fill='x', pady=3)

        tk.Label(row, text=field['label'], bg=C['card'], fg=C['dim'],
                 font=('Segoe UI', 9), width=18, anchor='w').pack(side='left')

        # Special handling for google_status field type
        if field['type'] == 'google_status':
            self._render_google_status_row(row)
            return

        var = tk.StringVar(value=saved_cfg.get(field['key'], ''))
        self._field_vars.setdefault(mod_name, {})[field['key']] = var

        show = '•' if field['type'] == 'password' else ''
        entry = tk.Entry(
            row, textvariable=var, show=show,
            bg=C['panel'], fg=C['text'], insertbackground=C['text'],
            font=('Segoe UI', 9), relief='flat', bd=5,
        )
        entry.pack(side='left', fill='x', expand=True)

        if field['type'] == 'path':
            tk.Button(
                row, text='Browse',
                command=lambda v=var: self._browse(v),
                bg=C['pill'], fg=C['dim'],
                activebackground=C['border'], activeforeground='white',
                font=('Segoe UI', 8), relief='flat', bd=0,
                padx=8, pady=2, cursor='hand2',
            ).pack(side='left', padx=(4, 0))

        # auto-save on every keystroke
        def _save(*_, mn=mod_name, fk=field['key'], v=var):
            cfg = crab_config.get_module_config(mn)
            cfg[fk] = v.get()
            crab_config.set_module_config(mn, cfg)

        var.trace_add('write', _save)

    def _browse(self, var: tk.StringVar):
        path = filedialog.askdirectory(title='Select folder', parent=self.win)
        if path:
            var.set(path)

    # ── Google status / connect button ─────────────────────────────────────

    def _render_google_status_row(self, row: tk.Frame):
        """Renders one of:
            - "Connected as <email>" + Disconnect button   (token exists, valid)
            - "Connecting…"                                (OAuth in progress)
            - "Not connected" + Connect button             (default)
        """
        # Avoid double-render when called twice within the same _refresh
        for w in row.winfo_children()[1:]:
            w.destroy()

        if os.path.exists(_GOOGLE_TOKEN):
            email = self._google_email() or 'Google account'
            tk.Label(row, text=f'✓  {email}', bg=C['card'], fg=C['teal'],
                     font=('Segoe UI', 9)).pack(side='left')
            tk.Button(
                row, text='Disconnect',
                command=self._disconnect_google,
                bg=C['pill'], fg=C['err'],
                activebackground=C['border'], activeforeground='white',
                font=('Segoe UI', 8), relief='flat', bd=0,
                padx=10, pady=2, cursor='hand2',
            ).pack(side='right')
        else:
            tk.Label(row, text='Not connected', bg=C['card'], fg='#f0a050',
                     font=('Segoe UI', 9)).pack(side='left')
            tk.Button(
                row, text='Connect Google Account',
                command=self._connect_google,
                bg=C['accent'], fg='white',
                activebackground=C['accent2'], activeforeground='white',
                font=('Segoe UI', 8, 'bold'), relief='flat', bd=0,
                padx=12, pady=3, cursor='hand2',
            ).pack(side='right')

    def _google_email(self) -> str:
        """Best-effort: extract the user's email from the saved token."""
        try:
            import json
            data = json.loads(open(_GOOGLE_TOKEN, 'r', encoding='utf-8').read())
            # google-auth-oauthlib doesn't always save email; fall back to None
            return data.get('account', '') or ''
        except Exception:
            return ''

    def _connect_google(self):
        """Start the OAuth flow in a background thread. Browser opens automatically."""
        import threading

        # Disable any other connect buttons / show status
        self._refresh()  # show current state
        # We can't easily mutate "in progress" UI from here without more plumbing;
        # OAuth typically takes a few seconds — the user will see the browser open.

        def _do_auth():
            error = None
            try:
                from modules.google_auth import GoogleServices
                GoogleServices()  # creates ~/.pebble/google_token.json on success
            except Exception as e:
                error = str(e)
            # Back on the Tk thread
            self.win.after(0, lambda: self._on_auth_done(error))

        threading.Thread(target=_do_auth, daemon=True, name='pebble-google-oauth').start()

    def _on_auth_done(self, error: str | None):
        if error:
            try:
                import tkinter.messagebox as mbox
                mbox.showerror('Google sign-in failed', error[:500], parent=self.win)
            except Exception:
                pass
        # Re-render the entire module list — token file existence drives the UI
        self._refresh()

    def _disconnect_google(self):
        try:
            if os.path.exists(_GOOGLE_TOKEN):
                os.unlink(_GOOGLE_TOKEN)
        except OSError:
            pass
        self._refresh()

    def _refresh(self):
        self._build_module_cards()


# ── helpers ────────────────────────────────────────────────────────────────────

def _dark_titlebar(win):
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            win.winfo_id(), 20,
            ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass
