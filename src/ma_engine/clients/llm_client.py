"""OpenRouter chat client: one per run, with a disk cache, usage and cost, structured output.

Requests go through the `openai` library pointed at OpenRouter. The library's own retries are
off (`max_retries=0`). This client retries temporary failures itself: connection errors,
timeouts, and HTTP 408, 409, 429 and 5xx, with provider cooldowns for rate limits and exponential backoff otherwise, up to
`config.MAX_RETRIES` attempts. Business errors such as 400 or 401 are raised at once.
Each attempt's error text is returned on the result (or on the exception if every attempt
fails) so the activity event can record it. The API key, response headers and environment
values are never included.

Structured output uses strict json_schema when advertised by the selected model;
otherwise JSON mode or a schema-instruction fallback is used. Every path is parsed
through the same Pydantic schema and downstream evidence checks. Research requires
advertised tool support and fails clearly before a run when it is absent.
Optional parameters come from OpenRouter model metadata. Unified reasoning is used
when advertised, otherwise the reasoning_effort parameter is sent only if supported.
Reasoning text is not retained. Cache identity includes the selected model and the
capability adapter version.

"""

import asyncio
import copy
import hashlib
import json
import os
import time
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from ma_engine import config
from ma_engine.app_logging import logger

# Fallback when a caller does not pass max_tokens. Research and write caps live in agent.py.
MAX_OUTPUT_TOKENS = 8000
# Anthropic's strict mode rejects these keywords (400); Pydantic still enforces them after parsing.
UNSUPPORTED_SCHEMA_KEYWORDS = {"maxItems": "At most {} items.", "minItems": "At least {} items.",
                               "minimum": "Minimum {}.", "maximum": "Maximum {}.",
                               "minLength": "At least {} characters.",
                               "maxLength": "At most {} characters."}
OPTIONAL_PARAMETERS = ("temperature", "seed", "reasoning_effort", "max_tokens")
EMPTY_SUPPORTED_WARNING = ("OpenRouter lists no supported parameters for this model, so no "
                           "optional parameters were sent.")


class LLMConfigError(RuntimeError):
    """The model setting cannot be used, so every call in the run would fail the same way."""


@dataclass
class LLMResult:
    content: str | None
    tool_calls: list[dict]  # [{"id", "name", "arguments" (JSON text)}]
    parsed: BaseModel | None = None
    parse_error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    duration_ms: int = 0
    dropped_parameters: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # One entry per try: {"n": 1, "error": None} on success. `error` is the safe text of a failure.
    attempts: list[dict] = field(default_factory=lambda: [{"n": 1, "error": None}])
    finish_reason: str | None = None
    # True when the provider stopped for length, or structured JSON ended before it closed.
    truncated: bool = False

    def assistant_message(self) -> dict:
        """This response as a chat message, to append to the conversation."""
        message = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in self.tool_calls
            ]
        return message


def strict_schema(model: type[BaseModel]) -> dict:
    """The model's JSON schema with keywords that strict mode rejects moved into descriptions."""
    schema = copy.deepcopy(model.model_json_schema())

    def clean(node):
        if isinstance(node, dict):
            # OpenAI rejects descriptions beside $ref in strict output schemas.
            # Inline that referenced definition, preserving all field constraints.
            ref = node.get("$ref", "")
            if len(node) > 1 and ref.startswith("#/$defs/"):
                definition = copy.deepcopy(schema["$defs"][ref.removeprefix("#/$defs/")])
                definition.update({key: value for key, value in node.items() if key != "$ref"})
                node.clear()
                node.update(definition)
            if node.get("type") == "object" and "properties" in node:
                # Strict providers require every output field, including fields with
                # a local Pydantic default. The default remains enforced locally.
                node["required"] = list(node["properties"])
            notes = [note.format(node.pop(key)) for key, note in UNSUPPORTED_SCHEMA_KEYWORDS.items()
                     if key in node]
            if notes:
                node["description"] = " ".join([node.get("description", ""), *notes]).strip()
            for value in node.values():
                clean(value)
        elif isinstance(node, list):
            for value in node:
                clean(value)

    clean(schema)
    return schema


def scrub_secret(text: str) -> str:
    """The text with the API key removed. Safe to store in a trace or an event."""
    secret = config.OPENROUTER_API_KEY
    if secret and secret in text:
        text = text.replace(secret, "***")
    return text


def _public_error(exc: Exception) -> str:
    """Error text for an event: type and message only, never headers or the API key."""
    text = f"{type(exc).__name__}: {exc}"
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        metadata = body.get("metadata") or {}
        raw = metadata.get("raw") if isinstance(metadata, dict) else None
        if isinstance(raw, str):
            try:
                provider_error = json.loads(raw).get("error", {})
                detail = provider_error.get("message") if isinstance(provider_error, dict) else None
                if isinstance(detail, str) and detail not in text:
                    text += ": " + detail
            except (ValueError, AttributeError):
                pass
    return scrub_secret(text)


def _retryable(exc: Exception) -> bool:
    """Connection errors, timeouts, and HTTP 408, 409, 429 and 5xx."""
    if isinstance(exc, openai.APIConnectionError):
        return True
    status = getattr(exc, "status_code", None)
    return status in (408, 409, 429) or (isinstance(status, int) and 500 <= status <= 599)


def _backoff(attempt: int) -> float:
    """Seconds to wait after a failed attempt, before the next one. Short, and it doubles."""
    return 0.2 * (2 ** (attempt - 1))


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Honor provider cooldowns; missing 429 guidance uses a one-minute cooldown."""
    headers = getattr(getattr(exc, "response", None), "headers", {})
    value = headers.get("retry-after")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    return 60.0 if getattr(exc, "status_code", None) == 429 else _backoff(attempt)


def _json_text(content: str | None) -> str:
    text = (content or "").strip()
    # Some providers wrap JSON in a Markdown fence even in json_schema mode.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text


def json_was_cut_off(content: str | None) -> bool:
    """True when `content` is JSON that stops before the value is closed."""
    text = _json_text(content)
    if not text:
        return False
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return "Unterminated" in exc.msg or exc.pos >= len(text.rstrip())
    return False


def _parse(content: str | None, schema: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    try:
        return schema.model_validate_json(_json_text(content)), None
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'output'}: {item['msg']}"
            + (f" (you gave {len(item['input'])})" if item["type"] == "too_long" else "")
            for item in exc.errors())
        return None, f"The output is not a valid {schema.__name__}. Fix exactly these fields: {problems}"


class LLMClient:
    """One OpenRouter client per run. Keeps running totals of calls, tokens and cost."""

    def __init__(self, client: AsyncOpenAI | None = None):
        self._client = client or AsyncOpenAI(
            base_url=config.OPENROUTER_BASE_URL, api_key=config.require_api_key(),
            timeout=config.REQUEST_TIMEOUT_S, max_retries=0)
        if not config.LLM_MODEL:
            raise LLMConfigError("LLM_MODEL is not set. Copy .env.example to .env and set the "
                               "OpenRouter model id, for example anthropic/claude-sonnet-5.5.")
        self.model = config.LLM_MODEL
        self._supported: set[str] | None = None
        self._max_completion_tokens: int | None = None
        self._reasoning: dict | None = None
        self._lock = asyncio.Lock()
        self.totals = {"calls": 0, "api_calls": 0, "cache_hits": 0, "prompt_tokens": 0,
                       "completion_tokens": 0, "cost_usd": 0.0}

    async def supported_parameters(self) -> set[str]:
        """The model's supported request parameters, as listed by OpenRouter (read once)."""
        async with self._lock:
            if self._supported is None:
                async for model in self._client.models.list():
                    if model.id == self.model:
                        self._reasoning = (model.model_extra or {}).get("reasoning")
                        limit = ((model.model_extra or {}).get("top_provider") or {}).get("max_completion_tokens")
                        self._max_completion_tokens = limit if isinstance(limit, int) and limit > 0 else None
                        self._supported = set((model.model_extra or {}).get("supported_parameters")
                                              or [])
                        break
                else:
                    raise LLMConfigError(f"LLM_MODEL {self.model!r} is not in OpenRouter's model "
                                       "list. Check the model id in .env.")
        return self._supported

    async def validate_research_model(self) -> None:
        supported = await self.supported_parameters()
        if "tools" not in supported:
            raise LLMConfigError(f"The selected model {self.model!r} does not advertise tool calling, which this MCP research workflow requires. Select an OpenRouter model with tool support.")

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   tool_choice: str | None = None, response_schema: type[BaseModel] | None = None,
                   use_cache: bool = True, max_tokens: int | None = None) -> LLMResult:
        """One chat completion. `response_schema` asks for structured output of that model.

        `max_tokens` is the output cap. Callers that write reports pass their own cap
        (`agent.WRITE_MAX_TOKENS`, `agent.RESEARCH_MAX_TOKENS`).
        """
        started = time.perf_counter()
        request = {"model": self.model, "messages": messages, "tools": tools,
                   "tool_choice": tool_choice,
                   "schema": response_schema and strict_schema(response_schema),
                   "temperature": config.TEMPERATURE, "seed": config.SEED,
                   "reasoning_effort": config.REASONING_EFFORT, "reasoning_transport": "capability-v3",
                   "max_tokens": MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens}
        path = config.CACHE_DIR / f"{_hash(request)}.json"
        cached = use_cache and path.is_file()
        if cached:
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = await self._call(request)
        attempts = raw.pop("attempts", None)
        result = LLMResult(content=raw["content"], tool_calls=raw["tool_calls"], cache_hit=cached,
                           attempts=attempts or [{"n": 1, "error": None}],
                           finish_reason=raw.get("finish_reason"))
        if response_schema is not None:
            result.parsed, result.parse_error = _parse(result.content, response_schema)
        result.truncated = result.finish_reason == "length" or (
            response_schema is not None and json_was_cut_off(result.content))
        if not cached:
            result.prompt_tokens, result.completion_tokens = raw["prompt_tokens"], raw["completion_tokens"]
            result.cost_usd, result.dropped_parameters = raw["cost_usd"], raw["dropped_parameters"]
            result.warnings = raw["warnings"]
            if result.parse_error is None and not result.truncated:
                _write_atomic(path, raw)
        result.duration_ms = round((time.perf_counter() - started) * 1000)
        self._count(result)
        return result

    async def _call(self, request: dict) -> dict:
        supported = await self.supported_parameters()
        optional = {name: request[name] for name in OPTIONAL_PARAMETERS}
        sent = {k: v for k, v in optional.items() if k in supported}
        if "max_tokens" in sent and self._max_completion_tokens:
            sent["max_tokens"] = min(sent["max_tokens"], self._max_completion_tokens)
        unified_reasoning = "reasoning" in supported
        if unified_reasoning:
            sent["reasoning_effort"] = request["reasoning_effort"]
        kwargs = {"model": request["model"], "messages": request["messages"], **sent}
        reasoning_notes = []
        if unified_reasoning:
            kwargs.pop("reasoning_effort", None)
            reasoning = {"effort": request["reasoning_effort"], "exclude": True}
            if isinstance(self._reasoning, dict):
                efforts = self._reasoning.get("supported_efforts")
                if isinstance(efforts, list) and efforts and request["reasoning_effort"] not in efforts:
                    order = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
                    effort = next((level for level in order if level in efforts), efforts[0])
                    reasoning["effort"] = effort
                    reasoning_notes.append(f"Requested reasoning effort is unavailable; using the model's lowest advertised effort {effort}.")
                elif "supported_efforts" not in self._reasoning and not self._reasoning.get("supports_max_tokens"):
                    enabled = bool(self._reasoning.get("mandatory")) or request["reasoning_effort"] not in ("none", "minimal", "low")
                    reasoning = {"enabled": enabled, "exclude": True}
                    reasoning_notes.append(f"Model advertises reasoning on/off only; configured {request['reasoning_effort']} maps to reasoning {'on' if enabled else 'off'}.")
            kwargs["extra_body"] = {"reasoning": reasoning}
        writing = request["tool_choice"] == "none"
        if request["tools"] and not writing:
            if "tools" not in supported:
                raise LLMConfigError(f"The selected model {self.model!r} does not advertise tool calling required for MCP research.")
            kwargs["tools"] = request["tools"]
            if request["tool_choice"] and "tool_choice" in supported:
                kwargs["tool_choice"] = request["tool_choice"]
        if request["schema"]:
            if "structured_outputs" in supported:
                kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": request["schema"].get("title", "output"), "strict": True,
                    "schema": request["schema"]}}
                kwargs.setdefault("extra_body", {})["provider"] = {"require_parameters": True}
            else:
                # Provider-side constrained decoding is optional. Local schema and
                # evidence validation are mandatory on every path.
                if "response_format" in supported:
                    kwargs["response_format"] = {"type": "json_object"}
                kwargs["messages"] = [*request["messages"], {"role": "user", "content":
                    "Return only a JSON object matching this schema. No Markdown fences. " + json.dumps(request["schema"])}]
        response, attempts = await self._create(kwargs)
        if not response.choices:
            raise RuntimeError(f"OpenRouter returned no choices: "
                               f"{(response.model_extra or {}).get('error')}")
        message = response.choices[0].message
        usage = response.usage.model_dump() if response.usage else {}
        return {
            "content": message.content,
            "tool_calls": [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                           for c in message.tool_calls or []],
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "cost_usd": float(usage.get("cost") or 0.0),
            "dropped_parameters": sorted(set(optional) - set(sent)),
            "warnings": reasoning_notes + ([] if supported else [EMPTY_SUPPORTED_WARNING]),
            "attempts": attempts,
            "finish_reason": response.choices[0].finish_reason,
        }

    async def _create(self, kwargs: dict):
        """One completion, retried up to MAX_RETRIES times. Returns (response, attempts)."""
        attempts = []
        for n in range(1, config.MAX_RETRIES + 1):
            delay = getattr(self, "_cooldown_until", 0) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                response = await self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                attempts.append({"n": n, "error": _public_error(exc)})
                exc.attempts = list(attempts)
                if not _retryable(exc) or n == config.MAX_RETRIES:
                    raise
                delay = _retry_delay(exc, n)
                logger.warning("model=%s retry_after_s=%s attempt=%s error=%s", self.model, delay, n,
                               _public_error(exc).replace("\n", " ").replace("\r", " "))
                if getattr(exc, "status_code", None) == 429:
                    self._cooldown_until = max(getattr(self, "_cooldown_until", 0),
                                               time.monotonic() + delay)
                else:
                    await asyncio.sleep(delay)
            else:
                attempts.append({"n": n, "error": None})
                return response, attempts
        raise RuntimeError("LLM retry loop ended without a result or an error.")

    def _count(self, result: LLMResult) -> None:
        t = self.totals
        t["calls"] += 1
        t["cache_hits"] += result.cache_hit
        t["api_calls"] += not result.cache_hit
        t["prompt_tokens"] += result.prompt_tokens
        t["completion_tokens"] += result.completion_tokens
        t["cost_usd"] = round(t["cost_usd"] + result.cost_usd, 6)


def _hash(request: dict) -> str:
    text = json.dumps(request, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_atomic(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{id(data)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
