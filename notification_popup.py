"""Small floating notification popup for Pebble."""
import ctypes
import ctypes.wintypes
import tkinter as tk

POPUP_BG     = '#1e1e2e'
POPUP_PANEL  = '#2a2a3e'
POPUP_FG     = '#cdd6f4'
POPUP_DIM    = '#7f849c'
POPUP_ACCENT = '#534AB7'
POPUP_ERR    = '#e05c7a'
POPUP_W      = 300
POPUP_H_MIN  = 90


def _taskbar_top() -> int:
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.bottom


def _screen_w() -> int:
    return ctypes.windll.user32.GetSystemMetrics(0)


class NotificationPopup:
    def __init__(self, root: tk.Tk, title: str, body: str,
                 buttons: list[dict], auto_dismiss_ms: int = 8000):
        """
        root: parent tkinter Tk/Toplevel
        title: bold short title e.g. "📧 professor@cmu.edu"
        body: message text (truncated to ~60 chars if needed)
        buttons: list of dicts:
            {'label': str, 'command': callable, 'style': 'primary'|'default'|'danger'}
        auto_dismiss_ms: auto-close after this many ms (0 = no auto-dismiss)
        """
        self._auto_dismiss_ms = auto_dismiss_ms
        self._after_id = None

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=POPUP_BG)
        self.win.withdraw()

        # 4px accent bar at very top
        accent_bar = tk.Frame(self.win, bg=POPUP_ACCENT, height=4)
        accent_bar.pack(fill='x', side='top')

        # Title
        tk.Label(
            self.win,
            text=title,
            bg=POPUP_BG,
            fg=POPUP_FG,
            font=('Segoe UI', 10, 'bold'),
            anchor='w',
        ).pack(fill='x', padx=14, pady=(10, 2))

        # Body
        tk.Label(
            self.win,
            text=body,
            bg=POPUP_BG,
            fg=POPUP_DIM,
            font=('Segoe UI', 9),
            anchor='w',
            justify='left',
            wraplength=POPUP_W - 28,
        ).pack(fill='x', padx=14, pady=(0, 10))

        # Button row
        btn_frame = tk.Frame(self.win, bg=POPUP_PANEL)
        btn_frame.pack(fill='x', side='bottom')

        _style_map = {
            'primary': {'bg': POPUP_ACCENT, 'fg': 'white'},
            'default': {'bg': '#3a3a52',    'fg': POPUP_FG},
            'danger':  {'bg': POPUP_ERR,    'fg': 'white'},
        }

        for btn_cfg in buttons:
            style    = btn_cfg.get('style', 'default')
            colors   = _style_map.get(style, _style_map['default'])
            cmd_orig = btn_cfg.get('command', lambda: None)

            def _make_cmd(c=cmd_orig):
                def _cmd():
                    self.dismiss()
                    c()
                return _cmd

            tk.Button(
                btn_frame,
                text=btn_cfg.get('label', ''),
                command=_make_cmd(),
                bg=colors['bg'],
                fg=colors['fg'],
                font=('Segoe UI', 9),
                relief='flat',
                padx=12,
                pady=4,
                cursor='hand2',
                activebackground=colors['bg'],
                activeforeground=colors['fg'],
                bd=0,
            ).pack(side='left', padx=(10, 4), pady=8)

    def show(self):
        self._reposition()
        self.win.deiconify()
        if self._auto_dismiss_ms > 0:
            self._after_id = self.win.after(self._auto_dismiss_ms, self.dismiss)

    def dismiss(self):
        if self._after_id is not None:
            try:
                self.win.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.win.withdraw()

    def _reposition(self):
        self.win.update_idletasks()
        h  = max(self.win.winfo_reqheight(), POPUP_H_MIN)
        sw = _screen_w()
        tb = _taskbar_top()
        x  = sw - POPUP_W - 16
        y  = tb - h - 8
        self.win.geometry(f'{POPUP_W}x{h}+{x}+{y}')
