"""Durable submitted questions, saved before model work begins.

One atomic file per submission avoids concurrent questions overwriting each other.
Pending means no final result was recorded, not that a worker is known to be alive.
"""
import json
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
from ma_engine import config


def begin(question: str, run_id: str | None = None) -> str:
    token = uuid4().hex
    _save(token, {'id': token, 'question': question, 'run_id': run_id,
                  'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'pending'})
    return token


def _path(token):
    if len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid submission ID')
    return config.RUNS_DIR / '_submissions' / f'{token}.json'


def _save(token, record):
    path = _path(token)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def _edit_lock():
    folder = config.RUNS_DIR / '_submissions'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def finish(token: str, status: str, run_id: str | None = None):
    with _edit_lock():
        path = _path(token)
        if not path.exists():
            return  # A deleted question must not be recreated by a late completion.
        record = json.loads(path.read_text(encoding='utf-8'))
        record['status'] = status
        if run_id is not None:
            record['run_id'] = run_id
        _save(token, record)


def delete(token: str):
    with _edit_lock():
        _path(token).unlink(missing_ok=True)


def unfinished():
    records = []
    for path in (config.RUNS_DIR / '_submissions').glob('*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue
        if record['status'] != 'completed':
            # Deleting a research run should not bring its questions back into History.
            if record.get('run_id') and not (config.RUNS_DIR / record['run_id']).is_dir():
                continue
            records.append(record)
    return sorted(records, key=lambda r: r['created_at'], reverse=True)
