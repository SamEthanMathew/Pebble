# PyInstaller spec for Pebble.
# Build:  pyinstaller pebble.spec  (or `pwsh release/build.ps1`)

# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# All optional integration modules — soft-imported at runtime, but PyInstaller
# can't see them via static analysis. Include explicitly so the .exe ships
# with everything the registry expects.
HIDDEN_INTEGRATIONS = [
    'modules.alpaca_market', 'modules.brave_search', 'modules.canvas',
    'modules.clipboard', 'modules.crypto', 'modules.discord_module',
    'modules.entity_module', 'modules.file_search', 'modules.focus_timer',
    'modules.gcal', 'modules.github_module', 'modules.gmail',
    'modules.google_auth', 'modules.journal', 'modules.kalshi',
    'modules.memory', 'modules.news_feed', 'modules.notion',
    'modules.obsidian', 'modules.reminders', 'modules.screenshot',
    'modules.slack_module', 'modules.spotify', 'modules.stt',
    'modules.system_context', 'modules.tasks', 'modules.todoist',
    'modules.weather',
]

HIDDEN_PLANNERS = [
    'planners.base', 'planners.schedule', 'planners.comms',
    'planners.school', 'planners.dispatcher', 'planners.morning',
    'planners.exam_prep', 'planners.wrapup',
]

HIDDEN_INFRA = [
    'audit', 'audit_reader', 'autonomy', 'approval_queue',
    'atomic_io', 'cache', 'crab_config', 'dry_run', 'entity_store',
    'entity_suggest', 'events', 'feedback', 'first_time_ledger',
    'idle_detect', 'metrics', 'model_backend', 'notification_popup',
    'proactive_engine', 'prompts', 'scraper', 'tool_orchestrator',
    'settings_window', 'setup_wizard', 'chat_window',
]

# Provider SDKs are optional; let PyInstaller follow them if present.
HIDDEN_OPTIONAL = collect_submodules('anthropic')

hiddenimports = HIDDEN_INFRA + HIDDEN_INTEGRATIONS + HIDDEN_PLANNERS + HIDDEN_OPTIONAL

# Bundled assets
datas = [
    ('crabpics',          'crabpics'),
    ('pebble_logo_pack',  'pebble_logo_pack'),
    ('prompts',           'prompts'),
    ('docs/contracts.md', 'docs'),
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy unused stdlib modules
        'tkinter.test', 'unittest', 'pytest',
        # Heavy site-packages that show up via transitive imports but Pebble
        # doesn't actually use. Excluding short-circuits their hooks (~30 min
        # of analysis on a developer's polluted environment).
        'cv2', 'opencv-python',
        'transformers', 'datasets', 'tokenizers',
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'tf-keras', 'keras',
        'sklearn', 'scikit-learn', 'skimage', 'scikit-image',
        'statsmodels', 'patsy',
        'pygame',
        'plotly', 'narwhals', 'altair',
        'pdfminer', 'pypdfium2', 'pypdfium2_raw',
        'mako',
        'sentry_sdk',
        'uvicorn', 'fastapi', 'starlette',
        'orjson',
        'pydub',
        'matplotlib', 'mpl_toolkits',
        'scipy',
        'IPython', 'ipykernel', 'jupyter', 'notebook',
        'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Pebble',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                                       # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join('pebble_logo_pack', 'pebble-favicon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Pebble',
)
