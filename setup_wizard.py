"""Pebble first-run setup wizard.

Flow: Welcome → Hardware Scan → Model Library → Done

Model Library lets users add any mix of local (Ollama) and cloud API models.
Already-installed Ollama models are detected and shown as ready immediately.
"""

from __future__ import annotations

import ctypes
import json
import platform
import shutil
import subprocess
import threading
import tkinter as tk
import webbrowser
from typing import Optional

import requests

import crab_config

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

import os as _os
_LOGO_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'pebble_logo_pack')

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

W, H = 620, 520

GEMMA_MODELS = [
    {
        'tag':      'gemma4:27b',
        'name':     'Gemma 4  27B',
        'size':     '~17 GB',
        'desc':     'Most capable — best reasoning & long context',
        'min_ram':  32,
        'min_vram': 16,
    },
    {
        'tag':      'gemma4:12b',
        'name':     'Gemma 4  12B',
        'size':     '~7.5 GB',
        'desc':     'Balanced — great on most modern laptops',
        'min_ram':  16,
        'min_vram': 8,
    },
    {
        'tag':      'gemma4:4b',
        'name':     'Gemma 4  4B',
        'size':     '~3 GB',
        'desc':     'Efficient — runs well on any machine',
        'min_ram':  8,
        'min_vram': 4,
    },
]

CLOUD_PROVIDERS = [
    {'id': 'anthropic', 'label': 'Claude',  'model': 'claude-sonnet-4-6',    'ph': 'sk-ant-api03-…'},
    {'id': 'openai',    'label': 'OpenAI',  'model': 'gpt-4o',               'ph': 'sk-proj-…'},
    {'id': 'gemini',    'label': 'Gemini',  'model': 'gemini-2.0-flash-exp',  'ph': 'AIzaSy…'},
]


# ── first-run defaults (Phase 5 onboarding) ────────────────────────────────────

def _apply_first_run_defaults() -> None:
    """Seed safe defaults the first time setup_complete becomes True.

    - dry_run=True so the user can build trust before actions go live
    - tiers: outbound = ASK, drafts = NOTIFY, reads = AUTO
    These are no-ops if the user has already touched the relevant config.
    """
    if crab_config.get('first_run_defaults_applied'):
        return
    if crab_config.get('dry_run') is None:
        crab_config.set_value('dry_run', True)
    if not crab_config.get('tiers'):
        crab_config.set_value('tiers', {
            'gmail':   {'search': 'auto', 'draft': 'notify', 'send': 'ask'},
            'gcal':    {'list_events': 'auto', 'create_event': 'ask', 'update_event': 'ask'},
            'slack':   {'read': 'auto', 'send': 'ask'},
            'tasks':   {'list': 'auto', 'complete': 'notify', 'create': 'notify'},
            'obsidian':{'search': 'auto', 'read': 'auto', 'append': 'notify'},
            'memory':  {'recall': 'auto', 'remember': 'notify', 'forget': 'notify'},
            'entities':{'lookup': 'auto', 'list': 'auto', 'add': 'notify', 'delete': 'ask'},
        })
    crab_config.set_value('first_run_defaults_applied', True)


def _mark_setup_complete() -> None:
    crab_config.set_value('setup_complete', True)
    _apply_first_run_defaults()


# ── hardware detection ─────────────────────────────────────────────────────────

def detect_hardware() -> dict:
    ram_gb = 8.0
    if _PSUTIL:
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)

    cpu = platform.processor() or platform.machine() or 'Unknown CPU'
    if ', ' in cpu:
        cpu = cpu.split(', ')[0]

    vram_gb, gpu_name = 0.0, ''
    try:
        r = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            parts    = r.stdout.strip().split(',')
            gpu_name = parts[0].strip()
            vram_gb  = int(parts[1].strip()) / 1024
    except Exception:
        pass

    if not gpu_name:
        try:
            r = subprocess.run(
                ['wmic', 'path', 'win32_VideoController',
                 'get', 'Name,AdapterRAM', '/format:csv'],
                capture_output=True, text=True, timeout=5)
            for line in r.stdout.strip().splitlines():
                parts = line.split(',')
                if len(parts) >= 3 and parts[2].strip().isdigit() and int(parts[2].strip()) > 0:
                    gpu_name = parts[1].strip()
                    vram_gb  = int(parts[2].strip()) / (1024 ** 3)
                    break
        except Exception:
            pass

    return {'cpu': cpu, 'ram_gb': ram_gb, 'gpu': gpu_name, 'vram_gb': vram_gb}


def recommend_model(hw: dict) -> dict:
    for m in GEMMA_MODELS:
        if hw['vram_gb'] >= m['min_vram'] or hw['ram_gb'] >= m['min_ram']:
            return m
    return GEMMA_MODELS[-1]


# ── wizard ─────────────────────────────────────────────────────────────────────

class SetupWizard:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title('Pebble Setup')
        self.root.configure(bg=C['bg'])
        self.root.resizable(False, False)
        self.root.geometry(f'{W}x{H}')
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f'{W}x{H}+{(sw - W)//2}+{(sh - H)//2}')
        _dark_titlebar(self.root)

        self._hw:             dict       = {}
        self._recommended:    dict       = {}
        self._added:          list[dict] = []
        self._installed_tags: set[str]   = set()   # Ollama tags confirmed installed

        self._pages: dict[str, tk.Frame] = {}
        self._container = tk.Frame(self.root, bg=C['bg'])
        self._container.pack(fill='both', expand=True)

        self._build_welcome()
        self._build_scan()
        self._build_models()
        self._build_done()
        self._show('welcome')

    # ── navigation ─────────────────────────────────────────────────────────────

    def _show(self, name: str):
        for p in self._pages.values():
            p.pack_forget()
        self._pages[name].pack(fill='both', expand=True)
        if name == 'scan':
            self._start_scan()
        elif name == 'models':
            self._init_models_page()
        elif name == 'done':
            self._refresh_done()

    def _page(self, name: str) -> tk.Frame:
        f = tk.Frame(self._container, bg=C['bg'])
        self._pages[name] = f
        return f

    def _btn(self, parent, text, cmd, primary=True, small=False) -> tk.Button:
        bg  = C['accent']  if primary else C['pill']
        abg = C['accent2'] if primary else C['border']
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg, fg='#ffffff', activebackground=abg, activeforeground='#ffffff',
            font=('Segoe UI', 9 if small else 10, 'bold' if primary else 'normal'),
            relief='flat', bd=0,
            padx=12 if small else 22, pady=5 if small else 8,
            cursor='hand2',
        )

    # ── PAGE 1: Welcome ────────────────────────────────────────────────────────

    def _build_welcome(self):
        p = self._page('welcome')
        inner = tk.Frame(p, bg=C['bg'])
        inner.place(relx=0.5, rely=0.44, anchor='center')

        tile = tk.Frame(inner, bg=C['accent'], width=96, height=96)
        tile.pack_propagate(False)
        tile.pack(pady=(0, 18))
        logo_path = _os.path.join(_LOGO_DIR, 'pebble-crab-icon-128x128-transparent.png')
        if _HAS_PIL and _os.path.exists(logo_path):
            try:
                img = Image.open(logo_path).convert('RGBA').resize((76, 76),
                                                                    Image.Resampling.LANCZOS)
                self._welcome_logo = ImageTk.PhotoImage(img)
                tk.Label(tile, image=self._welcome_logo, bg=C['accent'], bd=0).pack(expand=True)
            except Exception:
                tk.Label(tile, text='🦀', bg=C['accent'], font=('Segoe UI', 40)).pack(expand=True)
        else:
            tk.Label(tile, text='🦀', bg=C['accent'], font=('Segoe UI', 40)).pack(expand=True)

        tk.Label(inner, text='Pebble', bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 30, 'bold')).pack()
        tk.Label(inner, text='Your AI desktop companion', bg=C['bg'], fg=C['dim'],
                 font=('Segoe UI', 12)).pack(pady=(4, 28))
        self._btn(inner, 'Get Started  →', lambda: self._show('scan')).pack()

        tk.Label(p, text='v2.0  ·  local-first by default', bg=C['bg'], fg=C['muted'],
                 font=('Segoe UI', 8)).pack(side='bottom', pady=14)

    # ── PAGE 2: Hardware scan ──────────────────────────────────────────────────

    def _build_scan(self):
        p = self._page('scan')

        # pack bottom button first
        tk.Frame(p, bg=C['border'], height=1).pack(side='bottom', fill='x')
        row = tk.Frame(p, bg=C['bg'])
        row.pack(side='bottom', pady=22)
        self._scan_next = self._btn(row, 'Continue  →', lambda: self._show('models'))
        self._scan_next.pack()
        self._scan_next.config(state='disabled', bg=C['muted'])

        tk.Label(p, text='Analyzing your system', bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 17, 'bold')).pack(pady=(36, 4))
        self._scan_status = tk.Label(p, text='', bg=C['bg'], fg=C['dim'],
                                      font=('Segoe UI', 10))
        self._scan_status.pack()

        card = tk.Frame(p, bg=C['card'], padx=26, pady=6)
        card.pack(padx=70, pady=18, fill='x')

        self._spec: dict[str, tk.Label] = {}
        for key, label in [('cpu', 'CPU'), ('ram', 'RAM'), ('gpu', 'GPU'), ('vram', 'VRAM')]:
            row2 = tk.Frame(card, bg=C['card'])
            row2.pack(fill='x', pady=7)
            tk.Label(row2, text=label, bg=C['card'], fg=C['dim'],
                     font=('Segoe UI', 9, 'bold'), width=6, anchor='w').pack(side='left')
            lbl = tk.Label(row2, text='—', bg=C['card'], fg=C['text'],
                           font=('Segoe UI', 10), anchor='w')
            lbl.pack(side='left')
            self._spec[key] = lbl

        self._scan_rec = tk.Label(p, text='', bg=C['bg'], fg=C['teal'],
                                   font=('Segoe UI', 10, 'bold'))
        self._scan_rec.pack(pady=(10, 0))

    def _start_scan(self):
        self._scan_status.config(text='Detecting hardware…')
        self._scan_next.config(state='disabled', bg=C['muted'])
        for lbl in self._spec.values():
            lbl.config(text='…')
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        hw  = detect_hardware()
        rec = recommend_model(hw)
        self._hw, self._recommended = hw, rec
        self.root.after(0, self._scan_done)

    def _scan_done(self):
        hw = self._hw
        self._spec['cpu'].config(text=(hw['cpu'] or 'Unknown')[:54])
        self._spec['ram'].config(text=f"{hw['ram_gb']:.1f} GB")
        self._spec['gpu'].config(text=(hw['gpu'] or 'No dedicated GPU')[:54])
        self._spec['vram'].config(
            text=f"{hw['vram_gb']:.1f} GB" if hw['vram_gb'] > 0 else 'Shared / N/A')
        rec = self._recommended
        self._scan_rec.config(text=f"Recommended:  {rec['name']}  ·  {rec['size']}")
        self._scan_status.config(text='Scan complete')
        self._scan_next.config(state='normal', bg=C['accent'])

    # ── PAGE 3: Model Library ──────────────────────────────────────────────────

    def _build_models(self):
        p = self._page('models')

        # ── continue button — packed FIRST so side='bottom' always reserves space ──
        tk.Frame(p, bg=C['border'], height=1).pack(side='bottom', fill='x')
        bot = tk.Frame(p, bg=C['bg'])
        bot.pack(side='bottom', fill='x', padx=44, pady=12)
        self._continue_btn = self._btn(bot, 'Continue  →', lambda: self._show('done'))
        self._continue_btn.pack(side='right')
        self._continue_btn.config(state='disabled', bg=C['muted'])

        # ── header ─────────────────────────────────────────────────────────────
        tk.Label(p, text='Your AI models', bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 17, 'bold')).pack(pady=(22, 3))
        tk.Label(p, text='Add any mix of local and cloud models — enable or disable any time.',
                 bg=C['bg'], fg=C['dim'], font=('Segoe UI', 10)).pack()

        # ── model list ─────────────────────────────────────────────────────────
        list_border = tk.Frame(p, bg=C['border'], padx=1, pady=1)
        list_border.pack(fill='x', padx=44, pady=(12, 0))
        self._list_frame = tk.Frame(list_border, bg=C['card'])
        self._list_frame.pack(fill='x')

        # ── add-model form ─────────────────────────────────────────────────────
        form_wrap = tk.Frame(p, bg=C['panel'])
        form_wrap.pack(fill='x', padx=44, pady=(3, 0))

        # toggle row
        tog = tk.Frame(form_wrap, bg=C['panel'])
        tog.pack(fill='x', padx=14, pady=(10, 0))
        tk.Label(tog, text='Add a model:', bg=C['panel'], fg=C['dim'],
                 font=('Segoe UI', 9)).pack(side='left', padx=(0, 10))

        self._local_tab = tk.Button(
            tog, text='🖥  Local',
            bg=C['accent'], fg='white',
            activebackground=C['accent2'], activeforeground='white',
            font=('Segoe UI', 9, 'bold'), relief='flat', bd=0,
            padx=14, pady=5, cursor='hand2',
            command=lambda: self._switch_form('local'),
        )
        self._local_tab.pack(side='left', padx=(0, 4))

        self._cloud_tab = tk.Button(
            tog, text='☁  Cloud',
            bg=C['pill'], fg=C['dim'],
            activebackground=C['border'], activeforeground='white',
            font=('Segoe UI', 9), relief='flat', bd=0,
            padx=14, pady=5, cursor='hand2',
            command=lambda: self._switch_form('cloud'),
        )
        self._cloud_tab.pack(side='left')

        tk.Frame(form_wrap, bg=C['border'], height=1).pack(fill='x', padx=14, pady=(8, 0))

        # sub-forms (built here so widgets exist before _show('models') is called)
        self._local_frame = tk.Frame(form_wrap, bg=C['panel'])
        self._build_local_form(self._local_frame)

        self._cloud_frame = tk.Frame(form_wrap, bg=C['panel'])
        self._build_cloud_form(self._cloud_frame)

        self._switch_form('local')

    # ── form toggling ──────────────────────────────────────────────────────────

    def _switch_form(self, which: str):
        if which == 'local':
            self._cloud_frame.pack_forget()
            self._local_frame.pack(fill='x', padx=14, pady=(8, 12))
            self._local_tab.config(bg=C['accent'], fg='white', font=('Segoe UI', 9, 'bold'))
            self._cloud_tab.config(bg=C['pill'],   fg=C['dim'], font=('Segoe UI', 9, 'normal'))
        else:
            self._local_frame.pack_forget()
            self._cloud_frame.pack(fill='x', padx=14, pady=(8, 12))
            self._local_tab.config(bg=C['pill'],   fg=C['dim'], font=('Segoe UI', 9, 'normal'))
            self._cloud_tab.config(bg=C['accent'], fg='white', font=('Segoe UI', 9, 'bold'))

    # ── local sub-form ─────────────────────────────────────────────────────────

    def _build_local_form(self, parent: tk.Frame):
        # model pills
        pill_row = tk.Frame(parent, bg=C['panel'])
        pill_row.pack(fill='x', pady=(0, 5))

        self._local_sel: dict[str, tk.Button] = {}
        self._local_model_var = tk.StringVar(value='')

        for m in GEMMA_MODELS:
            btn = tk.Button(
                pill_row, text=m['name'],
                bg=C['pill'], fg=C['dim'],
                activebackground=C['accent'], activeforeground='white',
                font=('Segoe UI', 9), relief='flat', bd=0,
                padx=12, pady=4, cursor='hand2',
                command=lambda t=m['tag']: self._pick_local(t),
            )
            btn.pack(side='left', padx=(0, 5))
            self._local_sel[m['tag']] = btn

        self._local_desc = tk.Label(parent, text='', bg=C['panel'], fg=C['dim'],
                                     font=('Segoe UI', 9))
        self._local_desc.pack(anchor='w', pady=(0, 5))

        # progress bar
        self._local_pb = tk.Canvas(parent, bg=C['card'], height=6,
                                    highlightthickness=0, bd=0)
        self._local_pb.pack(fill='x', pady=(0, 3))
        self._local_pb_bar = self._local_pb.create_rectangle(0, 0, 0, 6,
                                                               fill=C['accent'], width=0)
        self._local_pb_lbl = tk.Label(parent, text='', bg=C['panel'], fg=C['dim'],
                                       font=('Segoe UI', 8))
        self._local_pb_lbl.pack(anchor='w')

        btn_row = tk.Frame(parent, bg=C['panel'])
        btn_row.pack(fill='x', pady=(5, 0))
        self._download_btn = self._btn(btn_row, 'Download & Add',
                                        self._start_download, primary=False, small=True)
        self._download_btn.pack(side='left')
        self._ollama_link = tk.Label(btn_row, text='', bg=C['panel'], fg=C['accent'],
                                      font=('Segoe UI', 8), cursor='hand2')
        self._ollama_link.pack(side='left', padx=10)
        self._ollama_link.bind('<Button-1>', lambda e: webbrowser.open('https://ollama.com/download'))

    def _init_models_page(self):
        # Load already-configured models
        self._added = list(crab_config.get_models())

        # Auto-detect installed Ollama models in background
        threading.Thread(target=self._detect_ollama, daemon=True).start()

        # Set pill selection to recommended model
        rec = self._recommended
        default_tag = rec.get('tag') if rec else None
        self._pick_local(default_tag or GEMMA_MODELS[-1]['tag'])

        self._rebuild_list()

    def _detect_ollama(self):
        """Background: find already-installed Ollama models, add to list."""
        try:
            from model_backend import ollama_running, local_models, ollama_installed
            if not ollama_installed():
                self.root.after(0, lambda: self._ollama_link.config(
                    text='Ollama not installed — click to download'))
                return
            if not ollama_running():
                return
            installed   = local_models()
            self.root.after(0, lambda tags=set(installed): self._set_installed(tags))
            existing_ids = {m['id'] for m in self._added}
            new_entries  = []
            for tag in installed:
                model_id = f'ollama::{tag}'
                if model_id in existing_ids:
                    continue
                cat     = next((m for m in GEMMA_MODELS if m['tag'] == tag), None)
                display = cat['name'] if cat else tag
                entry   = {
                    'id':           model_id,
                    'type':         'ollama',
                    'display_name': display,
                    'tag':          tag,
                    'model_name':   '',
                    'api_key':      '',
                    'enabled':      True,
                }
                new_entries.append(entry)
                existing_ids.add(model_id)
            if new_entries:
                self.root.after(0, lambda entries=new_entries:
                    self._apply_detected(entries))
        except Exception:
            pass

    def _set_installed(self, tags: set[str]):
        self._installed_tags = tags
        self._rebuild_list()

    def _apply_detected(self, entries: list[dict]):
        for entry in entries:
            self._added.append(entry)
            crab_config.add_model(entry)
        if self._added:
            _mark_setup_complete()
        self._rebuild_list()

    def _pick_local(self, tag: str):
        self._local_model_var.set(tag)
        model = next((m for m in GEMMA_MODELS if m['tag'] == tag), None)
        for t, btn in self._local_sel.items():
            if t == tag:
                btn.config(bg=C['accent'], fg='white', font=('Segoe UI', 9, 'bold'))
            else:
                btn.config(bg=C['pill'],   fg=C['dim'], font=('Segoe UI', 9, 'normal'))
        if model:
            rec  = self._recommended
            note = '  ← recommended' if rec and rec.get('tag') == tag else ''
            self._local_desc.config(text=f"{model['size']}  ·  {model['desc']}{note}")

    def _start_download(self):
        tag = self._local_model_var.get()
        if not tag:
            return

        model_id = f'ollama::{tag}'

        # In config AND confirmed installed — nothing to do
        if any(m['id'] == model_id for m in self._added) and tag in self._installed_tags:
            self._local_pb_lbl.config(text='✓  Already downloaded', fg=C['teal'])
            return

        # In config but NOT installed — strip the stale entry and re-download
        if any(m['id'] == model_id for m in self._added) and tag not in self._installed_tags:
            self._added = [m for m in self._added if m['id'] != model_id]
            crab_config.remove_model(model_id)
            self._rebuild_list()

        # Already installed in Ollama but not yet in config
        if tag in self._installed_tags:
            model = next((m for m in GEMMA_MODELS if m['tag'] == tag), None)
            entry = {
                'id':           model_id,
                'type':         'ollama',
                'display_name': model['name'] if model else tag,
                'tag':          tag,
                'model_name':   '',
                'api_key':      '',
                'enabled':      True,
            }
            self._set_local_pb(1.0)
            self._local_pb_lbl.config(text='✓  Already installed — added!', fg=C['teal'])
            self._add_model(entry)
            return

        self._download_btn.config(state='disabled', bg=C['muted'])
        self._local_pb_lbl.config(text='Starting…', fg=C['dim'])
        self._set_local_pb(0)
        threading.Thread(target=self._download_thread, args=(tag,), daemon=True).start()

    def _download_thread(self, tag: str):
        from model_backend import ollama_running, start_ollama, ollama_installed
        if not ollama_installed():
            self.root.after(0, lambda: (
                self._local_pb_lbl.config(
                    text='Ollama not installed. Download from ollama.com', fg=C['err']),
                self._download_btn.config(state='normal', bg=C['pill']),
            ))
            return
        if not ollama_running():
            self.root.after(0, lambda: self._local_pb_lbl.config(
                text='Starting Ollama server…', fg=C['dim']))
            if not start_ollama():
                self.root.after(0, lambda: (
                    self._local_pb_lbl.config(text='Could not start Ollama.', fg=C['err']),
                    self._download_btn.config(state='normal', bg=C['pill']),
                ))
                return
        self._pull(tag)

    def _pull(self, tag: str):
        try:
            with requests.post(
                'http://localhost:11434/api/pull',
                json={'name': tag, 'stream': True},
                stream=True, timeout=7200,
            ) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines():
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue
                    status    = data.get('status', '')
                    total     = data.get('total', 0)
                    completed = data.get('completed', 0)
                    if total and completed:
                        pct = completed / total
                        a, b = completed / (1024**2), total / (1024**2)
                        self.root.after(0, lambda p=pct: self._set_local_pb(p))
                        self.root.after(0, lambda s=status, a=a, b=b:
                            self._local_pb_lbl.config(
                                text=f'{s}   {a:.0f} / {b:.0f} MB', fg=C['dim']))
                    else:
                        self.root.after(0, lambda s=status:
                            self._local_pb_lbl.config(text=s, fg=C['dim']))
                    if status == 'success':
                        self.root.after(0, lambda t=tag: self._pull_done(t))
                        return
            # Stream ended without a 'success' status
            self.root.after(0, lambda: (
                self._local_pb_lbl.config(text='Download incomplete — try again.', fg=C['err']),
                self._download_btn.config(state='normal', bg=C['pill']),
            ))
        except Exception as exc:
            self.root.after(0, lambda e=exc: (
                self._local_pb_lbl.config(text=f'Error: {e}', fg=C['err']),
                self._download_btn.config(state='normal', bg=C['pill']),
            ))

    def _set_local_pb(self, frac: float):
        self._local_pb.update_idletasks()
        w = self._local_pb.winfo_width()
        self._local_pb.coords(self._local_pb_bar, 0, 0, int(w * frac), 6)

    def _pull_done(self, tag: str):
        self._installed_tags.add(tag)
        self._set_local_pb(1.0)
        model = next((m for m in GEMMA_MODELS if m['tag'] == tag), None)
        name = model['name'] if model else tag
        self._local_pb_lbl.config(text=f'✓  {name} installed', fg=C['teal'])
        self._download_btn.config(state='normal', bg=C['pill'])
        entry = {
            'id':           f'ollama::{tag}',
            'type':         'ollama',
            'display_name': name,
            'tag':          tag,
            'model_name':   '',
            'api_key':      '',
            'enabled':      True,
        }
        self._add_model(entry)

    # ── cloud sub-form ─────────────────────────────────────────────────────────

    def _build_cloud_form(self, parent: tk.Frame):
        prov_row = tk.Frame(parent, bg=C['panel'])
        prov_row.pack(fill='x', pady=(0, 8))
        self._prov_btns: dict[str, tk.Button] = {}
        for prov in CLOUD_PROVIDERS:
            b = tk.Button(
                prov_row, text=prov['label'],
                bg=C['pill'], fg=C['dim'],
                activebackground=C['accent'], activeforeground='white',
                font=('Segoe UI', 9), relief='flat', bd=0,
                padx=14, pady=4, cursor='hand2',
                command=lambda id=prov['id']: self._pick_provider(id),
            )
            b.pack(side='left', padx=(0, 5))
            self._prov_btns[prov['id']] = b

        key_row = tk.Frame(parent, bg=C['panel'])
        key_row.pack(fill='x', pady=(0, 5))
        tk.Label(key_row, text='API Key', bg=C['panel'], fg=C['dim'],
                 font=('Segoe UI', 9, 'bold'), width=8, anchor='w').pack(side='left')
        self._cloud_key = tk.Entry(
            key_row, font=('Segoe UI', 9), bg=C['card'], fg=C['text'],
            insertbackground=C['text'], relief='flat', bd=5, show='•',
        )
        self._cloud_key.pack(side='left', fill='x', expand=True)

        foot = tk.Frame(parent, bg=C['panel'])
        foot.pack(fill='x')
        self._cloud_status = tk.Label(foot, text='', bg=C['panel'], fg=C['dim'],
                                       font=('Segoe UI', 8))
        self._cloud_status.pack(side='left')
        self._verify_btn = self._btn(foot, 'Verify & Add',
                                      self._verify_cloud, primary=False, small=True)
        self._verify_btn.pack(side='right')

        self._current_provider = 'anthropic'
        self._pick_provider('anthropic')

    def _pick_provider(self, pid: str):
        self._current_provider = pid
        prov = next(p for p in CLOUD_PROVIDERS if p['id'] == pid)
        for p_id, btn in self._prov_btns.items():
            sel = p_id == pid
            btn.config(bg=C['accent'] if sel else C['pill'],
                       fg='white'    if sel else C['dim'],
                       font=('Segoe UI', 9, 'bold' if sel else 'normal'))
        self._cloud_key.delete(0, 'end')
        self._cloud_key.insert(0, prov['ph'])
        self._cloud_key.config(fg=C['dim'])
        self._cloud_status.config(text=f"Model: {prov['model']}", fg=C['dim'])

        def _clear(e, ph=prov['ph']):
            if self._cloud_key.get() == ph:
                self._cloud_key.delete(0, 'end')
                self._cloud_key.config(fg=C['text'])
        self._cloud_key.bind('<FocusIn>', _clear)

    def _verify_cloud(self):
        pid  = self._current_provider
        prov = next(p for p in CLOUD_PROVIDERS if p['id'] == pid)
        key  = self._cloud_key.get().strip()
        if not key or key == prov['ph']:
            self._cloud_status.config(text='Enter your API key first.', fg=C['err'])
            return
        # Prevent duplicate
        dup_id = f"{pid}::{prov['model']}::{key[:8]}"
        if any(m['id'] == dup_id for m in self._added):
            self._cloud_status.config(text='Already added.', fg=C['teal'])
            return
        self._verify_btn.config(state='disabled', bg=C['muted'])
        self._cloud_status.config(text='Verifying…', fg=C['dim'])
        threading.Thread(target=self._verify_thread, args=(prov, key), daemon=True).start()

    def _verify_thread(self, prov: dict, key: str):
        ok, msg = _test_key(prov['id'], prov['model'], key)
        self.root.after(0, lambda: self._verify_done(ok, msg, prov, key))

    def _verify_done(self, ok: bool, msg: str, prov: dict, key: str):
        if ok:
            entry = {
                'id':           f"{prov['id']}::{prov['model']}::{key[:8]}",
                'type':         prov['id'],
                'display_name': f"{prov['label']}  —  {prov['model']}",
                'tag':          '',
                'model_name':   prov['model'],
                'api_key':      key,
                'enabled':      True,
            }
            self._cloud_status.config(text=f'✓  {msg}', fg=C['teal'])
            self._add_model(entry)
        else:
            self._cloud_status.config(text=msg[:70], fg=C['err'])
        self._verify_btn.config(state='normal', bg=C['pill'])

    # ── model list management ──────────────────────────────────────────────────

    def _add_model(self, entry: dict):
        self._added = [m for m in self._added if m['id'] != entry['id']]
        self._added.append(entry)
        crab_config.add_model(entry)
        _mark_setup_complete()
        self._rebuild_list()

    def _remove_model(self, model_id: str):
        self._added = [m for m in self._added if m['id'] != model_id]
        crab_config.remove_model(model_id)
        if not self._added:
            crab_config.set_value('setup_complete', False)
        self._rebuild_list()

    def _rebuild_list(self):
        for w in self._list_frame.winfo_children():
            w.destroy()

        if not self._added:
            tk.Label(
                self._list_frame,
                text='No models yet — add one below',
                bg=C['card'], fg=C['dim'],
                font=('Segoe UI', 9),
            ).pack(pady=20, padx=20, anchor='w')
            self._continue_btn.config(state='disabled', bg=C['muted'])
            return

        for entry in self._added:
            self._render_card(entry)

        _mark_setup_complete()
        self._continue_btn.config(state='normal', bg=C['accent'])

    def _render_card(self, entry: dict):
        is_local = entry['type'] == 'ollama'
        icon  = '🖥' if is_local else '☁'
        badge = {'ollama': 'local', 'anthropic': 'Claude',
                 'openai': 'OpenAI', 'gemini': 'Gemini'}.get(entry['type'], entry['type'])
        bar_c = C['accent'] if is_local else C['teal']

        row = tk.Frame(self._list_frame, bg=C['card'])
        row.pack(fill='x')
        tk.Frame(row, bg=bar_c, width=3).pack(side='left', fill='y')
        tk.Label(row, text=icon, bg=C['card'],
                 font=('Segoe UI', 12)).pack(side='left', padx=(10, 5), pady=9)
        tk.Label(row, text=entry['display_name'], bg=C['card'], fg=C['text'],
                 font=('Segoe UI', 10, 'bold')).pack(side='left')
        tk.Label(row, text=badge, bg=C['card'], fg=C['dim'],
                 font=('Segoe UI', 8), padx=8).pack(side='left')
        if entry['type'] == 'ollama':
            installed = entry.get('tag', '') in self._installed_tags
            status_text  = '✓ ready' if installed else '⚠ not downloaded'
            status_color = C['teal'] if installed else C['err']
        else:
            status_text, status_color = '✓ ready', C['teal']
        tk.Label(row, text=status_text, bg=C['card'], fg=status_color,
                 font=('Segoe UI', 8)).pack(side='left')
        tk.Button(
            row, text='  ×  ',
            command=lambda eid=entry['id']: self._remove_model(eid),
            bg=C['card'], fg=C['dim'],
            activebackground=C['err'], activeforeground='white',
            font=('Segoe UI', 12), relief='flat', bd=0, cursor='hand2', pady=4,
        ).pack(side='right', padx=4)
        tk.Frame(self._list_frame, bg=C['border'], height=1).pack(fill='x')

    # ── PAGE 4: Done ───────────────────────────────────────────────────────────

    def _build_done(self):
        p = self._page('done')
        inner = tk.Frame(p, bg=C['bg'])
        inner.place(relx=0.5, rely=0.44, anchor='center')

        tile = tk.Frame(inner, bg=C['teal'], width=76, height=76)
        tile.pack_propagate(False)
        tile.pack(pady=(0, 18))
        tk.Label(tile, text='✓', bg=C['teal'], fg='white',
                 font=('Segoe UI', 36, 'bold')).pack(expand=True)

        tk.Label(inner, text="You're all set!", bg=C['bg'], fg=C['text'],
                 font=('Segoe UI', 24, 'bold')).pack()
        self._done_sub = tk.Label(inner, text='', bg=C['bg'], fg=C['dim'],
                                   font=('Segoe UI', 10))
        self._done_sub.pack(pady=(6, 30))
        self._btn(inner, 'Launch Pebble  🦀', self.root.quit).pack()

    def _refresh_done(self):
        count = len(self._added)
        if count == 0:
            self._done_sub.config(text='No models configured.')
            return
        names = ',   '.join(m['display_name'] for m in self._added[:2])
        suffix = f'  + {count - 2} more' if count > 2 else ''
        is_dry = bool(crab_config.get('dry_run'))
        dry_note = (
            '\n\n🧪  Dry-run mode is ON for now — Pebble will log every action it WOULD take '
            'instead of calling external APIs. Use /review-drafts to inspect, then turn off '
            'in Settings when you trust it.\n\n'
            'Authenticate Google, Canvas, Obsidian, etc. from Settings → Modules.'
        ) if is_dry else ''
        self._done_sub.config(
            text=f'{count} model{"s" if count > 1 else ""} ready:\n{names}{suffix}{dry_note}',
            justify='center', wraplength=W - 80)


# ── helpers ────────────────────────────────────────────────────────────────────

def _test_key(provider: str, model: str, key: str) -> tuple[bool, str]:
    try:
        if provider == 'anthropic':
            import anthropic
            anthropic.Anthropic(api_key=key).messages.create(
                model=model, max_tokens=4, messages=[{'role': 'user', 'content': 'hi'}])
        elif provider == 'openai':
            import openai
            openai.OpenAI(api_key=key).chat.completions.create(
                model=model, max_tokens=4, messages=[{'role': 'user', 'content': 'hi'}])
        elif provider == 'gemini':
            import google.generativeai as genai
            genai.configure(api_key=key)
            genai.GenerativeModel(model).generate_content('hi')
        return True, 'Connected!'
    except Exception as exc:
        return False, str(exc)[:80]


def _dark_titlebar(win: tk.Tk):
    try:
        hwnd = win.winfo_id()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def run_setup(root: tk.Tk | None = None) -> tk.Tk:
    """Run the setup wizard. Returns the Tk root so the caller can reuse it."""
    if root is None:
        root = tk.Tk()
    SetupWizard(root)
    root.mainloop()  # exits when user clicks "Launch Pebble" (root.quit)
    root.withdraw()  # hide the wizard window; caller will repurpose the root
    return root


if __name__ == '__main__':
    run_setup()
