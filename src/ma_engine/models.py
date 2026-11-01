"""Pydantic models shared by more than one module."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ma_engine.config import DEFAULT_TARGET
from ma_engine.prepare import KNOWN_VALUES, SECTORS

ANY_REGION = "Any region"
GEOGRAPHIES = [*KNOWN_VALUES["geography"], ANY_REGION]
OWNERSHIPS = KNOWN_VALUES["target_ownership_pre"]


def _allowed(name: str, value: str, allowed: list[str]) -> str:
    if value not in allowed:
        raise ValueError(f"Unknown {name} {value!r}. Allowed values: {', '.join(allowed)}.")
    return value


class Target(BaseModel):
    """The company being sold. 'strong' margin = above the target sector's median EBITDA margin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sector: str
    ev_mm: float = Field(ge=1, le=100_000, description="Enterprise value in $ millions.")
    ownership: str
    geography: str
    margin_profile: Literal["strong", "any"]

    @field_validator("sector")
    @classmethod
    def _sector(cls, value: str) -> str:
        return _allowed("sector", value, SECTORS)

    @field_validator("ownership")
    @classmethod
    def _ownership(cls, value: str) -> str:
        return _allowed("ownership", value, OWNERSHIPS)

    @field_validator("geography")
    @classmethod
    def _geography(cls, value: str) -> str:
        return _allowed("geography", value, GEOGRAPHIES)

    @classmethod
    def default(cls) -> "Target":
        return cls(**DEFAULT_TARGET)


# ---------- buyer report ----------
# ReportDraft is the only part the AI writes; its JSON schema is sent as structured output, so
# it stays flat: no unions beyond Literal, no recursion, no defaults, max item counts only.

Kind = Literal["fact", "interpretation", "unknown"]
Level = Literal["High", "Medium", "Low"]
MAX_STATEMENTS = 5
MAX_RISK_FLAGS = 4

_IDS = ("MA- transaction IDs for a deal's own figures, or ST- statistic IDs for a calculated "
        "figure (the stat_id beside its display). At least one for a fact; empty for unknown.")
_KIND = ("fact = stated in the tool results (cite IDs); interpretation = your reasoning from "
         "the facts; unknown = the data cannot answer it (no numbers, no IDs).")


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="One or two sentences. Copy every number exactly as the tools "
                                  "displayed it, in digits. Never calculate.")
    kind: Kind = Field(description=_KIND)
    source_ids: list[str] = Field(description=_IDS)


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk: str = Field(description="Short name of the risk, a few words.")
    explanation: str = Field(description="Why it matters for this buyer, from the data. Copy "
                                         "numbers exactly as displayed.")
    kind: Kind = Field(description=_KIND)
    source_ids: list[str] = Field(description=_IDS)


class Conviction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Level = Field(description="High, Medium or Low; at most one step from the starting "
                                     "conviction.")
    reason: str = Field(description="One or two sentences tied to the data, quoting display "
                                    "strings exactly.")
    source_ids: list[str] = Field(description="At least one transaction ID supporting the reason.")


class ReportDraft(BaseModel):
    """What the writer produces for one buyer. Python adds identity, status and issues.

    `summary` is one sentence: this buyer's thesis. It is checked like every other
    statement. The six sections stay lists of statements. Python joins each list
    into a paragraph, so the prose is the checked sentences.
    """

    model_config = ConfigDict(extra="forbid")

    summary: Statement = Field(description="One sentence, at most 40 words: why this buyer "
                                           "belongs on the list. Cite MA- or ST- ids.")
    acquirer_overview: list[Statement] = Field(
        max_length=MAX_STATEMENTS, description="Who the buyer is in this dataset: type, deal "
                                               "count, sectors, typical deal size.")
    strategic_fit: list[Statement] = Field(
        max_length=MAX_STATEMENTS, description="Why this buyer fits this target specifically.")
    precedent_activity: list[Statement] = Field(
        max_length=MAX_STATEMENTS, description="Specific relevant past deals. Deals that did not "
                                               "close go in their own statement, described by "
                                               "their outcome.")
    valuation_context: list[Statement] = Field(
        max_length=MAX_STATEMENTS, description="Multiples from closed deals only; every fact "
                                               "states its sample size as n=<count>.")
    risk_flags: list[RiskFlag] = Field(
        max_length=MAX_RISK_FLAGS, description="At least two different risks.")
    conviction: Conviction


class CommitteeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquirer_key: str = Field(description="The buyer's acquirer_key exactly as given.")
    level: Level = Field(description="Final conviction: the starting level or one step from it.")
    reason: str = Field(description="One or two sentences tied to this buyer's cited facts. Copy "
                                    "numbers exactly from its summary; no new numbers.")
    source_ids: list[str] = Field(description="At least one transaction ID from this buyer's "
                                              "summary.")


class SimilarPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_key: str
    second_key: str
    shared_argument: str = Field(description="The argument both fit theses make, in a few words.")


class CommitteeReview(BaseModel):
    """The committee's single structured answer over all reports in a run."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[CommitteeDecision] = Field(description="Exactly one decision per buyer.")
    similar_pairs: list[SimilarPair] = Field(description="Pairs of buyers whose strategic fit "
                                                         "theses make the same argument; empty "
                                                         "if none.")


class FollowupAnswer(BaseModel):
    """The answer to one follow-up question about a run. Python adds status and issues."""

    model_config = ConfigDict(extra="forbid")

    include_source_catalog: bool = Field(default=False, description="True when the user requests a list of all report sources. Python appends the complete saved catalog; do not squeeze it into prose.")

    statements: list[Statement] = Field(
        description="The complete answer, one claim per statement. Use as many statements as the question requires. If "
                                                      "the data cannot answer the question, one "
                                                      "unknown statement saying so.")


class FollowupResult(BaseModel):
    """A checked follow-up answer. Citation tags in `markdown` are added by Python.

    `events` uses the same dicts as a run's `trace["events"]` (see `agent.py`).
    `totals` is `wall_time_s`, `llm_calls`, `mcp_tool_calls`, `tokens_in`, `tokens_out`,
    `cost_usd` and `cache_hits`.
    """

    question: str
    source_catalog: list[dict] = Field(default_factory=list)
    statements: list[Statement]
    status: Literal["verified", "not_verified"]
    issues: list[str]
    markdown: str
    events: list[dict]
    totals: dict


class RequestFields(BaseModel):
    """What a banker's request sentence states about the target. null = not stated.

    `title` is a short name for the request, for the history list only. It is not
    part of the target, and a missing or unusable title does not reject the parse.
    """

    model_config = ConfigDict(extra="forbid")

    sector: Literal[tuple(SECTORS)] | None = Field(description="The sector, only if stated.")
    ev_value: float | None = Field(description="The enterprise value exactly as written, without "
                                               "its unit (for \"$1.2B\" write 1.2).")
    ev_unit: Literal["million", "billion"] | None = Field(
        description="The unit written with the enterprise value; null if no value.")
    ownership: Literal[tuple(OWNERSHIPS)] | None = Field(description="Ownership, only if stated.")
    geography: Literal[tuple(GEOGRAPHIES)] | None = Field(
        description=f"A named region, or \"{ANY_REGION}\" for regional or local with no region "
                    "named; null if the sentence says nothing about geography.")
    margin_profile: Literal["strong", "any"] | None = Field(
        description="strong if the sentence asks for strong or above-median EBITDA margins.")
    title: str | None = Field(
        default=None,
        description="Optional plain-text name for this request: 3 to 6 words, sentence case, "
                    "under 45 characters. No valuation, date, Markdown, quotes, emoji, or "
                    "trailing punctuation. Null when the request does not support a specific name.")

    @field_validator("title", mode="before")
    @classmethod
    def _title_text_or_empty(cls, value):
        """A non-text title is dropped. It must not fail the rest of the parse."""
        if value is None or isinstance(value, str):
            return value
        return None

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        """Ask the model for `title`, and still accept older JSON that omits it.

        Strict mode wants every property listed as required and rejects `default`.
        The Python field keeps its default, so a saved response without `title` still loads.
        """
        schema = super().model_json_schema(*args, **kwargs)
        prop = schema.get("properties", {}).get("title")
        if isinstance(prop, dict):
            prop.pop("default", None)
        required = schema.setdefault("required", [])
        if "title" not in required:
            required.append("title")
        return schema


class BuyerReport(BaseModel):
    """A checked report. Identity comes from the ranking entry; status and issues from Python.

    `status` is `verified`, `not_verified` (a report that failed the checks) or `failed`
    (research raised an error). A failed buyer keeps its rank. `draft` is null and
    `error` holds the message. There is no report body and no substitute buyer.
    """

    model_config = ConfigDict(extra="forbid")

    acquirer_key: str
    display_name: str
    acquirer_type: str
    rank: int
    score_rank: int
    score: str
    selection_note: str | None = None
    starting_level: Level
    draft: ReportDraft | None = None
    status: Literal["verified", "not_verified", "failed"]
    issues: list[str]
    error: str | None = None

    @classmethod
    def from_ranking(cls, entry: dict, draft: ReportDraft) -> "BuyerReport":
        """Unchecked report: not verified until `guardrails.apply_check` says otherwise."""
        return cls(
            acquirer_key=entry["acquirer_key"],
            display_name=entry["display_name"],
            acquirer_type=entry["acquirer_type"],
            rank=entry["rank"],
            score_rank=entry["score_rank"],
            score=entry["score"]["display"],
            selection_note=entry.get("selection_note"),
            starting_level=entry["starting_conviction"]["level"],
            draft=draft,
            status="not_verified",
            issues=["Not checked by guardrails yet."],
        )
