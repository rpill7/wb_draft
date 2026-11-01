"""Shared settings. The only module that reads environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

RAW_CSV = ROOT / "data" / "raw" / "ma_transactions_500.csv"
PROCESSED_DIR = ROOT / "data" / "processed"
TRANSACTIONS_CSV = PROCESSED_DIR / "transactions.csv"
ACQUIRER_PROFILES_JSON = PROCESSED_DIR / "acquirer_profiles.json"
VALUATION_COMPS_CSV = PROCESSED_DIR / "valuation_comps.csv"
SECTOR_STATS_CSV = PROCESSED_DIR / "sector_stats.csv"
SECTOR_SIMILARITY_CSV = PROCESSED_DIR / "sector_similarity.csv"
DATA_QUALITY_JSON = PROCESSED_DIR / "data_quality.json"
OUTPUTS_DIR = ROOT / "outputs"
RUNS_DIR = OUTPUTS_DIR / "runs"
SAMPLE_DIR = OUTPUTS_DIR / "sample"
CACHE_DIR = ROOT / ".cache" / "llm"
PROMPTS_DIR = ROOT / "src" / "ma_engine" / "prompts"

TEMPERATURE = 0.2
SEED = 7
REASONING_EFFORT = "low"
REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 2

MAX_TOOL_ROUNDS = 4
MAX_PARALLEL_BUYERS = 10
MAX_REPAIR_ATTEMPTS = 1

SHORTLIST_SIZE = 15
REPORT_COUNT = 10

DEFAULT_TARGET = {
    "sector": "Healthcare Services",
    "ev_mm": 200,
    "ownership": "Private",
    "geography": "Any region",
    "margin_profile": "strong",
}


def require_api_key() -> str:
    """Return the OpenRouter key, or raise a clear error if it is not set."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your OpenRouter key."
        )
    return OPENROUTER_API_KEY
