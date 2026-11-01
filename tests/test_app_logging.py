import json
from types import SimpleNamespace
from ma_engine import config
from ma_engine.app_logging import logger, log_event
from ma_engine.clients.llm_client import _public_error, scrub_secret


def test_terminal_logs_metadata_and_scrubs_secrets(caplog, monkeypatch):
    monkeypatch.setattr(config, 'OPENROUTER_API_KEY', 'test-secret')
    logger.addHandler(caplog.handler)
    try:
        log_event({'step': 1, 'category': 'llm', 'name': 'write', 'status': 'failed',
                   'input': {'model': 'test/model', 'messages': 'PRIVATE PROMPT'},
                   'output': 'PRIVATE RESPONSE', 'error': 'bad test-secret\nprovider detail'}, scrub_secret)
    finally:
        logger.removeHandler(caplog.handler)
    text = caplog.records[-1].getMessage()
    assert 'model=test/model' in text and 'provider detail' in text
    assert 'test-secret' not in text and 'PRIVATE' not in text and '\n' not in text


def test_provider_schema_detail_is_retained_without_headers(monkeypatch):
    monkeypatch.setattr(config, 'OPENROUTER_API_KEY', 'test-secret')
    error = RuntimeError('Provider returned error')
    error.body = {'metadata': {'raw': json.dumps({'error': {'message': 'Invalid schema test-secret'}})},
                  'headers': {'Authorization': 'PRIVATE HEADER'}}
    message = _public_error(error)
    assert 'Invalid schema ***' in message
    assert 'PRIVATE HEADER' not in message and 'test-secret' not in message
