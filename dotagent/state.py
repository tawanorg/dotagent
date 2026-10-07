"""Durable claims and evidence. All operational files stay outside source trees."""
import contextlib
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


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
    SCOPED_KEYS = {'worker', 'heartbeat', 'executing_phase', 'iteration_worker', 'bridge_worker'}

    def __init__(self, root, scope=None, readonly=False):
        if scope is not None and (not isinstance(scope, str) or not scope or len(scope) > 256):
            raise ValueError('scope must be a nonempty task identifier of at most 256 characters')
        self.scope = scope
        self._scope_hash = hashlib.sha256(scope.encode()).hexdigest() if scope is not None else None
        self.root = Path(root).expanduser().resolve()
        if readonly and (self.root / 'state.sqlite').exists():
            self.db = sqlite3.connect((self.root / 'state.sqlite').as_uri() + '?mode=ro', uri=True, timeout=30)
            self.db.row_factory = sqlite3.Row
            return
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(':memory:' if readonly else self.root / 'state.sqlite', timeout=30)
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
          CREATE TABLE IF NOT EXISTS spend_reservations (
            id TEXT PRIMARY KEY, amount TEXT NOT NULL, settled TEXT);
        ''')
        self.db.commit()
        if not readonly:
            os.chmod(self.root / 'state.sqlite', 0o600)
        if scope is not None and not readonly:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                                (f'scope:{self._scope_hash}:identity', json.dumps(scope)))

    def scopes(self):
        return sorted(json.loads(row[0]) for row in self.db.execute(
            "SELECT value FROM settings WHERE key GLOB 'scope:*:identity'"))

    def _key(self, key):
        return f'scope:{self._scope_hash}:{key}' if self.scope is not None and key in self.SCOPED_KEYS else key

    @contextlib.contextmanager
    def lock(self, name='supervisor'):
        filename = name + ('-' + self._scope_hash if name == 'iteration' and self.scope is not None else '')
        with open(self.root / (filename + '.lock'), 'a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f'{name} already running') from None
            yield f

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (self._key(key),)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (self._key(key), json.dumps(value)))

    @staticmethod
    def _usd(value):
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError('USD amount must be a finite nonnegative number')
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError('USD amount must be a finite nonnegative number')
        return amount

    def reserve_spend(self, cap_usd, requested_usd):
        """Atomically charge a reservation; unknown/crashed sessions retain that charge."""
        cap, requested = self._usd(cap_usd), self._usd(requested_usd)
        if requested <= 0:
            raise ValueError('requested USD reservation must be positive')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            spent = self._usd(self.get('spend_usd', 0))
            amount = min(requested, cap - spent)
            if amount <= 0:
                raise RuntimeError('persistent spending budget exhausted')
            receipt = str(uuid.uuid4())
            self.db.execute('INSERT INTO spend_reservations VALUES (?,?,NULL)', (receipt, str(amount)))
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('spend_usd', str(spent + amount)))
        return receipt, float(amount)

    def settle_spend(self, receipt, actual_usd):
        """Replace a reservation with reported cost exactly once, across all workers."""
        actual = self._usd(actual_usd)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT amount, settled FROM spend_reservations WHERE id=?', (receipt,)).fetchone()
            if row is None:
                raise ValueError('unknown spending reservation')
            spent = self._usd(self.get('spend_usd', 0))
            if row['settled'] is not None:
                if Decimal(row['settled']) != actual:
                    raise ValueError('spending reservation already settled with a different cost')
                return float(spent)
            total = spent - Decimal(row['amount']) + actual
            if total < 0:
                raise ValueError('spending total is inconsistent with its reservation')
            self.db.execute('UPDATE spend_reservations SET settled=? WHERE id=?', (str(actual), receipt))
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('spend_usd', str(total)))
        return float(total)

    def event(self, task, kind, data):
        with self.db:
            self.db.execute('INSERT INTO events(task,at,kind,data) VALUES (?,?,?,?)',
                            (task, time.time(), kind, json.dumps(data)))

    def claim(self, ticket, repo):
        key = ticket['key']
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', key):
            raise ValueError('task ID must contain 1–100 letters, digits, underscores or hyphens')
        task = dict(id=key, ticket=ticket, repo=repo, status='active', phase='plan',
                    attempts=0, failures=0, stagnant=0, elapsed=0, cost=0,
                    cost_known=True, criteria=[], decisions=[], assumptions=[], blockers=[],
                    next_action='Read repository instructions and translate the ticket into criteria',
                    evidence=[], artifacts=[], delivery={}, created=time.time())
        with self.db:
            changed = self.db.execute('INSERT OR IGNORE INTO tasks VALUES (?,?,?,?)',
                                     (key, 'active', time.time(), json.dumps(task))).rowcount
        return task if changed else None

    def instruct(self, key, text, identity=None):
        if not self.task(key):
            raise ValueError('unknown task')
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError('instruction must contain 1–20000 characters')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            marker = 'instruction:' + identity if identity else None
            if marker and self.get(marker):
                return
            self.db.execute('INSERT INTO events(task,at,kind,data) VALUES (?,?,?,?)',
                            (key, time.time(), 'user-instruction', json.dumps({'text':redact(text.strip())})))
            if marker:
                self.db.execute('INSERT INTO settings VALUES (?,?)', (marker, 'true'))
        self.set('control_revision', time.time())

    def instructions(self, key):
        return [dict(seq=r['seq'], at=r['at'], **json.loads(r['data'])) for r in self.db.execute(
            "SELECT seq,at,data FROM events WHERE task=? AND kind='user-instruction' ORDER BY seq", (key,))]

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
            'Reconcile Git, services, PR and configured task source before trusting checkpoint claims.',
            'Suggested skills: repository-specific skills; diagnosing-bugs for unexplained failures;',
            'tdd for behavior changes; code-review for requirements review.',
        ]) + '\n')
        return directory
