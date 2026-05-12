"""Daily Journal module — guided reflection prompts, save and read journal entries."""

from __future__ import annotations
from datetime import date
from pathlib import Path

from .base import ActionTier, PebbleModule
import crab_config

_JOURNAL_DIR = Path.home() / '.pebble' / 'journal'


class JournalModule(PebbleModule):
    name         = 'journal'
    display_name = 'Daily Journal'
    description  = 'Guided daily reflection — start a journal entry, save it, and review past entries'
    icon         = '📔'

    _default_tiers = {
        'start':     ActionTier.AUTO,
        'get_today': ActionTier.AUTO,
        'get_date':  ActionTier.AUTO,
        'save':      ActionTier.NOTIFY,
    }

    config_fields = [
        {
            'key':   'save_folder',
            'label': 'Journal save folder (optional, for Obsidian)',
            'type':  'path',
        },
    ]

    QUESTIONS = [
        "1. What did you accomplish today?",
        "2. What went well?",
        "3. What got in the way?",
        "4. What needs to carry forward to tomorrow?",
        "5. What are tomorrow's top 3 priorities?",
        "6. What's the first thing you'll do tomorrow morning?",
    ]

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'journal'

    def tool_description(self) -> str:
        return (
            'Manage your daily journal. Supports: '
            'start (show guided reflection questions to answer), '
            'save (save a completed journal entry for today), '
            'get_today (read today\'s journal entry), '
            'get_date (read the journal entry for a specific YYYY-MM-DD date).'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['start', 'save', 'get_today', 'get_date'],
                    'description': 'Action to perform',
                },
                'content': {
                    'type': 'string',
                    'description': 'Journal content to save (used with save)',
                },
                'date': {
                    'type': 'string',
                    'description': 'Date in YYYY-MM-DD format (used with get_date)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = '', content: str = '', date: str = '', **_) -> str:
        if action == 'start':
            return self._action_start()
        elif action == 'save':
            return self._action_save(content)
        elif action == 'get_today':
            return self._action_get_today()
        elif action == 'get_date':
            return self._action_get_date(date)
        else:
            return (
                f'Unknown action "{action}". '
                'Valid actions: start, save, get_today, get_date.'
            )

    # ── actions ────────────────────────────────────────────────────────────────

    def _action_start(self) -> str:
        header = f'Daily Reflection — {_today()}\n' + '=' * 36
        questions = '\n\n'.join(self.QUESTIONS)
        return f'{header}\n\n{questions}\n\nAnswer each question, then use journal(save) to save your entry.'

    def _action_save(self, content: str) -> str:
        if not content.strip():
            return 'No content provided to save.'
        today = _today()
        filename = f'{today}.md'
        saved_to = []

        # Always save to local ~/.pebble/journal/
        try:
            _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
            local_path = _JOURNAL_DIR / filename
            local_path.write_text(content, encoding='utf-8')
            saved_to.append(str(local_path))
        except Exception as e:
            return f'Error saving journal entry: {e}'

        # Also save to save_folder if configured and valid
        cfg = crab_config.get_module_config(self.name)
        save_folder = cfg.get('save_folder', '').strip()
        if save_folder:
            extra_dir = Path(save_folder)
            if extra_dir.is_dir():
                try:
                    extra_path = extra_dir / filename
                    extra_path.write_text(content, encoding='utf-8')
                    saved_to.append(str(extra_path))
                except Exception as e:
                    saved_to.append(f'(also tried {extra_dir / filename} — error: {e})')

        locations = '\n  '.join(saved_to)
        return f'Journal entry saved for {today}:\n  {locations}'

    def _action_get_today(self) -> str:
        path = _JOURNAL_DIR / f'{_today()}.md'
        if not path.exists():
            return 'No journal entry for today yet.'
        try:
            return path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            return f'Error reading today\'s journal: {e}'

    def _action_get_date(self, date_str: str) -> str:
        if not date_str.strip():
            return 'No date provided. Use YYYY-MM-DD format.'
        path = _JOURNAL_DIR / f'{date_str.strip()}.md'
        if not path.exists():
            return f'No entry found for {date_str.strip()}.'
        try:
            return path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            return f'Error reading journal for {date_str}: {e}'


# ── module-level helper ────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()
