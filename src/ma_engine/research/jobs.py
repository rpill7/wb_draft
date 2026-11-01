"""Browser-independent execution in this application process.

Refresh only observes jobs. A process restart marks outstanding jobs interrupted;
there is deliberately no automatic replay of potentially billable model calls.
"""
import asyncio
from copy import deepcopy
from threading import Lock, Thread

from ma_engine.persistence import submissions
from ma_engine.clients.llm_client import _public_error
from fastmcp.exceptions import MCPError
from ma_engine.research import engine

_lock = Lock()
_jobs = {}


class BusyRunError(ValueError):
    pass


def submit(question, run=None, *, target=None, excluded=()):
    """Coalesce active identical research and serialize follow-ups for each run."""
    from ma_engine import config
    scope = (str(config.RUNS_DIR), run['run_id'] if run else None)
    identity = (scope, question, repr(target), tuple(excluded))
    with _lock:
        for token, job in _jobs.items():
            if job['status'] == 'running':
                if job['identity'] == identity:
                    return token
                if run and job['scope'] == scope:
                    raise BusyRunError('A follow-up is already running for this research. Wait for its answer before asking another question.')
        token = submissions.begin(question, run['run_id'] if run else None,
                                  target=target.model_dump() if target else None, excluded=excluded)
        job = dict(identity=identity, scope=scope, status='running', events=[])
        _jobs[token] = job
        thread = Thread(target=_execute, args=(token, question, deepcopy(run), target,
                                               tuple(excluded)), daemon=True)
        job['thread'] = thread
        thread.start()
        return token


def _execute(token, question, run, target, excluded):
    def progress(event):
        with _lock:
            _jobs[token]['events'].append(deepcopy(event))

    async def work():
        if run:
            await engine.run_followup(question, run, progress=progress, submission_id=token)
            return run
        values = target
        if values is None:
            values, defaulted, title = await engine.parse_target(question, progress=progress)
            with _lock:
                _jobs[token].update(target=values.model_dump(), defaulted=defaulted)
        else:
            title = None
        return await engine.run_engine(values, use_cache=True, excluded_keys=excluded,
                                       progress=progress, request=question, research_title=title)
    try:
        result = asyncio.run(work())
        submissions.finish(token, 'completed', result['run_id'])
        with _lock:
            _jobs[token].update(status='completed', run_id=result['run_id'])
    except BaseException as exc:
        with _lock:
            events = deepcopy(_jobs[token]['events'])
        message = _public_error(exc)
        if isinstance(exc, MCPError):
            message = 'The MCP server failed to start. Run `make mcp` to check its setup. ' + message
        submissions.finish(token, 'interrupted', error=message, events=events)
        with _lock:
            _jobs[token].update(status='interrupted', error=message)


def snapshot(token):
    record = submissions.get(token)
    if record is None:
        return None
    with _lock:
        job = _jobs.get(token)
        if job:
            return {**record, **deepcopy({k: v for k, v in job.items()
                                         if k not in ('thread', 'identity', 'scope')})}
    if record['status'] == 'pending':
        submissions.finish(token, 'interrupted')
        record['status'] = 'interrupted'
    return record


def active_for(run_id):
    from ma_engine import config
    with _lock:
        return next((token for token, job in _jobs.items()
                     if job['scope'] == (str(config.RUNS_DIR), run_id)
                     and job['status'] == 'running'), None)
