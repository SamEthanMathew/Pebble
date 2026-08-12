"""Persistent config for Pebble. Stored at ~/.pebble/config.json

Schema:
{
  "setup_complete": bool,
  "active_model_id": str,          # ID of the model Pebble currently uses
  "models": [
    {
      "id":           str,          # unique key, e.g. "ollama::gemma4:12b"
      "type":         str,          # "ollama" | "anthropic" | "openai" | "gemini"
      "display_name": str,
      "tag":          str,          # ollama model tag
      "model_name":   str,          # cloud model name
      "api_key":      str,          # cloud only
      "enabled":      bool
    },
    ...
  ]
}
"""

from __future__ import annotations
import json

import atomic_io
import paths

_CONFIG_PATH = paths.config_path()


def _load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save(data: dict):
    # Atomic write (write-tmp + os.replace) per contracts.md §11: config.json
    # holds API keys and per-action tier overrides and is read/written across
    # processes, so a torn write must never expose a partial file to a reader.
    atomic_io.write_json(_CONFIG_PATH, data)


def get(key: str, default=None):
    return _load().get(key, default)


def set_value(key: str, value):
    data = _load()
    data[key] = value
    _save(data)


def get_all() -> dict:
    return _load()


# ── model list helpers ─────────────────────────────────────────────────────────

def get_models() -> list[dict]:
    return _load().get('models', [])


def get_enabled_models() -> list[dict]:
    return [m for m in get_models() if m.get('enabled', True)]


def add_model(entry: dict):
    """Add or replace a model entry (matched by id)."""
    data   = _load()
    models = data.get('models', [])
    models = [m for m in models if m.get('id') != entry['id']]
    models.append(entry)
    data['models'] = models
    # auto-set as active if it's the first enabled model
    if not data.get('active_model_id') and entry.get('enabled', True):
        data['active_model_id'] = entry['id']
    _save(data)


def remove_model(model_id: str):
    data   = _load()
    models = [m for m in data.get('models', []) if m.get('id') != model_id]
    data['models'] = models
    if data.get('active_model_id') == model_id:
        remaining = [m for m in models if m.get('enabled', True)]
        data['active_model_id'] = remaining[0]['id'] if remaining else ''
    _save(data)


def set_model_enabled(model_id: str, enabled: bool):
    data = _load()
    for m in data.get('models', []):
        if m['id'] == model_id:
            m['enabled'] = enabled
    if not enabled and data.get('active_model_id') == model_id:
        remaining = [m for m in data['models']
                     if m.get('enabled', True) and m['id'] != model_id]
        data['active_model_id'] = remaining[0]['id'] if remaining else ''
    _save(data)


def get_active_model_id() -> str:
    return get('active_model_id', '')


def set_active_model(model_id: str):
    set_value('active_model_id', model_id)


def reset():
    """Wipe config — re-triggers setup wizard on next launch."""
    if _CONFIG_PATH.exists():
        _CONFIG_PATH.unlink()


# ── module config helpers ──────────────────────────────────────────────────────

def get_all_module_configs() -> dict:
    return _load().get('modules', {})


def get_module_config(name: str) -> dict:
    return _load().get('modules', {}).get(name, {})


def set_module_config(name: str, cfg: dict):
    data = _load()
    data.setdefault('modules', {})[name] = cfg
    _save(data)
