"""Execution survives fresh UI sessions; no network/model calls."""
from threading import Event

from ma_engine import config
from ma_engine.models import Target
from ma_engine.persistence import submissions
from ma_engine.research import engine, jobs


def test_refresh_and_duplicate_submit_share_one_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    entered, release = Event(), Event()
    calls = []

    async def parse(question, progress=None):
        calls.append(question)
        entered.set()
        assert release.wait(5)
        return Target.model_validate(config.DEFAULT_TARGET), [], 'Title'

    async def run(target, **kwargs):
        return {'run_id': 'saved'}

    monkeypatch.setattr(engine, 'parse_target', parse)
    monkeypatch.setattr(engine, 'run_engine', run)
    token = jobs.submit('Find buyers')
    try:
        assert entered.wait(5)
        assert jobs.submit('Find buyers') == token
        # Fresh readers only observe the same durable identifier, never execute work.
        for _ in range(4):
            assert jobs.snapshot(token)['status'] == 'running'
        assert submissions.get(token)['question'] == 'Find buyers'
    finally:
        release.set()
        jobs._jobs[token]['thread'].join(5)
    assert jobs.snapshot(token)['status'] == 'completed'
    assert submissions.get(token)['run_id'] == 'saved'
    assert calls == ['Find buyers']


def test_followup_refresh_serializes_run_and_saves_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    entered, release = Event(), Event()
    calls = []
    run = {'run_id': 'saved', 'run_dir': str(tmp_path / 'saved')}
    (tmp_path / 'saved').mkdir()

    async def answer(question, run, use_cache, progress):
        calls.append(question)
        entered.set()
        assert release.wait(5)
        (tmp_path / 'saved' / 'answer.txt').write_text('Saved answer')

    monkeypatch.setattr(engine, '_run_followup_saved', answer)
    token = jobs.submit('Compare buyers', run)
    try:
        assert entered.wait(5)
        assert jobs.submit('Compare buyers', run) == token
        assert jobs.active_for('saved') == token
        assert jobs.snapshot(token)['status'] == 'running'
        assert len(submissions.unfinished()) == 1
    finally:
        release.set()
        jobs._jobs[token]['thread'].join(5)
    assert jobs.snapshot(token)['status'] == 'completed'
    assert (tmp_path / 'saved' / 'answer.txt').read_text() == 'Saved answer'
    assert calls == ['Compare buyers']


def test_restart_marks_interrupted_without_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    token = submissions.begin('Question before server restart')
    assert jobs.snapshot(token)['status'] == 'interrupted'
    assert submissions.get(token)['status'] == 'interrupted'
    assert token not in jobs._jobs


def test_failure_is_recorded_and_can_be_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    async def fail(*args, **kwargs):
        raise RuntimeError('Unavailable')
    monkeypatch.setattr(engine, 'parse_target', fail)
    token = jobs.submit('Question')
    jobs._jobs[token]['thread'].join(5)
    assert jobs.snapshot(token)['status'] == 'interrupted'
    retry = jobs.submit('Question')
    jobs._jobs[retry]['thread'].join(5)
    assert retry != token


def test_fresh_browser_reconnects_to_research_and_followup(tmp_path, monkeypatch):
    import asyncio
    from streamlit.testing.v1 import AppTest
    from test_app import APP, fake_engine
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(config, 'SAMPLE_DIR', tmp_path / 'sample')
    fake_engine(monkeypatch)
    saved = asyncio.run(engine.run_engine(config.DEFAULT_TARGET))
    entered, release = Event(), Event()
    calls = []
    async def parse(question, progress=None):
        calls.append(question)
        entered.set()
        assert release.wait(15)
        return Target.model_validate(config.DEFAULT_TARGET), [], 'Title'
    async def research(*args, **kwargs):
        return saved
    monkeypatch.setattr(engine, 'parse_target', parse)
    monkeypatch.setattr(engine, 'run_engine', research)
    first = AppTest.from_file(APP).run()
    first.chat_input(key='research_chat').set_value('Research my company').run()
    token = first.query_params['job']
    if isinstance(token, list):
        token = token[0]
    try:
        assert entered.wait(5)
        fresh = AppTest.from_file(APP)
        fresh.query_params['job'] = token
        fresh.run()
        assert not fresh.exception
        assert any(item.label == 'Agent activity · Running' for item in fresh.expander)
        fresh.run()
        assert calls == ['Research my company']
        fresh.button(key='nav-history').click().run()
        assert any(header.value == 'Research History' for header in fresh.header)
        fresh.button(key=f'watch-{token}').click().run()
        assert any(item.label == 'Agent activity · Running' for item in fresh.expander)
        fresh.button(key=f'menu-submission-{token}').click().run()
        fresh.button(key='delete-confirm').click().run()
        assert any('still running' in error.value for error in fresh.error)
        assert submissions.get(token) is not None
        fresh.button(key='delete-cancel').click().run()

    finally:
        release.set()
        jobs._jobs[token]['thread'].join(15)
    fresh.run()
    assert not fresh.exception
    assert fresh.session_state.current_run['run_id'] == saved['run_id']
    assert 'job' not in fresh.query_params
    assert fresh.chat_input(key='followup_chat')

    entered.clear()
    release.clear()
    original = engine._run_followup_saved
    async def answer(*args, **kwargs):
        calls.append('followup')
        entered.set()
        assert release.wait(15)
        return await original(*args, **kwargs)
    monkeypatch.setattr(engine, '_run_followup_saved', answer)
    fresh.chat_input(key='followup_chat').set_value('What is unknown?').run()
    followup_token = fresh.query_params['job']
    if isinstance(followup_token, list):
        followup_token = followup_token[0]
    try:
        assert entered.wait(5)
        refreshed = AppTest.from_file(APP)
        refreshed.query_params['job'] = followup_token
        refreshed.run()
        assert not refreshed.exception
        assert jobs.submit('What is unknown?', saved) == followup_token
    finally:
        release.set()
        jobs._jobs[followup_token]['thread'].join(30)
    refreshed.run()
    assert not refreshed.exception
    assert len(refreshed.session_state.followups) == 1
    assert calls == ['Research my company', 'followup']
    another = AppTest.from_file(APP)
    another.query_params['run'] = saved['run_id']
    another.run()
    assert len(another.session_state.followups) == 1


def test_distinct_followup_is_not_silently_dropped(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    entered, release = Event(), Event()
    run = {'run_id': 'saved', 'run_dir': str(tmp_path / 'saved')}
    (tmp_path / 'saved').mkdir()
    async def answer(*args, **kwargs):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(engine, '_run_followup_saved', answer)
    token = jobs.submit('First question', run)
    try:
        assert entered.wait(5)
        with pytest.raises(jobs.BusyRunError, match='already running'):
            jobs.submit('Different question', run)
    finally:
        release.set()
        jobs._jobs[token]['thread'].join(5)


def test_target_edit_preserves_inputs_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    target = Target.model_validate({**config.DEFAULT_TARGET, 'ev_mm': 350})
    async def fail(values, **kwargs):
        assert values == target
        assert kwargs['excluded_keys'] == ('blackstone',)
        raise RuntimeError('Temporary failure')
    monkeypatch.setattr(engine, 'run_engine', fail)
    token = jobs.submit('Original question', target=target, excluded=('blackstone',))
    jobs._jobs[token]['thread'].join(5)
    record = submissions.get(token)
    assert record['target']['ev_mm'] == 350
    assert record['excluded'] == ['blackstone']
    assert record['status'] == 'interrupted'
    assert 'Temporary failure' in record['error']
