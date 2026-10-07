"""Explicit project knowledge with provenance; personal standards stay in the playbook."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .projects import repository_identity
from .state import redact


class ProjectMemory:
    def __init__(self, config):
        _, identity = repository_identity(config['repository']['path'])
        if config.get('_project_id', identity) != identity:
            raise ValueError('memory configuration belongs to another project')
        root = Path(config['state_dir']).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(root / 'brain.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS owner (singleton INTEGER PRIMARY KEY CHECK(singleton=1), project TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT NOT NULL,
                    kind TEXT NOT NULL, revision TEXT, created REAL NOT NULL);
            ''')
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('INSERT OR IGNORE INTO owner VALUES (1,?)', (identity,))
                if self.db.execute('SELECT project FROM owner WHERE singleton=1').fetchone()[0] != identity:
                    raise ValueError('memory database belongs to another project')
                columns = {row['name'] for row in self.db.execute('PRAGMA table_info(memories)')}
                for name, kind in [('retired', 'REAL'), ('retirement_reason', 'TEXT'), ('superseded_by', 'TEXT')]:
                    if name not in columns:
                        self.db.execute(f'ALTER TABLE memories ADD COLUMN {name} {kind}')
            os.chmod(root / 'brain.sqlite', 0o600)
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def remember(self, text, *, source, kind='fact', revision=None):
        with self.db:
            return self._remember(text, source=source, kind=kind, revision=revision)

    def _remember(self, text, *, source, kind, revision):
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError('memory text must contain 1–20000 characters')
        if not isinstance(source, str) or not source.strip() or len(source) > 2000:
            raise ValueError('memory source is required (file, URL, ticket, or explicit user correction)')
        if kind not in ('fact', 'decision', 'lesson', 'correction', 'assumption', 'guidance'):
            raise ValueError('memory kind must be fact, decision, lesson, correction, assumption, or guidance')
        if revision is not None and (not isinstance(revision, str) or len(revision) > 200):
            raise ValueError('memory revision must be a string of at most 200 characters')
        content = redact(dict(text=text.strip(), source=source.strip(), kind=kind, revision=revision))
        identity = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:24]
        self.db.execute('INSERT OR IGNORE INTO memories (id,text,source,kind,revision,created) VALUES (?,?,?,?,?,?)',
                        (identity, content['text'], content['source'], kind, content['revision'], time.time()))
        return dict(self.db.execute('SELECT * FROM memories WHERE id=?', (identity,)).fetchone())

    def _retire(self, identity, reason, superseded_by=None):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('retirement reason must contain 1–2000 characters')
        row = self.db.execute('SELECT * FROM memories WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('unknown project memory')
        if row['retired'] is not None:
            if row['superseded_by'] != superseded_by:
                raise ValueError('memory is already retired with another replacement')
            return dict(row)
        self.db.execute('UPDATE memories SET retired=?, retirement_reason=?, superseded_by=? WHERE id=?',
                        (time.time(), redact(reason.strip()), superseded_by, identity))
        return dict(self.db.execute('SELECT * FROM memories WHERE id=?', (identity,)).fetchone())

    def retire(self, identity, *, reason):
        """Retain an entry as history and remove it from ordinary retrieval."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._retire(identity, reason)

    def supersede(self, identity, text, *, reason, source, kind='correction', revision=None):
        """Atomically record corrected knowledge and retire the stale entry."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            replacement = self._remember(text, source=source, kind=kind, revision=revision)
            if replacement['id'] == identity or replacement['retired'] is not None:
                raise ValueError('replacement must be distinct and active')
            self._retire(identity, reason, replacement['id'])
            return replacement

    @staticmethod
    def _limit(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('memory limit must be an integer between 1 and 100')
        return limit

    def list(self, limit=50, *, include_retired=False):
        condition = '' if include_retired else 'WHERE retired IS NULL'
        return [dict(row) for row in self.db.execute(f'SELECT * FROM memories {condition} ORDER BY created DESC, id LIMIT ?', (self._limit(limit),))]

    def guidance(self):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM memories WHERE retired IS NULL AND kind='guidance' ORDER BY created DESC LIMIT 20")]

    def search(self, query, limit=10):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError('memory query must contain 1–2000 characters')
        terms = query.split()
        # ponytail: a small local brain uses literal substring search; use FTS5 when measured retrieval grows slow.
        clauses = ' AND '.join("(text || ' ' || source) LIKE ? ESCAPE '\\'" for _ in terms)
        values = ['%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' for term in terms]
        return [dict(row) for row in self.db.execute(
            f'SELECT * FROM memories WHERE retired IS NULL AND {clauses} ORDER BY created DESC, id LIMIT ?',
            (*values, self._limit(limit)))]
