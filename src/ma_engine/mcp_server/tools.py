"""Read-only research tools as plain Python functions (registered by `server.py`).

Every tool returns `{"data": ..., "source_ids": [...], "warnings": [...]}`. Tools only look up
pre-calculated tables and filter rows; they never calculate new statistics. Invalid argument
values raise `ValueError` with the allowed values; an empty result is a normal response with a
warning.

The tools share one `DataClient`, created on first use by `get_client()` and reused after that.
The server calls `get_client()` at start so the data is loaded once, before the first request;
tests call the tool functions directly.
"""

from typing import Literal

from ma_engine.data import ranking
from ma_engine.clients.data_client import get_client, sector_stat_id
from ma_engine.config import DEFAULT_TARGET, SHORTLIST_SIZE
from ma_engine.models import Target
from ma_engine.data.prepare import (
    ALL_SIZES,
    CLOSED,
    KNOWN_VALUES,
    MIN_COMPS_SAMPLE,
    NON_CLOSED_OUTCOMES,
    SCOPES,
    SECTORS,
    SIZE_BANDS,
    normalize_acquirer,
)

DEFAULT_LIMIT = 10
MAX_LIMIT = 25
MAX_KEY_SUGGESTIONS = 5
SIZE_BAND_LABELS = [label for label, _, _ in SIZE_BANDS]
NOT_CLOSED_WARNING = (
    "Rows with is_closed=false did not close (see outcome). Never describe them as acquired, "
    "completed or closed."
)


# ---------- shared helpers ----------

def _result(data, source_ids, warnings=None) -> dict:
    return {"data": data, "source_ids": sorted(set(source_ids)), "warnings": warnings or []}


def _check(name: str, value: str, allowed: list[str]) -> None:
    if value not in allowed:
        raise ValueError(f"Unknown {name} {value!r}. Allowed values: {', '.join(allowed)}.")


def _check_each(name: str, values: list[str] | None, allowed: list[str]) -> None:
    for value in values or []:
        _check(name, value, allowed)


def _check_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be a whole number from 1 to {MAX_LIMIT}; got {limit!r}.")


def _resolve_acquirer(acquirer_key: str) -> str:
    key = normalize_acquirer(acquirer_key)
    if get_client().profile(key) is not None:
        return key
    matches = [k for k in get_client().acquirer_keys() if key and key in k][:MAX_KEY_SUGGESTIONS]
    hint = (f" Keys containing {key!r}: {', '.join(matches)}." if matches
            else " Use search_deals to find acquirer keys (field acquirer_key).")
    raise ValueError(f"Unknown acquirer_key {acquirer_key!r}.{hint}")


def _count(value: int, source_ids: list[str] | None = None) -> dict:
    out = {"value": int(value), "display": str(int(value))}
    return out if source_ids is None else out | {"source_ids": source_ids}


def _deal_result(frame, limit: int, empty_message: str, extra_warnings: list[str]) -> dict:
    shown = frame.head(limit)
    rows = get_client().deal_rows(shown)
    warnings = list(extra_warnings)
    if not rows:
        warnings.append(empty_message)
    elif len(frame) > limit:
        warnings.append(f"Showing {limit} of {len(frame)} matching deals (newest first). "
                        "Narrow the filters or raise limit (max 25) to see more.")
    if not shown["is_closed"].all():
        warnings.append(NOT_CLOSED_WARNING)
    return _result({"deals": rows, "returned": len(rows), "total_matches": len(frame),
                    "truncated": len(frame) > len(rows)}, [r["transaction_id"] for r in rows],
                   warnings)


# ---------- tools ----------

def get_dataset_overview() -> dict:
    """Describe the whole dataset and list the allowed argument values for the other tools.

    Returns the sector list, size band labels and edges, comparison scopes, outcomes, buyer
    types, deal types and geographies with row counts, and data-quality notes. Call this first
    if you are unsure which values are allowed. The strong-margin threshold depends on the
    target's sector, so it is in the ranking's target block, not here.
    The counts cover the whole dataset, so source_ids is empty: cite transaction IDs from the
    other tools instead.
    """
    dq = get_client().data_quality
    counts = dq["category_counts"]
    data = {
        "row_count": dq["row_count"],
        "sectors": {s: counts["sector"].get(s, 0) for s in SECTORS},
        "size_bands": {
            "labels": SIZE_BAND_LABELS,
            "edges": dq["size_bands"]["edges"],
            "deal_counts": dq["size_bands"]["counts"],
            "comps_also_accept": ALL_SIZES,
        },
        "scopes": {
            "sector_only": "Only the named sector.",
            "sector_plus_adjacent": "The sector plus every sector with closeness of at least "
                                    f"{dq['adjacency_threshold']}.",
        },
        "outcomes": counts["outcome"],
        "closed_outcome": CLOSED,
        "not_closed_outcomes": NON_CLOSED_OUTCOMES,
        "buyer_types": counts["acquirer_type"],
        "deal_types": counts["deal_type"],
        "geographies": counts["geography"],
        "target_ownership_pre": counts["target_ownership_pre"],
        "acquirers": dq["acquirers"],
        "assumptions": dq["assumptions"],
    }
    warnings = [
        f"{c['check']}: {c['rows']} rows. Quote the stated column; never recalculate it."
        for c in dq["consistency_checks"] if c["rows"]
    ]
    warnings.append("Financial Sponsors are "
                    f"{counts['acquirer_type'].get('Financial Sponsor', 0)} of {dq['row_count']} "
                    "deals; this is the dataset's mix, not evidence that sponsors are better buyers.")
    return _result(data, [], warnings)


def get_sector_overview(sector: str) -> dict:
    """Pre-calculated statistics for one sector and how close every other sector is to it.

    Returns deal and closed-deal counts, median deal size, EBITDA margin median and 75th
    percentile, Financial Sponsor share, top deal types, and the other sectors with is_adjacent.
    Each other sector's hand-written closeness score and one-line reason sit in its
    `assumption` field: they are judgment, not data, so anything you write from them is an
    interpretation. Use it to judge sector fit and to decide which adjacent sectors to research.

    Args:
        sector: Exact sector name, for example "Healthcare Services". See get_dataset_overview.
    """
    _check("sector", sector, SECTORS)
    row = get_client().sector_stats_row(sector)
    points = get_client().sector_points(sector)
    data = {
        "sector": sector,
        "basis": "All deals in the sector (any outcome) unless the field says closed.",
        **points,
        "top_deal_types": [
            item if not item.get("display") else {
                **item, "stat_id": sector_stat_id(sector, "deal_type", item["deal_type"])}
            for item in row["top_deal_types"]
        ],
        "other_sectors": [
            {"sector": s["sector_b"], "is_adjacent": s["is_adjacent"],
             "assumption": {"closeness": s["closeness"],
                            "closeness_display": f"{s['closeness']:g}", "reason": s["reason"]}}
            for s in get_client().similar_sectors(sector)
        ],
    }
    warnings = ["Sector closeness scores and reasons (the assumption fields) are hand-written "
                "judgment, not calculated from deals. Anything taken from an assumption field "
                "is an interpretation, never a fact."]
    if row["deal_count"] == 0:
        warnings.append(f"No deals in {sector}.")
    return _result(data, row["source_ids"], warnings)


def get_acquirer_profile(acquirer_key: str) -> dict:
    """Pre-calculated profile of one buyer from its historical deals in the dataset.

    Returns deal and closed counts, deals that did not close by outcome, closed deals by
    sector, deal type and geography, closed deal size (median, smallest, largest), median
    EV/EBITDA and EV/Revenue paid on closed deals, private-target count, financing mix,
    median bidders and days to close, first and last deal year, and top rationale tags.
    Every number has a display string and its source transaction IDs.

    Args:
        acquirer_key: Buyer name in lower case, for example "blackstone" (the acquirer_key
            field in deal rows). Case and extra spaces are ignored.
    """
    key = _resolve_acquirer(acquirer_key)
    profile = get_client().annotated_profile(key)
    warnings = []
    for outcome, stat in profile["non_closed_by_outcome"].items():
        if stat["value"]:
            warnings.append(f"{stat['display']} deal(s) {outcome} ({', '.join(stat['source_ids'])}): "
                            "did not close; never describe them as acquired or completed.")
    if profile["deal_count"]["value"] < MIN_COMPS_SAMPLE:
        warnings.append(f"Thin history: only {profile['deal_count']['display']} deal(s) in the dataset.")
    if len(profile["acquirer_types_seen"]) > 1:
        warnings.append(f"Buyer appears with more than one type: {profile['acquirer_types_seen']}.")
    return _result(profile, profile["deal_count"]["source_ids"], warnings)


def get_acquirer_deals(acquirer_key: str, sectors: list[str] | None = None,
                       closed_only: bool = True, limit: int = DEFAULT_LIMIT) -> dict:
    """One buyer's deal rows, newest first, with the outcome of each deal shown.

    Each row has the transaction_id, target, sector, year and quarter, deal type, geography,
    outcome, is_closed, stated deal size, EV/EBITDA, EV/Revenue and EBITDA margin (value and
    display), ownership before the deal, financing, bidders, days to close, rationale tags
    and size band. Use it to cite specific precedent deals.

    Args:
        acquirer_key: Buyer name in lower case, for example "blackstone".
        sectors: Only deals in these sectors. Omit for all sectors.
        closed_only: True (default) returns closed deals only. Set False to also see
            Withdrawn, Pending, Terminated and Rumored deals, for example for risk flags.
        limit: Maximum rows to return, 1 to 25 (default 10).
    """
    key = _resolve_acquirer(acquirer_key)
    _check_each("sector", sectors, SECTORS)
    _check_limit(limit)
    frame = get_client().filter_deals(acquirer_key=key, sectors=sectors,
                                      outcomes=[CLOSED] if closed_only else None)
    warnings = []
    profile = get_client().profile(key)
    if closed_only and any(s["value"] for s in profile["non_closed_by_outcome"].values()):
        warnings.append("Only closed deals are shown. This buyer also has deals that did not "
                        "close; call again with closed_only=false to see them.")
    return _deal_result(frame, limit, f"No matching deals for {key} with these filters.", warnings)


def get_valuation_comps(sector: str,
                        scope: Literal["sector_only", "sector_plus_adjacent"],
                        size_band: str) -> dict:
    """Pre-calculated valuation multiples from closed deals for a sector and size band.

    Returns the 25th percentile, median and 75th percentile of EV/EBITDA and EV/Revenue, the
    median EBITDA margin, the sample size of each, and the sectors included. Only closed deals
    are used. Always quote the sample size with a multiple. Use scope "sector_plus_adjacent"
    when the sector alone has few deals, to see the wider comparable set.

    Args:
        sector: Exact sector name, for example "Healthcare Services".
        scope: "sector_only" for the named sector, or "sector_plus_adjacent" to add the
            adjacent sectors listed by get_sector_overview.
        size_band: One of "Under $100M", "$100M–$400M", "$400M–$1B", "Over $1B", or
            "All sizes".
    """
    _check("sector", sector, SECTORS)
    _check("scope", scope, SCOPES)
    _check("size_band", size_band, SIZE_BAND_LABELS + [ALL_SIZES])
    row = get_client().comps_row(sector, scope, size_band)
    block = get_client().comps_block(sector, scope, size_band)
    data = {
        "sector": sector,
        "scope": scope,
        "size_band": size_band,
        "sectors_included": row["sectors_included"],
        "basis": "Closed deals only, stated multiples as given in the data.",
        "closed_deal_count": _count(row["deal_count"]),
        **block,
    }
    warnings = []
    if row["deal_count"] == 0:
        warnings.append(f"No matching closed deals for {sector}, {scope}, {size_band}.")
    if isinstance(row["sample_warning"], str) and row["sample_warning"]:
        warnings.append(row["sample_warning"])
    return _result(data, row["source_ids"], warnings)


def search_deals(sectors: list[str] | None = None, size_band: str | None = None,
                 outcomes: list[str] | None = None, acquirer_type: str | None = None,
                 deal_types: list[str] | None = None, geography: str | None = None,
                 limit: int = DEFAULT_LIMIT) -> dict:
    """Find deal rows across all buyers that match every filter given, newest first.

    Filtering only, no totals or averages. Each row has the same fields as
    get_acquirer_deals plus acquirer_key. Use it to find comparable deals or other buyers
    active in a sector, size band or region. Omit a filter to not filter on it.

    Args:
        sectors: Only these sectors, for example ["Healthcare Services", "Dental"].
        size_band: One of "Under $100M", "$100M–$400M", "$400M–$1B", "Over $1B".
        outcomes: Only these outcomes: Closed, Withdrawn, Pending, Terminated, Rumored.
        acquirer_type: "Financial Sponsor" or "Strategic".
        deal_types: Only these deal types, for example ["Platform Investment"].
        geography: One region, for example "Midwest". See get_dataset_overview.
        limit: Maximum rows to return, 1 to 25 (default 10).
    """
    _check_each("sector", sectors, SECTORS)
    if size_band is not None:
        _check("size_band", size_band, SIZE_BAND_LABELS)
    _check_each("outcome", outcomes, KNOWN_VALUES["outcome"])
    if acquirer_type is not None:
        _check("acquirer_type", acquirer_type, KNOWN_VALUES["acquirer_type"])
    _check_each("deal_type", deal_types, KNOWN_VALUES["deal_type"])
    if geography is not None:
        _check("geography", geography, KNOWN_VALUES["geography"])
    _check_limit(limit)
    frame = get_client().filter_deals(sectors=sectors, outcomes=outcomes, size_band=size_band,
                                      acquirer_type=acquirer_type, deal_types=deal_types,
                                      geography=geography)
    return _deal_result(frame, limit, "No matching deals for these filters.", [])


def rank_acquirers(sector: str = DEFAULT_TARGET["sector"],
                   ev_mm: float = DEFAULT_TARGET["ev_mm"],
                   ownership: str = DEFAULT_TARGET["ownership"],
                   geography: str = DEFAULT_TARGET["geography"],
                   margin_profile: Literal["strong", "any"] = DEFAULT_TARGET["margin_profile"],
                   limit: int = SHORTLIST_SIZE,
                   excluded_keys: list[str] | None = None) -> dict:
    """Rank the buyers most likely to acquire a target company, best first.

    The engine calls this first in every run to build the shortlist; outside clients can call
    it too. Python scores every buyer with at least one closed deal in the target sector or an
    adjacent sector on six signals (sector fit, size fit, repeat activity, recency, profile fit,
    rationale fit). Each buyer comes with its score, every signal's value, weight,
    contribution and a ready-to-quote explanation with source IDs, a starting conviction
    (High, Medium or Low) with the rule that produced it, the supporting closed-deal IDs,
    weak-signal IDs (deals that did not close; never describe them as completed) and warnings.
    The shortlist and reserves follow score order; buyer type does not reserve places.
    Up to three strategic alternatives outside the top 10 are returned separately, without
    displacing higher-scoring buyers. `rank` is the returned position and `score_rank` the
    position by score. Also returns the target as understood, candidate count, conviction
    score bars and buyer-type mix. Historical saved runs may contain selection notes from
    the former buyer-type coverage policy.
    Omit every argument to rank the default target.

    Args:
        sector: Target sector, for example "Healthcare Services". See get_dataset_overview.
        ev_mm: Target enterprise value in $ millions, 1 to 100000 (for example 200).
        ownership: Target ownership before the deal: Private, PE-Backed, Public or Non-Profit.
        geography: The target's region, for example "Midwest", or "Any region" for a regional
            business whose region is not named.
        margin_profile: "strong" (above the sector median EBITDA margin) or "any".
        limit: Number of buyers to return, 1 to 25 (default 15).
        excluded_keys: Acquirer keys to leave out, for example ["blackstone"].
    """
    _check_limit(limit)
    target = Target(sector=sector, ev_mm=ev_mm, ownership=ownership, geography=geography,
                    margin_profile=margin_profile)
    return ranking.rank_acquirers(target, limit=limit, excluded_keys=excluded_keys or [])
