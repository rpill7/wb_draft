"""Shared display helpers and read-only views of report data."""


from pathlib import Path
from ma_engine.models import Target
from ma_engine.ui.activity import as_markdown
from ma_engine import config
from ma_engine.clients.data_client import get_client
import json
import streamlit as st
from ma_engine.ui.reports import target_sentence


ASSISTANT_AVATAR = ":material/auto_awesome:"


SUMMARY_BUYERS = 3


def missing_files() -> list[Path]:
    required_files = [
        config.RAW_CSV, config.TRANSACTIONS_CSV, config.ACQUIRER_PROFILES_JSON,
        config.VALUATION_COMPS_CSV, config.SECTOR_STATS_CSV, config.SECTOR_SIMILARITY_CSV,
        config.DATA_QUALITY_JSON,
    ]
    return [path for path in required_files if not path.is_file()]


def safe(text: str) -> str:
    key = config.OPENROUTER_API_KEY
    if key and text:
        return text.replace(key, "***")
    return text


def key_error_text() -> str | None:
    try:
        config.require_api_key()
    except RuntimeError as exc:
        return f"{exc} Saved runs in the sidebar still open without a key."
    return None


def target_dump(target) -> dict:
    return target.model_dump() if isinstance(target, Target) else dict(target)


def md(text, **kwargs) -> None:
    st.markdown(as_markdown(text), **kwargs)


def cap(text) -> None:
    st.caption(as_markdown(text))


def write(text) -> None:
    st.write(as_markdown(text))


def err(text) -> None:
    st.error(as_markdown(text))


def warn(text) -> None:
    st.warning(as_markdown(text))


def info(text) -> None:
    st.info(as_markdown(text))


def show_html(html_text: str) -> None:
    """HTML for the report. `$` becomes an entity so Streamlit does not draw math,
    and the page shows a dollar sign rather than a backslash."""
    st.markdown(html_text.replace("$", "&#36;"), unsafe_allow_html=True)


def _index(options: list, value) -> int:
    try:
        return list(options).index(value)
    except ValueError:
        return 0


def slug(acquirer_key: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in acquirer_key.lower()).strip("-")


def buyer_names(run: dict) -> dict[str, str]:
    return {report.acquirer_key: report.display_name for report in run.get("reports") or []}


def ordered_reports(run: dict):
    return sorted(run.get("reports") or [], key=lambda report: report.rank)


def ranking_entry(run: dict, acquirer_key: str) -> dict | None:
    ranking = run.get("ranking") or {}
    for buyer in ranking.get("buyers") or []:
        if buyer.get("acquirer_key") == acquirer_key:
            return buyer
    return None


def sources_of(run: dict) -> dict:
    sources = run.get("sources") or {}
    return {"numbers": dict(sources.get("numbers") or {}),
            "entries": list(sources.get("entries") or [])}


def deals_analysed() -> int | None:
    """The dataset's stored row count. Not counted again here."""
    quality = getattr(get_client(), "data_quality", None)
    count = quality.get("row_count") if isinstance(quality, dict) else None
    return count if isinstance(count, int) else None


def display_question(run: dict) -> str:
    """The sentence the user typed, or a description of the target when that was not stored."""
    request = (run.get("trace") or {}).get("request")
    if isinstance(request, str) and request.strip():
        return request.strip()
    return target_sentence(run["target"])


def refresh_sources(run: dict) -> None:
    """Read sources.json into the session run. A follow-up appends numbers there."""
    path = Path(run["run_dir"]) / "sources.json"
    if not path.is_file():
        return
    try:
        run["sources"] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
