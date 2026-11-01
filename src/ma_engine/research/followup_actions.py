"""Read-only UI requests resolved from saved state, never invented by the writer."""
import re
from ma_engine.models import FollowupAnswer, Statement


def saved_action(question, context, entries):
    q = question.lower()
    sid = context['run_record']['stat_id']
    def fact(text, ids=None):
        return Statement(text=text, kind='fact', source_ids=ids or [sid])
    if re.search(r'\b(?:list|show|give)\b.*\ball (?:the )?sources\b', q):
        return FollowupAnswer(statements=[fact('The source list below contains the citations used by the saved buyer reports.')],
                              include_source_catalog=True)
    if re.match(r'^(?:please )?download\b', q) and re.search(r'\breport|research\b', q):
        return FollowupAnswer(statements=[fact('Open full report, then choose Download report in the report panel header. This chat does not trigger the download.')])
    cited = context.get('requested_citations') or []
    if len(cited) == 1 and re.search(r'\b(?:citation|source)\s*\[\d+\]', q):
        source = cited[0]
        tid = source['id']
        if source.get('kind') == 'deal':
            row = next((r for r in context['saved_evidence'] if r and r.get('transaction_id') == tid), None)
            if row is None:
                return None
            text = (f"{row['target_company']} — {row['acquirer']}; {row['sector']}; "
                    f"{row['deal_type']}; {row['deal_year']}; outcome {row['outcome']}.")
            # Put amounts in a separate statement, copied from the original displays.
            statements = [fact(text, [tid])]
            amounts = [f"Deal size {row['deal_size_mm']['display']}" if row['deal_size_mm'].get('display') else '',
                       f"EV/EBITDA {row['ev_ebitda_multiple']['display']}" if row['ev_ebitda_multiple'].get('display') else '']
            if any(amounts):
                statements.append(fact('; '.join(v for v in amounts if v) + '.', [tid]))
        else:
            record = source['record']
            text = f"{record.get('calculation', 'Recorded statistic')}: {record['display']}"
            if record.get('n') is not None:
                text += f" (n={record['n']})"
            statements = [fact(text + '.', [tid])]
        membership = context['run_record']['details']['report_source_membership']
        for name, ids in membership.items():
            if name.lower() in q:
                verb = 'is' if tid in ids else 'is not'
                statements.append(fact(f'This source {verb} cited by the saved {name} report.'))
        return FollowupAnswer(statements=statements)
    if re.match(r'^(?:please )?(?:remove|exclude|omit)\b', q):
        named = [e for e in entries if e['display_name'].lower() in q]
        if len(named) == 1:
            statements = [fact('Buyer exclusions are unsupported in New Research and Edit target. The saved shortlist has not been changed.')]
            pos = entries.index(named[0])
            if 'next' in q and pos + 1 < len(entries):
                following = entries[pos + 1]
                statements.append(fact(f"The next saved candidate is {following['display_name']}, with score {following['score']['display']}.",
                                       [following['score']['stat_id']]))
            return FollowupAnswer(statements=statements)
    return None
