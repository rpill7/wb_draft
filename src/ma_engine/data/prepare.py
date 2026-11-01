"""Clean the raw transaction CSV and pre-calculate every target-independent table.

Run with `make data` (`python -m ma_engine.data.prepare`). Functions take DataFrames so they
can be tested; `write_outputs` and `main` do the file I/O.
"""

import json
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

from ma_engine.config import (
    ACQUIRER_PROFILES_JSON,
    DATA_QUALITY_JSON,
    DEFAULT_TARGET,
    PROCESSED_DIR,
    RAW_CSV,
    ROOT,
    SECTOR_SIMILARITY_CSV,
    SECTOR_STATS_CSV,
    TRANSACTIONS_CSV,
    VALUATION_COMPS_CSV,
)

EXPECTED_COLUMNS = [
    "transaction_id", "target_company", "acquirer", "sector", "sub_sector", "deal_year",
    "deal_quarter", "deal_type", "geography", "financing_type", "deal_size_mm",
    "target_revenue_mm", "target_ebitda_mm", "ebitda_margin_pct", "revenue_growth_pct",
    "ev_ebitda_multiple", "ev_revenue_multiple", "synergy_pct_of_deal", "outcome",
    "strategic_rationale_tags", "num_bidders", "days_to_close", "acquirer_type",
    "target_ownership_pre",
]
NUMERIC_COLUMNS = [
    "deal_year", "deal_size_mm", "target_revenue_mm", "target_ebitda_mm", "ebitda_margin_pct",
    "revenue_growth_pct", "ev_ebitda_multiple", "ev_revenue_multiple", "synergy_pct_of_deal",
    "num_bidders", "days_to_close",
]
POSITIVE_COLUMNS = ["deal_size_mm", "ev_ebitda_multiple", "ev_revenue_multiple"]
PERCENT_COLUMNS = ["ebitda_margin_pct", "revenue_growth_pct", "synergy_pct_of_deal"]

SECTORS = [
    "Healthcare Services", "Physician Groups", "Behavioral Health", "Home Health/Hospice",
    "Dental", "Revenue Cycle", "Health IT", "Health Insurance", "Medical Devices",
    "Pharma/Biotech",
]
CLOSED = "Closed"
NON_CLOSED_OUTCOMES = ["Withdrawn", "Pending", "Terminated", "Rumored"]
KNOWN_VALUES = {
    "sector": SECTORS,
    "outcome": [CLOSED, *NON_CLOSED_OUTCOMES],
    "acquirer_type": ["Financial Sponsor", "Strategic"],
    "geography": [
        "Great Plains", "Mid-Atlantic", "Midwest", "Mountain West", "Multi-Regional",
        "National", "Northeast", "Southeast", "Southwest", "West Coast",
    ],
    "deal_type": [
        "Bolt-on Acquisition", "Carve-out", "Leveraged Buyout", "Management Buyout",
        "Merger of Equals", "Minority Investment", "Platform Investment", "Recapitalization",
        "SPAC Merger", "Strategic Acquisition",
    ],
    "target_ownership_pre": ["Non-Profit", "PE-Backed", "Private", "Public"],
    "deal_quarter": ["Q1", "Q2", "Q3", "Q4"],
    "financing_type": [
        "All Cash", "Cash + Stock", "Earnout", "Leveraged", "Mixed", "Seller Financing", "Stock",
    ],
}
NON_REGIONAL_GEOGRAPHIES = {"National", "Multi-Regional"}

# Lower edge inclusive, upper edge exclusive: a $400.0M deal is in "$400M–$1B".
SIZE_BANDS = [
    ("Under $100M", 0.0, 100.0),
    ("$100M–$400M", 100.0, 400.0),
    ("$400M–$1B", 400.0, 1000.0),
    ("Over $1B", 1000.0, float("inf")),
]
ALL_SIZES = "All sizes"
SCOPES = ["sector_only", "sector_plus_adjacent"]
ADJACENCY_THRESHOLD = 0.6
MIN_COMPS_SAMPLE = 3
TOP_TAGS = 5
TOP_DEAL_TYPES = 3

# Hand-written business judgment, not calculated from data. One entry per unordered pair;
# the diagonal is 1.0 and the table is mirrored so every ordered pair exists.
SECTOR_SIMILARITY = {
    ("Healthcare Services", "Physician Groups"): (0.8, "Both are provider services built around clinicians and patient visits."),
    ("Healthcare Services", "Behavioral Health"): (0.75, "Both are provider services with similar reimbursement and site-of-care models."),
    ("Healthcare Services", "Home Health/Hospice"): (0.75, "Both are provider services delivering care to patients, often regionally."),
    ("Healthcare Services", "Dental"): (0.7, "Dental service organizations are provider services with a practice roll-up model."),
    ("Healthcare Services", "Revenue Cycle"): (0.5, "Revenue cycle firms sell services to providers rather than deliver care."),
    ("Healthcare Services", "Health IT"): (0.35, "Health IT sells software to providers; different business model."),
    ("Healthcare Services", "Health Insurance"): (0.3, "Insurers pay providers; related through value-based care only."),
    ("Healthcare Services", "Medical Devices"): (0.15, "Device makers sell products, not care services."),
    ("Healthcare Services", "Pharma/Biotech"): (0.1, "Drug development has little overlap with care delivery."),
    ("Physician Groups", "Behavioral Health"): (0.6, "Both are clinician-led outpatient practices."),
    ("Physician Groups", "Home Health/Hospice"): (0.55, "Physician referrals drive home health, but care settings differ."),
    ("Physician Groups", "Dental"): (0.6, "Dental and physician practice management share the roll-up model."),
    ("Physician Groups", "Revenue Cycle"): (0.5, "Physician groups are major revenue cycle customers."),
    ("Physician Groups", "Health IT"): (0.35, "Physician groups buy practice software; different business model."),
    ("Physician Groups", "Health Insurance"): (0.4, "Value-based care links insurers and physician groups."),
    ("Physician Groups", "Medical Devices"): (0.15, "Physicians use devices, but the businesses differ."),
    ("Physician Groups", "Pharma/Biotech"): (0.1, "Little overlap beyond prescribing."),
    ("Behavioral Health", "Home Health/Hospice"): (0.55, "Both deliver community and home-based care."),
    ("Behavioral Health", "Dental"): (0.4, "Both are outpatient provider services with different clinical focus."),
    ("Behavioral Health", "Revenue Cycle"): (0.35, "Behavioral providers buy billing services; different business model."),
    ("Behavioral Health", "Health IT"): (0.3, "Behavioral EHR vendors serve these providers; different business model."),
    ("Behavioral Health", "Health Insurance"): (0.3, "Insurers manage behavioral benefits; payer versus provider."),
    ("Behavioral Health", "Medical Devices"): (0.1, "Little overlap."),
    ("Behavioral Health", "Pharma/Biotech"): (0.15, "Psychiatric medicines are a weak link to care delivery."),
    ("Home Health/Hospice", "Dental"): (0.35, "Both are provider services, but care settings differ."),
    ("Home Health/Hospice", "Revenue Cycle"): (0.35, "Home health agencies buy billing services; different business model."),
    ("Home Health/Hospice", "Health IT"): (0.3, "Home care software serves these agencies; different business model."),
    ("Home Health/Hospice", "Health Insurance"): (0.35, "Medicare Advantage plans increasingly manage home-based care."),
    ("Home Health/Hospice", "Medical Devices"): (0.15, "Home medical equipment is a weak link."),
    ("Home Health/Hospice", "Pharma/Biotech"): (0.1, "Little overlap."),
    ("Dental", "Revenue Cycle"): (0.3, "Dental practices buy billing services; different business model."),
    ("Dental", "Health IT"): (0.25, "Dental practice software is a weak link."),
    ("Dental", "Health Insurance"): (0.2, "Dental benefits are a separate insurance line."),
    ("Dental", "Medical Devices"): (0.3, "Dental devices and aligners sell into dental practices."),
    ("Dental", "Pharma/Biotech"): (0.1, "Little overlap."),
    ("Revenue Cycle", "Health IT"): (0.7, "Both are technology-enabled services sold to providers."),
    ("Revenue Cycle", "Health Insurance"): (0.4, "Both sit in the claims and payment flow."),
    ("Revenue Cycle", "Medical Devices"): (0.1, "Little overlap."),
    ("Revenue Cycle", "Pharma/Biotech"): (0.1, "Little overlap."),
    ("Health IT", "Health Insurance"): (0.35, "Insurers buy data and software; payer versus vendor."),
    ("Health IT", "Medical Devices"): (0.25, "Connected devices overlap with health software."),
    ("Health IT", "Pharma/Biotech"): (0.2, "Life-sciences data and software is a modest link."),
    ("Health Insurance", "Medical Devices"): (0.1, "Little overlap."),
    ("Health Insurance", "Pharma/Biotech"): (0.2, "Pharmacy benefits link insurers and drug makers."),
    ("Medical Devices", "Pharma/Biotech"): (0.5, "Both are regulated life-sciences product makers."),
}


# ---------- formatting (the one shared helper for every display string) ----------

def round_half_up(value: float, places: int) -> float:
    """Round like a person would (2.25 -> 2.3), not banker's rounding."""
    quantum = Decimal(1).scaleb(-places)
    # 12 significant digits drops float noise, so (9.1 + 9.2) / 2 rounds as 9.15, not 9.1499...
    exact = Decimal(f"{float(value):.12g}")
    return float(exact.quantize(quantum, rounding=ROUND_HALF_UP))


def _missing(value) -> bool:
    return value is None or pd.isna(value)


def fmt_money(value) -> str | None:
    """$ millions, e.g. `$185.0M`, `$1,234.6M`."""
    return None if _missing(value) else f"${round_half_up(value, 1):,.1f}M"


def fmt_multiple(value) -> str | None:
    """Valuation multiple, e.g. `13.2x`."""
    return None if _missing(value) else f"{round_half_up(value, 1):.1f}x"


def fmt_pct(value) -> str | None:
    """Value already on a 0–100 scale, e.g. `24.1%`."""
    return None if _missing(value) else f"{round_half_up(value, 1):.1f}%"


def fmt_number(value) -> str | None:
    """Plain number with one decimal, for medians of counts such as bidders or days."""
    return None if _missing(value) else f"{round_half_up(value, 1):.1f}"


def raw_value(value, places: int) -> float | None:
    return None if _missing(value) else round_half_up(value, places)


# ---------- small field helpers ----------

def normalize_acquirer(name: str) -> str:
    """Lower-case and collapse whitespace. Never merges different names."""
    return " ".join(str(name).split()).lower()


def parse_tags(text: str) -> list[str]:
    if _missing(text):
        return []
    return [tag.strip() for tag in str(text).split("|") if tag.strip()]


def size_band(deal_size_mm) -> str:
    if _missing(deal_size_mm) or deal_size_mm <= 0:
        return ""
    for label, low, high in SIZE_BANDS:
        if low <= deal_size_mm < high:
            return label
    return ""


def _ids(frame: pd.DataFrame) -> list[str]:
    return sorted(frame["transaction_id"].tolist())


# ---------- Step 1.1: load and validate ----------

def load_raw(path: Path) -> pd.DataFrame:
    """Read every column as text so the original values are kept exactly."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"Raw CSV not found: {path}")
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"Could not read raw CSV {path}: {exc}") from exc


def check_columns(raw: pd.DataFrame) -> list[str]:
    """Raise if expected columns are missing; return unexpected extra columns."""
    missing = [c for c in EXPECTED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")
    return [c for c in raw.columns if c not in EXPECTED_COLUMNS]


def check_ids(raw: pd.DataFrame) -> dict:
    ids = raw["transaction_id"].str.strip()
    empty_rows = [int(i) + 2 for i in raw.index[ids == ""]]  # CSV line numbers
    counts = ids[ids != ""].value_counts()
    return {
        "unique": not empty_rows and bool((counts == 1).all()),
        "empty_id_csv_lines": empty_rows,
        "duplicate_ids": sorted(counts[counts > 1].index.tolist()),
    }


def to_numeric(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Convert numeric columns. Bad values become blank (never zero) and are reported."""
    numbers = pd.DataFrame(index=raw.index)
    invalid = []
    for column in NUMERIC_COLUMNS:
        text = raw[column].str.strip()
        values = pd.to_numeric(text.where(text != ""), errors="coerce")
        bad = (text != "") & values.isna()
        if column in POSITIVE_COLUMNS:
            bad |= values <= 0
        for i in raw.index[bad]:
            invalid.append({
                "transaction_id": raw.at[i, "transaction_id"],
                "column": column,
                "value": raw.at[i, column],
                "problem": "not a number" if pd.isna(values[i]) else "must be positive",
            })
        numbers[column] = values.where(~bad)
    return numbers, invalid


def check_categories(raw: pd.DataFrame) -> list[dict]:
    unexpected = []
    for column, allowed in KNOWN_VALUES.items():
        values = raw[column].str.strip()
        for i in raw.index[~values.isin(allowed)]:
            unexpected.append({
                "transaction_id": raw.at[i, "transaction_id"],
                "column": column,
                "value": raw.at[i, column],
            })
    return unexpected


# ---------- Step 1.2: per-deal derived fields ----------

def build_deals(raw: pd.DataFrame) -> pd.DataFrame:
    """Typed working copy: stripped text, numeric columns, and the derived fields."""
    deals = raw.apply(lambda col: col.str.strip())
    numbers, _ = to_numeric(raw)
    for column in NUMERIC_COLUMNS:
        deals[column] = numbers[column]
    deals["acquirer_key"] = deals["acquirer"].map(normalize_acquirer)
    deals["is_closed"] = deals["outcome"] == CLOSED
    deals["rationale_tags"] = deals["strategic_rationale_tags"].map(
        lambda t: json.dumps(parse_tags(t), ensure_ascii=False)
    )
    deals["size_band"] = deals["deal_size_mm"].map(size_band)
    sector_median = deals.groupby("sector")["ebitda_margin_pct"].median().map(
        lambda m: raw_value(m, 2)
    )
    deals["margin_vs_sector"] = [
        _compare(margin, sector_median.get(sector))
        for margin, sector in zip(deals["ebitda_margin_pct"], deals["sector"])
    ]
    deals["is_regional"] = (deals["geography"] != "") & ~deals["geography"].isin(
        NON_REGIONAL_GEOGRAPHIES
    )
    return deals


def _compare(value, median) -> str:
    if _missing(value) or _missing(median):
        return ""
    value = raw_value(value, 2)
    return "above" if value > median else "below" if value < median else "at"


DERIVED_COLUMNS = [
    "acquirer_key", "is_closed", "rationale_tags", "size_band", "margin_vs_sector", "is_regional",
]


def build_transactions(raw: pd.DataFrame, deals: pd.DataFrame) -> pd.DataFrame:
    """Every original column exactly as read, plus the derived columns."""
    return pd.concat([raw, deals[DERIVED_COLUMNS]], axis=1)


# ---------- statistics with source IDs ----------

def count_stat(frame: pd.DataFrame) -> dict:
    return {"value": len(frame), "display": str(len(frame)), "source_ids": _ids(frame)}


def median_stat(frame: pd.DataFrame, column: str, fmt, places: int) -> dict:
    rows = frame[frame[column].notna()]
    median = rows[column].median() if len(rows) else None
    return {"value": raw_value(median, places), "display": fmt(median), "n": len(rows),
            "source_ids": _ids(rows)}


def extreme_stat(frame: pd.DataFrame, column: str, fmt, places: int, which: str) -> dict:
    rows = frame[frame[column].notna()]
    if rows.empty:
        return {"value": None, "display": None, "source_ids": []}
    target = rows[column].min() if which == "min" else rows[column].max()
    return {"value": raw_value(target, places), "display": fmt(target),
            "source_ids": _ids(rows[rows[column] == target])}


def counts_by(frame: pd.DataFrame, column: str) -> dict:
    """{value: count_stat}, ordered by count (high to low) then name."""
    groups = [(key, group) for key, group in frame.groupby(column, sort=True)]
    groups.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    return {key: count_stat(group) for key, group in groups}


def _distribution(frame: pd.DataFrame, column: str, prefix: str, fmt, places: int) -> dict:
    """Flat columns: count, p25, median, p75 (each with display) and source IDs."""
    rows = frame[frame[column].notna()]
    out = {f"{prefix}_count": len(rows)}
    for name, q in [("p25", 0.25), ("median", 0.5), ("p75", 0.75)]:
        value = rows[column].quantile(q) if len(rows) else None
        out[f"{prefix}_{name}"] = raw_value(value, places)
        out[f"{prefix}_{name}_display"] = fmt(value)
    out[f"{prefix}_source_ids"] = json.dumps(_ids(rows))
    return out


# ---------- Step 1.3: pre-calculated tables ----------

def build_similarity() -> pd.DataFrame:
    rows = []
    for a in SECTORS:
        for b in SECTORS:
            if a == b:
                score, reason = 1.0, "Same sector."
            else:
                score, reason = SECTOR_SIMILARITY.get((a, b)) or SECTOR_SIMILARITY[(b, a)]
            rows.append({
                "sector_a": a, "sector_b": b, "closeness": score,
                "is_adjacent": score >= ADJACENCY_THRESHOLD, "reason": reason,
            })
    return pd.DataFrame(rows)


def adjacent_sectors(similarity: pd.DataFrame, sector: str) -> list[str]:
    """The sector itself plus every sector at or above ADJACENCY_THRESHOLD, closest first."""
    rows = similarity[(similarity["sector_a"] == sector) & similarity["is_adjacent"]]
    rows = rows.sort_values(["closeness", "sector_b"], ascending=[False, True])
    return rows["sector_b"].tolist()


def build_valuation_comps(deals: pd.DataFrame, similarity: pd.DataFrame) -> pd.DataFrame:
    """Closed deals with at least one valid positive multiple, by sector x scope x size band."""
    closed = deals[deals["is_closed"]]
    priced = closed[closed["ev_ebitda_multiple"].notna() | closed["ev_revenue_multiple"].notna()]
    rows = []
    for sector in SECTORS:
        for scope in SCOPES:
            sectors = [sector] if scope == "sector_only" else adjacent_sectors(similarity, sector)
            in_scope = priced[priced["sector"].isin(sectors)]
            for band in [label for label, _, _ in SIZE_BANDS] + [ALL_SIZES]:
                group = in_scope if band == ALL_SIZES else in_scope[in_scope["size_band"] == band]
                row = {
                    "sector": sector, "scope": scope, "size_band": band,
                    "sectors_included": json.dumps(sectors), "deal_count": len(group),
                }
                row |= _distribution(group, "ev_ebitda_multiple", "ev_ebitda", fmt_multiple, 2)
                row |= _distribution(group, "ev_revenue_multiple", "ev_revenue", fmt_multiple, 2)
                margin = median_stat(group, "ebitda_margin_pct", fmt_pct, 2)
                row |= {
                    "ebitda_margin_count": margin["n"],
                    "ebitda_margin_median": margin["value"],
                    "ebitda_margin_median_display": margin["display"],
                    "source_ids": json.dumps(_ids(group)),
                    "sample_warning": _thin_sample_warning(row),
                }
                rows.append(row)
    return pd.DataFrame(rows)


def _thin_sample_warning(row: dict) -> str:
    thin = [
        f"{label} n={row[key]}"
        for label, key in [("EV/EBITDA", "ev_ebitda_count"), ("EV/Revenue", "ev_revenue_count")]
        if row[key] < MIN_COMPS_SAMPLE
    ]
    return f"Thin sample (fewer than {MIN_COMPS_SAMPLE} deals): " + ", ".join(thin) if thin else ""


def build_sector_stats(deals: pd.DataFrame) -> pd.DataFrame:
    """Per sector, over all deals (any outcome) unless the column says closed."""
    rows = []
    for sector in SECTORS:
        group = deals[deals["sector"] == sector]
        closed = group[group["is_closed"]]
        sponsors = group[group["acquirer_type"] == "Financial Sponsor"]
        size = median_stat(group, "deal_size_mm", fmt_money, 1)
        margin = _distribution(group, "ebitda_margin_pct", "ebitda_margin", fmt_pct, 2)
        share = 100 * len(sponsors) / len(group) if len(group) else None
        deal_types = counts_by(group, "deal_type")
        rows.append({
            "sector": sector,
            "deal_count": len(group),
            "closed_count": len(closed),
            "median_deal_size_mm": size["value"],
            "median_deal_size_display": size["display"],
            "ebitda_margin_count": margin["ebitda_margin_count"],
            "ebitda_margin_median": margin["ebitda_margin_median"],
            "ebitda_margin_median_display": margin["ebitda_margin_median_display"],
            "ebitda_margin_p75": margin["ebitda_margin_p75"],
            "ebitda_margin_p75_display": margin["ebitda_margin_p75_display"],
            "financial_sponsor_count": len(sponsors),
            "financial_sponsor_share_pct": raw_value(share, 2),
            "financial_sponsor_share_display": fmt_pct(share),
            "top_deal_types": json.dumps(
                [{"deal_type": k, **v} for k, v in list(deal_types.items())[:TOP_DEAL_TYPES]]
            ),
            "source_ids": json.dumps(_ids(group)),
            "closed_source_ids": json.dumps(_ids(closed)),
            "financial_sponsor_source_ids": json.dumps(_ids(sponsors)),
        })
    return pd.DataFrame(rows)


def build_acquirer_profiles(deals: pd.DataFrame) -> dict:
    profiles = {}
    for key, group in sorted(deals[deals["acquirer_key"] != ""].groupby("acquirer_key")):
        closed = group[group["is_closed"]]
        names = Counter(group["acquirer"])
        types = Counter(group["acquirer_type"])
        tags = Counter()
        tag_ids: dict[str, list[str]] = {}
        for tx_id, text in zip(group["transaction_id"], group["strategic_rationale_tags"]):
            for tag in parse_tags(text):
                tags[tag] += 1
                tag_ids.setdefault(tag, []).append(tx_id)
        top_tags = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_TAGS]
        profiles[key] = {
            "acquirer_key": key,
            "display_name": _most_common(names),
            "name_variants": sorted(names),
            "acquirer_type": _most_common(types),
            "acquirer_types_seen": sorted(types),
            "deal_count": count_stat(group),
            "closed_count": count_stat(closed),
            "non_closed_by_outcome": {
                outcome: count_stat(group[group["outcome"] == outcome])
                for outcome in NON_CLOSED_OUTCOMES
            },
            "closed_by_sector": counts_by(closed, "sector"),
            "closed_by_deal_type": counts_by(closed, "deal_type"),
            "closed_by_geography": counts_by(closed, "geography"),
            "closed_deal_size": {
                "median": median_stat(closed, "deal_size_mm", fmt_money, 1),
                "smallest": extreme_stat(closed, "deal_size_mm", fmt_money, 1, "min"),
                "largest": extreme_stat(closed, "deal_size_mm", fmt_money, 1, "max"),
            },
            "closed_ev_ebitda_median": median_stat(closed, "ev_ebitda_multiple", fmt_multiple, 2),
            "closed_ev_revenue_median": median_stat(closed, "ev_revenue_multiple", fmt_multiple, 2),
            "closed_private_target_count": count_stat(
                closed[closed["target_ownership_pre"] == "Private"]
            ),
            "closed_financing_mix": counts_by(closed, "financing_type"),
            "closed_median_bidders": median_stat(closed, "num_bidders", fmt_number, 1),
            "closed_median_days_to_close": median_stat(closed, "days_to_close", fmt_number, 1),
            "first_deal_year": _year_stat(group, "min"),
            "last_deal_year": _year_stat(group, "max"),
            "top_rationale_tags_all_deals": [
                {"tag": tag, "value": n, "display": str(n), "source_ids": sorted(tag_ids[tag])}
                for tag, n in top_tags
            ],
        }
    return profiles


def _most_common(counter: Counter) -> str:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _year_stat(frame: pd.DataFrame, which: str) -> dict:
    years = frame[frame["deal_year"].notna()]
    if years.empty:
        return {"value": None, "display": None, "source_ids": []}
    year = int(years["deal_year"].min() if which == "min" else years["deal_year"].max())
    return {"value": year, "display": str(year), "source_ids": _ids(years[years["deal_year"] == year])}


def build_data_quality(raw: pd.DataFrame, deals: pd.DataFrame, sector_stats: pd.DataFrame,
                       unexpected_columns: list[str], id_report: dict) -> dict:
    _, invalid = to_numeric(raw)
    blanks = {c: int((raw[c].str.strip() == "").sum()) for c in raw.columns}
    acquirer_counts = deals[deals["acquirer_key"] != ""].groupby("acquirer_key").size()
    profiles_top = sorted(acquirer_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    focus = DEFAULT_TARGET["sector"]
    focus_deals = deals[deals["sector"] == focus]
    focus_mid = focus_deals[focus_deals["size_band"] == SIZE_BANDS[1][0]]
    focus_row = sector_stats[sector_stats["sector"] == focus].iloc[0]
    closed = deals[deals["is_closed"]]
    return {
        "source_file": str(RAW_CSV.relative_to(ROOT)),
        "row_count": len(raw),
        "column_count": len(raw.columns),
        "unexpected_columns": unexpected_columns,
        "transaction_id_checks": id_report,
        "blank_counts": {c: n for c, n in blanks.items() if n},
        "days_to_close_blank_by_closed": {
            "closed": int((closed["days_to_close"].isna()).sum()),
            "not_closed": int((deals[~deals["is_closed"]]["days_to_close"].isna()).sum()),
        },
        "invalid_values": invalid,
        "unexpected_category_values": check_categories(raw),
        "category_counts": {
            column: _value_counts(deals[column])
            for column in ["outcome", "acquirer_type", "sector", "deal_type", "geography",
                           "target_ownership_pre", "financing_type", "deal_quarter"]
        },
        "deal_year_counts": {
            str(int(y)): int(n) for y, n in deals["deal_year"].value_counts().sort_index().items()
        },
        "percent_scale": {
            c: {"min": raw_value(deals[c].min(), 2), "max": raw_value(deals[c].max(), 2),
                "scale": "0-100" if deals[c].max() > 1 else "possibly 0-1, check"}
            for c in PERCENT_COLUMNS
        },
        "size_bands": {
            "edges": "lower edge inclusive, upper edge exclusive (in $M): "
                     + "; ".join(f"{label} = [{lo:g}, {hi:g})" for label, lo, hi in SIZE_BANDS),
            "counts": {label: int((deals["size_band"] == label).sum()) for label, _, _ in SIZE_BANDS},
            "unbanded": int((deals["size_band"] == "").sum()),
        },
        "acquirers": {
            "distinct": int(len(acquirer_counts)),
            "with_2_plus_deals": int((acquirer_counts >= 2).sum()),
            "with_3_plus_deals": int((acquirer_counts >= 3).sum()),
            "single_deal": int((acquirer_counts == 1).sum()),
            "top_10_by_deal_count": [{"acquirer_key": k, "deal_count": int(n)} for k, n in profiles_top],
        },
        "focus_sector": {
            "sector": focus,
            "total": count_stat(focus_deals),
            "size_100_400": count_stat(focus_mid),
            "size_100_400_closed": count_stat(focus_mid[focus_mid["is_closed"]]),
        },
        "strong_margin": {
            "definition": f"Strong EBITDA margin = above the {focus} median EBITDA margin "
                          "(all deals in the sector, any outcome).",
            "threshold_pct": float(focus_row["ebitda_margin_median"]),
            "threshold_display": focus_row["ebitda_margin_median_display"],
            "p75_pct": float(focus_row["ebitda_margin_p75"]),
            "p75_display": focus_row["ebitda_margin_p75_display"],
            "n": int(focus_row["ebitda_margin_count"]),
        },
        "adjacency_threshold": ADJACENCY_THRESHOLD,
        "min_comps_sample": MIN_COMPS_SAMPLE,
        "consistency_checks": _consistency_checks(deals),
        "assumptions": [
            "transaction_id from the raw file is the only ID; no new IDs are created.",
            "is_closed is true only for outcome 'Closed'; Withdrawn, Pending, Terminated and Rumored are not closed.",
            "is_regional is true for named regions; 'National' and 'Multi-Regional' are not regional.",
            "Percentage columns are kept on their original 0-100 scale.",
            "Invalid numbers become blank in calculations (never zero); the raw text is kept in transactions.csv.",
            "Sector medians, margin thresholds and sponsor share use all deals in the sector; "
            "valuation comps and buyer-paid multiples use closed deals only.",
            "Stated columns (margins, multiples) are used as given and never re-derived.",
            "List-valued CSV cells (source IDs, tags, sectors) are JSON lists.",
            f"Sub-sector differs from sector in {int((deals['sub_sector'] != deals['sector']).sum())} rows.",
        ],
    }


def _value_counts(series: pd.Series) -> dict:
    counts = series.value_counts()
    return {k: int(n) for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}


def _consistency_checks(deals: pd.DataFrame) -> list[dict]:
    """Report (never fix) rows where stated columns disagree with the other columns."""
    checks = [
        ("ebitda_margin_pct differs from target_ebitda_mm / target_revenue_mm by more than 1 point",
         (deals["ebitda_margin_pct"] - 100 * deals["target_ebitda_mm"] / deals["target_revenue_mm"]).abs() > 1),
        ("ev_ebitda_multiple differs from deal_size_mm / target_ebitda_mm by more than 10%",
         (deals["deal_size_mm"] / deals["target_ebitda_mm"] / deals["ev_ebitda_multiple"] - 1).abs() > 0.1),
        ("ev_revenue_multiple differs from deal_size_mm / target_revenue_mm by more than 10%",
         (deals["deal_size_mm"] / deals["target_revenue_mm"] / deals["ev_revenue_multiple"] - 1).abs() > 0.1),
    ]
    return [
        {"check": name, "rows": int(mask.sum()), "example_ids": _ids(deals[mask])[:5]}
        for name, mask in checks
    ]


# ---------- pipeline and file output ----------

def prepare(raw: pd.DataFrame) -> dict:
    """Run every step on a raw (all-text) DataFrame and return the six outputs."""
    unexpected_columns = check_columns(raw)
    id_report = check_ids(raw)
    if not id_report["unique"]:
        raise ValueError(
            "transaction_id must be unique and non-empty. "
            f"Empty at CSV lines: {id_report['empty_id_csv_lines']}; "
            f"duplicates: {id_report['duplicate_ids']}"
        )
    deals = build_deals(raw)
    similarity = build_similarity()
    sector_stats = build_sector_stats(deals)
    return {
        "transactions": build_transactions(raw, deals),
        "acquirer_profiles": build_acquirer_profiles(deals),
        "valuation_comps": build_valuation_comps(deals, similarity),
        "sector_stats": sector_stats,
        "sector_similarity": similarity,
        "data_quality": build_data_quality(raw, deals, sector_stats, unexpected_columns, id_report),
    }


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")


def _write_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_outputs(raw_csv: Path, out_dir: Path) -> dict:
    outputs = prepare(load_raw(raw_csv))
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(outputs["transactions"], out_dir / TRANSACTIONS_CSV.name)
    _write_json(outputs["acquirer_profiles"], out_dir / ACQUIRER_PROFILES_JSON.name)
    _write_csv(outputs["valuation_comps"], out_dir / VALUATION_COMPS_CSV.name)
    _write_csv(outputs["sector_stats"], out_dir / SECTOR_STATS_CSV.name)
    _write_csv(outputs["sector_similarity"], out_dir / SECTOR_SIMILARITY_CSV.name)
    _write_json(outputs["data_quality"], out_dir / DATA_QUALITY_JSON.name)
    return outputs


def print_summary(outputs: dict) -> None:
    dq = outputs["data_quality"]
    focus = dq["focus_sector"]
    margin = dq["strong_margin"]
    print(f"Rows: {dq['row_count']}  Columns: {dq['column_count']}")
    print(f"Outcomes: {dq['category_counts']['outcome']}")
    print(f"Buyer types: {dq['category_counts']['acquirer_type']}")
    print(f"Acquirers: {dq['acquirers']['distinct']} distinct, "
          f"{dq['acquirers']['with_3_plus_deals']} with 3+ deals")
    print(f"Size bands: {dq['size_bands']['counts']}")
    print(f"{focus['sector']}: {focus['total']['value']} deals, "
          f"{focus['size_100_400']['value']} in $100M–$400M, "
          f"{focus['size_100_400_closed']['value']} of those closed")
    print(f"Strong-margin threshold: {margin['threshold_display']} (p75 {margin['p75_display']})")
    print(f"Invalid values: {len(dq['invalid_values'])}  "
          f"Unexpected categories: {len(dq['unexpected_category_values'])}")
    print(f"Written: {len(outputs['acquirer_profiles'])} acquirer profiles, "
          f"{len(outputs['valuation_comps'])} comps rows, "
          f"{len(outputs['sector_similarity'])} similarity rows")


def main() -> None:
    outputs = write_outputs(RAW_CSV, PROCESSED_DIR)
    print_summary(outputs)
    print(f"Output folder: {PROCESSED_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
