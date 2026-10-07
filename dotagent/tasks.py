"""Task sources share the existing claim, verification and delivery boundaries."""
import hashlib
import json
import time
from pathlib import Path

from .integrations import Jira, select_ticket
from .state import redact


def source_config(config):
    source = dict(config.get('tasks', {}))
    source.setdefault('provider', 'jira' if config.get('jira') else 'local')
    if source['provider'] not in ('local', 'jira'):
        raise ValueError('tasks.provider must be local or jira; import other tracker tasks through the host skill')
    source.setdefault('poll_seconds', config.get('jira', {}).get('poll_seconds', 300))
    if type(source['poll_seconds']) is not int or source['poll_seconds'] < 1:
        raise ValueError('tasks.poll_seconds must be a positive integer')
    return source


def add_task(state, config, request, key=None, url=None, origin='user'):
    if not isinstance(request, str) or not request.strip() or len(request) > 50000:
        raise ValueError('request must contain 1–50000 characters')
    if url:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('source URL must be an HTTP(S) URL without credentials')
    request = redact(request.strip())
    key = key or 'task-' + hashlib.sha256((url or request).encode()).hexdigest()[:12]
    ticket = dict(key=key, summary=request.splitlines()[0][:180], description=request, url=url or '',
                  source={'kind':'local', 'origin':origin, 'url':url}, status='Ready', priority='',
                  updated=str(time.time()), comments=[], attachments=[], linked_issues=[],
                  acceptance_criteria=[], blockers=[])
    task = state.claim(ticket, config['repository']['path'])
    if task is None:
        task = state.task(key)
        if task['ticket'].get('description') != request or task['ticket'].get('url') != (url or ''):
            raise ValueError('task ID already belongs to a different request; use instruct or choose another ID')
    state.set('control_revision', time.time())
    return task


def intake(state, config):
    if source_config(config)['provider'] == 'local':
        return None
    tickets = Jira(state, config['jira'], config['host']).intake()
    ticket = select_ticket(tickets, config['jira'], {t['id'] for t in state.tasks()})
    if ticket:
        ticket['source'] = {'kind': 'jira', 'url': ticket['url']}
    return ticket


def reconcile_source(state, task, config):
    if task['ticket'].get('source', {}).get('kind') == 'local':
        return  # Source is the durable user request; Git/resources still reconcile.
    if source_config(config)['provider'] != 'jira':
        raise RuntimeError('this saved task requires its original Jira configuration')
    cache = state.root / 'jira/backlog.json'
    backlog = json.loads(cache.read_text()) if cache.exists() else {}
    tickets = (Jira(state, config['jira'], config['host']).intake()
               if time.time() - backlog.get('fetched_at', 0) > source_config(config)['poll_seconds']
               else backlog.get('tickets', []))
    source = next((t for t in tickets if t['key'] == task['id']), None)
    if not source or source['status'] not in config['jira']['eligible_statuses']:
        raise RuntimeError('task is no longer actionable in configured backlog')
    if source['blockers']:
        raise RuntimeError('Source blockers: ' + '; '.join(source['blockers']))
    if task['criteria'] and any(source.get(k) != task['ticket'].get(k) for k in ('description', 'acceptance_criteria')):
        raise RuntimeError('task requirements changed; reconcile frozen criteria before resuming')
    source['source'] = {'kind':'jira', 'url':source.get('url')}
    task['ticket'] = source


def sync_source(state, task, config):
    if task.get('github_reply'):
        from .github_mentions import reply
        reply(state, task, config)
    if task['ticket'].get('source', {}).get('origin') == 'github-mention':
        from .github_mentions import reply
        return reply(state, task, config)
    if task['ticket'].get('source', {}).get('kind') == 'local':
        task['delivery']['source_sync'] = 'local request; no external tracker update configured'
        return True
    Jira(state, config['jira'], config['host']).progress(task,
        f"Draft PR ready for review: {task['delivery']['pr']}\nVerified commit: {task['commit']}. Local checks passed.\n"
        + '\n'.join(task['limitations']))
    return bool(task['delivery'].get('jira_comment'))
