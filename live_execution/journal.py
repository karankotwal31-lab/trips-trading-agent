"""Durable reservations: an uncertain submission cannot be retried automatically."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class JournalError(RuntimeError):
    pass


class ExecutionJournal:
    def __init__(self, path):
        if str(path) == ':memory:':
            raise JournalError('a durable journal path is required')
        self.path = Path(path)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions (intent_key TEXT PRIMARY KEY, intent TEXT NOT NULL, status TEXT NOT NULL, result TEXT)')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, intent):
        payload = json.dumps(intent, sort_keys=True, allow_nan=False)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM submissions WHERE status IN ('submitting', 'unknown') LIMIT 1").fetchone():
                raise JournalError('unresolved submission: reconcile before further orders')
            try:
                db.execute('INSERT INTO submissions VALUES (?, ?, ?, NULL)', (intent['idempotency_key'], payload, 'submitting'))
            except sqlite3.IntegrityError:
                raise JournalError('duplicate intent: automatic replay prohibited') from None

    def finish(self, key, result):
        payload = json.dumps(result, sort_keys=True, allow_nan=False)
        with self._connect() as db:
            cursor = db.execute("UPDATE submissions SET status='submitted', result=? WHERE intent_key=? AND status='submitting'", (payload, key))
            if cursor.rowcount != 1:
                raise JournalError('submission reservation missing')

    def mark_unknown(self, key):
        with self._connect() as db:
            db.execute("UPDATE submissions SET status='unknown' WHERE intent_key=? AND status='submitting'", (key,))
