"""Target-dependent buyer scoring. Python only, no AI.

`rank_acquirers(target)` finds every buyer with a closed deal in the target sector or an
adjacent sector, scores six signals between 0 and 1, and returns a deterministic shortlist.
Every number has a display string, and every explanation is a template filled with display
strings or whole counts, so the agent can quote it as evidence. Stated margins and multiples
are never re-derived; deals that did not close add nothing to the score.

Run `python -m ma_engine.ranking` to rank the default target and print a diagnostic table, or
`python -m ma_engine.ranking --sensitivity` for the top 10 under alternative weights, adjacency
thresholds, no small-sample prior and no coverage rule (`--sector` and `--ev` change the target).
"""

import argparse
import math
import time
from collections import Counter
from types import SimpleNamespace

import pandas as pd

from ma_engine.clients.data_client import get_client
from ma_engine.config import DEFAULT_TARGET, REPORT_COUNT, SHORTLIST_SIZE
from ma_engine.models import ANY_REGION, Target
from ma_engine.prepare import fmt_money, normalize_acquirer, round_half_up, size_band

WEIGHTS = {
    "sector_fit": 0.25,
    "size_fit": 0.20,
    "repeat_activity": 0.20,
    "profile_fit": 0.15,
    "recency": 0.10,
    "rationale_fit": 0.10,
}
SIZE_WINDOW = 2.0          # "near the target size" = within 2x of target EV, either way
SIZE_ZERO_FACTOR = 10.0    # a deal 10x larger or smaller than the target earns no size fit
REPEAT_HALF_CREDIT = 3     # weighted relevant closed deals that earn half the repeat credit
RECENCY_HALF_LIFE_YEARS = 3
RECENT_YEARS = 3
# Per-deal averages are blended with this many neutral pseudo-deals, so a buyer with one
# perfect deal does not outscore a buyer with a long, mostly fitting record.
PRIOR_DEALS = 2
PRIOR_VALUE = 0.5
SMALL_SAMPLE_NOTE = (f" The value blends these deals with {PRIOR_DEALS} neutral "
                     f"({PRIOR_VALUE:.2f}) deals so thin histories are not over-trusted.")
# Geographic Expansion is left out on purpose: it is on almost every deal, so it cannot
# separate buyers (regional fit is scored in profile_fit). Margin Improvement counts only when
# the target's margin profile is "any": a strong-margin target offers little margin upside.
RATIONALE_TAGS = ("Platform Build", "Scale", "Market Share")
MARGIN_TAG = "Margin Improvement"

# Thresholds are percentiles of this target's own candidate scores, so every target gets
# the same relative bar.
HIGH_PERCENTILE = 90
MEDIUM_PERCENTILE = 50
MIN_NEAR_SIZE_DEALS = 2    # for both High and Medium
CONVICTION_RULE = (
    f"High: score at or above the {HIGH_PERCENTILE}th percentile of this target's candidate "
    f"scores and at least {MIN_NEAR_SIZE_DEALS} relevant closed deals within {SIZE_WINDOW:g}x of "
    f"the target EV. Medium: score at or above the median ({MEDIUM_PERCENTILE}th percentile) "
    f"candidate score and at least {MIN_NEAR_SIZE_DEALS} relevant closed deals within "
    f"{SIZE_WINDOW:g}x of the target EV. Low: everything else."
)

MIN_PER_TYPE = 3
BUYER_TYPES = ("Financial Sponsor", "Strategic")
COVERAGE_RULE = (
    f"The first {REPORT_COUNT} positions hold at least {MIN_PER_TYPE} buyers of each type "
    f"({' and '.join(BUYER_TYPES)}) when the candidate pool has them. Missing-type buyers with "
    "at least one closed deal in the target sector are promoted first, best score first; only "
    "if none are left are other buyers of that type promoted, with a warning. Promoted buyers "
    "displace the lowest-scoring buyers of the other type, which move down the shortlist. The "
    f"reserve places after the first {REPORT_COUNT} hold at least one buyer of each type when "
    "the pool has one, so a failed buyer can be replaced by a buyer of the same type."
)


def _two_places(value: float) -> str:
    return f"{round_half_up(value, 2):.2f}"


def _three_places(value: float) -> str:
    return f"{round_half_up(value, 3):.3f}"


def _ids(frame: pd.DataFrame) -> list[str]:
    return sorted(frame["transaction_id"].tolist())


def _blended(total: float, n: int) -> float:
    return (total + PRIOR_DEALS * PRIOR_VALUE) / (n + PRIOR_DEALS)


# ---------- candidate pool ----------

def relevant_sectors(client, sector: str) -> dict[str, float]:
    """{sector: closeness} for the target sector and every pre-calculated adjacent sector."""
    sim = client.sector_similarity
    rows = sim[(sim["sector_a"] == sector) & sim["is_adjacent"]]
    rows = rows.sort_values(["closeness", "sector_b"], ascending=[False, True])
    return {s: float(c) for s, c in zip(rows["sector_b"], rows["closeness"])}


def _relevant_deals(client, closeness: dict[str, float]) -> pd.DataFrame:
    deals = client.transactions
    deals = deals[deals["sector"].isin(closeness) & (deals["acquirer_key"] != "")].copy()
    deals["closeness"] = deals["sector"].map(closeness)
    return deals


# ---------- signals (each returns value in [0, 1] or None, explanation, source IDs) ----------

def _sector_fit(closed: pd.DataFrame, target: Target) -> tuple:
    direct = int((closed["sector"] == target.sector).sum())
    value = _blended(float(closed["closeness"].sum()), len(closed))
    text = (f"{direct} of {len(closed)} relevant closed deals were in {target.sector}, the rest in "
            f"adjacent sectors; average sector closeness "
            f"{_two_places(closed['closeness'].mean())} before the blend below, "
            f"{_two_places(value)} after it (the value scored)." + SMALL_SAMPLE_NOTE)
    return value, text, _ids(closed)


def _size_distance(closed: pd.DataFrame, target: Target) -> pd.Series:
    sized = closed["deal_size_mm"].dropna()
    return (sized / target.ev_mm).map(math.log).abs()


def _size_fit(closed: pd.DataFrame, target: Target) -> tuple:
    distance = _size_distance(closed, target)
    if distance.empty:
        return None, "No relevant closed deal has a stated deal size, so size fit is unknown.", []
    sized = closed.loc[distance.index]
    fits = (1 - distance / math.log(SIZE_ZERO_FACTOR)).clip(lower=0)
    near = int((distance <= math.log(SIZE_WINDOW)).sum())
    text = (f"{near} of {len(sized)} relevant closed deals were within {SIZE_WINDOW:g}x of the "
            f"target's {fmt_money(target.ev_mm)} (between {fmt_money(target.ev_mm / SIZE_WINDOW)} "
            f"and {fmt_money(target.ev_mm * SIZE_WINDOW)}); median relevant closed deal size "
            f"{fmt_money(sized['deal_size_mm'].median())}. A deal {SIZE_ZERO_FACTOR:g}x larger or "
            "smaller earns no size credit." + SMALL_SAMPLE_NOTE)
    return _blended(float(fits.sum()), len(sized)), text, _ids(sized)


def _repeat_activity(closed: pd.DataFrame, target: Target) -> tuple:
    direct = int((closed["sector"] == target.sector).sum())
    weighted = float(closed["closeness"].sum())
    text = (f"{direct} direct + {len(closed) - direct} adjacent relevant closed deal(s) = "
            f"{round_half_up(weighted, 1):.1f} weighted (a {target.sector} deal counts in full, "
            "an adjacent-sector deal counts its sector closeness); "
            f"{REPEAT_HALF_CREDIT} weighted deals earn half credit, with diminishing returns "
            "after that.")
    return weighted / (weighted + REPEAT_HALF_CREDIT), text, _ids(closed)


def _recency(closed: pd.DataFrame, target: Target, latest_year: int) -> tuple:
    dated = closed[closed["deal_year"].notna()]
    if dated.empty:
        return None, "No relevant closed deal has a deal year, so recency is unknown.", []
    age = latest_year - dated["deal_year"]
    weights = 0.5 ** (age / RECENCY_HALF_LIFE_YEARS)
    first_recent = latest_year - RECENT_YEARS + 1
    recent = int((dated["deal_year"] >= first_recent).sum())
    text = (f"Most recent relevant closed deal in {int(dated['deal_year'].max())}; {recent} of "
            f"{len(dated)} were in {first_recent}–{latest_year} (the dataset ends in {latest_year}). "
            f"A deal's weight halves every {RECENCY_HALF_LIFE_YEARS} years." + SMALL_SAMPLE_NOTE)
    return _blended(float(weights.sum()), len(dated)), text, _ids(dated)


def _share(frame: pd.DataFrame, column: str, matches) -> tuple[int, int]:
    known = frame[frame[column].notna()]
    return int(matches(known[column]).sum()), len(known)


def _profile_fit(closed: pd.DataFrame, target: Target) -> tuple:
    if target.geography == ANY_REGION:
        geo_label, geo = "regional", _share(closed, "is_regional", lambda s: s.astype(bool))
    else:
        geo_label = f"in {target.geography}"
        geo = _share(closed, "geography", lambda s: s == target.geography)
    parts = [
        (f"{target.ownership} targets",
         _share(closed, "target_ownership_pre", lambda s: s == target.ownership)),
        (geo_label, geo),
    ]
    if target.margin_profile == "strong":
        parts.append(("above their sector's median EBITDA margin",
                      _share(closed, "margin_vs_sector", lambda s: s == "above")))
    shares = [_blended(matched, known) for _, (matched, known) in parts if known]
    text = f"Of {len(closed)} relevant closed deals: " + "; ".join(
        f"{matched} of {known} {label}" for label, (matched, known) in parts) + "."
    return (sum(shares) / len(shares) if shares else None), text + SMALL_SAMPLE_NOTE, _ids(closed)


def rationale_tags(target: Target) -> tuple[str, ...]:
    return RATIONALE_TAGS + ((MARGIN_TAG,) if target.margin_profile == "any" else ())


def _rationale_fit(closed: pd.DataFrame, target: Target) -> tuple:
    wanted = rationale_tags(target)
    tags = closed["rationale_tags"]
    tagged = closed[tags.map(lambda t: any(tag in wanted for tag in t))]
    counts = Counter(tag for t in tags for tag in t if tag in wanted)
    detail = ", ".join(f"{tag} {counts[tag]}" for tag in wanted)
    text = (f"{len(tagged)} of {len(closed)} relevant closed deals carry at least one of "
            f"{', '.join(wanted)} ({detail})." + SMALL_SAMPLE_NOTE)
    return _blended(len(tagged), len(closed)), text, _ids(tagged)


def _signal(name: str, value, text: str, source_ids: list[str]) -> dict:
    weight = WEIGHTS[name]
    contribution = 0.0 if value is None else value * weight
    if value is None:
        text += " Unknown, so it adds nothing to the score."
    return {
        "name": name,
        "value": None if value is None else round_half_up(value, 4),
        "value_display": None if value is None else _two_places(value),
        "weight": weight,
        "weight_display": _two_places(weight),
        "contribution": round_half_up(contribution, 4),
        "contribution_display": _two_places(contribution),
        "explanation": text,
        "source_ids": source_ids,
        "_raw": contribution,
    }


# ---------- per buyer ----------

def conviction_thresholds(buyers: list[dict]) -> dict:
    """High and Medium score bars: percentiles of this target's candidate scores."""
    scores = pd.Series([b["score"]["value"] for b in buyers], dtype=float)
    out = {}
    for level, pct in [("high", HIGH_PERCENTILE), ("medium", MEDIUM_PERCENTILE)]:
        value = float(scores.quantile(pct / 100))
        out[level] = {"percentile": pct, "value": round_half_up(value, 4),
                      "display": _three_places(value), "candidates": len(scores)}
    return out


def _conviction(buyer: dict, thresholds: dict) -> dict:
    score = buyer["score"]["value"]
    near = buyer["near_size_closed_count"]["value"]
    high, medium = thresholds["high"], thresholds["medium"]
    facts = (f"Score {_three_places(score)}; {near} relevant closed deal(s) within "
             f"{SIZE_WINDOW:g}x of the target EV. High bar {high['display']} "
             f"({HIGH_PERCENTILE}th percentile of {high['candidates']} candidates), Medium bar "
             f"{medium['display']} (median); both need {MIN_NEAR_SIZE_DEALS} deals near the "
             "target size.")
    near_enough = near >= MIN_NEAR_SIZE_DEALS
    if score >= high["value"] and near_enough:
        level = "High"
    elif score >= medium["value"] and near_enough:
        level = "Medium"
    else:
        level = "Low"
    return {"level": level, "rule": CONVICTION_RULE, "reason": facts}


def _warnings(closed: pd.DataFrame, not_closed: pd.DataFrame, target: Target, profile: dict,
              near_count: int, signals: list[dict]) -> list[str]:
    warnings = []
    if len(closed) == 1:
        warnings.append(f"Only 1 relevant closed deal ({closed['transaction_id'].iloc[0]}); "
                        "one deal is an example, not a pattern.")
    if not (closed["sector"] == target.sector).any():
        warnings.append(f"No closed deal in {target.sector}; fit rests on adjacent sectors only.")
    if near_count == 0:
        warnings.append(f"No relevant closed deal within {SIZE_WINDOW:g}x of the target EV.")
    for column, label in [("ev_ebitda_multiple", "EV/EBITDA"), ("ev_revenue_multiple", "EV/Revenue")]:
        missing = closed[closed[column].isna()]
        if len(missing):
            warnings.append(f"{len(missing)} relevant closed deal(s) have no stated {label} "
                            f"multiple: {', '.join(_ids(missing))}.")
    if len(profile["acquirer_types_seen"]) > 1:
        warnings.append(f"Buyer appears with more than one type: {profile['acquirer_types_seen']}.")
    if len(not_closed):
        warnings.append(f"{len(not_closed)} relevant deal(s) did not close "
                        f"({', '.join(_ids(not_closed))}); listed as weak signals, not scored. "
                        "Never describe them as acquired or completed.")
    warnings += [f"{s['name']} is unknown for this buyer." for s in signals if s["value"] is None]
    return warnings


def _score_buyer(key: str, deals: pd.DataFrame, target: Target, profile: dict,
                 latest_year: int) -> dict:
    closed = deals[deals["is_closed"].astype(bool)]
    not_closed = deals[~deals["is_closed"].astype(bool)]
    signals = [
        _signal("sector_fit", *_sector_fit(closed, target)),
        _signal("size_fit", *_size_fit(closed, target)),
        _signal("repeat_activity", *_repeat_activity(closed, target)),
        _signal("profile_fit", *_profile_fit(closed, target)),
        _signal("recency", *_recency(closed, target, latest_year)),
        _signal("rationale_fit", *_rationale_fit(closed, target)),
    ]
    score = sum(s.pop("_raw") for s in signals)
    near = int((_size_distance(closed, target) <= math.log(SIZE_WINDOW)).sum())
    direct = int((closed["sector"] == target.sector).sum())
    return {
        "rank": None,
        "score_rank": None,
        "selection_note": None,
        "acquirer_key": key,
        "display_name": profile["display_name"],
        "acquirer_type": profile["acquirer_type"],
        "score": {"value": round_half_up(score, 4), "display": _two_places(score)},
        "relevant_closed_count": {"value": len(closed), "display": str(len(closed))},
        "direct_closed_count": {"value": direct, "display": str(direct)},
        "near_size_closed_count": {"value": near, "display": str(near)},
        "signals": signals,
        "starting_conviction": None,
        "supporting_closed_ids": _ids(closed),
        "weak_signal_ids": _ids(not_closed),
        "warnings": _warnings(closed, not_closed, target, profile, near, signals),
    }


def score_candidates(target: Target, client=None) -> list[dict]:
    """Every candidate buyer, scored, with conviction, in score order (nothing excluded)."""
    client = client or get_client()
    deals = _relevant_deals(client, relevant_sectors(client, target.sector))
    latest_year = int(client.transactions["deal_year"].max())
    buyers = []
    for key, group in deals.groupby("acquirer_key", sort=True):
        if not group["is_closed"].astype(bool).any():
            continue
        buyers.append(_score_buyer(key, group, target, client.acquirer_profiles[key], latest_year))
    buyers.sort(key=lambda b: (-b["score"]["value"], -b["relevant_closed_count"]["value"],
                               b["acquirer_key"]))
    if buyers:
        thresholds = conviction_thresholds(buyers)
        for buyer in buyers:
            buyer["starting_conviction"] = _conviction(buyer, thresholds)
    return buyers


# ---------- buyer-type coverage ----------

def _has_direct(buyer: dict) -> bool:
    return buyer["direct_closed_count"]["value"] > 0


def _promotion_order(pool: list[dict]) -> list[dict]:
    """Buyers with a closed target-sector deal first, then the rest; each group by score."""
    return sorted(pool, key=lambda b: (not _has_direct(b), b["score_rank"]))


def _direct_note(buyer: dict, sector: str) -> str:
    if _has_direct(buyer):
        return f"It has {buyer['direct_closed_count']['display']} closed {sector} deal(s)."
    return (f"It has no closed {sector} deal; no {buyer['acquirer_type']} candidate with one "
            "was left.")


def _fallback_warning(buyers: list[dict], buyer_type: str, sector: str, place: str) -> list[str]:
    names = [b["display_name"] for b in buyers if not _has_direct(b)]
    if not names:
        return []
    return [f"Not enough {buyer_type} candidates have a closed {sector} deal, so {len(names)} "
            f"{buyer_type} buyer(s) without one were added {place} on score alone: "
            f"{', '.join(names)}."]


def _cover_reserves(reserves: list[dict], beyond: list[dict], total: int,
                    sector: str) -> tuple[list[dict], list[str], list[str]]:
    """Reserves holding at least one buyer of each type when one exists beyond them.

    A missing type's best candidate (direct deals first) replaces the lowest-scoring reserve
    of a type that has more than one reserve. Returns the reserves, added keys, and warnings.
    """
    added, warnings = [], []
    for buyer_type in BUYER_TYPES:
        counts = Counter(b["acquirer_type"] for b in reserves)
        if counts[buyer_type]:
            continue
        pool = _promotion_order([b for b in beyond if b["acquirer_type"] == buyer_type])
        removable = [b for b in reserves if counts[b["acquirer_type"]] > 1]
        if not pool or not removable:
            continue
        incoming = pool[0]
        outgoing = max(removable, key=lambda b: b["score_rank"])
        reserves = [incoming if b is outgoing else b for b in reserves]
        incoming["selection_note"] = (
            f"Added as a reserve so a failed {buyer_type} buyer can be replaced by one of the same "
            f"type; score rank {incoming['score_rank']} of {total} candidates. "
            + _direct_note(incoming, sector))
        added.append(incoming["acquirer_key"])
        warnings += _fallback_warning([incoming], buyer_type, sector, "as a reserve")
    return sorted(reserves, key=lambda b: b["score_rank"]), added, warnings


def apply_type_coverage(ranked: list[dict], limit: int,
                        sector: str) -> tuple[list[dict], dict, list[str]]:
    """Final order: at least MIN_PER_TYPE of each buyer type in the first REPORT_COUNT places,
    and at least one of each type among the reserves after them.

    `ranked` is in score order with `score_rank` set. Missing-type buyers with a closed deal in
    `sector` are promoted first. Returns the shortlist (`limit` long), the promoted, displaced
    and reserve-added keys, and warnings when the pool lacks a type or the guard falls back.
    """
    head_size = min(REPORT_COUNT, limit)
    head, rest = ranked[:head_size], ranked[head_size:]
    promoted, displaced, warnings = [], [], []
    for buyer_type in BUYER_TYPES:
        have = [b for b in head if b["acquirer_type"] == buyer_type]
        need = MIN_PER_TYPE - len(have)
        if need <= 0:
            continue
        pool = _promotion_order([b for b in rest if b["acquirer_type"] == buyer_type])
        others = sorted((b for b in head if b["acquirer_type"] != buyer_type),
                        key=lambda b: -b["score_rank"])
        moves = min(need, len(pool), max(0, len(others) - MIN_PER_TYPE))
        if moves < need:
            warnings.append(f"Only {len(have) + moves} {buyer_type} buyer(s) in the first "
                            f"{head_size}: the candidate pool has {len(have) + len(pool)} in "
                            f"total, so the {MIN_PER_TYPE}-per-type rule cannot be met.")
        warnings += _fallback_warning(pool[:moves], buyer_type, sector,
                                      f"to the top {head_size}")
        for incoming, outgoing in zip(pool[:moves], others[:moves]):
            head.remove(outgoing)
            head.append(incoming)
            rest.remove(incoming)
            rest.append(outgoing)
            incoming["selection_note"] = (
                f"Included to ensure at least {MIN_PER_TYPE} {buyer_type} buyers in the top "
                f"{head_size}; score rank {incoming['score_rank']} of {len(ranked)} candidates. "
                + _direct_note(incoming, sector))
            outgoing["selection_note"] = (
                f"Moved below the top {head_size} to make room for {buyer_type} buyers; score "
                f"rank {outgoing['score_rank']} of {len(ranked)} candidates.")
            promoted.append(incoming["acquirer_key"])
            displaced.append(outgoing["acquirer_key"])
    head.sort(key=lambda b: b["score_rank"])
    rest.sort(key=lambda b: b["score_rank"])
    slots = limit - head_size
    reserves, added, reserve_warnings = _cover_reserves(rest[:slots], rest[slots:], len(ranked),
                                                        sector)
    coverage = {"promoted_keys": promoted, "displaced_keys": displaced,
                "reserve_added_keys": added}
    return head + reserves, coverage, warnings + reserve_warnings


# ---------- public entry point ----------

def _check_excluded(client, excluded_keys) -> list[str]:
    keys = sorted({normalize_acquirer(k) for k in excluded_keys})
    unknown = [k for k in keys if k not in client.acquirer_profiles]
    if unknown:
        raise ValueError(f"Unknown acquirer_key(s) in excluded_keys: {', '.join(unknown)}.")
    return keys


def _target_echo(client, target: Target, closeness: dict[str, float]) -> dict:
    stats = client.sector_stats
    row = stats[stats["sector"] == target.sector].iloc[0]
    return {
        **target.model_dump(),
        "ev_mm": {"value": target.ev_mm, "display": fmt_money(target.ev_mm)},
        "size_band": size_band(target.ev_mm),
        "strong_margin_threshold": {
            "definition": f"Above the {target.sector} median EBITDA margin (all deals, any outcome).",
            "value": float(row["ebitda_margin_median"]),
            "display": row["ebitda_margin_median_display"],
            "n": int(row["ebitda_margin_count"]),
        },
        "relevant_sectors": [{"sector": s, "closeness": c, "closeness_display": f"{c:g}"}
                             for s, c in closeness.items()],
    }


def _type_mix(buyers: list[dict]) -> dict:
    return dict(sorted(Counter(b["acquirer_type"] for b in buyers).items()))


def rank_acquirers(target: Target, limit: int = SHORTLIST_SIZE, excluded_keys=(),
                   client=None) -> dict:
    """Ranked shortlist for the target, in the {data, source_ids, warnings} tool shape."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit must be a whole number of at least 1; got {limit!r}.")
    client = client or get_client()
    excluded = _check_excluded(client, excluded_keys)
    candidates = score_candidates(target, client)
    kept = [b for b in candidates if b["acquirer_key"] not in excluded]
    for score_rank, buyer in enumerate(kept, start=1):
        buyer["score_rank"] = score_rank
    buyers, coverage, coverage_warnings = apply_type_coverage(kept, limit, target.sector)
    for rank, buyer in enumerate(buyers, start=1):
        buyer["rank"] = rank
    warnings = ["Scores rank buyers within this dataset only; they are not probabilities.",
                "Buyer-type mix follows the dataset, which has more Financial Sponsor deals; "
                "it is not evidence that one type is a better buyer.", *coverage_warnings]
    if len(buyers) < limit:
        warnings.append(f"Only {len(buyers)} candidate buyer(s) available for limit {limit}.")
    data = {
        "target": _target_echo(client, target, relevant_sectors(client, target.sector)),
        "candidate_count": len(candidates),
        "excluded_keys": excluded,
        "returned": len(buyers),
        "buyer_type_mix": _type_mix(buyers),
        f"buyer_type_mix_top_{REPORT_COUNT}": _type_mix(buyers[:REPORT_COUNT]),
        "weights": WEIGHTS,
        "conviction_rule": CONVICTION_RULE,
        "conviction_thresholds": conviction_thresholds(candidates) if candidates else None,
        "coverage_rule": COVERAGE_RULE,
        **coverage,
        "buyers": buyers,
    }
    source_ids = sorted({i for b in buyers for i in b["supporting_closed_ids"] + b["weak_signal_ids"]})
    return {"data": data, "source_ids": source_ids, "warnings": warnings}


# ---------- sensitivity ----------

def _variants(client) -> list[tuple[str, dict, object]]:
    """(label, module constants to override, client) for each sensitivity case."""
    def renormalised(dropped: str) -> dict:
        rest = 1 - WEIGHTS[dropped]
        return {k: (0.0 if k == dropped else w / rest) for k, w in WEIGHTS.items()}

    def adjacency(threshold: float):
        sim = client.sector_similarity.copy()
        sim["is_adjacent"] = sim["closeness"] >= threshold
        return SimpleNamespace(transactions=client.transactions, sector_similarity=sim,
                               sector_stats=client.sector_stats,
                               acquirer_profiles=client.acquirer_profiles)

    return [("base", {}, client),
            ("equal weights", {"WEIGHTS": {k: 1 / len(WEIGHTS) for k in WEIGHTS}}, client),
            *[(f"drop {name}", {"WEIGHTS": renormalised(name)}, client) for name in WEIGHTS],
            *[(f"adjacency {t:g}", {}, adjacency(t)) for t in (0.5, 0.75)],
            ("prior off", {"PRIOR_DEALS": 0}, client),
            ("coverage rule off", {"MIN_PER_TYPE": 0}, client)]


def sensitivity(target: Target, client=None) -> list[dict]:
    """Top REPORT_COUNT under each variant, with its overlap with the base top REPORT_COUNT.

    Each variant re-runs `rank_acquirers` with some module constants temporarily replaced
    (restored afterwards) or with adjacency recomputed in memory from the closeness scores;
    no file is changed. A dropped signal gets weight 0 and the others are scaled to sum to 1.
    """
    client = client or get_client()
    rows, names, base = [], {}, None
    for label, overrides, variant_client in _variants(client):
        saved = {name: globals()[name] for name in overrides}
        globals().update(overrides)
        try:
            top = rank_acquirers(target, client=variant_client)["data"]["buyers"][:REPORT_COUNT]
        finally:
            globals().update(saved)
        names.update({b["acquirer_key"]: b["display_name"] for b in top})
        keys = [b["acquirer_key"] for b in top]
        base = base or keys
        rows.append({"variant": label, "top": [names[k] for k in keys],
                     "overlap": f"{len(set(keys) & set(base))}/{len(base)}",
                     "names_in": [names[k] for k in keys if k not in base],
                     "names_out": [names[k] for k in base if k not in keys]})
    return rows


def print_sensitivity(target: Target) -> None:
    print(f"Target: {target.model_dump()}")
    rows = sensitivity(target)
    print("Base top 10: " + ", ".join(rows[0]["top"]))
    print("| Variant | Overlap with base | Names in | Names out |")
    print("|---|---|---|---|")
    for row in rows[1:]:
        print(f"| {row['variant']} | {row['overlap']} | {', '.join(row['names_in']) or '-'} | "
              f"{', '.join(row['names_out']) or '-'} |")


def main(target: Target | None = None) -> None:
    target = target or Target.default()
    started = time.perf_counter()
    result = rank_acquirers(target)
    elapsed = time.perf_counter() - started
    everyone = score_candidates(target)
    scores = pd.Series([b["score"]["value"] for b in everyone])
    data = result["data"]
    print(f"Target: {target.model_dump()}")
    print(f"Candidates: {data['candidate_count']}  Time to rank: {elapsed * 1000:.0f} ms")
    print(f"Score spread (all candidates): min {scores.min():.2f}  p25 {scores.quantile(.25):.2f}  "
          f"median {scores.median():.2f}  p75 {scores.quantile(.75):.2f}  max {scores.max():.2f}")
    print(f"Conviction (all candidates): {_levels(everyone)}")
    bars = data["conviction_thresholds"]
    print(f"Conviction bars: High {bars['high']['display']} (p{HIGH_PERCENTILE}), "
          f"Medium {bars['medium']['display']} (p{MEDIUM_PERCENTILE}), "
          f"both need {MIN_NEAR_SIZE_DEALS} deals within {SIZE_WINDOW:g}x")
    shortlist, top = data["buyers"], data["buyers"][:REPORT_COUNT]
    print(f"Buyer-type mix top {len(shortlist)}: {data['buyer_type_mix']}  "
          f"top {REPORT_COUNT}: {_type_mix(top)}")
    print(f"Conviction top {len(shortlist)}: {_levels(shortlist)}  top {REPORT_COUNT}: {_levels(top)}")
    print(f"Promoted: {data['promoted_keys']}  Displaced: {data['displaced_keys']}  "
          f"Reserves added: {data['reserve_added_keys']}")
    print(f"{'#':>2} {'SR':>3}  {'Buyer':<26} {'Type':<17} {'Score':>5}  {'Conv.':<6} {'Rel':>3} "
          f"{'Near':>4}  " + " ".join(f"{name[:6]:>6}" for name in WEIGHTS))
    for b in shortlist:
        values = {s["name"]: s["value_display"] or "-" for s in b["signals"]}
        print(f"{b['rank']:>2} {b['score_rank']:>3}  {b['display_name']:<26} {b['acquirer_type']:<17} "
              f"{b['score']['display']:>5}  {b['starting_conviction']['level']:<6} "
              f"{b['relevant_closed_count']['value']:>3} {b['near_size_closed_count']['value']:>4}  "
              + " ".join(f"{values[name]:>6}" for name in WEIGHTS))
        if b["selection_note"]:
            print(f"{'':>8}note: {b['selection_note']}")


def _levels(buyers: list[dict]) -> dict:
    counts = Counter(b["starting_conviction"]["level"] for b in buyers)
    return {level: counts[level] for level in ("High", "Medium", "Low")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rank the default target (diagnostic table).")
    parser.add_argument("--sensitivity", action="store_true",
                        help="Print the top 10 under each weight, adjacency, prior and coverage "
                             "variant, with the overlap with the base top 10.")
    parser.add_argument("--sector", default=DEFAULT_TARGET["sector"])
    parser.add_argument("--ev", type=float, default=DEFAULT_TARGET["ev_mm"])
    args = parser.parse_args()
    chosen = Target(**{**DEFAULT_TARGET, "sector": args.sector, "ev_mm": args.ev})
    print_sensitivity(chosen) if args.sensitivity else main(chosen)
