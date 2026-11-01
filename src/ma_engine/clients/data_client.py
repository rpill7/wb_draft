"""Loads every file in `data/processed/` once and offers small lookups and row filters.

No statistics are calculated here: every aggregate comes from the files written by
`prepare.py`. Display strings for single deal rows use the formatting helpers in
`prepare.py`, so there is one formatter for the whole project.
"""

import json
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


@cache
def get_client() -> DataClient:
    """The one shared DataClient, created on first use."""
    return DataClient()
