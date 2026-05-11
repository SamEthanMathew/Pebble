"""Model backend — routes chat() to Ollama (local) or a cloud API provider."""

from __future__ import annotations
import shutil
import subprocess
import time

import requests

import crab_config

OLLAMA_BASE = 'http://localhost:11434'
TIMEOUT     = 120


class ModelBackend:
    def __init__(self, entry: dict):
        self.entry    = entry
        self.type     = entry['type']
        self.model    = entry.get('model_name') or entry.get('tag', '')
        self.api_key  = entry.get('api_key', '')
        self.tool_mode = entry.get('tool_mode', '')   # set by orchestrator at runtime

    # ── factory ────────────────────────────────────────────────────────────────

    @classmethod
    def active(cls) -> ModelBackend:
        """Return a backend for the currently active model."""
        mid    = crab_config.get_active_model_id()
        models = crab_config.get_models()
        entry  = next((m for m in models if m['id'] == mid), None)
        if not entry:
            enabled = [m for m in models if m.get('enabled', True)]
            if not enabled:
                raise RuntimeError('No models configured. Run setup.')
            entry = enabled[0]
        return cls(entry)

    @classmethod
    def for_id(cls, model_id: str) -> ModelBackend:
        models = crab_config.get_models()
        entry  = next((m for m in models if m['id'] == model_id), None)
        if not entry:
            raise ValueError(f'Model {model_id!r} not found in config.')
        return cls(entry)

    # ── chat ───────────────────────────────────────────────────────────────────

    def chat(self, messages: list[dict], system: str = '', *,
             max_tokens: int = 4096) -> str:
        """Send a chat completion. max_tokens caps OUTPUT length only.
        Planner-style large-JSON callers should pass 8192 or higher.
        """
        if self.type == 'ollama':
            return self._ollama(messages, system, max_tokens=max_tokens)
        if self.type == 'anthropic':
            return self._anthropic(messages, system, max_tokens=max_tokens)
        if self.type == 'openai':
            return self._openai(messages, system, max_tokens=max_tokens)
        if self.type == 'gemini':
            return self._gemini(messages, system, max_tokens=max_tokens)
        raise ValueError(f'Unknown backend type: {self.type!r}')

    # ── providers ──────────────────────────────────────────────────────────────

    def _ollama(self, messages, system, *, max_tokens: int = 4096):
        if not ollama_running():
            raise RuntimeError('Ollama is not running. Start it with: ollama serve')
        installed = local_models()
        if installed and self.model not in installed:
            raise RuntimeError(
                f'Model "{self.model}" is not installed in Ollama.\n'
                f'Installed models: {", ".join(installed)}\n'
                f'Run: ollama pull {self.model}'
            )
        msgs = ([{'role': 'system', 'content': system}] if system else []) + messages
        r = requests.post(
            f'{OLLAMA_BASE}/api/chat',
            json={'model': self.model, 'messages': msgs, 'stream': False,
                  'options': {'num_predict': max_tokens}},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()['message']['content']

    def _anthropic(self, messages, system, *, max_tokens: int = 4096):
        import anthropic
        kwargs: dict = dict(model=self.model, max_tokens=max_tokens, messages=messages)
        if system:
            kwargs['system'] = system
        c = anthropic.Anthropic(api_key=self.api_key)
        return c.messages.create(**kwargs).content[0].text

    def _openai(self, messages, system, *, max_tokens: int = 4096):
        import openai
        msgs = ([{'role': 'system', 'content': system}] if system else []) + messages
        c = openai.OpenAI(api_key=self.api_key)
        return c.chat.completions.create(
            model=self.model, messages=msgs, max_tokens=max_tokens,
        ).choices[0].message.content

    def _gemini(self, messages, system, *, max_tokens: int = 4096):
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        m       = genai.GenerativeModel(
            self.model, system_instruction=system or None,
            generation_config={'max_output_tokens': max_tokens},
        )
        history = [
            {'role': 'user' if msg['role'] == 'user' else 'model',
             'parts': [msg['content']]}
            for msg in messages[:-1]
        ]
        return m.start_chat(history=history).send_message(messages[-1]['content']).text

    # ── vision ─────────────────────────────────────────────────────────────────

    def chat_with_image(self, messages: list[dict], system: str = '', image_path: str = '') -> str:
        """
        Send a chat request with an attached image.
        Only works with vision-capable models (Anthropic claude-sonnet-4-6, OpenAI gpt-4o, etc.)
        Falls back to regular chat() if image_path is empty or model doesn't support vision.
        """
        if not image_path:
            return self.chat(messages, system)

        import base64
        from pathlib import Path

        # Load image as base64
        try:
            img_data = Path(image_path).read_bytes()
            b64 = base64.b64encode(img_data).decode()
            # Determine media type
            media_type = (
                'image/jpeg'
                if image_path.endswith('.jpg') or image_path.endswith('.jpeg')
                else 'image/png'
            )
        except Exception:
            return self.chat(messages, system)

        if self.type == 'anthropic':
            return self._anthropic_vision(messages, system, b64, media_type)
        elif self.type == 'openai':
            return self._openai_vision(messages, system, b64, media_type)
        else:
            # Ollama and Gemini vision support is inconsistent — fall back
            return self.chat(messages, system)

    def _anthropic_vision(self, messages, system, b64, media_type):
        import anthropic
        c = anthropic.Anthropic(api_key=self.api_key)

        # Build messages with image in the last user message
        msgs = list(messages)
        if msgs and msgs[-1]['role'] == 'user':
            last_content = msgs[-1]['content']
            if isinstance(last_content, str):
                msgs[-1] = {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type':       'base64',
                                'media_type': media_type,
                                'data':       b64,
                            },
                        },
                        {'type': 'text', 'text': last_content},
                    ],
                }

        kw = dict(model=self.model, max_tokens=1024, messages=msgs)
        if system:
            kw['system'] = system
        resp = c.messages.create(**kw)
        return next((b.text for b in resp.content if hasattr(b, 'text')), '')

    def _openai_vision(self, messages, system, b64, media_type):
        import openai
        c = openai.OpenAI(api_key=self.api_key)

        msgs = ([{'role': 'system', 'content': system}] if system else []) + list(messages)
        if msgs and msgs[-1]['role'] == 'user':
            last_content = msgs[-1]['content']
            if isinstance(last_content, str):
                msgs[-1] = {
                    'role': 'user',
                    'content': [
                        {'type': 'image_url', 'image_url': {'url': f'data:{media_type};base64,{b64}'}},
                        {'type': 'text', 'text': last_content},
                    ],
                }

        resp = c.chat.completions.create(model=self.model, messages=msgs)
        return resp.choices[0].message.content or ''


# ── Ollama server helpers ──────────────────────────────────────────────────────

def ollama_running() -> bool:
    try:
        requests.get(f'{OLLAMA_BASE}/', timeout=3)
        return True
    except Exception:
        return False


def ollama_installed() -> bool:
    return shutil.which('ollama') is not None


def start_ollama() -> bool:
    if not ollama_installed():
        return False
    try:
        subprocess.Popen(
            ['ollama', 'serve'],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(10):
            time.sleep(0.7)
            if ollama_running():
                return True
    except Exception:
        pass
    return False


def local_models() -> list[str]:
    try:
        r = requests.get(f'{OLLAMA_BASE}/api/tags', timeout=5)
        return [m['name'] for m in r.json().get('models', [])]
    except Exception:
        return []
