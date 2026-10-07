"""Durable claims and evidence. All operational files stay outside source trees."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time


def redact(value):
    text = json.dumps(value)
    for key, secret in os.environ.items():
        if re.search(r'(TOKEN|PASSWORD|SECRET|API_KEY)$', key) and len(secret) >= 8:
            text = text.replace(secret, '<redacted>')
    text = re.sub(r'(?i)(bearer\s+)[a-z0-9._~+/=-]{12,}', r'\1<redacted>', text)
    text = re.sub(r'(?i)((?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*)[^\s"\\,;]{8,}',
                  r'\1<redacted>', text)
    return json.loads(text)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        f.write(value if isinstance(value, str) else json.dumps(value, indent=2) + '\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class State:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / 'state.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, task TEXT, at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS ports (
            port INTEGER PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, UNIQUE(owner,name));
        ''')
        self.db.commit()
        os.chmod(self.root / 'state.sqlite', 0o600)

    @contextlib.contextmanager
    def lock(self, name='supervisor'):
        with open(self.root / (name + '.lock'), 'a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f'{name} already running') from None
            yield

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def event(self, task, kind, data):
        with self.db:
            self.db.execute('INSERT INTO events(task,at,kind,data) VALUES (?,?,?,?)',
                            (task, time.time(), kind, json.dumps(data)))

    def claim(self, ticket, repo):
        key = ticket['key']
        task = dict(id=key, ticket=ticket, repo=repo, status='active', phase='plan',
                    attempts=0, failures=0, stagnant=0, elapsed=0, cost=0,
                    cost_known=True, criteria=[], decisions=[], assumptions=[], blockers=[],
                    next_action='Read repository instructions and translate the ticket into criteria',
                    evidence=[], artifacts=[], delivery={}, created=time.time())
        with self.db:
            changed = self.db.execute('INSERT OR IGNORE INTO tasks VALUES (?,?,?,?)',
                                     (key, 'active', time.time(), json.dumps(task))).rowcount
        return task if changed else None

    def task(self, key):
        row = self.db.execute('SELECT data FROM tasks WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def tasks(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM tasks ORDER BY updated')]

    def save(self, task, allow_reactivate=False):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.db.execute('SELECT status FROM tasks WHERE id=?', (task['id'],)).fetchone()
            if current and current[0] == 'cancelled' and not allow_reactivate:
                task['status'] = 'cancelled'
            self.db.execute('UPDATE tasks SET status=?, updated=?, data=? WHERE id=?',
                            (task['status'], time.time(), json.dumps(task), task['id']))

    def directory(self, key):
        # Ticket key is validated by intake; hash additionally prevents path traversal.
        suffix = hashlib.sha256(key.encode()).hexdigest()[:12]
        path = self.root / 'tasks' / suffix
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def handover(self, task):
        task = redact(task)
        directory = self.directory(task['id'])
        atomic(directory / 'handover.json', task)
        atomic(directory / 'handover.md', '\n'.join([
            f"# {task['id']}: {task['ticket']['summary']}",
            f"Phase: {task['phase']}; status: {task['status']}",
            f"Worktree: {task.get('worktree', 'not provisioned')}",
            f"Branch: {task.get('branch', 'not provisioned')}",
            f"Next action: {task['next_action']}",
            'Load handover.json for criteria, decisions, assumptions, blockers, evidence and ownership.',
            'Reconcile Git, services, PR and Jira before trusting checkpoint claims.',
            'Suggested skills: repository-specific skills; diagnosing-bugs for unexplained failures;',
            'tdd for behavior changes; code-review for requirements review.',
        ]) + '\n')
        return directory
