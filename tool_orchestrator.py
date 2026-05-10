"""Tool orchestrator — routes chat calls through two paths:
  - native:   model API handles tool call/result formatting (Anthropic, OpenAI, Gemini,
              modern Ollama models like gemma4 / llama3.2)
  - prompted: ReAct-style prompt engineering for weaker local models (tinyllama, etc.)

Both paths look identical to the caller; just call orchestrator.chat().
"""

from __future__ import annotations
import json
import re

import requests

from model_backend import ModelBackend, OLLAMA_BASE, TIMEOUT
from modules.base import PebbleModule

MAX_ROUNDS = 8

# Ollama model tags known to support native tool calling
_NATIVE_OLLAMA = [
    'gemma', 'llama3', 'mistral', 'mixtral',
    'qwen2', 'qwen3', 'phi3', 'phi4',
    'command-r', 'deepseek', 'solar', 'firefunction',
]

_PROMPTED_SYSTEM = """\
You have access to these tools — use them whenever they would help answer the question:
{tool_lines}

To call a tool, output EXACTLY this format on its own line:
TOOL: tool_name({"param1": "value1", "param2": "value2"})

For simple single-param tools: TOOL: tool_name(argument)
For tools with no arguments: TOOL: tool_name()
Only one tool call per turn. Wait for the result before continuing."""

_TOOL_RE = re.compile(r'TOOL:\s*(\w+)\((\{.*?\}|[^)]*)\)', re.IGNORECASE | re.DOTALL)


def tool_mode_for(entry: dict) -> str:
    """Return 'native', 'prompted', or 'none' for a model config entry."""
    t = entry.get('type', '')
    if t in ('anthropic', 'openai', 'gemini'):
        return 'native'
    if t == 'ollama':
        tag = (entry.get('tag') or entry.get('model_name', '')).lower()
        if any(p in tag for p in _NATIVE_OLLAMA):
            return 'native'
        return 'prompted'
    return 'none'


class ToolOrchestrator:
    def __init__(self, backend: ModelBackend, modules: list[PebbleModule]):
        self.backend   = backend
        self.modules   = {m.tool_name(): m for m in modules}
        self.tool_mode = tool_mode_for(backend.entry)

    # ── public ─────────────────────────────────────────────────────────────────

    def chat(self, messages: list[dict], system: str = '') -> str:
        if not self.modules:
            return self.backend.chat(messages, system)
        if self.tool_mode == 'native':
            try:
                return self._native(messages, system)
            except Exception:
                # Fall through to prompted if native fails (e.g. model doesn't
                # actually support tool calling despite the tag suggesting it)
                pass
        return self._prompted(messages, system)

    # ── tool execution ─────────────────────────────────────────────────────────

    def _run(self, name: str, args: dict) -> str:
        mod = self.modules.get(name)
        if not mod:
            return f'Unknown tool: {name!r}'
        try:
            return mod.execute(**args)
        except Exception as exc:
            return f'Tool error: {exc}'

    # ── native path ────────────────────────────────────────────────────────────

    def _native(self, messages: list[dict], system: str) -> str:
        t = self.backend.type
        if t == 'anthropic':
            return self._native_anthropic(messages, system)
        if t == 'openai':
            return self._native_openai_compat(messages, system,
                                               api_key=self.backend.api_key,
                                               base_url=None)
        if t == 'ollama':
            return self._native_ollama(messages, system)
        if t == 'gemini':
            return self._native_gemini(messages, system)
        raise ValueError(f'No native tool path for type {t!r}')

    def _native_anthropic(self, messages: list[dict], system: str) -> str:
        import anthropic
        tools = [m.to_anthropic_schema() for m in self.modules.values()]
        c     = anthropic.Anthropic(api_key=self.backend.api_key)
        msgs  = list(messages)

        for _ in range(MAX_ROUNDS):
            kw: dict = dict(model=self.backend.model, max_tokens=1024,
                            messages=msgs, tools=tools)
            if system:
                kw['system'] = system
            resp = c.messages.create(**kw)

            if resp.stop_reason != 'tool_use':
                return next((b.text for b in resp.content if hasattr(b, 'text')), '')

            results = []
            for block in resp.content:
                if block.type == 'tool_use':
                    out = self._run(block.name, dict(block.input))
                    results.append({'type': 'tool_result',
                                    'tool_use_id': block.id,
                                    'content': out})
            msgs.append({'role': 'assistant', 'content': resp.content})
            msgs.append({'role': 'user',      'content': results})

        return 'Tool call limit reached.'

    def _native_openai_compat(self, messages, system, *, api_key, base_url):
        import openai
        kw: dict = {}
        if base_url:
            kw['base_url'] = base_url
        c     = openai.OpenAI(api_key=api_key, **kw)
        tools = [m.to_openai_schema() for m in self.modules.values()]
        msgs  = ([{'role': 'system', 'content': system}] if system else []) + list(messages)

        for _ in range(MAX_ROUNDS):
            resp = c.chat.completions.create(model=self.backend.model,
                                             messages=msgs, tools=tools)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or ''
            msgs.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                out  = self._run(tc.function.name, args)
                msgs.append({'role': 'tool', 'tool_call_id': tc.id, 'content': out})

        return 'Tool call limit reached.'

    def _native_ollama(self, messages: list[dict], system: str) -> str:
        tools = [m.to_openai_schema() for m in self.modules.values()]
        msgs  = ([{'role': 'system', 'content': system}] if system else []) + list(messages)

        for _ in range(MAX_ROUNDS):
            r = requests.post(
                f'{OLLAMA_BASE}/api/chat',
                json={'model': self.backend.model, 'messages': msgs,
                      'tools': tools, 'stream': False},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            msg  = data.get('message', {})

            tool_calls = msg.get('tool_calls') or []
            if not tool_calls:
                return msg.get('content', '')

            msgs.append(msg)
            for tc in tool_calls:
                fn   = tc.get('function', {})
                args = fn.get('arguments', {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                out  = self._run(fn.get('name', ''), args)
                msgs.append({'role': 'tool', 'content': out})

        return 'Tool call limit reached.'

    def _native_gemini(self, messages: list[dict], system: str) -> str:
        import google.generativeai as genai

        genai.configure(api_key=self.backend.api_key)

        declarations = [
            {'name': m.tool_name(), 'description': m.tool_description(),
             'parameters': m.tool_parameters()}
            for m in self.modules.values()
        ]
        model = genai.GenerativeModel(
            self.backend.model,
            tools=[{'function_declarations': declarations}],
            system_instruction=system or None,
        )

        history = [
            {'role': 'user' if msg['role'] == 'user' else 'model',
             'parts': [msg['content']]}
            for msg in messages[:-1]
        ]
        chat = model.start_chat(history=history)
        resp = chat.send_message(messages[-1]['content'])

        for _ in range(MAX_ROUNDS):
            fc = None
            try:
                for part in resp.candidates[0].content.parts:
                    if hasattr(part, 'function_call') and part.function_call.name:
                        fc = part.function_call
                        break
            except Exception:
                pass

            if fc is None:
                try:
                    return resp.text
                except Exception:
                    return ''

            out  = self._run(fc.name, dict(fc.args))
            resp = chat.send_message(
                genai.protos.Content(
                    role='function',
                    parts=[genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name,
                            response={'result': out},
                        )
                    )],
                )
            )

        try:
            return resp.text
        except Exception:
            return 'Tool call limit reached.'

    # ── prompted / ReAct path ──────────────────────────────────────────────────

    def _prompted(self, messages: list[dict], system: str) -> str:
        tool_lines = '\n'.join(m.to_prompted_line() for m in self.modules.values())
        augmented  = (system + '\n\n' if system else '') + \
                     _PROMPTED_SYSTEM.format(tool_lines=tool_lines)

        msgs = list(messages)
        for _ in range(MAX_ROUNDS):
            response = self.backend.chat(msgs, augmented)
            match    = _TOOL_RE.search(response)
            if not match:
                return response

            tool_name = match.group(1).strip()
            raw_arg   = match.group(2).strip()

            if raw_arg.startswith('{'):
                try:
                    args = json.loads(raw_arg)
                except json.JSONDecodeError:
                    args = {}
            elif raw_arg:
                mod = self.modules.get(tool_name)
                if mod:
                    props = mod.tool_parameters().get('properties', {})
                    if props and raw_arg.strip('"\''):
                        args = {next(iter(props)): raw_arg.strip('"\'')}
                    else:
                        args = {}
                else:
                    args = {}
            else:
                args = {}

            result = self._run(tool_name, args)

            msgs = msgs + [
                {'role': 'assistant', 'content': response},
                {'role': 'user',
                 'content': f'[Tool result: {tool_name}]\n{result}\n\nNow answer using this.'},
            ]

        return self.backend.chat(msgs, augmented)
