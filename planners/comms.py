"""Comms planner — triage only (Phase 2). Drafting activates in Phase 3.

Two-tier trigger model:
  - immediate: EMAIL_RECEIVED_IMPORTANT or likely-important (display name + cmu.edu domain
    or known-important domain set)
  - catch-up: every 2 hours (handled by caller; this planner just runs when invoked)

Cloud-only: disabled when no planner_model is configured.
Output: ~/.pebble/state/comms_pending.json (envelope per contracts.md §7b).
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

import audit
import entity_store
import paths
import prompts as prompt_lib
from atomic_io import read_json, write_json
from planners.base import BasePlanner

# Newsletter set learned over time. Persisted at ~/.pebble/comms_newsletters.json.
_NEWSLETTERS_PATH = paths.data_dir() / 'comms_newsletters.json'

# Domains we treat as "likely-important unknown" by default.
KNOWN_IMPORTANT_DOMAINS = {
    'cmu.edu', 'andrew.cmu.edu', 'cs.cmu.edu', 'ece.cmu.edu',
}


def _load_newsletter_domains() -> set[str]:
    data = read_json(_NEWSLETTERS_PATH, default={'domains': []}) or {'domains': []}
    return set(data.get('domains', []))


def _add_newsletter_domain(domain: str) -> None:
    data = read_json(_NEWSLETTERS_PATH, default={'domains': []}) or {'domains': []}
    domains = set(data.get('domains', []))
    domains.add(domain)
    write_json(_NEWSLETTERS_PATH, {'domains': sorted(domains)})


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r'^```(?:json)?\s*\n(.*)\n```\s*$', text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def is_likely_important_unknown(message: dict[str, Any]) -> bool:
    """Sender-side heuristic for the comms re-run gate.

    Returns True if a message from an unknown sender is likely important enough
    to trigger immediate triage (rather than waiting for the 2hr catch-up pass).
    """
    name   = (message.get('from_name') or '').strip()
    email  = (message.get('from_email') or '').lower()
    if not email or '@' not in email:
        return False
    domain = email.rsplit('@', 1)[1]

    # Skip newsletters
    if domain in _load_newsletter_domains():
        return False
    headers = message.get('headers', {}) or {}
    if any(k.lower() == 'list-unsubscribe' for k in headers):
        return False

    # Has display name AND (known-important domain OR has prior thread)
    if name and (domain in KNOWN_IMPORTANT_DOMAINS or message.get('thread_message_count', 0) > 1):
        return True

    return False


class CommsPlanner(BasePlanner):
    name        = 'comms'
    state_doc   = 'comms_pending.json'
    ttl_seconds = 60 * 60 * 2  # 2 hours; triggers fire sooner

    # ── input collection ──────────────────────────────────────────────────────

    def collect_inputs(self) -> dict[str, Any]:
        return {
            'new_messages': self._read_recent_unread(),
            'contacts':     self._read_contacts(),
            'priorities':   self._read_priorities(),
            'schedule':     self._read_schedule_summary(),
            'now':          datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def _read_recent_unread(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read up to `limit` recent unread messages. Stub when Gmail not connected."""
        try:
            from modules.google_auth import GoogleServices, is_google_connected
            if not is_google_connected():
                return []
            svc = GoogleServices()
            res = svc.gmail.users().messages().list(
                userId='me', q='is:unread newer_than:7d', maxResults=limit,
            ).execute()
            ids = [m['id'] for m in res.get('messages', [])]
            out: list[dict[str, Any]] = []
            for mid in ids:
                msg = svc.gmail.users().messages().get(
                    userId='me', id=mid, format='metadata',
                    metadataHeaders=['From', 'Subject', 'List-Unsubscribe'],
                ).execute()
                hdrs = {h['name']: h['value'] for h in (msg.get('payload', {}).get('headers') or [])}
                from_raw = hdrs.get('From', '')
                m = re.match(r'^(.*?)\s*<(.+?)>$', from_raw)
                if m:
                    name, email = m.group(1).strip().strip('"'), m.group(2).strip()
                else:
                    name, email = '', from_raw.strip()
                out.append({
                    'message_id': mid,
                    'thread_id':  msg.get('threadId', ''),
                    'from_email': email,
                    'from_name':  name,
                    'subject':    hdrs.get('Subject', ''),
                    'snippet':    msg.get('snippet', ''),
                    'headers':    {k: v for k, v in hdrs.items() if k.lower() == 'list-unsubscribe'},
                })
            return out
        except Exception as exc:
            audit.append({
                'module': 'planner.comms', 'action': 'gmail_read_failed',
                'result': {'error': str(exc)}, 'tier': 'auto', 'source': 'planner.comms',
            })
            return []

    def _read_contacts(self) -> list[dict[str, Any]]:
        try:
            entity_store.init()
            return [{'name': e.name, 'aliases': e.aliases, 'payload': e.payload}
                    for e in entity_store.list_entities(type='person')]
        except Exception:
            return []

    def _read_priorities(self) -> list[str]:
        """Pulled from a free-form list at config.priorities (config-driven)."""
        try:
            import crab_config
            return list(crab_config.get('priorities', []) or [])
        except Exception:
            return []

    def _read_schedule_summary(self) -> str:
        """A short snippet of today's schedule (for tone calibration)."""
        try:
            from planners.base import read_state_doc
            env = read_state_doc('schedule_today.json')
            if not env:
                return '(no schedule state yet)'
            payload = env.get('payload', {})
            blocks = payload.get('blocks', [])
            return f'{len(blocks)} blocks today' + ('' if not blocks else
                                                     '; first: ' + blocks[0].get('title', ''))
        except Exception:
            return '(unavailable)'

    # ── prompt rendering ──────────────────────────────────────────────────────

    def render_prompt(self, inputs: dict[str, Any]) -> tuple[str, str]:
        system_prompt = prompt_lib.render('comms_triage', {
            'new_messages': json.dumps(inputs['new_messages'], indent=2, default=str),
            'contacts':     json.dumps(inputs['contacts'], indent=2, default=str),
            'priorities':   json.dumps(inputs['priorities'], default=str),
            'schedule':     inputs['schedule'],
        })
        user_msg = 'Triage the messages and produce comms_pending.json now.'
        return system_prompt, user_msg

    # ── output parsing ────────────────────────────────────────────────────────

    def parse_output(self, llm_text: str, inputs: dict[str, Any]) -> dict[str, Any]:
        from planners.base import extract_json_object
        data = extract_json_object(llm_text)
        if not isinstance(data, dict):
            raise ValueError('comms planner output must be a JSON object')
        data.setdefault('action_required', [])
        data.setdefault('fyi',             [])
        data.setdefault('ignore_count',    0)

        # Drafting activation: per config.comms.draft_enabled (default False).
        # When False, strip any drafts the model speculatively returned.
        if not _draft_enabled():
            for item in data.get('action_required', []):
                if isinstance(item, dict):
                    item.pop('draft', None)
        return data

    # ── draft proposals (Phase 3) ─────────────────────────────────────────────

    def draft_proposals(self, payload: dict[str, Any]) -> list:
        """Yield Proposal objects for each action_required item with a draft.

        Routed through the autonomy layer (NOTIFY tier on gmail.draft).
        Caller pattern:
            payload = CommsPlanner().run()
            for p in CommsPlanner().draft_proposals(payload):
                autonomy.route(p)
        """
        from planners.base import Proposal

        if not _draft_enabled():
            return []

        out = []
        for item in payload.get('action_required', []) or []:
            draft = item.get('draft')
            if not draft:
                continue
            from_block = item.get('from', {}) or {}
            to_email = from_block.get('email')
            subject  = item.get('subject', '')
            thread_id = item.get('thread_id', '') or item.get('message_id', '')
            if not to_email:
                continue
            out.append(Proposal(
                module='gmail',
                action='draft',
                args={
                    'to':      to_email,
                    'subject': f'Re: {subject}' if subject and not subject.lower().startswith('re:') else subject,
                    'body':    draft,
                    'thread_id': thread_id,
                },
                source='comms_planner',
                urgency=item.get('urgency', 'normal'),
                reversible=True,
                target_id=to_email,
                rationale=f'Draft reply to {from_block.get("name") or to_email}: {item.get("summary", "")[:80]}',
            ))
        return out


def _draft_enabled() -> bool:
    try:
        import crab_config
        cfg = crab_config.get('comms', {}) or {}
        return bool(cfg.get('draft_enabled', False))
    except Exception:
        return False
