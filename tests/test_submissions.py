import asyncio
import pytest
from ma_engine import config, engine, submissions


def test_requests_survive_new_reads_and_completion_hides_only_finished(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    first = submissions.begin('Compare buyers')
    second = submissions.begin('Show transactions')
    assert {r['question'] for r in submissions.unfinished()} == {'Compare buyers', 'Show transactions'}
    submissions.finish(first, 'completed')
    assert [r['id'] for r in submissions.unfinished()] == [second]


def test_followup_saved_before_client_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    folder = tmp_path / 'run'
    folder.mkdir()
    def unavailable():
        assert submissions.unfinished()[0]['question'] == 'Compare buyers'
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(engine, 'LLMClient', unavailable)
    with pytest.raises(RuntimeError, match='provider unavailable'):
        asyncio.run(engine.run_followup('Compare buyers', {'run_id': 'run', 'run_dir': str(folder)}))
    assert submissions.unfinished()[0]['status'] == 'interrupted'
    folder.rmdir()
    assert not submissions.unfinished()


def test_deleted_submission_is_not_restored_by_late_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'RUNS_DIR', tmp_path)
    token = submissions.begin('Delete this unfinished question')
    submissions.delete(token)
    submissions.finish(token, 'interrupted')
    assert not submissions.unfinished()
    assert not (tmp_path / '_submissions' / f'{token}.json').exists()
    with pytest.raises(ValueError):
        submissions.delete('../outside')
