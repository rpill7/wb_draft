"""OpenRouter chat client: one per run, with a disk cache, usage and cost, structured output.

Requests go through the `openai` library pointed at OpenRouter. The library's own retry
handles temporary failures only (connection errors, timeouts, 408, 409, 429 and 5xx, with
exponential backoff); business errors such as 400 or 401 are raised at once.

Structured output uses `response_format` json_schema with `strict: true` and
`provider.require_parameters: true`, so OpenRouter routes only to providers that support every
parameter sent. Because of that, sampling parameters the model does not list as supported
(for example `temperature` and `seed` on `anthropic/claude-sonnet-5.5`) are left out; the
model's supported parameters are read once from OpenRouter's model list (the engine reads them
before research starts, so a wrong model id stops the run once). If the list is empty, no
optional parameters are sent and the call records a warning.
"""

import asyncio
import copy
import hashlib
import json
import os
import time
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from ma_engine import config

MAX_OUTPUT_TOKENS = 4096
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


def _parse(content: str | None, schema: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    text = (content or "").strip()
    # Some providers wrap JSON in a Markdown fence even in json_schema mode.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return schema.model_validate_json(text), None
    except ValidationError as exc:
        return None, f"The output is not a valid {schema.__name__}: {exc}"


class LLMClient:
    """One OpenRouter client per run. Keeps running totals of calls, tokens and cost."""

    def __init__(self, client: AsyncOpenAI | None = None):
        self._client = client or AsyncOpenAI(
            base_url=config.OPENROUTER_BASE_URL, api_key=config.require_api_key(),
            timeout=config.REQUEST_TIMEOUT_S, max_retries=config.MAX_RETRIES)
        if not config.LLM_MODEL:
            raise LLMConfigError("LLM_MODEL is not set. Copy .env.example to .env and set the "
                               "OpenRouter model id, for example anthropic/claude-sonnet-5.5.")
        self.model = config.LLM_MODEL
        self._supported: set[str] | None = None
        self._lock = asyncio.Lock()
        self.totals = {"calls": 0, "api_calls": 0, "cache_hits": 0, "prompt_tokens": 0,
                       "completion_tokens": 0, "cost_usd": 0.0}

    async def supported_parameters(self) -> set[str]:
        """The model's supported request parameters, as listed by OpenRouter (read once)."""
        async with self._lock:
            if self._supported is None:
                async for model in self._client.models.list():
                    if model.id == self.model:
                        self._supported = set((model.model_extra or {}).get("supported_parameters")
                                              or [])
                        break
                else:
                    raise LLMConfigError(f"LLM_MODEL {self.model!r} is not in OpenRouter's model "
                                       "list. Check the model id in .env.")
        return self._supported

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   tool_choice: str | None = None, response_schema: type[BaseModel] | None = None,
                   use_cache: bool = True) -> LLMResult:
        """One chat completion. `response_schema` asks for structured output of that model."""
        started = time.perf_counter()
        request = {"model": self.model, "messages": messages, "tools": tools,
                   "tool_choice": tool_choice,
                   "schema": response_schema and strict_schema(response_schema),
                   "temperature": config.TEMPERATURE, "seed": config.SEED,
                   "reasoning_effort": config.REASONING_EFFORT, "max_tokens": MAX_OUTPUT_TOKENS}
        path = config.CACHE_DIR / f"{_hash(request)}.json"
        cached = use_cache and path.is_file()
        if cached:
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = await self._call(request)
        result = LLMResult(content=raw["content"], tool_calls=raw["tool_calls"], cache_hit=cached)
        if response_schema is not None:
            result.parsed, result.parse_error = _parse(result.content, response_schema)
        if not cached:
            result.prompt_tokens, result.completion_tokens = raw["prompt_tokens"], raw["completion_tokens"]
            result.cost_usd, result.dropped_parameters = raw["cost_usd"], raw["dropped_parameters"]
            result.warnings = raw["warnings"]
            if result.parse_error is None:
                _write_atomic(path, raw)
        result.duration_ms = round((time.perf_counter() - started) * 1000)
        self._count(result)
        return result

    async def _call(self, request: dict) -> dict:
        supported = await self.supported_parameters()
        optional = {name: request[name] for name in OPTIONAL_PARAMETERS}
        sent = {k: v for k, v in optional.items() if k in supported}
        kwargs = {"model": request["model"], "messages": request["messages"], **sent}
        if request["tools"]:
            kwargs["tools"] = request["tools"]
        if request["tool_choice"]:
            kwargs["tool_choice"] = request["tool_choice"]
        if request["schema"]:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                "name": request["schema"].get("title", "output"), "strict": True,
                "schema": request["schema"]}}
            kwargs["extra_body"] = {"provider": {"require_parameters": True}}
        response = await self._client.chat.completions.create(**kwargs)
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
            "warnings": [] if supported else [EMPTY_SUPPORTED_WARNING],
        }

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
