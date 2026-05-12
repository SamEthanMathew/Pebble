"""Gmail module — read emails, search, and create drafts from Pebble."""
from __future__ import annotations

import base64
import email.mime.multipart
import email.mime.text
import threading
from typing import Callable

from .base import ActionTier, PebbleModule


class GmailModule(PebbleModule):
    name         = 'gmail'
    display_name = 'Gmail'
    icon         = '📧'

    _default_tiers = {
        'recent':      ActionTier.AUTO,
        'search':      ActionTier.AUTO,
        'unread':      ActionTier.AUTO,
        'draft_reply': ActionTier.NOTIFY,
    }

    config_fields = [
        {
            'key':   '_google_status',
            'label': 'Google Account',
            'type':  'google_status',
        },
        {
            'key':   'important_senders',
            'label': 'Important senders (comma-separated)',
            'type':  'text',
        },
        {
            'key':   'check_interval_minutes',
            'label': 'Check interval (minutes)',
            'type':  'text',
        },
    ]

    def __init__(self, cfg: dict):
        super().__init__(cfg)

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True

    # ── PebbleModule interface ────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'gmail'

    def tool_description(self) -> str:
        return (
            'Interact with Gmail. Supports: '
            'recent (list recent emails), '
            'search (search emails by query), '
            'unread (list unread emails), '
            'draft_reply (create a draft reply for review before sending).'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['recent', 'search', 'unread', 'draft_reply'],
                },
                'query': {
                    'type': 'string',
                    'description': "Gmail search query (e.g. 'from:prof@cmu.edu')",
                },
                'max_results': {
                    'type': 'integer',
                    'description': 'Max emails to return (default 5)',
                },
                'to': {
                    'type': 'string',
                    'description': 'Recipient email for draft',
                },
                'subject': {
                    'type': 'string',
                    'description': 'Subject for draft reply',
                },
                'body': {
                    'type': 'string',
                    'description': 'Body text for draft reply',
                },
                'thread_id': {
                    'type': 'string',
                    'description': 'Thread ID to reply to',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = '', query: str = '', max_results: int = 5,
                to: str = '', subject: str = '', body: str = '',
                thread_id: str = '', **_) -> str:
        try:
            max_results = min(max(1, int(max_results)), 25)
        except (TypeError, ValueError):
            max_results = 5

        try:
            service = self._get_gmail()
        except Exception:
            return 'Gmail not connected — check credentials in Settings.'

        try:
            if action == 'recent':
                return self._action_list(service, q='', max_results=max_results)
            elif action == 'search':
                return self._action_list(service, q=query, max_results=max_results)
            elif action == 'unread':
                return self._action_list(service, q='is:unread', max_results=min(max_results, 25))
            elif action == 'draft_reply':
                return self._action_draft_reply(service, to=to, subject=subject,
                                                body=body, thread_id=thread_id)
            else:
                return (
                    f'Unknown action "{action}". '
                    'Valid actions: recent, search, unread, draft_reply.'
                )
        except Exception as e:
            return f'Gmail error: {e}'

    # ── private helpers ───────────────────────────────────────────────────────

    def _get_gmail(self):
        from .google_auth import GoogleServices
        return GoogleServices().gmail

    def _action_list(self, service, q: str, max_results: int) -> str:
        try:
            max_results = min(max(1, int(max_results)), 25)
        except (TypeError, ValueError):
            max_results = 5
        kwargs: dict = {'userId': 'me', 'maxResults': max_results}
        if q:
            kwargs['q'] = q

        result = service.users().messages().list(**kwargs).execute()
        messages = result.get('messages', [])
        if not messages:
            return 'No recent emails.'

        lines: list[str] = []
        for msg in messages:
            mid = msg['id']
            detail = service.users().messages().get(
                userId='me', id=mid,
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date'],
            ).execute()
            headers = {h['name']: h['value']
                       for h in detail.get('payload', {}).get('headers', [])}
            sender  = headers.get('From', '(unknown)')
            subj    = headers.get('Subject', '(no subject)')
            date    = headers.get('Date', '')
            lines.append(f'From: {sender} | Subject: {subj} | Date: {date}')

        return '\n'.join(lines)

    def _action_draft_reply(self, service, to: str, subject: str,
                            body: str, thread_id: str) -> str:
        if not to.strip() and not subject.strip() and not body.strip():
            return (
                'Please provide a recipient (to), subject, and body '
                'to create a draft reply.'
            )

        msg = email.mime.multipart.MIMEMultipart()
        msg['To']      = to
        msg['Subject'] = subject
        msg.attach(email.mime.text.MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message_body: dict = {'raw': raw}
        if thread_id:
            message_body['threadId'] = thread_id

        service.users().drafts().create(
            userId='me',
            body={'message': message_body},
        ).execute()

        return (
            f"Draft created for '{subject}'. "
            "Open Gmail to review and send before it goes out."
        )


# ── Background watcher ────────────────────────────────────────────────────────

class GmailWatcherThread:
    """Polls Gmail every 60 seconds for new emails from important senders."""

    def __init__(self, important_senders: list[str], on_notification: Callable):
        """
        on_notification(email_info: dict) is called with:
        {
            'sender':       str,
            'sender_email': str,
            'subject':      str,
            'snippet':      str,
            'thread_id':    str,
            'message_id':   str,
        }
        """
        self._important_senders = [s.lower().strip() for s in important_senders]
        self._on_notification   = on_notification
        self._stop_event        = threading.Event()
        self._seen_ids: set[str] = set()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background polling thread (daemon=True)."""
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name='GmailWatcher'
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._stop_event.set()

    # ── internal ──────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """
        Runs every 60 seconds.
        - First run: populates _seen_ids silently (no notifications on startup).
        - Subsequent runs: notifies for any new messages not yet seen.
        Exceptions are swallowed so the thread never crashes silently mid-session.
        """
        first_run = True
        while not self._stop_event.wait(timeout=60 if not first_run else 0):
            try:
                new_emails = self._get_new_emails()
                for info in new_emails:
                    if not first_run:
                        try:
                            self._on_notification(info)
                        except Exception:
                            pass
                    self._seen_ids.add(info['message_id'])
            except Exception:
                pass
            first_run = False

    def _get_new_emails(self) -> list[dict]:
        """
        Builds a Gmail service, queries for recent messages from important senders,
        and returns those not already in _seen_ids.
        """
        if not self._important_senders:
            return []

        try:
            from .google_auth import GoogleServices
            svc = GoogleServices().gmail
        except Exception:
            return []

        # Build a query that matches any of the important senders
        from_parts = ' OR '.join(
            f'from:{sender}' for sender in self._important_senders
        )
        query = f'({from_parts}) is:unread'

        try:
            result = svc.users().messages().list(
                userId='me', q=query, maxResults=20
            ).execute()
        except Exception:
            return []

        messages = result.get('messages', [])
        new_emails: list[dict] = []

        for msg in messages:
            mid = msg['id']
            if mid in self._seen_ids:
                continue
            try:
                detail = svc.users().messages().get(
                    userId='me', id=mid,
                    format='metadata',
                    metadataHeaders=['From', 'Subject', 'Date'],
                ).execute()
            except Exception:
                continue

            headers  = {h['name']: h['value']
                        for h in detail.get('payload', {}).get('headers', [])}
            sender   = headers.get('From', '')
            subj     = headers.get('Subject', '(no subject)')
            snippet  = detail.get('snippet', '')
            tid      = detail.get('threadId', '')

            # Parse display name and email address from "Name <email>" format
            sender_email = sender
            sender_name  = sender
            if '<' in sender and '>' in sender:
                sender_name  = sender[:sender.index('<')].strip().strip('"')
                sender_email = sender[sender.index('<') + 1:sender.index('>')].strip()

            new_emails.append({
                'sender':       sender_name,
                'sender_email': sender_email,
                'subject':      subj,
                'snippet':      snippet,
                'thread_id':    tid,
                'message_id':   mid,
            })

        return new_emails
