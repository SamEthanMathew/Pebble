"""GitHub module — issues, PRs, notifications, and repo info from Pebble."""

from __future__ import annotations

import requests

from .base import ActionTier, PebbleModule

_API_BASE = 'https://api.github.com'


class GitHubModule(PebbleModule):
    name         = 'github'
    display_name = 'GitHub'
    description  = 'Check issues, PRs, notifications, and repos on GitHub'
    icon         = '🐙'

    _default_tiers = {
        'notifications': ActionTier.AUTO,
        'issues':        ActionTier.AUTO,
        'prs':           ActionTier.AUTO,
        'search_repos':  ActionTier.AUTO,
        'repo_info':     ActionTier.AUTO,
        'create_issue':  ActionTier.ASK,
    }

    config_fields = [
        {'key': 'personal_access_token', 'label': 'Personal Access Token (PAT)', 'type': 'password'},
        {'key': 'default_repo',          'label': 'Default repo (owner/repo)',    'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return bool(self.cfg.get('personal_access_token', '').strip())

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'github'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['notifications', 'issues', 'prs', 'search_repos', 'repo_info', 'create_issue'],
                    'description': (
                        'notifications: unread GitHub notifications, '
                        'issues: open issues for a repo, '
                        'prs: open pull requests for a repo, '
                        'search_repos: search GitHub repositories, '
                        'repo_info: details about a repo, '
                        'create_issue: open a new issue'
                    ),
                },
                'query': {
                    'type': 'string',
                    'description': 'Search query (used by search_repos)',
                },
                'repo': {
                    'type': 'string',
                    'description': 'Repository in owner/repo format (overrides default_repo)',
                },
                'title': {
                    'type': 'string',
                    'description': 'Issue title (used by create_issue)',
                },
                'body': {
                    'type': 'string',
                    'description': 'Issue body text (used by create_issue)',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        action: str = '',
        query:  str = '',
        repo:   str = '',
        title:  str = '',
        body:   str = '',
        **_,
    ) -> str:
        pat = self.cfg.get('personal_access_token', '').strip()
        if not pat:
            return 'GitHub not configured — add your Personal Access Token in Settings.'

        headers = {
            'Authorization':        f'token {pat}',
            'Accept':               'application/vnd.github.v3+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }
        _repo = repo.strip() or self.cfg.get('default_repo', '').strip()

        try:
            if action == 'notifications':
                return self._notifications(headers)
            elif action == 'issues':
                return self._issues(headers, _repo)
            elif action == 'prs':
                return self._prs(headers, _repo)
            elif action == 'search_repos':
                return self._search_repos(headers, query)
            elif action == 'repo_info':
                return self._repo_info(headers, _repo)
            elif action == 'create_issue':
                return self._create_issue(headers, _repo, title, body)
            else:
                return (
                    f'Unknown action "{action}". '
                    'Valid: notifications, issues, prs, search_repos, repo_info, create_issue.'
                )
        except Exception as e:
            return f'GitHub error: {e}'

    # ── private helpers ───────────────────────────────────────────────────────

    def _get(self, headers: dict, path: str, params: dict | None = None) -> requests.Response:
        url = path if path.startswith('http') else f'{_API_BASE}{path}'
        return requests.get(url, headers=headers, params=params, timeout=10)

    def _post(self, headers: dict, path: str, json: dict) -> requests.Response:
        url = path if path.startswith('http') else f'{_API_BASE}{path}'
        return requests.post(url, headers=headers, json=json, timeout=10)

    def _fmt_date(self, iso: str) -> str:
        """Trim ISO timestamp to YYYY-MM-DD."""
        return iso[:10] if iso else ''

    # ── actions ───────────────────────────────────────────────────────────────

    def _notifications(self, headers: dict) -> str:
        resp = self._get(headers, '/notifications', params={'all': 'false', 'participating': 'false'})
        if resp.status_code == 401:
            return 'GitHub auth failed — check your PAT in Settings.'
        if resp.status_code != 200:
            return f'GitHub error: {resp.status_code}'

        notifs = resp.json()
        if not notifs:
            return 'No unread notifications.'

        lines: list[str] = []
        for n in notifs[:10]:
            ntype  = n.get('subject', {}).get('type', 'Notification')
            title  = n.get('subject', {}).get('title', '(no title)')
            rname  = n.get('repository', {}).get('full_name', '')
            updated = self._fmt_date(n.get('updated_at', ''))
            lines.append(f'{ntype}: {title} [{rname}] — {updated}')

        total = len(notifs)
        suffix = f'\n(showing 10 of {total})' if total > 10 else ''
        return '\n'.join(lines) + suffix

    def _issues(self, headers: dict, repo: str) -> str:
        if not repo:
            return 'Provide a repo (owner/repo) or set a default repo in Settings.'

        resp = self._get(
            headers,
            f'/repos/{repo}/issues',
            params={'state': 'open', 'sort': 'updated', 'per_page': 10},
        )
        if resp.status_code == 404:
            return f'Repo not found: {repo}'
        if resp.status_code != 200:
            return f'GitHub error: {resp.status_code}'

        issues = [i for i in resp.json() if 'pull_request' not in i]  # exclude PRs
        if not issues:
            return f'No open issues in {repo}.'

        lines: list[str] = []
        for i, issue in enumerate(issues, 1):
            num    = issue.get('number', '?')
            title  = issue.get('title', '(no title)')
            labels = ', '.join(lb['name'] for lb in issue.get('labels', []))
            label_str = f' [{labels}]' if labels else ''
            user   = issue.get('user', {}).get('login', '?')
            updated = self._fmt_date(issue.get('updated_at', ''))
            lines.append(f'#{num}: {title}{label_str} by {user} — {updated}')

        return '\n'.join(lines)

    def _prs(self, headers: dict, repo: str) -> str:
        if not repo:
            return 'Provide a repo (owner/repo) or set a default repo in Settings.'

        resp = self._get(
            headers,
            f'/repos/{repo}/pulls',
            params={'state': 'open', 'sort': 'updated', 'per_page': 10},
        )
        if resp.status_code == 404:
            return f'Repo not found: {repo}'
        if resp.status_code != 200:
            return f'GitHub error: {resp.status_code}'

        prs = resp.json()
        if not prs:
            return f'No open pull requests in {repo}.'

        lines: list[str] = []
        for pr in prs:
            num    = pr.get('number', '?')
            title  = pr.get('title', '(no title)')
            user   = pr.get('user', {}).get('login', '?')
            updated = self._fmt_date(pr.get('updated_at', ''))
            draft  = ' [DRAFT]' if pr.get('draft') else ''
            lines.append(f'#{num}: {title}{draft} by {user} — {updated}')

        return '\n'.join(lines)

    def _search_repos(self, headers: dict, query: str) -> str:
        if not query.strip():
            return 'Provide a search query (e.g. "machine learning python")'

        resp = self._get(
            headers,
            '/search/repositories',
            params={'q': query, 'sort': 'stars', 'per_page': 5},
        )
        if resp.status_code != 200:
            return f'GitHub search error: {resp.status_code}'

        items = resp.json().get('items', [])
        if not items:
            return f'No repositories found for "{query}"'

        lines: list[str] = []
        for repo in items:
            full_name = repo.get('full_name', '?')
            stars     = repo.get('stargazers_count', 0)
            desc      = repo.get('description') or ''
            if len(desc) > 100:
                desc = desc[:97] + '...'
            lines.append(f'{full_name} ⭐{stars:,} — {desc}')

        return '\n'.join(lines)

    def _repo_info(self, headers: dict, repo: str) -> str:
        if not repo:
            return 'Provide a repo (owner/repo) or set a default repo in Settings.'

        resp = self._get(headers, f'/repos/{repo}')
        if resp.status_code == 404:
            return f'Repo not found: {repo}'
        if resp.status_code != 200:
            return f'GitHub error: {resp.status_code}'

        r       = resp.json()
        name    = r.get('full_name', repo)
        desc    = r.get('description') or '(no description)'
        stars   = r.get('stargazers_count', 0)
        forks   = r.get('forks_count', 0)
        lang    = r.get('language') or 'N/A'
        issues  = r.get('open_issues_count', 0)
        pushed  = self._fmt_date(r.get('pushed_at', ''))
        return (
            f'{name}\n'
            f'Description: {desc}\n'
            f'Stars: {stars:,}  |  Forks: {forks:,}  |  Language: {lang}\n'
            f'Open issues: {issues}  |  Last push: {pushed}'
        )

    def _create_issue(self, headers: dict, repo: str, title: str, body: str) -> str:
        if not title.strip():
            return 'Provide a title for the issue.'
        if not repo:
            return 'Provide a repo (owner/repo) or set a default repo in Settings.'

        resp = self._post(
            headers,
            f'/repos/{repo}/issues',
            json={'title': title.strip(), 'body': body.strip()},
        )
        if resp.status_code == 404:
            return f'Repo not found: {repo}'
        if resp.status_code == 401:
            return 'GitHub auth failed — check your PAT in Settings.'
        if resp.status_code not in (200, 201):
            return f'GitHub error: {resp.status_code}'

        issue = resp.json()
        number  = issue.get('number', '?')
        url     = issue.get('html_url', '')
        created_title = issue.get('title', title)
        return f'Issue #{number} created: {created_title} at {url}'
