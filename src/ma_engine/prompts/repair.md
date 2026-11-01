Your report did not pass the automated checks. Return the complete report again in the same JSON format, fixing exactly the problems below and keeping every other statement word for word.

Problems (each starts with where it is):
{issues}

How to fix:
- If the message says your output was cut off: rewrite the whole report with shorter statements, at most 40 words each.
- Number not in the tool results: copy the display string exactly from a tool result above (the message may suggest one), or remove the number.
- A statistic figure: cite the `ST-` id the message names (`cite ST-… for 12.1x`) and copy that statistic's display and `n`. Use an `MA-` id only for a figure on that deal's own row.
- A figure, name, year, outcome or deal type that belongs to a different deal: either cite the deal the message names, or replace it with the cited deal's own figures (the message lists them).
- Comps or other buyers' deals in acquirer_overview, strategic_fit or precedent_activity: cite this buyer's own deals, or move the statement to valuation_context.
- n= that does not match: copy the n shown beside the multiple you quote.
- ID not in the tool results or not in the dataset: remove it, or replace it with an ID a tool returned that supports the statement.
- Completion word on a deal that did not close: give that deal its own statement and describe it only by its outcome (was Withdrawn, is Pending, was Terminated, is Rumored), with no completion word at all.
- Valuation statement without a sample size: add n=<count> copied from the tool result.
- Interpretation without an ID: cite the IDs of the facts it rests on.
- Company not in the tool results: remove the name.
- Conviction too far from the starting level: choose a level the message allows.
- A fact you cannot support: relabel it as interpretation (citing the facts it rests on), as unknown (no numbers, no IDs), or delete it.

Do not call tools. Use only the evidence already in this conversation.

Repair wording precisely:
- Do not repeat a disallowed phrase to negate or contrast with it. If direct-sector deals exist, omit the phrase "adjacent-only" entirely, including "not adjacent-only" or "rather than adjacent-only". State "limited direct-sector evidence" positively.
- For deal types, describe the cited deal's actual type only. Do not mention other exact deal-type names to deny them. Put aggregate deal-type counts in a separate sentence citing their statistic IDs, without unrelated individual-deal IDs.
- Never derive an adjacent-deal count by subtracting direct deals from relevant deals. Copy the displayed direct count and relevant count side by side; omit an unstored remainder.
- A numerical hint naming an unrelated transaction does not make that transaction evidence for a buyer-wide count. If the intended count is not explicitly supplied as a statistic, remove it. Do not change the subject or fabricate a supporting source.
