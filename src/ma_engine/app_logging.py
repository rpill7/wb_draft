"""Concise terminal progress; detailed inputs/outputs remain in the activity trace."""
import logging

logger = logging.getLogger('ma_engine.activity')
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def log_event(event, scrub):
    summary = event.get('output_summary') or {}
    inputs = event.get('input') or {}
    fields = {key: event.get(key) for key in
              ('step', 'category', 'name', 'status', 'acquirer_key', 'duration_ms',
               'tokens_in', 'tokens_out', 'cost_usd') if event.get(key) is not None}
    if event.get('category') == 'llm':
        fields['model'] = inputs.get('model')
        if 'cache_hit' in summary:
            fields['cache_hit'] = summary['cache_hit']
    message = ' '.join(f'{key}={value}' for key, value in fields.items())
    error = event.get('error')
    if error:
        message += f' error={error}'
    level = logging.ERROR if event.get('status') == 'failed' else logging.WARNING if error else logging.INFO
    # Newlines in provider messages must not impersonate additional log entries.
    logger.log(level, '%s', scrub(message).replace('\n', ' ').replace('\r', ' '))
