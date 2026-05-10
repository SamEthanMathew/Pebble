"""Shared Google OAuth helper — uses embedded client config, no credentials.json needed."""
from __future__ import annotations
from pathlib import Path

_CLIENT_CONFIG = {
    "installed": {
        "client_id":     "REDACTED_GOOGLE_OAUTH_CLIENT_ID",
        "client_secret": "REDACTED_GOOGLE_OAUTH_CLIENT_SECRET",
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar',
]

TOKEN_PATH = Path.home() / '.pebble' / 'google_token.json'


def is_google_connected() -> bool:
    """True if the OAuth token file exists (user has connected their Google account)."""
    return TOKEN_PATH.exists()


class GoogleServices:
    """
    Builds both Gmail and Calendar service objects using embedded OAuth.
    First call triggers a browser sign-in (InstalledAppFlow).
    Subsequent calls load+refresh the saved token.

    Usage:
        svc = GoogleServices()
        svc.gmail     # gmail API service
        svc.calendar  # calendar API service
    """

    def __init__(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow  = InstalledAppFlow.from_client_config(_CLIENT_CONFIG, SCOPES)
                creds = flow.run_local_server(port=0)
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(creds.to_json(), encoding='utf-8')

        self.gmail    = build('gmail',    'v1', credentials=creds)
        self.calendar = build('calendar', 'v3', credentials=creds)

    # ── Gmail helpers ──────────────────────────────────────────────────────────

    def search_emails(self, query: str, max_results: int = 10) -> list[dict]:
        """Search Gmail. Returns list of {id, threadId, subject, from, to, date, snippet}."""
        result = self.gmail.users().messages().list(
            userId='me', q=query, maxResults=max_results
        ).execute()
        out = []
        for msg in result.get('messages', []):
            detail = self.gmail.users().messages().get(
                userId='me', id=msg['id'], format='metadata',
                metadataHeaders=['Subject', 'From', 'Date', 'To']
            ).execute()
            hdrs = {h['name']: h['value'] for h in detail.get('payload', {}).get('headers', [])}
            out.append({
                'id':       msg['id'],
                'threadId': detail.get('threadId', ''),
                'subject':  hdrs.get('Subject', '(no subject)'),
                'from':     hdrs.get('From', ''),
                'to':       hdrs.get('To', ''),
                'date':     hdrs.get('Date', ''),
                'snippet':  detail.get('snippet', ''),
            })
        return out

    def read_email(self, message_id: str) -> dict:
        """Read full email body. Returns {id, subject, from, to, date, body}."""
        msg  = self.gmail.users().messages().get(userId='me', id=message_id, format='full').execute()
        hdrs = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        return {
            'id':      message_id,
            'subject': hdrs.get('Subject', '(no subject)'),
            'from':    hdrs.get('From', ''),
            'to':      hdrs.get('To', ''),
            'date':    hdrs.get('Date', ''),
            'body':    self._extract_body(msg.get('payload', {})),
        }

    def create_draft(self, to: str, subject: str, body: str, thread_id: str = '') -> str:
        """Create a Gmail draft (does NOT send). Returns confirmation string."""
        import base64
        from email.mime.text import MIMEText
        msg            = MIMEText(body)
        msg['to']      = to
        msg['subject'] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_dict: dict = {'message': {'raw': raw}}
        if thread_id:
            body_dict['message']['threadId'] = thread_id
        result = self.gmail.users().drafts().create(userId='me', body=body_dict).execute()
        return f"Draft created (ID: {result['id']}). Open Gmail to review before sending."

    def _extract_body(self, payload: dict) -> str:
        import base64
        mime = payload.get('mimeType', '')
        if mime == 'text/plain':
            data = payload.get('body', {}).get('data', '')
            return base64.urlsafe_b64decode(data + '==').decode('utf-8', errors='replace')
        if mime.startswith('multipart/'):
            for part in payload.get('parts', []):
                text = self._extract_body(part)
                if text:
                    return text
        return ''

    # ── Calendar helpers ───────────────────────────────────────────────────────

    def list_events(self, days_ahead: int = 7, max_results: int = 20,
                    calendar_id: str = 'primary') -> list[dict]:
        """List upcoming events. Returns list of {id, summary, start, end, location, description}."""
        from datetime import datetime, timedelta, timezone
        now   = datetime.now(timezone.utc)
        until = now + timedelta(days=days_ahead)
        result = self.calendar.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=until.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime',
        ).execute()
        out = []
        for e in result.get('items', []):
            start = e.get('start', {})
            out.append({
                'id':          e.get('id'),
                'summary':     e.get('summary', '(no title)'),
                'start':       start.get('dateTime') or start.get('date'),
                'end':         e.get('end', {}).get('dateTime') or e.get('end', {}).get('date'),
                'location':    e.get('location', ''),
                'description': e.get('description', ''),
            })
        return out

    def get_events_for_day(self, date_obj, calendar_id: str = 'primary') -> list[dict]:
        """Get all events on a specific date. date_obj is a datetime.date."""
        import datetime as dt
        local_tz = dt.datetime.now().astimezone().tzinfo
        start = dt.datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, 0, tzinfo=local_tz)
        end   = start + dt.timedelta(days=1)
        result = self.calendar.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy='startTime',
        ).execute()
        out = []
        for e in result.get('items', []):
            start_e = e.get('start', {})
            out.append({
                'id':          e.get('id'),
                'summary':     e.get('summary', '(no title)'),
                'start':       start_e.get('dateTime') or start_e.get('date'),
                'end':         e.get('end', {}).get('dateTime') or e.get('end', {}).get('date'),
                'location':    e.get('location', ''),
                'description': e.get('description', ''),
                'all_day':     'date' in start_e,
            })
        return out

    def create_event(self, summary: str, start_dt: str, end_dt: str,
                     description: str = '', location: str = '',
                     calendar_id: str = 'primary',
                     timezone: str = 'America/New_York') -> str:
        """Create a calendar event. start_dt/end_dt are RFC3339 strings."""
        body: dict = {
            'summary': summary,
            'start':   {'dateTime': start_dt, 'timeZone': timezone},
            'end':     {'dateTime': end_dt,   'timeZone': timezone},
        }
        if description: body['description'] = description
        if location:    body['location']    = location
        result = self.calendar.events().insert(calendarId=calendar_id, body=body).execute()
        return f"Created: {result.get('summary')} (ID: {result.get('id')})"
