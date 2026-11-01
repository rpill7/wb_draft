"""Loads every file in `data/processed/` once and offers small lookups and row filters.

No statistics are calculated here: every aggregate comes from the files written by
`prepare.py`. Display strings for single deal rows use the formatting helpers in
`prepare.py`, so there is one formatter for the whole project.

Statistic registry
------------------
`get_client().stat_record(stat_id)` looks up one pre-calculated statistic. It does
no new math. The id is stable across runs because it is built from the statistic's
place in the processed tables:

- `ST-COMPS-<sector>-<scope>-<size band>-<metric>-<stat>`
  for example `ST-COMPS-healthcare-services-sector_only-100-400-ev_ebitda-median`
- `ST-SECTOR-<sector>-<field>` for a sector-overview statistic
- `ST-PROFILE-<acquirer key>-<field>` for a buyer-profile statistic
- `ST-RANK-<acquirer key>-<signal>` for a ranking signal. Those depend on the
  target, so they are stored by `note_ranking(ranking)` from `ranking.json`
  (the engine calls this when it ranks and again when it builds `sources.json`).

The record is `{stat_id, calculation, filters, n, value, display, source_ids}`.
`display` is the string a report must quote. `n` is the sample size stored with
the statistic (`None` when the table does not store one). `filters` names the
sector, scope, size band, buyer and closed-only flag that define the statistic.

`key_metrics(acquirer_key)` is the closed-deal profile statistics for the Key
metrics card, in card order, each a full record. The same records are on
`get_acquirer_profile` under `data["key_metrics"]`.
"""

import copy
import json
import re
from functools import cache
from pathlib import Path

import pandas as pd

from ma_engine.config import (
    ACQUIRER_PROFILES_JSON,
    DATA_QUALITY_JSON,
    SECTOR_SIMILARITY_CSV,
    SECTOR_STATS_CSV,
    TRANSACTIONS_CSV,
    VALUATION_COMPS_CSV,
)
from ma_engine.prepare import fmt_money, fmt_multiple, fmt_pct

# Columns written by prepare.py as JSON lists inside CSV cells.
LIST_COLUMNS = {
    "transactions": ["rationale_tags"],
    "valuation_comps": ["sectors_included", "ev_ebitda_source_ids", "ev_revenue_source_ids",
                        "source_ids"],
    "sector_stats": ["top_deal_types", "source_ids", "closed_source_ids",
                     "financial_sponsor_source_ids"],
}


def _read_csv(path: Path, list_columns: list[str]) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Processed file not found: {path}. Run `make data` first.")
    # Only empty cells are missing; text such as "NA" stays text.
    frame = pd.read_csv(path, keep_default_na=False, na_values=[""])
    for column in list_columns:
        frame[column] = frame[column].map(lambda text: json.loads(text) if isinstance(text, str) else [])
    return frame


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Processed file not found: {path}. Run `make data` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _native(value):
    """NaN becomes None so results are valid JSON."""
    return None if value is None or (isinstance(value, float) and pd.isna(value)) else value


def _number(value, fmt) -> dict:
    value = _native(value)
    return {"value": value, "display": fmt(value) if value is not None else None}


def _whole(value) -> str:
    return str(int(value))


def _records(frame: pd.DataFrame) -> list[dict]:
    return [{k: _native(v) for k, v in row.items()} for row in frame.to_dict("records")]


# Size-band slugs are fixed so the example id in the plan stays stable.
BAND_SLUG = {
    "Under $100M": "under-100",
    "$100M–$400M": "100-400",
    "$400M–$1B": "400-1b",
    "Over $1B": "over-1b",
    "All sizes": "all-sizes",
}
_PERCENTILE = {"p25": "25th percentile", "median": "median", "p75": "75th percentile"}


def slug(text: str) -> str:
    """Lower case, with spaces and punctuation collapsed to single hyphens."""
    text = str(text).lower().replace("–", " ").replace("—", " ").replace("&", " ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def comps_stat_id(sector: str, scope: str, size_band: str, metric: str, stat: str) -> str:
    """Id of one valuation-comps statistic. `metric` is ev_ebitda, ev_revenue or ebitda_margin."""
    return f"ST-COMPS-{slug(sector)}-{scope}-{BAND_SLUG[size_band]}-{metric}-{stat}"


def sector_stat_id(sector: str, *parts: str) -> str:
    tail = "-".join(slug(part) for part in parts)
    return f"ST-SECTOR-{slug(sector)}-{tail}"


def profile_stat_id(acquirer_key: str, *parts: str) -> str:
    tail = "-".join(slug(part) for part in parts)
    return f"ST-PROFILE-{slug(acquirer_key)}-{tail}"


def rank_stat_id(acquirer_key: str, name: str) -> str:
    """Id of one ranking signal, for example `ST-RANK-blackstone-size_fit`."""
    return f"ST-RANK-{slug(acquirer_key)}-{name}"


def _record(stat_id: str, calculation: str, filters: dict, n, value, display,
            source_ids) -> dict:
    return {
        "stat_id": stat_id,
        "calculation": calculation,
        "filters": filters,
        "n": None if n is None else int(n),
        "value": _native(value),
        "display": display,
        "source_ids": list(source_ids or []),
    }


def _as_record(stat: dict, stat_id: str, calculation: str, filters: dict) -> dict | None:
    """A registry record from a stored stat dict. `None` when there is no display string."""
    display = stat.get("display")
    if not display:
        return None
    if "n" in stat and stat["n"] is not None:
        n = stat["n"]
    elif "year" in calculation:
        n = None
    elif isinstance(stat.get("value"), int) and not isinstance(stat.get("value"), bool) \
            and display == str(stat["value"]):
        n = stat["value"]
    else:
        n = None
    return _record(stat_id, calculation, filters, n, stat.get("value"), display,
                   stat.get("source_ids") or [])


def _profile_nodes(profile: dict):
    """Each stored profile statistic: (dict, stat_id, calculation, filters, key metric?)."""
    key = profile["acquirer_key"]
    any_deal = {"acquirer_key": key, "closed_only": False}
    closed = {"acquirer_key": key, "closed_only": True}
    yield profile["deal_count"], profile_stat_id(key, "deal_count"), "count of deals", any_deal, False
    yield (profile["closed_count"], profile_stat_id(key, "closed_count"),
           "count of closed deals", closed, True)
    for outcome, stat in profile["non_closed_by_outcome"].items():
        yield (stat, profile_stat_id(key, "non_closed", outcome), f"count of {outcome} deals",
               {**any_deal, "outcome": outcome}, False)
    for sector, stat in profile["closed_by_sector"].items():
        yield (stat, profile_stat_id(key, "sector", sector),
               f"count of closed deals in {sector}", {**closed, "sector": sector}, False)
    for deal_type, stat in profile["closed_by_deal_type"].items():
        yield (stat, profile_stat_id(key, "deal_type", deal_type),
               f"count of closed {deal_type} deals", {**closed, "deal_type": deal_type}, False)
    for geo, stat in profile["closed_by_geography"].items():
        yield (stat, profile_stat_id(key, "geography", geo),
               f"count of closed deals in {geo}", {**closed, "geography": geo}, False)
    for which, calculation in (
            ("median", "median of closed deal size"),
            ("smallest", "smallest closed deal size"),
            ("largest", "largest closed deal size")):
        yield (profile["closed_deal_size"][which], profile_stat_id(key, "closed_deal_size", which),
               calculation, closed, True)
    yield (profile["closed_ev_ebitda_median"], profile_stat_id(key, "closed_ev_ebitda_median"),
           "median of EV/EBITDA on closed deals", closed, True)
    yield (profile["closed_ev_revenue_median"], profile_stat_id(key, "closed_ev_revenue_median"),
           "median of EV/Revenue on closed deals", closed, True)
    yield (profile["closed_private_target_count"],
           profile_stat_id(key, "closed_private_target_count"),
           "count of closed deals with a private target", closed, True)
    for financing, stat in profile["closed_financing_mix"].items():
        yield (stat, profile_stat_id(key, "financing", financing),
               f"count of closed deals financed by {financing}",
               {**closed, "financing_type": financing}, False)
    yield (profile["closed_median_bidders"], profile_stat_id(key, "closed_median_bidders"),
           "median bidders on closed deals", closed, True)
    yield (profile["closed_median_days_to_close"],
           profile_stat_id(key, "closed_median_days_to_close"),
           "median days to close on closed deals", closed, True)
    yield (profile["first_deal_year"], profile_stat_id(key, "first_deal_year"),
           "year of the earliest deal", any_deal, False)
    yield (profile["last_deal_year"], profile_stat_id(key, "last_deal_year"),
           "year of the latest deal", any_deal, False)
    for tag in profile["top_rationale_tags_all_deals"]:
        yield (tag, profile_stat_id(key, "tag", tag["tag"]),
               f"count of deals tagged {tag['tag']}", {**any_deal, "tag": tag["tag"]}, False)


def _comps_records(row: dict) -> list[dict]:
    """Registry records for one valuation-comps row. Empty displays are left out."""
    sector, scope, band = row["sector"], row["scope"], row["size_band"]
    base = {"sector": sector, "scope": scope, "size_band": band, "closed_only": True}
    records = []
    for metric, label in (("ev_ebitda", "EV/EBITDA"), ("ev_revenue", "EV/Revenue")):
        n, ids = row[f"{metric}_count"], row[f"{metric}_source_ids"]
        filters = {**base, "metric": metric}
        for name in ("p25", "median", "p75"):
            record = _record(
                comps_stat_id(sector, scope, band, metric, name),
                f"{_PERCENTILE[name]} of {label}", filters, n, row[f"{metric}_{name}"],
                row[f"{metric}_{name}_display"], ids)
            if record["display"]:
                records.append(record)
        records.append(_record(
            comps_stat_id(sector, scope, band, metric, "n"), f"sample size of {label}",
            filters, n, n, str(int(n)), ids))
    margin_filters = {**base, "metric": "ebitda_margin"}
    margin = _record(
        comps_stat_id(sector, scope, band, "ebitda_margin", "median"),
        "median of EBITDA margin", margin_filters, row["ebitda_margin_count"],
        row["ebitda_margin_median"], row["ebitda_margin_median_display"], row["source_ids"])
    if margin["display"]:
        records.append(margin)
    return records


def _point(record: dict | None, value, display) -> dict:
    """A tool field. The id, n and source ids are attached only when a display exists."""
    if record is None:
        return {"value": value, "display": display}
    return {"value": record["value"], "display": record["display"], "n": record["n"],
            "stat_id": record["stat_id"], "source_ids": record["source_ids"]}


class DataClient:
    """In-memory view of the processed data. Create once and reuse."""

    def __init__(self):
        self.transactions = _read_csv(TRANSACTIONS_CSV, LIST_COLUMNS["transactions"])
        self.valuation_comps = _read_csv(VALUATION_COMPS_CSV, LIST_COLUMNS["valuation_comps"])
        self.sector_stats = _read_csv(SECTOR_STATS_CSV, LIST_COLUMNS["sector_stats"])
        self.sector_similarity = _read_csv(SECTOR_SIMILARITY_CSV, [])
        self.acquirer_profiles = _read_json(ACQUIRER_PROFILES_JSON)
        self.data_quality = _read_json(DATA_QUALITY_JSON)
        self.transaction_ids = set(self.transactions["transaction_id"])
        self._stats: dict[str, dict] = {}
        self._key_metric_ids: dict[str, list[str]] = {}
        self._ranking_stats: dict[str, dict] = {}
        self._index_tables()

    def acquirer_keys(self) -> list[str]:
        return sorted(self.acquirer_profiles)

    def profile(self, acquirer_key: str) -> dict | None:
        return self.acquirer_profiles.get(acquirer_key)

    def sector_stats_row(self, sector: str) -> dict | None:
        rows = _records(self.sector_stats[self.sector_stats["sector"] == sector])
        return rows[0] if rows else None

    def similar_sectors(self, sector: str) -> list[dict]:
        """Every other sector with its hand-written closeness score, closest first."""
        sim = self.sector_similarity
        rows = sim[(sim["sector_a"] == sector) & (sim["sector_b"] != sector)]
        rows = rows.sort_values(["closeness", "sector_b"], ascending=[False, True])
        return _records(rows)

    def comps_row(self, sector: str, scope: str, size_band: str) -> dict | None:
        comps = self.valuation_comps
        match = comps[
            (comps["sector"] == sector) & (comps["scope"] == scope) & (comps["size_band"] == size_band)
        ]
        rows = _records(match)
        return rows[0] if rows else None

    def filter_deals(self, acquirer_key: str | None = None, sectors: list[str] | None = None,
                     outcomes: list[str] | None = None, size_band: str | None = None,
                     acquirer_type: str | None = None, deal_types: list[str] | None = None,
                     geography: str | None = None) -> pd.DataFrame:
        """Matching transaction rows, newest first (ties by transaction_id). None = no filter."""
        deals = self.transactions
        mask = pd.Series(True, index=deals.index)
        if acquirer_key is not None:
            mask &= deals["acquirer_key"] == acquirer_key
        if sectors:
            mask &= deals["sector"].isin(sectors)
        if outcomes:
            mask &= deals["outcome"].isin(outcomes)
        if size_band is not None:
            mask &= deals["size_band"] == size_band
        if acquirer_type is not None:
            mask &= deals["acquirer_type"] == acquirer_type
        if deal_types:
            mask &= deals["deal_type"].isin(deal_types)
        if geography is not None:
            mask &= deals["geography"] == geography
        return deals[mask].sort_values(
            ["deal_year", "deal_quarter", "transaction_id"], ascending=[False, False, True]
        )

    @staticmethod
    def deal_rows(frame: pd.DataFrame) -> list[dict]:
        """Key columns only. Stated margins and multiples are quoted as given, never re-derived."""
        out = []
        for row in _records(frame):
            out.append({
                "transaction_id": row["transaction_id"],
                "target_company": row["target_company"],
                "acquirer": row["acquirer"],
                "acquirer_key": row["acquirer_key"],
                "acquirer_type": row["acquirer_type"],
                "sector": row["sector"],
                "deal_year": row["deal_year"],
                "deal_quarter": row["deal_quarter"],
                "deal_type": row["deal_type"],
                "geography": row["geography"],
                "outcome": row["outcome"],
                "is_closed": row["is_closed"],
                "deal_size_mm": _number(row["deal_size_mm"], fmt_money),
                "ev_ebitda_multiple": _number(row["ev_ebitda_multiple"], fmt_multiple),
                "ev_revenue_multiple": _number(row["ev_revenue_multiple"], fmt_multiple),
                "ebitda_margin_pct": _number(row["ebitda_margin_pct"], fmt_pct),
                "target_ownership_pre": row["target_ownership_pre"],
                "financing_type": row["financing_type"],
                "num_bidders": _number(row["num_bidders"], _whole),
                "days_to_close": _number(row["days_to_close"], _whole),
                "rationale_tags": row["rationale_tags"],
                "size_band": row["size_band"],
            })
        return out

    def deal_record(self, transaction_id: str) -> dict | None:
        """The full processed CSV row for one deal, or None if the id is unknown."""
        rows = self.transactions[self.transactions["transaction_id"] == transaction_id]
        if rows.empty:
            return None
        return {key: _native(value) for key, value in rows.iloc[0].to_dict().items()}

    def stat_record(self, stat_id: str) -> dict | None:
        """One statistic by id, or None. Ranking ids resolve after `note_ranking`."""
        if stat_id in self._ranking_stats:
            return self._ranking_stats[stat_id]
        return self._stats.get(stat_id)

    def note_run_record(self, record: dict) -> None:
        """Register separately identified, immutable original-run metadata."""
        self._stats[record["stat_id"]] = record

    def note_ranking(self, ranking: dict) -> None:
        """Replace ranking-stat records from one `rank_acquirers` data dict.

        Ranking numbers depend on the target, so they are not in the processed
        tables. `sources.json` rebuilds them from `ranking.json` through this call.
        """
        found = {}
        for buyer in ranking.get("buyers") or []:
            key = buyer["acquirer_key"]
            for signal in buyer.get("signals") or []:
                sid = signal.get("stat_id") or rank_stat_id(key, signal["name"])
                found[sid] = _record(
                    sid, signal.get("explanation") or signal["name"],
                    {"acquirer_key": key, "signal": signal["name"]},
                    len(signal.get("source_ids") or []), signal.get("value"),
                    signal.get("value_display"), signal.get("source_ids") or [])
            for name, calculation, ids in (
                    ("score", "blended score of the six signals", []),
                    ("relevant_closed_count", "count of relevant closed deals",
                     buyer.get("supporting_closed_ids") or []),
                    ("direct_closed_count", "count of closed deals in the target sector",
                     buyer.get("supporting_closed_ids") or []),
                    ("near_size_closed_count",
                     "count of relevant closed deals within 2x of the target enterprise value",
                     buyer.get("supporting_closed_ids") or [])):
                stat = buyer.get(name)
                if not isinstance(stat, dict) or not stat.get("display"):
                    continue
                sid = stat.get("stat_id") or rank_stat_id(key, name)
                n = None if name == "score" else stat.get("value")
                filters = {"acquirer_key": key, "signal": name}
                target = ranking.get("target") or {}
                if name != "score":
                    filters["closed_only"] = True
                    if name == "direct_closed_count":
                        filters["sector"] = target.get("sector")
                    else:
                        filters["sectors"] = [item["sector"] for item in
                                              target.get("relevant_sectors") or []]
                    if name == "near_size_closed_count":
                        filters["size_band"] = target.get("size_band")
                found[sid] = _record(sid, stat.get("calculation") or calculation,
                                     filters, n, stat.get("value"), stat.get("display"),
                                     stat.get("source_ids", ids))
        self._ranking_stats = found

    def key_metrics(self, acquirer_key: str) -> list[dict]:
        """Closed-deal profile statistics for the Key metrics card, in card order."""
        return [self._stats[sid] for sid in self._key_metric_ids.get(acquirer_key, [])]

    def annotated_profile(self, acquirer_key: str) -> dict | None:
        """A copy of the profile with `stat_id` on each statistic and `key_metrics` attached."""
        profile = self.profile(acquirer_key)
        if profile is None:
            return None
        profile = copy.deepcopy(profile)
        for stat, stat_id, *_rest in _profile_nodes(profile):
            if stat.get("display"):
                stat["stat_id"] = stat_id
        profile["key_metrics"] = self.key_metrics(acquirer_key)
        return profile

    def sector_points(self, sector: str) -> dict[str, dict]:
        """Sector-overview statistic fields, each with its stat_id when it has a display."""
        row = self.sector_stats_row(sector)
        if row is None:
            return {}
        by_id = {record["stat_id"]: record for record in self._sector_records(row)}

        def take(*parts, value, display):
            return _point(by_id.get(sector_stat_id(sector, *parts)), value, display)

        return {
            "deal_count": take("deal_count", value=row["deal_count"],
                               display=str(int(row["deal_count"]))),
            "closed_count": take("closed_count", value=row["closed_count"],
                                 display=str(int(row["closed_count"]))),
            "median_deal_size": take("median_deal_size", value=row["median_deal_size_mm"],
                                     display=row["median_deal_size_display"]),
            "ebitda_margin_median": take("ebitda_margin_median", value=row["ebitda_margin_median"],
                                         display=row["ebitda_margin_median_display"]),
            "ebitda_margin_p75": take("ebitda_margin_p75", value=row["ebitda_margin_p75"],
                                      display=row["ebitda_margin_p75_display"]),
            "financial_sponsor_count": take(
                "financial_sponsor_count", value=row["financial_sponsor_count"],
                display=str(int(row["financial_sponsor_count"]))),
            "financial_sponsor_share": take(
                "financial_sponsor_share", value=row["financial_sponsor_share_pct"],
                display=row["financial_sponsor_share_display"]),
        }

    def comps_block(self, sector: str, scope: str, size_band: str) -> dict | None:
        """The comps tool's statistic fields for one sector, scope and size band."""
        row = self.comps_row(sector, scope, size_band)
        if row is None:
            return None
        by_id = {record["stat_id"]: record for record in _comps_records(row)}

        def distribution(metric: str) -> dict:
            count = row[f"{metric}_count"]
            return {
                "sample_size": _point(by_id.get(comps_stat_id(sector, scope, size_band, metric, "n")),
                                      count, str(int(count))),
                **{name: _point(by_id.get(comps_stat_id(sector, scope, size_band, metric, name)),
                                row[f"{metric}_{name}"], row[f"{metric}_{name}_display"])
                   for name in ("p25", "median", "p75")},
                "source_ids": row[f"{metric}_source_ids"],
            }

        margin_id = comps_stat_id(sector, scope, size_band, "ebitda_margin", "median")
        return {
            "ev_ebitda": distribution("ev_ebitda"),
            "ev_revenue": distribution("ev_revenue"),
            "ebitda_margin_median": {
                **_point(by_id.get(margin_id), row["ebitda_margin_median"],
                         row["ebitda_margin_median_display"]),
                "sample_size": _point(None, row["ebitda_margin_count"],
                                      str(int(row["ebitda_margin_count"]))),
            },
        }

    def _index_tables(self) -> None:
        for row in _records(self.valuation_comps):
            for record in _comps_records(row):
                self._stats[record["stat_id"]] = record
        for row in _records(self.sector_stats):
            for record in self._sector_records(row):
                self._stats[record["stat_id"]] = record
        for profile in self.acquirer_profiles.values():
            ids = []
            for stat, stat_id, calculation, filters, key_metric in _profile_nodes(profile):
                record = _as_record(stat, stat_id, calculation, filters)
                if record is None:
                    continue
                self._stats[stat_id] = record
                if key_metric:
                    ids.append(stat_id)
            self._key_metric_ids[profile["acquirer_key"]] = ids

    @staticmethod
    def _sector_records(row: dict) -> list[dict]:
        sector = row["sector"]
        any_deal = {"sector": sector, "closed_only": False}
        closed = {"sector": sector, "closed_only": True}
        specs = [
            ("deal_count", "count of deals in the sector", any_deal, row["deal_count"],
             str(int(row["deal_count"])), row["source_ids"], row["deal_count"]),
            ("closed_count", "count of closed deals in the sector", closed, row["closed_count"],
             str(int(row["closed_count"])), row["closed_source_ids"], row["closed_count"]),
            ("median_deal_size", "median deal size in the sector", any_deal,
             row["median_deal_size_mm"], row["median_deal_size_display"], row["source_ids"], None),
            ("ebitda_margin_median", "median of EBITDA margin in the sector", any_deal,
             row["ebitda_margin_median"], row["ebitda_margin_median_display"], row["source_ids"],
             row["ebitda_margin_count"]),
            ("ebitda_margin_p75", "75th percentile of EBITDA margin in the sector", any_deal,
             row["ebitda_margin_p75"], row["ebitda_margin_p75_display"], row["source_ids"],
             row["ebitda_margin_count"]),
            ("financial_sponsor_count", "count of Financial Sponsor deals in the sector", any_deal,
             row["financial_sponsor_count"], str(int(row["financial_sponsor_count"])),
             row["financial_sponsor_source_ids"], row["financial_sponsor_count"]),
            ("financial_sponsor_share", "Financial Sponsor share of deals in the sector", any_deal,
             row["financial_sponsor_share_pct"], row["financial_sponsor_share_display"],
             row["source_ids"], row["deal_count"]),
        ]
        records = []
        for name, calculation, filters, value, display, source_ids, n in specs:
            record = _record(sector_stat_id(sector, name), calculation, filters, n, value,
                             display, source_ids)
            if record["display"]:
                records.append(record)
        for item in row["top_deal_types"]:
            record = _as_record(
                item, sector_stat_id(sector, "deal_type", item["deal_type"]),
                f"count of {item['deal_type']} deals in the sector",
                {**any_deal, "deal_type": item["deal_type"]})
            if record:
                records.append(record)
        return records


@cache
def get_client() -> DataClient:
    """The one shared DataClient, created on first use."""
    return DataClient()
