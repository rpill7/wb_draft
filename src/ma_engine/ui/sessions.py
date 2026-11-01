"""Saved research catalog, session state and deletion persistence."""


from pathlib import Path
from ma_engine.ui.activity import as_markdown
from ma_engine import config
from datetime import datetime
from ma_engine.research.agent import display_research_title
from ma_engine.validation import guardrails
from ma_engine.research import jobs
import json
from ma_engine.ui.reports import money_text
import shutil
import streamlit as st
from ma_engine.ui.common import (
    display_question,
    refresh_sources,
)


def init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("excluded", [])
    st.session_state.setdefault("session_runs", [])
    st.session_state.setdefault("followups", [])
    st.session_state.setdefault("stopped_followups", [])
    st.session_state.setdefault("screen", "research")
    st.session_state.setdefault("composing", False)
    st.session_state.setdefault("selected_buyer", None)
    st.session_state.setdefault("panel_open", False)
    st.session_state.setdefault("editing_target", False)
    st.session_state.setdefault("edited_target", None)
    st.session_state.setdefault("pending_delete", None)


def remember_run(run: dict) -> None:
    path = str(run["run_dir"])
    if path not in st.session_state.session_runs:
        st.session_state.session_runs.append(path)


def read_followups(run_dir) -> list:
    path = Path(run_dir) / "followups.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def activate_run(run: dict) -> None:
    refresh_sources(run)
    st.session_state.current_run = run
    st.session_state.followups = read_followups(run["run_dir"])
    st.session_state.excluded = list((run.get("trace") or {}).get("excluded_keys") or [])
    st.session_state.question_run_id = run["run_id"]
    st.session_state.run_question = display_question(run)
    st.session_state.user_request = (run.get("trace") or {}).get("request")
    st.session_state.screen = "research"
    st.session_state.composing = False
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False
    st.session_state.editing_target = False
    st.session_state.edited_target = None
    remember_run(run)


def run_catalog() -> list[tuple[str, Path]]:
    """This session's runs, every saved run under outputs/runs, and the sample run."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, path: Path) -> None:
        path = Path(path)
        if path in seen or not (path / "reports.json").is_file():
            return
        seen.add(path)
        found.append((label, path))

    latest = guardrails.latest_run_dir(config.RUNS_DIR)
    for raw in st.session_state.session_runs:
        path = Path(raw)
        label = f"Latest run ({path.name})" if path == latest else f"Run {path.name}"
        add(label, path)
    if config.RUNS_DIR.is_dir():
        for path in sorted(p for p in config.RUNS_DIR.iterdir() if (p / "reports.json").is_file()):
            label = f"Latest run ({path.name})" if path == latest else f"Run {path.name}"
            add(label, path)
    sample = (config.SAMPLE_DIR if (config.SAMPLE_DIR / "reports.json").is_file()
              else guardrails.latest_run_dir(config.SAMPLE_DIR))
    if sample:
        add("Sample run (outputs/sample)", sample)
    return found


def _runs_root() -> Path:
    return config.RUNS_DIR.resolve()


def _plain_run_id(run_id: str) -> str:
    """A run folder name. A path, blank id, or parent reference is rejected."""
    if not isinstance(run_id, str) or run_id != Path(run_id).name or run_id in {".", ".."}:
        raise ValueError("That is not a saved research session.")
    return run_id


def _direct_run_path(run_id: str) -> Path | None:
    """The folder for this id when it is a real directory directly inside the generated runs folder."""
    path = config.RUNS_DIR / run_id
    if path.is_symlink():
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved.parent != _runs_root():
        return None
    return path


def can_delete_run(path: Path) -> bool:
    """True when this catalog entry is a finished user-generated run, not the sample or a link."""
    if path.is_symlink() or not (path / "reports.json").is_file():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.parent == _runs_root()


def _being_written(path: Path) -> bool:
    """A run folder exists before its report file is saved. That window is still being written."""
    return path.is_dir() and not path.is_symlink() and not (path / "reports.json").is_file()


def catalog_path(run_id: str) -> Path | None:
    for _label, path in run_catalog():
        if path.name == run_id:
            return path
    return None


def delete_research_session(run_id: str) -> str:
    """Delete one generated run directory. Returns `deleted` or `missing`.

    The id is a folder name from the catalog, never a path typed into the page.
    Raises ValueError when the delete is refused or the folder cannot be removed.
    The message is safe to show. A refusal leaves the folder where it is.
    """
    run_id = _plain_run_id(run_id)
    if jobs.active_for(run_id):
        raise ValueError("This research session has an active follow-up and cannot be deleted yet.")
    path = catalog_path(run_id)
    if path is None:
        candidate = config.RUNS_DIR / run_id
        if candidate.exists() and _direct_run_path(run_id) is None:
            raise ValueError("Only a saved research session can be deleted.")
        direct = _direct_run_path(run_id)
        if direct is not None and direct.exists() and _being_written(direct):
            raise ValueError("This research session is still being written and cannot be deleted.")
        return "missing"
    if not can_delete_run(path) or _being_written(path):
        if _being_written(path):
            raise ValueError("This research session is still being written and cannot be deleted.")
        raise ValueError("Only a saved research session can be deleted.")
    try:
        shutil.rmtree(path)
    except OSError:
        raise ValueError("Could not delete that research session.") from None
    if path.exists():
        raise ValueError("Could not delete that research session.")
    run_target_text.clear()
    return "deleted"


def active_run_id() -> str | None:
    run = st.session_state.get("current_run")
    if not isinstance(run, dict):
        return None
    run_id = run.get("run_id")
    if isinstance(run_id, str) and run_id:
        return run_id
    raw = run.get("run_dir")
    if isinstance(raw, str) and raw:
        return Path(raw).name
    return None


def clear_active_session() -> None:
    """Drop the open session and show the new-research screen. Does not start a run."""
    st.session_state.current_run = None
    st.session_state.followups = []
    st.session_state.selected_buyer = None
    st.session_state.panel_open = False
    st.session_state.editing_target = False
    st.session_state.edited_target = None
    st.session_state.excluded = []
    st.session_state.question_run_id = None
    st.session_state.run_question = None
    st.session_state.user_request = None
    st.session_state.messages = []
    st.session_state.pop("pending_target", None)
    st.session_state.pop("pending_question", None)
    st.session_state.pop("pending_reset_excluded", None)
    st.session_state.pending_open = None
    st.session_state.screen = "research"
    st.session_state.composing = True


def forget_deleted_run(run_id: str) -> None:
    """Refresh the lists after a delete. The open session is cleared only when it was this run."""
    st.session_state.session_runs = [
        item for item in st.session_state.get("session_runs") or []
        if Path(str(item)).name != run_id]
    pending = st.session_state.get("pending_open")
    if isinstance(pending, str) and Path(pending).name == run_id:
        st.session_state.pending_open = None
    stopped = st.session_state.get("stopped_followups") or []
    st.session_state.stopped_followups = [
        item for item in stopped if item.get("run_id") != run_id]
    if active_run_id() == run_id:
        clear_active_session()
    st.session_state.pending_delete = None
    run_target_text.clear()


def session_identity(run_id: str) -> tuple[str, str]:
    """Title and timestamp for the confirmation. A missing folder still shows its id and time."""
    try:
        plain = _plain_run_id(run_id)
    except ValueError:
        return str(run_id), ""
    path = catalog_path(plain)
    if path is None:
        return plain, run_time_text(plain)
    _sector, _size, title = run_target_text(str(path))
    return title or plain, run_time_text(path.name)


def run_label(path: Path) -> str:
    """'Healthcare Services · $200.0M · Oct 31, 22:27' from the run's saved target."""
    sector, size, _title = run_target_text(str(path))
    when = run_time_text(path.name)
    return " · ".join(part for part in (sector, size, when) if part)


def sidebar_size_text(size: str) -> str:
    """Whole millions without a trailing `.0`. Sidebar text only."""
    if size.endswith(".0M"):
        return f"{size[:-3]}M"
    return size


def sidebar_run_text(path: Path) -> tuple[str, str]:
    """Two-line recent-run label, plus the full title and time for hover and access.

    Spaces inside the time are non-breaking, so a narrow sidebar wraps before the
    date instead of leaving the clock on a line by itself.
    """
    _sector, size, title = run_target_text(str(path))
    when = run_time_text(path.name)
    shown = sidebar_size_text(size)
    # A plain colon is a markdown marker, so the clock would split into two
    # pieces and wrap in the middle. The backslash keeps it one time.
    stamp = when.replace(":", "\\:").replace(" ", "\u00a0")
    meta = " · ".join(part for part in (shown, stamp) if part)
    label = f"**{title}**  \n{meta}" if meta else f"**{title}**"
    hover = " · ".join(part for part in (title, shown, when.replace(":", "\\:")) if part)
    return as_markdown(label), as_markdown(hover)


@st.cache_data(show_spinner=False)
def run_target_text(path_str: str) -> tuple[str, str, str]:
    """Saved sector, valuation text, and the research title for one run folder.

    The title is the name stored with the run. An older file with no name uses the
    sector fallback. This does not call the model and does not rewrite the file.
    """
    try:
        data = json.loads((Path(path_str) / "reports.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", display_research_title(None, None)
    target = data.get("target") if isinstance(data, dict) else None
    if not isinstance(target, dict):
        return "", "", display_research_title(None, None)
    sector = str(target.get("sector") or "")
    stored = data.get("research_title") if isinstance(data, dict) else None
    return sector, money_text(target.get("ev_mm")), display_research_title(stored, sector)


def run_time_text(run_id: str) -> str:
    try:
        stamp = datetime.strptime(run_id[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return run_id
    return stamp.strftime("%b %d, %H:%M")
