"""Pebble chat panel — toggled by left-clicking the crab."""

from __future__ import annotations

import ctypes
import os
import threading
import tkinter as tk
from typing import Optional

import crab_config

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'pebble_logo_pack', 'pebble-crab-icon-64x64-transparent.png',
)

C = {
    'bg':      '#0d0d1a',
    'panel':   '#141427',
    'card':    '#1a1a30',
    'accent':  '#534AB7',
    'accent2': '#6560c8',
    'teal':    '#1D9E75',
    'text':    '#e2e2f0',
    'dim':     '#62628a',
    'muted':   '#2e2e48',
    'err':     '#e05c7a',
    'user_bg': '#1e1e38',
}

W, H = 340, 460


class ChatWindow:
    def __init__(self, parent: tk.Tk):
        self._parent   = parent
        self._msgs:  list[dict] = []
        self._busy   = False
        self._visible = False
        self._active_id   = ''
        self._active_name = ''

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=C['bg'])
        self.win.withdraw()

        _dark_titlebar(self.win)
        self._build()

    # ── show / hide ────────────────────────────────────────────────────────────

    def toggle(self, crab_x: int, crab_y: int):
        if self._visible:
            self.hide()
        else:
            self.show(crab_x, crab_y)

    def show(self, crab_x: int, crab_y: int):
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        # center above the crab sprite
        x  = max(4, min(crab_x - W // 2 + 78, sw - W - 4))
        y  = crab_y - H - 10
        self.win.geometry(f'{W}x{H}+{x}+{y}')
        self.win.deiconify()
        self.win.lift()
        self._visible = True
        self._refresh_model_selector()
        self._refresh_dry_banner()
        self._input.focus_set()

    def _refresh_dry_banner(self):
        """Show/hide the orange dry-run strip based on current config."""
        import dry_run
        if dry_run.is_enabled():
            # pack right after the title bar — fill='x' top
            self._dry_banner.pack(fill='x', after=None)
            try:
                self._dry_banner.pack_configure(after=self.win.children[next(iter(self.win.children))])
            except Exception:
                pass
        else:
            self._dry_banner.pack_forget()

    def hide(self):
        self.win.withdraw()
        self._visible = False

    # ── build ──────────────────────────────────────────────────────────────────

    def _build(self):
        # title bar
        title = tk.Frame(self.win, bg=C['accent'], height=42)
        title.pack(fill='x')
        title.pack_propagate(False)

        # Pebble logo + wordmark in the title bar
        brand = tk.Frame(title, bg=C['accent'])
        brand.pack(side='left', padx=(10, 0))
        if _HAS_PIL and os.path.exists(_LOGO_PATH):
            try:
                img = Image.open(_LOGO_PATH).convert('RGBA').resize((28, 28),
                                                                     Image.Resampling.LANCZOS)
                self._logo_img = ImageTk.PhotoImage(img)
                tk.Label(brand, image=self._logo_img, bg=C['accent'],
                         bd=0).pack(side='left', padx=(0, 6), pady=6)
            except Exception:
                tk.Label(brand, text='🦀', bg=C['accent'], fg='white',
                         font=('Segoe UI', 14)).pack(side='left', padx=(0, 6))
        else:
            tk.Label(brand, text='🦀', bg=C['accent'], fg='white',
                     font=('Segoe UI', 14)).pack(side='left', padx=(0, 6))
        tk.Label(brand, text='Pebble', bg=C['accent'], fg='white',
                 font=('Segoe UI', 11, 'bold')).pack(side='left')

        # dry-run banner (hidden when dry_run=false)
        self._dry_banner = tk.Frame(self.win, bg='#d39a2c', height=22)
        self._dry_banner_lbl = tk.Label(
            self._dry_banner,
            text='🧪  DRY RUN — no external API calls will be made.  Use /review-drafts to inspect.',
            bg='#d39a2c', fg='#1a1a30',
            font=('Segoe UI', 8, 'bold'),
        )
        self._dry_banner_lbl.pack(fill='x', pady=2)
        self._refresh_dry_banner()

        tk.Button(
            title, text='✕', command=self.hide,
            bg=C['accent'], fg='#ccccdd',
            activebackground=C['err'], activeforeground='white',
            font=('Segoe UI', 10), relief='flat', bd=0,
            padx=12, cursor='hand2',
        ).pack(side='right')

        # model selector button (shows current model, click → popup)
        self._model_btn = tk.Button(
            title, text='No model',
            bg=C['accent2'], fg='white',
            activebackground=C['accent'], activeforeground='white',
            font=('Segoe UI', 8), relief='flat', bd=0,
            padx=10, pady=3, cursor='hand2',
            command=self._show_model_menu,
        )
        self._model_btn.pack(side='right', padx=(0, 6))

        # input bar — pack BEFORE msg_area so it always gets its space
        bar = tk.Frame(self.win, bg=C['panel'], pady=8)
        bar.pack(fill='x', side='bottom')

        # thinking label — pack before msg_area too (shown above input bar)
        self._thinking_lbl = tk.Label(
            self.win,
            text='  Pebble is thinking…',
            bg=C['panel'], fg=C['dim'],
            font=('Segoe UI', 9, 'italic'),
            anchor='w',
        )

        # message area — packed last so expand=True only takes remaining space
        msg_area = tk.Frame(self.win, bg=C['bg'])
        msg_area.pack(fill='both', expand=True)

        self._text = tk.Text(
            msg_area,
            bg=C['bg'], fg=C['text'],
            font=('Segoe UI', 10), wrap='word',
            state='disabled', relief='flat', bd=0,
            padx=14, pady=10,
            cursor='arrow',
            selectbackground=C['accent'],
        )
        sb = tk.Scrollbar(msg_area, orient='vertical',
                          command=self._text.yview,
                          bg=C['panel'], troughcolor=C['bg'],
                          relief='flat', bd=0)
        self._text.config(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self._text.pack(fill='both', expand=True)

        # text tags
        self._text.tag_config('bot_lbl',
                               foreground=C['teal'],
                               font=('Segoe UI', 8, 'bold'),
                               spacing1=12)
        self._text.tag_config('bot_msg',
                               foreground=C['text'],
                               font=('Segoe UI', 10),
                               lmargin1=0, lmargin2=0,
                               spacing3=6)
        self._text.tag_config('you_lbl',
                               foreground=C['accent'],
                               font=('Segoe UI', 8, 'bold'),
                               spacing1=12)
        self._text.tag_config('you_msg',
                               foreground='#a0a0c0',
                               font=('Segoe UI', 10),
                               spacing3=4)
        self._text.tag_config('err_msg',
                               foreground=C['err'],
                               font=('Segoe UI', 9, 'italic'),
                               spacing1=6, spacing3=4)

        # initial greeting
        self._append('bot_lbl', 'Pebble\n')
        self._append('bot_msg', 'Hey! What can I help you with?\n')

        self._input = tk.Entry(
            bar,
            bg=C['card'], fg=C['text'],
            insertbackground=C['text'],
            font=('Segoe UI', 10),
            relief='flat', bd=6,
        )
        self._input.pack(side='left', fill='x', expand=True, padx=(10, 6))
        self._input.bind('<Return>', lambda e: self._send())

        self._send_btn = tk.Button(
            bar, text='Send',
            command=self._send,
            bg=C['accent'], fg='white',
            activebackground=C['accent2'], activeforeground='white',
            font=('Segoe UI', 9, 'bold'),
            relief='flat', bd=0,
            padx=14, pady=6,
            cursor='hand2',
        )
        self._send_btn.pack(side='right', padx=(0, 10))

    # ── model selector ─────────────────────────────────────────────────────────

    def _refresh_model_selector(self):
        models = crab_config.get_enabled_models()
        if not models:
            self._model_btn.config(text='No model set')
            return
        active_id = crab_config.get_active_model_id()
        active = next((m for m in models if m['id'] == active_id), models[0])
        self._active_id   = active['id']
        self._active_name = active['display_name']
        # Truncate for button
        label = self._active_name[:22] + ('…' if len(self._active_name) > 22 else '')
        self._model_btn.config(text=f'{label}  ▾')

    def _show_model_menu(self):
        models = crab_config.get_enabled_models()
        if not models:
            return
        menu = tk.Menu(
            self.win, tearoff=0,
            bg=C['panel'], fg=C['text'],
            activebackground=C['accent'], activeforeground='white',
            font=('Segoe UI', 9), bd=0, relief='flat',
        )
        for m in models:
            is_active = m['id'] == self._active_id
            label = ('✓  ' if is_active else '    ') + m['display_name']
            menu.add_command(
                label=label,
                command=lambda mid=m['id'], mname=m['display_name']:
                    self._set_model(mid, mname),
            )
        try:
            bx = self._model_btn.winfo_rootx()
            by = self._model_btn.winfo_rooty() + self._model_btn.winfo_height()
            menu.tk_popup(bx, by)
        finally:
            menu.grab_release()

    def _set_model(self, model_id: str, display_name: str):
        self._active_id   = model_id
        self._active_name = display_name
        crab_config.set_active_model(model_id)
        label = display_name[:22] + ('…' if len(display_name) > 22 else '')
        self._model_btn.config(text=f'{label}  ▾')

    # ── messaging ──────────────────────────────────────────────────────────────

    def _send(self):
        if self._busy:
            return
        text = self._input.get().strip()
        if not text:
            return
        self._input.delete(0, 'end')

        self._append('you_lbl', 'You\n')
        self._append('you_msg', text + '\n')

        # ── Slash commands intercepted locally (never sent to LLM) ───────────
        if text.startswith('/'):
            self._handle_slash_command(text)
            return

        self._msgs.append({'role': 'user', 'content': text})

        self._busy = True
        self._send_btn.config(state='disabled', bg=C['muted'], text='…')
        self._thinking_lbl.pack(fill='x', side='bottom', pady=(0, 2))

        threading.Thread(target=self._call_model, daemon=True).start()

    def _handle_slash_command(self, text: str):
        import chat_commands
        result = chat_commands.handle(text)

        if isinstance(result, chat_commands.AsyncCommand):
            self._busy = True
            self._send_btn.config(state='disabled', bg=C['muted'], text='…')
            self._thinking_lbl.config(text=f'  {result.label}')
            self._thinking_lbl.pack(fill='x', side='bottom', pady=(0, 2))

            def _run():
                try:
                    out = result.fn()
                except Exception as e:
                    out = f'Error: {e}'
                self.win.after(0, lambda t=out: self._show_response(t))
            threading.Thread(target=_run, daemon=True).start()
            return

        # Synchronous result
        text_out = result if isinstance(result, str) else 'Unknown command.'
        self._append('bot_lbl', 'Pebble\n')
        self._append('bot_msg', text_out + '\n')

    def _call_model(self):
        try:
            from model_backend import ModelBackend
            from modules import get_active_modules
            from tool_orchestrator import ToolOrchestrator

            if self._active_id:
                backend = ModelBackend.for_id(self._active_id)
            else:
                backend = ModelBackend.active()

            modules      = get_active_modules()
            orchestrator = ToolOrchestrator(backend, modules)
            response     = orchestrator.chat(
                self._msgs,
                system=(
                    'You are Pebble, a friendly and concise AI desktop companion. '
                    'Keep replies short unless asked to elaborate. '
                    'Use tools when they would genuinely help answer the question.'
                ),
            )
            self._msgs.append({'role': 'assistant', 'content': response})
            self.win.after(0, lambda r=response: self._show_response(r))
        except Exception as exc:
            try:
                import error_reporter
                error_reporter.report(type(exc), exc, exc.__traceback__,
                                       source='chat_window',
                                       context={'model_id': self._active_id,
                                                'last_user_msg': (self._msgs[-1] if self._msgs else {}).get('content', '')[:200]})
            except Exception:
                pass
            self.win.after(0, lambda e=exc: self._show_error(str(e)))

    def _show_response(self, text: str):
        self._thinking_lbl.pack_forget()
        self._append('bot_lbl', 'Pebble\n')
        self._append('bot_msg', text + '\n')
        self._text.see('end')
        self._busy = False
        self._send_btn.config(state='normal', bg=C['accent'], text='Send')

    def _show_error(self, error: str):
        self._thinking_lbl.pack_forget()
        self._append('err_msg', f'Error: {error}\n')
        self._busy = False
        self._send_btn.config(state='normal', bg=C['accent'], text='Send')

    def _append(self, tag: str, text: str):
        self._text.config(state='normal')
        self._text.insert('end', text, tag)
        self._text.config(state='disabled')
        self._text.see('end')


# ── helpers ────────────────────────────────────────────────────────────────────

def _dark_titlebar(win):
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            win.winfo_id(), 20,
            ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass
