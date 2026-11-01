"""Report checks, Markdown rendering and run quality metrics. Python only, no AI.

`check(report, evidence)` returns a list of issue messages; an empty list means the report
passed. Every message starts with its location (for example `precedent_activity[1]`) and says
what to change, because Subplan 5 sends the messages back to the writer for one repair.

Evidence for one buyer is its ranking entry plus the `result` of every tool call its agent
received (`[{"tool", "arguments", "result"}]`; arguments are the agent's own input, not
evidence). Allowed IDs, numbers and company names are read from the evidence text:

- Numbers: one tokenizer runs on evidence and on report text, after removing transaction IDs
  and known company names (some contain digits, such as Dental365). It finds money (`$185.0M`,
  `$1.2B`), multiples (`12.1x`), percentages (`15.5%`), decimals and integers; a range such as
  `$100M–$400M` gives two tokens. Quarter labels (`Q3`) and ordinals (`1st`) are skipped; a
  minus sign is kept (`-12.1x`), except where it is a hyphen. Only commas are normalized (`$14,353.7M` == `$14353.7M`);
  everything else must match exactly, so `$185M` does not match `$185.0M`. A raw `value` that has
  a display string next to it is not evidence: the display string is what must be quoted.
  Only digits are seen. Number words ("four") are not detected, so the writer must use digits.
- Company names: every acquirer and target name in the dataset is known; a known name in the
  report must also appear in the evidence (the buyer's own name is always allowed).

Claims are also bound to the deals they cite (`fact` and `interpretation` statements, both of
which must cite at least one ID). `build_evidence` records a `Source` for every part of the
evidence: a deal row supports only its own transaction_id; a statistic supports its source_ids;
anything else inherits the IDs of the result it sits in. Sources are "buyer" (profile, this
buyer's deal rows, ranking entry), "comp" (comps, sector overview, other buyers' rows),
"target" (the target block, quotable without a citation) or "assumption" (hand-written judgment
in an `assumption` field, which supports no ID). Then, per statement:
- every number is in a source whose IDs overlap the cited IDs; a year also passes when it is the
  deal year of a cited deal. Overview, fit and precedent statements may not use comp sources.
- every n=<count> is the n stored beside a number quoted in the statement (if no quoted number
  has a stored n, any n stored for the cited IDs).
- a company name is the target or acquirer of a cited deal; outcome words (Withdrawn, Pending,
  Terminated, Rumored) and deal types in dataset spelling match a cited deal.
- overview and fit cite at least one of this buyer's deals; precedents cite only its deals.
Small counts ("6") appear in many buyer statistics, so the overlap rule cannot pin them to one
statistic; prices, multiples, margins, names and years are distinctive enough to bind.

`make check` (`python -m ma_engine.guardrails`) reads `reports.json` in the newest run folder
of `config.RUNS_DIR` (folder names are sortable timestamps such as `20261031-173000`):

    {"run_id": "20261031-173000",
     "target": {<Target fields>},
     "reports": [<BuyerReport.model_dump()>, ...]}
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from itertools import combinations
from pathlib import Path
from typing import NamedTuple

from ma_engine import config
from ma_engine.clients.data_client import get_client
from ma_engine.models import BuyerReport, ReportDraft
from ma_engine.prepare import CLOSED, KNOWN_VALUES, NON_CLOSED_OUTCOMES

ID_PATTERN = re.compile(r"MA-\d{4}-\d{4}")
YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
OUTCOME_PATTERN = re.compile(r"\b(?:" + "|".join(NON_CLOSED_OUTCOMES) + r")\b", re.IGNORECASE)
# Deal types are matched in their dataset spelling (plural allowed), so "a strategic
# acquisition" in ordinary prose is not read as the "Strategic Acquisition" deal type.
DEAL_TYPE_PATTERN = re.compile(
    r"(?<![\w-])(" + "|".join(map(re.escape, sorted(KNOWN_VALUES["deal_type"], key=len,
                                                    reverse=True))) + r")s?(?![\w-])")
_GROUPED_OR_PLAIN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
# A minus sign counts only where it cannot be a hyphen or range dash ("2022-2024", "$1M-$4M").
_MINUS = r"(?:(?<![\w$%.])-)?"
NUMBER_PATTERN = re.compile(
    rf"{_MINUS}\${_GROUPED_OR_PLAIN}[MBK]?"
    rf"|{_MINUS}{_GROUPED_OR_PLAIN}(?:x(?![A-Za-z])|%)?"
)
# Quarter labels (Q3) and ordinals (1st, 22nd) are labels, not quotable numbers.
LABEL_PATTERN = re.compile(r"\bQ[1-4]\b|\b\d+(?:st|nd|rd|th)\b")
SAMPLE_SIZE_PATTERN = re.compile(r"\bn\s*=\s*(\d+)")
COMPLETION_WORDS = ("acquired", "acquisition of", "completed", "closed", "bought", "purchased")
COMPLETION_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in COMPLETION_WORDS) + r")\b",
    re.IGNORECASE,
)

SECTIONS = ["acquirer_overview", "strategic_fit", "precedent_activity", "valuation_context"]
BUYER_SECTIONS = {"acquirer_overview", "strategic_fit", "precedent_activity"}
BUYER_TOOLS = {"get_acquirer_profile", "get_acquirer_deals"}
SECTION_TITLES = {
    "acquirer_overview": "Acquirer Overview",
    "strategic_fit": "Strategic Fit Thesis",
    "precedent_activity": "Precedent Activity",
    "valuation_context": "Valuation Context",
}
LEVELS = ["Low", "Medium", "High"]
MIN_RISK_FLAGS = 2
MAX_REPORT_WORDS = 650
MAX_STATEMENT_WORDS = 60

SIMILARITY_THRESHOLD = 0.5
STOPWORDS = frozenset(
    "a an and are as at be been but by for from has have in into is it its of on or that the "
    "their them these this those to was were which while with within".split()
)


# ---------- dataset lookups (loaded once) ----------

class Deal(NamedTuple):
    target: str
    acquirer: str
    outcome: str
    deal_type: str
    year: int


@cache
def _outcomes() -> dict[str, str]:
    deals = get_client().transactions
    return dict(zip(deals["transaction_id"], deals["outcome"]))


@cache
def _deals() -> dict[str, Deal]:
    d = get_client().transactions
    return {row.transaction_id: Deal(row.target_company, row.acquirer, row.outcome, row.deal_type,
                                     int(row.deal_year)) for row in d.itertuples()}


@cache
def _acquirer_names(acquirer_key: str) -> frozenset[str]:
    d = get_client().transactions
    return frozenset(d.loc[d["acquirer_key"] == acquirer_key, "acquirer"])


@cache
def _name_pattern() -> re.Pattern:
    client = get_client()
    names = (set(client.transactions["acquirer"]) | set(client.transactions["target_company"])
             | {p["display_name"] for p in client.acquirer_profiles.values()})
    # Longest first, so "North Smile Group" wins over a shorter name inside it.
    ordered = sorted((n for n in names if n), key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, ordered)) + r")(?!\w)")


def _number_matches(text: str) -> list[str]:
    text = LABEL_PATTERN.sub(" ", _name_pattern().sub(" ", ID_PATTERN.sub(" ", text)))
    return [m.group() for m in NUMBER_PATTERN.finditer(text)]


def number_tokens(text: str) -> set[str]:
    """Number tokens in `text`, commas removed, minus signs kept. Transaction IDs, company
    names, quarter labels and ordinals are skipped."""
    return {token.replace(",", "") for token in _number_matches(text)}


def company_names(text: str) -> set[str]:
    return set(_name_pattern().findall(text))


# ---------- evidence ----------

@dataclass(frozen=True)
class Source:
    """One piece of evidence: the numbers it shows and the transaction IDs that support them.

    kind is "buyer" (this buyer's profile, deals and ranking entry), "comp" (comps, sector
    statistics and other buyers' deal rows), "target" (the target block, always quotable) or
    "assumption" (hand-written judgment; no IDs, so never bound to a citation).
    sample_sizes pairs each number with the n stored beside it, e.g. ("12.1x", "10").
    """
    ids: frozenset[str]
    kind: str
    numbers: frozenset[str]
    sample_sizes: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class Evidence:
    acquirer_key: str
    display_name: str
    starting_level: str
    allowed_ids: frozenset[str]
    allowed_numbers: frozenset[str]
    allowed_names: frozenset[str]
    outcomes: dict[str, str] = field(repr=False)  # every dataset ID -> outcome
    sources: tuple[Source, ...] = field(repr=False)
    buyer_ids: frozenset[str] = field(repr=False)  # IDs in "buyer" sources: its own deals
    own_names: frozenset[str] = field(repr=False)  # display name and its names in the CSV
    target_names: frozenset[str] = field(repr=False)
    figures: dict[str, str] = field(repr=False)  # deal-row ID -> headline display strings
    deals: dict[str, Deal] = field(repr=False)  # every dataset ID -> target, buyer, outcome...


def _plain(number) -> str:
    return str(int(number)) if float(number).is_integer() else repr(number)


def _texts(obj):
    """Every leaf value as text, skipping raw values that have a display string beside them."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if (key == "value" and "display" in obj) or f"{key}_display" in obj:
                continue
            yield from _texts(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _texts(value)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, (int, float)):
        yield _plain(obj)
    else:
        yield str(obj)


def _sample_size(obj: dict) -> str | None:
    """The n stored in a statistic: `n` (profiles, sector stats) or `sample_size` (comps)."""
    if isinstance(obj.get("n"), int):
        return str(obj["n"])
    size = obj.get("sample_size")
    return size.get("display") if isinstance(size, dict) else None


def _figures(row: dict) -> str:
    shown = [row["deal_size_mm"]["display"],
             f"EV/EBITDA {row['ev_ebitda_multiple']['display']}",
             f"EV/Revenue {row['ev_revenue_multiple']['display']}", str(row["deal_year"])]
    return ", ".join(s for s in shown if s and "None" not in s)


def _walk(obj, ids: frozenset[str], kind: str, own_key: str, out: list, figures: dict) -> None:
    """Add a Source for every dict or list, with the IDs of the nearest deal row or source_ids.

    A deal row supports only its own transaction_id and is "buyer" only if it is this buyer's
    deal. A statistic with source_ids supports those IDs; anything else inherits its parent's.
    An `assumption` field (hand-written judgment) supports no ID, so no citation can bind it.
    """
    if isinstance(obj, dict):
        if "transaction_id" in obj:
            ids = frozenset([obj["transaction_id"]])
            kind = "buyer" if obj.get("acquirer_key") == own_key else "comp"
            figures[obj["transaction_id"]] = _figures(obj)
        elif obj.get("source_ids"):
            ids = frozenset(obj["source_ids"])
        if isinstance(obj.get("assumption"), dict):
            _walk(obj["assumption"], frozenset(), "assumption", own_key, out, figures)
        children = [v for k, v in obj.items() if k != "assumption"]
        leaves = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
        n = _sample_size(obj)
        rest = {k: v for k, v in obj.items() if k not in ("n", "sample_size")}
        pairs = (frozenset((t, n) for t in number_tokens("\n".join(_texts(rest))))
                 if n else frozenset())
    elif isinstance(obj, list):
        children = obj
        leaves = [v for v in obj if not isinstance(v, (dict, list))]
        pairs = frozenset()
    else:
        return
    numbers = frozenset(number_tokens("\n".join(_texts(leaves))))
    if numbers or pairs:
        out.append(Source(ids, kind, numbers, pairs))
    for child in children:
        if isinstance(child, (dict, list)):
            _walk(child, ids, kind, own_key, out, figures)


def build_evidence(ranking_entry: dict, tool_results: list[dict]) -> Evidence:
    """Evidence for one buyer: its ranking entry plus the results of its agent's tool calls.

    A result whose data has a "target" block (the engine adds the ranking's target block this
    way) makes that block quotable in every statement without a citation.
    """
    key = ranking_entry["acquirer_key"]
    text = "\n".join(_texts([ranking_entry, *[call["result"] for call in tool_results]]))
    entry_ids = frozenset(ranking_entry.get("supporting_closed_ids", [])
                          + ranking_entry.get("weak_signal_ids", []))
    sources, figures, target_text = [], {}, ""
    _walk(ranking_entry, entry_ids, "buyer", key, sources, figures)
    for call in tool_results:
        result, data = call["result"], call["result"].get("data")
        if isinstance(data, dict) and "target" in data:
            _walk(data["target"], frozenset(), "target", key, sources, figures)
            target_text = "\n".join(_texts(data["target"]))
            result = {**result, "data": {k: v for k, v in data.items() if k != "target"}}
        kind = "buyer" if call["tool"] in BUYER_TOOLS else "comp"
        _walk(result, frozenset(), kind, key, sources, figures)
    return Evidence(
        acquirer_key=key,
        display_name=ranking_entry["display_name"],
        starting_level=ranking_entry["starting_conviction"]["level"],
        allowed_ids=frozenset(ID_PATTERN.findall(text)),
        allowed_numbers=frozenset(number_tokens(text)),
        allowed_names=frozenset(company_names(text) | {ranking_entry["display_name"]}),
        outcomes=_outcomes(),
        sources=tuple(sources),
        buyer_ids=frozenset(i for s in sources if s.kind == "buyer" for i in s.ids),
        own_names=_acquirer_names(key) | {ranking_entry["display_name"]},
        target_names=frozenset(company_names(target_text)),
        figures=figures,
        deals=_deals(),
    )


# ---------- report items ----------

class Item(NamedTuple):
    location: str
    section: str
    text: str
    kind: str
    source_ids: list[str]


def _items(draft: ReportDraft) -> list[Item]:
    """Every checkable piece of text, with its label and citations."""
    items = [Item(f"{section}[{i}]", section, s.text, s.kind, s.source_ids)
             for section in SECTIONS for i, s in enumerate(getattr(draft, section))]
    items += [Item(f"risk_flags[{i}]", "risk_flags", f"{f.risk}. {f.explanation}", f.kind,
                   f.source_ids) for i, f in enumerate(draft.risk_flags)]
    c = draft.conviction
    items.append(Item("conviction", "conviction", c.reason, "fact", c.source_ids))
    return items


def _cited(item: Item) -> list[str]:
    """IDs in source_ids plus any written in the text, without repeats."""
    return list(dict.fromkeys([*item.source_ids, *ID_PATTERN.findall(item.text)]))


# ---------- checks ----------

def _check_structure(report: BuyerReport, ev: Evidence) -> list[str]:
    draft, issues = report.draft, []
    if report.acquirer_key != ev.acquirer_key:
        issues.append(f"report: is for {report.acquirer_key!r} but the evidence is for "
                      f"{ev.acquirer_key!r}.")
    if report.starting_level != ev.starting_level:
        issues.append(f"report: starting level {report.starting_level} does not match the "
                      f"ranking's {ev.starting_level}.")
    for section in SECTIONS:
        if not getattr(draft, section):
            issues.append(f"{section}: is empty. Add at least one statement.")
    for item in _items(draft):
        if not item.text.strip():
            issues.append(f"{item.location}: text is empty. Write the statement or remove it.")
    flags = draft.risk_flags
    if len(flags) < MIN_RISK_FLAGS:
        issues.append(f"risk_flags: has {len(flags)} risk flag(s); at least {MIN_RISK_FLAGS} "
                      "different risks are required.")
    seen = {}
    for i, flag in enumerate(flags):
        key = " ".join(flag.risk.lower().split())
        if key in seen:
            issues.append(f"risk_flags[{i}]: repeats the risk \"{flag.risk}\" from "
                          f"risk_flags[{seen[key]}]. Replace it with a different risk.")
        seen.setdefault(key, i)
    start, level = LEVELS.index(report.starting_level), LEVELS.index(draft.conviction.level)
    if abs(level - start) > 1:
        allowed = ", ".join(LEVELS[max(0, start - 1):start + 2])
        issues.append(f"conviction: level {draft.conviction.level} is {abs(level - start)} steps "
                      f"from the starting level {report.starting_level}. Choose one of: {allowed}.")
    return issues


def _check_citations(item: Item, ev: Evidence) -> list[str]:
    issues = []
    if not item.source_ids:
        if item.location == "conviction":
            issues.append("conviction: the reason cites no transaction ID. Add at least one ID "
                          "from the tool results that supports it.")
        elif item.kind == "fact":
            issues.append(f"{item.location}: is a fact but cites no transaction ID. Add the IDs "
                          "from the tool results that support it, or label it interpretation.")
        else:
            issues.append(f"{item.location}: is an interpretation but cites no transaction ID. "
                          "Cite the IDs of the facts it rests on, or label it unknown (no "
                          "numbers, no IDs).")
    for tid in item.source_ids:
        if not ID_PATTERN.fullmatch(tid):
            issues.append(f"{item.location}: \"{tid}\" is not a transaction ID (format "
                          "MA-YYYY-NNNN). Use IDs exactly as the tools returned them.")
        elif tid not in ev.outcomes:
            issues.append(f"{item.location}: cites {tid}, which does not exist in the dataset. "
                          "Remove it; cite only IDs from your tool results.")
        elif tid not in ev.allowed_ids:
            issues.append(f"{item.location}: cites {tid}, which is not in the tool results for "
                          "this buyer. Remove it, or call a tool that returns it first.")
    for tid in ID_PATTERN.findall(item.text):
        if tid not in item.source_ids:
            issues.append(f"{item.location}: the text mentions {tid} but source_ids does not "
                          "include it. Add it to source_ids or remove it from the text.")
    return issues


def _near_miss(token: str, allowed: frozenset[str]) -> str:
    def split(t: str) -> tuple[str, float, str]:
        sign, body = ("-", t[1:]) if t.startswith("-") else ("", t)
        unit = body[-1] if body[-1] in "MBKx%" else ""
        prefix = "$" if body.startswith("$") else ""
        return sign + prefix, float(body[len(prefix):len(body) - len(unit)]), unit

    target = split(token)
    matches = sorted(a for a in allowed if split(a) == target)
    return f" Did you mean \"{matches[0]}\"?" if matches else ""


def _id_list(ids, limit: int = 4) -> str:
    ids = sorted(ids)
    more = f" and {len(ids) - limit} more" if len(ids) > limit else ""
    return ", ".join(ids[:limit]) + more


def _kinds(section: str) -> set[str]:
    """Source kinds whose numbers a section may quote. Comps never describe this buyer."""
    return {"buyer"} if section in BUYER_SECTIONS else {"buyer", "comp"}


def _supported(source: Source, item: Item, cited: set[str]) -> bool:
    return source.kind == "target" or (source.kind in _kinds(item.section)
                                       and bool(source.ids & cited))


def _is_cited_year(token: str, cited: set[str], ev: Evidence) -> bool:
    """A year passes when it is the deal year of a cited deal."""
    return bool(YEAR_PATTERN.fullmatch(token)) and any(
        ev.deals[i].year == int(token) for i in cited if i in ev.deals)


def _cited_figures(cited: set[str], ev: Evidence) -> str:
    """Headline figures of the cited deal rows, e.g. "MA-2021-0407 ($341.7M, ...)"."""
    return "; ".join([f"{i} ({ev.figures[i]})" for i in sorted(cited) if i in ev.figures][:3])


def _unbound_number(raw: str, token: str, item: Item, cited: set[str], ev: Evidence) -> str:
    """Why a number that is in the evidence is not supported by this statement's IDs."""
    where, figures = f"{item.location}: \"{raw}\"", _cited_figures(cited, ev)
    use_cited = f" or use the figures for {figures}" if figures else ""
    if YEAR_PATTERN.fullmatch(token):
        years = ", ".join(f"{i} is {ev.deals[i].year}" for i in sorted(cited) if i in ev.deals)
        return (f"{where} is not the year of any cited deal ({years}). Use the cited deal's "
                f"year, or cite the {raw} deal you mean.")
    holders = [s for s in ev.sources if token in s.numbers]
    usable = [s for s in holders if s.kind in _kinds(item.section) and s.ids]
    if not usable and any(s.kind == "assumption" for s in holders):
        return (f"{where} is a hand-written assumption (sector closeness), not a fact about any "
                "deal, so no citation can support it. Remove the number, or quote the closeness "
                "shown in the target block.")
    if not usable and any(s.kind == "comp" for s in holders):
        own = f": {figures}" if figures else ""
        return (f"{where} is a comps or other-buyer figure, which may support valuation_context "
                f"and risk_flags only. In {item.section}, quote this buyer's own deal "
                f"figures{own}, or remove it.")
    if not usable:
        return (f"{where} comes from a dataset-wide figure with no transaction IDs, so no "
                "citation can support it. Remove it.")
    best = min(usable, key=lambda s: len(s.ids))
    if len(best.ids) == 1:
        (owner,) = best.ids
        return (f"{where} belongs to {owner}, but the statement cites {_id_list(cited)}. "
                f"Cite {owner}{use_cited}.")
    return (f"{where} comes from a statistic over {len(best.ids)} deals ({_id_list(best.ids)}), "
            f"none of which the statement cites. Cite one of them{use_cited}.")


def _check_numbers(item: Item, ev: Evidence) -> list[str]:
    """Every number must be in a source whose IDs overlap the cited IDs (target block exempt)."""
    issues, cited = [], set(_cited(item))
    for raw in dict.fromkeys(_number_matches(SAMPLE_SIZE_PATTERN.sub(" ", item.text))):
        token = raw.replace(",", "")
        if token not in ev.allowed_numbers:
            issues.append(f"{item.location}: number \"{raw}\" does not appear in the tool results "
                          "for this buyer. Copy numbers exactly as displayed (e.g. \"$185.0M\", "
                          f"\"12.1x\", \"15.2%\"), or remove it.{_near_miss(token, ev.allowed_numbers)}")
        elif cited and not _is_cited_year(token, cited, ev) and not any(
                token in s.numbers and _supported(s, item, cited) for s in ev.sources):
            issues.append(_unbound_number(raw, token, item, cited, ev))
    return issues


def _check_sample_sizes(item: Item, ev: Evidence) -> list[str]:
    """Each n=<count> must be the n stored next to a number quoted in the same statement.

    If no quoted number has a stored n (e.g. "a small sample, n=10"), the count must still be
    a sample size stored in a source the cited IDs support.
    """
    issues, cited = [], set(_cited(item))
    quoted = number_tokens(SAMPLE_SIZE_PATTERN.sub(" ", item.text))
    supported = {p for s in ev.sources if _supported(s, item, cited) for p in s.sample_sizes}
    quoted_pairs = {p for p in supported if p[0] in quoted}
    for count in dict.fromkeys(SAMPLE_SIZE_PATTERN.findall(item.text)):
        if any(n == count for _, n in quoted_pairs or supported):
            continue
        stored = sorted({f"{t} has n={n}" for t, n in quoted_pairs
                         if not t.isdigit() and not YEAR_PATTERN.fullmatch(t)})
        sizes = ", ".join(sorted({n for _, n in supported}, key=int))
        hint = (f" In the tool results for the cited IDs, {'; '.join(stored)}." if stored else
                f" The sample sizes stored for the cited IDs are {sizes}." if sizes else
                " The cited IDs have no stored sample size; quote the multiple and cite the "
                "comps IDs it comes from.")
        issues.append(f"{item.location}: n={count} is not the sample size stored next to any "
                      f"figure quoted in this statement.{hint} Copy the n shown beside the figure.")
    return issues


def _check_buyer_ids(item: Item, ev: Evidence) -> list[str]:
    """Overview and fit statements rest on this buyer's deals; precedents are its deals only."""
    cited = _cited(item)
    if item.section not in BUYER_SECTIONS or not cited:
        return []
    if item.section == "precedent_activity":
        return [f"{item.location}: cites {tid}, which is not this buyer's deal "
                f"({ev.deals[tid].acquirer} / {ev.deals[tid].target}). Precedents must be "
                f"{ev.display_name}'s own deals: remove it, or move comparison deals to "
                "valuation_context." for tid in cited if tid in ev.deals and tid not in ev.buyer_ids]
    if ev.buyer_ids.isdisjoint(cited):
        return [f"{item.location}: cites only comps or other buyers' deals "
                f"({_id_list(cited)}). Cite at least one of {ev.display_name}'s own deals (from "
                "get_acquirer_profile, get_acquirer_deals or the ranking entry), or move the "
                "statement to valuation_context."]
    return []


def _check_outcomes(item: Item, ev: Evidence) -> list[str]:
    """Outcome words must match the cited deals they describe."""
    cited = [i for i in _cited(item) if i in ev.deals]
    words = {m.group().capitalize(): m.group() for m in OUTCOME_PATTERN.finditer(item.text)}
    if not cited or not words:
        return []
    issues, described = [], ", ".join(f"{i} is {ev.deals[i].outcome}" for i in cited[:4])
    for outcome, written in words.items():
        if not any(ev.deals[i].outcome == outcome for i in cited):
            issues.append(f"{item.location}: says \"{written}\", but no cited deal is {outcome} "
                          f"({described}). Describe each deal by its own outcome, or cite the "
                          f"{outcome} deal.")
    if issues:
        return issues
    named = company_names(item.text)
    for tid in cited:
        deal = ev.deals[tid]
        if deal.target in named and deal.outcome not in words:
            issues.append(f"{item.location}: names {deal.target} ({tid}, {deal.outcome}) in a "
                          f"statement that says {', '.join(words.values())}. Give each deal its "
                          "own statement, described by its own outcome.")
    return issues


def _check_deal_types(item: Item, ev: Evidence) -> list[str]:
    """A deal type named in the text must be the type of at least one cited deal."""
    cited = [i for i in _cited(item) if i in ev.deals]
    if not cited:
        return []
    described = ", ".join(f"{i} is a {ev.deals[i].deal_type}" for i in cited[:4])
    issues = []
    for deal_type in dict.fromkeys(m.group(1) for m in DEAL_TYPE_PATTERN.finditer(item.text)):
        if not any(ev.deals[i].deal_type == deal_type for i in cited):
            issues.append(f"{item.location}: says \"{deal_type}\", but no cited deal is a "
                          f"{deal_type} ({described}). Cite a {deal_type} deal, or use the cited "
                          "deal's own type.")
    return issues


def _non_closed(ids: list[str], ev: Evidence) -> list[tuple[str, str]]:
    return [(i, ev.outcomes[i]) for i in ids if ev.outcomes.get(i, CLOSED) != CLOSED]


def _check_wording(item: Item, ev: Evidence) -> list[str]:
    word = COMPLETION_PATTERN.search(item.text)
    if not word:
        return []
    return [f"{item.location}: cites {tid} ({outcome}) but uses completion word "
            f"\"{word.group()}\". Put non-closed deals in their own statement and describe them "
            f"by their outcome (e.g. \"was {outcome}\")."
            for tid, outcome in _non_closed(_cited(item), ev)]


def _check_valuation(item: Item, ev: Evidence) -> list[str]:
    if item.section != "valuation_context" or item.kind == "unknown":
        return []
    issues = [f"{item.location}: valuation statements must cite closed deals only, but this "
              f"cites {tid} ({outcome}). Remove it and use closed-deal comps."
              for tid, outcome in _non_closed(_cited(item), ev)]
    if not SAMPLE_SIZE_PATTERN.search(item.text):
        issues.append(f"{item.location}: valuation {item.kind} has no sample size. State it as "
                      "n=<count>, copying the count from the tool result (e.g. n=10).")
    return issues


def _check_names(item: Item, ev: Evidence) -> list[str]:
    """Names must be in the evidence; in a cited statement, they must belong to a cited deal."""
    names = company_names(item.text) - ev.own_names - ev.target_names
    issues = [f"{item.location}: names \"{name}\", which is not in the tool results for this "
              "buyer. Remove it, or look it up with a tool first."
              for name in sorted(names - ev.allowed_names)]
    cited = [i for i in _cited(item) if i in ev.deals]
    if item.kind == "unknown" or not cited:
        return issues
    on_cited = {n for i in cited for n in (ev.deals[i].target, ev.deals[i].acquirer)}
    cited_targets = ", ".join(f"{i} ({ev.deals[i].target})" for i in cited[:4])
    for name in sorted((names & ev.allowed_names) - on_cited):
        owners = sorted(i for i in ev.allowed_ids if i in ev.deals
                        and name in (ev.deals[i].target, ev.deals[i].acquirer))
        role = "the target of" if any(ev.deals[i].target == name for i in owners) else "the buyer in"
        issues.append(f"{item.location}: \"{name}\" is {role} {_id_list(owners, 3)}, but the "
                      f"statement cites {cited_targets}. Cite {owners[0]} or name the cited "
                      "deal's target." if owners else
                      f"{item.location}: \"{name}\" is not part of any cited deal. Remove it.")
    return issues


def _check_unknown(item: Item) -> list[str]:
    issues = []
    numbers = list(dict.fromkeys(_number_matches(item.text)))
    if numbers:
        issues.append(f"{item.location}: unknown statements must not contain numbers (found "
                      f"{', '.join(numbers)}). Remove them, or make it a cited fact.")
    ids = _cited(item)
    if ids:
        issues.append(f"{item.location}: unknown statements must not cite transaction IDs "
                      f"({', '.join(ids)}). Remove them, or make it a cited fact.")
    return issues


def check(report: BuyerReport, evidence: Evidence) -> list[str]:
    """Every guardrail problem in the report, as fix-oriented messages. Empty list = pass."""
    issues = _check_structure(report, evidence)
    for item in _items(report.draft):
        if item.kind == "unknown":
            issues += _check_unknown(item)
        else:
            issues += _check_citations(item, evidence)
            issues += _check_buyer_ids(item, evidence)
            issues += _check_numbers(item, evidence)
            issues += _check_sample_sizes(item, evidence)
            issues += _check_outcomes(item, evidence)
            issues += _check_deal_types(item, evidence)
        issues += _check_wording(item, evidence)
        issues += _check_valuation(item, evidence)
        issues += _check_names(item, evidence)
    return issues


def apply_check(report: BuyerReport, evidence: Evidence) -> BuyerReport:
    """The report with status and issues set by `check`."""
    issues = check(report, evidence)
    return report.model_copy(update={"status": "not_verified" if issues else "verified",
                                     "issues": issues})


def length_warnings(report: BuyerReport) -> list[str]:
    """Length notes for a roughly one-page report. Warnings only, never failures."""
    items = _items(report.draft)
    warnings = [f"{item.location}: {len(item.text.split())} words; keep statements under "
                f"{MAX_STATEMENT_WORDS}." for item in items
                if len(item.text.split()) > MAX_STATEMENT_WORDS]
    total = sum(len(item.text.split()) for item in items)
    if total > MAX_REPORT_WORDS:
        warnings.append(f"report: {total} words; aim for at most {MAX_REPORT_WORDS} (one page).")
    return warnings


# ---------- rendering ----------

def _line(text: str, kind: str, ids: list[str]) -> str:
    label = {"interpretation": "*Interpretation:* ", "unknown": "*Unknown:* "}.get(kind, "")
    tags = " ".join(f"[{tid}]" for tid in dict.fromkeys(ids))
    return f"{label}{text}" + (f" {tags}" if tags else "")


def _status_label(report: BuyerReport) -> str:
    return "Verified" if report.status == "verified" else "Not verified"


def render_markdown(report: BuyerReport) -> str:
    """One-page Markdown report. Citation tags come from source_ids only."""
    draft, conviction = report.draft, report.draft.conviction
    lines = [
        f"## {report.rank}. {report.display_name}",
        "",
        f"**Type:** {report.acquirer_type} · **Score:** {report.score} (score rank "
        f"{report.score_rank}) · **Conviction:** {conviction.level} (starting level "
        f"{report.starting_level}) · **Status:** {_status_label(report)}",
    ]
    if report.selection_note:
        lines += ["", f"*Selection note:* {report.selection_note}"]
    if report.status != "verified":
        lines += ["", "> **Not verified.** This report did not pass the guardrail checks:",
                  *[f"> - {issue}" for issue in report.issues]]
    for section in SECTIONS:
        lines += ["", f"### {SECTION_TITLES[section]}",
                  *[f"- {_line(s.text, s.kind, s.source_ids)}" for s in getattr(draft, section)]]
    lines += ["", "### Risk Flags",
              *[f"- {_line(f'**{f.risk}:** {f.explanation}', f.kind, f.source_ids)}"
                for f in draft.risk_flags]]
    lines += ["", "### Conviction",
              _line(f"**{conviction.level}.** {conviction.reason}", "fact", conviction.source_ids)]
    return "\n".join(lines) + "\n"


def render_run_markdown(reports: list[BuyerReport]) -> str:
    """All reports, after a summary table."""
    rows = [f"| {r.rank} | {r.display_name} | {r.acquirer_type} | {r.draft.conviction.level} | "
            f"{_status_label(r)} |" for r in reports]
    header = ["# Buyer reports", "", "| Rank | Buyer | Type | Conviction | Status |",
              "|---|---|---|---|---|", *rows, ""]
    return "\n".join(header) + "\n---\n\n" + "\n---\n\n".join(map(render_markdown, reports))


# ---------- run quality metrics ----------

def _fit_words(report: BuyerReport) -> set[str]:
    text = ID_PATTERN.sub(" ", " ".join(s.text for s in report.draft.strategic_fit)).lower()
    own_name = set(re.findall(r"[a-z]+", report.display_name.lower()))
    return {w for w in re.findall(r"[a-z]+", text) if len(w) > 1} - STOPWORDS - own_name


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def quality_metrics(reports: list[BuyerReport]) -> dict:
    """Run-level quality numbers and a pass/fail summary."""
    outcomes = _outcomes()
    items = [item for r in reports for item in _items(r.draft)]
    invalid = sorted({tid for item in items for tid in item.source_ids
                      if not ID_PATTERN.fullmatch(tid) or tid not in outcomes})
    invalid_count = sum(1 for item in items for tid in item.source_ids if tid in invalid)
    facts = [item for item in items if item.kind == "fact"]
    cited = sum(1 for item in facts if item.source_ids)
    words = {r.acquirer_key: _fit_words(r) for r in reports}
    pairs = sorted(((round(_jaccard(words[a], words[b]), 2), a, b)
                    for a, b in combinations(words, 2)), reverse=True)
    conviction_mix = dict(Counter(r.draft.conviction.level for r in reports))
    not_verified = [r.acquirer_key for r in reports if r.status != "verified"]
    summary = {
        "full_report_count": len(reports) >= config.REPORT_COUNT,
        "all_reports_verified": not not_verified,
        "no_invalid_citations": invalid_count == 0,
        "all_facts_cited": cited == len(facts),
        "conviction_varies": len(conviction_mix) >= 2,
        "no_similar_pairs": not any(s > SIMILARITY_THRESHOLD for s, _, _ in pairs),
    }
    return {
        "report_count": len(reports),
        "required_report_count": config.REPORT_COUNT,
        "invalid_citation_count": invalid_count,
        "invalid_citation_ids": invalid,
        "not_verified": not_verified,
        "fact_count": len(facts),
        "facts_cited": cited,
        "facts_cited_share": cited / len(facts) if facts else 1.0,
        "conviction_mix": {level: conviction_mix.get(level, 0) for level in reversed(LEVELS)},
        "buyer_type_mix": dict(sorted(Counter(r.acquirer_type for r in reports).items())),
        "max_fit_similarity": pairs[0][0] if pairs else 0.0,
        "similarity_threshold": SIMILARITY_THRESHOLD,
        "similar_pairs": [{"a": a, "b": b, "similarity": s}
                          for s, a, b in pairs if s > SIMILARITY_THRESHOLD],
        "summary": summary,
        "passed": all(summary.values()),
    }


def latest_run_dir(runs_dir: Path) -> Path | None:
    """Newest run folder (by name) that has a reports.json."""
    if not runs_dir.is_dir():
        return None
    runs = sorted(p for p in runs_dir.iterdir() if (p / "reports.json").is_file())
    return runs[-1] if runs else None


def load_reports(run_dir: Path) -> tuple[dict, list[BuyerReport]]:
    run = json.loads((run_dir / "reports.json").read_text(encoding="utf-8"))
    return run, [BuyerReport.model_validate(r) for r in run["reports"]]


def _mix(counts: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()) or "none"


def print_metrics(run: dict, metrics: dict) -> None:
    print(f"Run {run['run_id']}: {metrics['report_count']} reports")
    print(f"Invalid citations: {metrics['invalid_citation_count']} "
          f"{metrics['invalid_citation_ids'] or ''}".rstrip())
    print(f"Reports not verified: {len(metrics['not_verified'])} "
          f"{metrics['not_verified'] or ''}".rstrip())
    print(f"Fact statements with citations: {metrics['facts_cited']} of {metrics['fact_count']} "
          f"({metrics['facts_cited_share']:.1%})")
    print(f"Conviction mix: {_mix(metrics['conviction_mix'])}")
    print(f"Buyer-type mix: {_mix(metrics['buyer_type_mix'])}")
    pairs = "; ".join(f"{p['a']} / {p['b']} {p['similarity']:.2f}"
                      for p in metrics["similar_pairs"]) or "none"
    print(f"Max strategic-fit similarity: {metrics['max_fit_similarity']:.2f} "
          f"(threshold {metrics['similarity_threshold']}); pairs above: {pairs}")
    print("Result: PASS" if metrics["passed"] else f"Result: FAIL ({_fail_reason(metrics)})")


def _fail_reason(metrics: dict) -> str:
    """One line naming every failed check, with the counts for the report checks."""
    reasons = {
        "full_report_count": f"{metrics['report_count']} of {metrics['required_report_count']} "
                             "reports",
        "all_reports_verified": f"{len(metrics['not_verified'])} report(s) not verified",
    }
    return "; ".join(reasons.get(name, name) for name, ok in metrics["summary"].items() if not ok)


def main() -> None:
    run_dir = latest_run_dir(config.RUNS_DIR)
    if run_dir is None:
        print(f"No runs found in {config.RUNS_DIR} (need <run_id>/reports.json). "
              "Run the engine first (make run).")
        raise SystemExit(1)
    run, reports = load_reports(run_dir)
    metrics = quality_metrics(reports)
    print_metrics(run, metrics)
    if not metrics["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
