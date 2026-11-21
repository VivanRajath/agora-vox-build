import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field

import anthropic
import openai

from . import config

log = logging.getLogger("vox-build.llm")

OPENAI_COMPATIBLE = {
    "openai": None,
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "custom": None,  # base URL comes from the user's settings
}

# Lower effort keeps voice edits snappy; full-page rewrites get a bit more.
EFFORT_BY_TIER = {"jnr": "low", "snr": "low", "architect": "medium"}

# Server-side refusal fallback is supported on these models (Claude API only).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


class LLMError(RuntimeError):
    pass


class LLMTruncated(LLMError):
    """The model hit max_tokens. Editors escalate instead of giving up."""


# A user's own keys/models (users.ai_settings), set per edit task.
# None means the server's .env configuration only.
_settings: ContextVar[dict | None] = ContextVar("ai_settings", default=None)
_clients: dict[tuple, object] = {}
# Output-token caps learned from "must be less than or equal to N" errors, per model.
_token_caps: dict[str, int] = {}


def use_settings(settings: dict | None) -> None:
    """Applies to the current asyncio task (and tasks it starts)."""
    _settings.set(settings)


def pexels_key() -> str:
    s = _settings.get() or {}
    return s.get("pexels_key") or config.PEXELS_API_KEY


def _target(tier: str) -> tuple[str, list[dict], str, bool]:
    """(provider, credentials to rotate through, model, user_chose_model) for this call.

    Credentials are the user's keys for the provider first, then the server's."""
    s = _settings.get() or {}
    model_tier = config.AGENT_TIER.get(tier, tier)
    pick = (s.get("models") or {}).get(model_tier) or {}
    provider = pick.get("provider") or next(iter(s.get("order") or []), None) or config.LLM_PROVIDER
    chosen = pick.get("model") or ""
    if chosen:
        model = chosen
    elif provider == config.LLM_PROVIDER:
        model = config.model_for(tier)  # honours MODEL_* overrides in .env
    else:
        model = config.DEFAULT_MODELS.get(provider, {}).get(model_tier, "")
    creds = list((s.get("keys") or {}).get(provider, [])) + [{"api_key": k, "base_url": ""} for k in config.ENV_KEYS.get(provider, [])]
    if creds and not model:
        raise LLMError(f"Pick a model for {model_tier} edits in Settings")
    return provider, creds, model, bool(chosen)


def client(provider: str, api_key: str, base_url: str = ""):
    key = (provider, api_key, base_url)
    if key not in _clients:
        if provider == "anthropic":
            _clients[key] = anthropic.AsyncAnthropic(api_key=api_key, timeout=120.0)
        else:
            _clients[key] = openai.AsyncOpenAI(api_key=api_key, base_url=base_url or OPENAI_COMPATIBLE[provider], timeout=120.0)
    return _clients[key]


async def list_models(provider: str, api_key: str, base_url: str = "") -> list[str]:
    """Model ids the key can use. Doubles as a key check."""
    try:
        page = await client(provider, api_key, base_url).models.list()
    except (anthropic.AuthenticationError, openai.AuthenticationError) as e:
        raise LLMError("That API key was rejected") from e
    except (anthropic.APIError, openai.APIError) as e:
        raise LLMError(f"Couldn't list models: {e}") from e
    return sorted(m.id.removeprefix("models/") for m in page.data)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


async def complete(system: str, user: str, tier: str, max_tokens: int = 16000, json_mode: bool = False) -> str:
    """json_mode asks OpenAI-compatible providers for a JSON object; callers still parse defensively."""
    reply = await chat(system, [{"role": "user", "content": user}], tier, max_tokens=max_tokens, json_mode=json_mode)
    return reply.text


async def chat(
    system: str, messages: list[dict], tier: str, tools: list[dict] | None = None,
    max_tokens: int = 16000, json_mode: bool = False,
) -> Reply:
    """One model turn over an OpenAI-style message list (user / assistant with tool_calls / tool).

    tools are neutral specs: {name, description, parameters (JSON schema)}."""
    provider, creds, model, chosen = _target(tier)
    if not creds:
        raise LLMError("No AI key is set up. Add one in Settings")
    if provider == "anthropic":
        return await _complete_anthropic(creds, system, messages, tools, model, tier, max_tokens)
    if provider in OPENAI_COMPATIBLE:
        # Model spillover only when the model wasn't picked by the user.
        fallbacks = [] if chosen else config.RATE_LIMIT_FALLBACKS.get(provider, [])
        return await _complete_openai(creds, provider, fallbacks, system, messages, tools, model, tier, max_tokens, json_mode)
    raise LLMError(f"Unknown AI provider {provider!r}")


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def _anthropic_messages(messages: list[dict]) -> list[dict]:
    """OpenAI-style history -> Anthropic content blocks, merging same-role neighbours."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            role, blocks = "user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": str(m.get("content") or "")}]
        elif role == "assistant":
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for call in m.get("tool_calls") or []:
                fn = call["function"]
                args = fn.get("arguments") or "{}"
                blocks.append({"type": "tool_use", "id": call["id"], "name": fn["name"], "input": json.loads(args) if isinstance(args, str) else args})
        elif role == "user":
            blocks = [{"type": "text", "text": text}] if (text := content_text(m.get("content"))).strip() else []
        else:
            continue  # system messages are passed separately
        if not blocks:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation started)"}]})
    return out


def content_text(content) -> str:
    """Message content as plain text (strings, or OpenAI content-part lists)."""
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


async def _complete_anthropic(creds: list[dict], system: str, messages: list[dict], tools: list[dict] | None, model: str, tier: str, max_tokens: int) -> Reply:
    """Tries each key in turn while they're rate limited."""
    for i, cred in enumerate(creds):
        last = i == len(creds) - 1
        api = client("anthropic", cred["api_key"])
        try:
            return await _anthropic_once(api if last else api.with_options(max_retries=0), system, messages, tools, model, tier, max_tokens)
        except anthropic.RateLimitError as e:
            if last:
                raise LLMError("The model is rate limited right now, try again in a moment") from e


async def _anthropic_once(api: anthropic.AsyncAnthropic, system: str, messages: list[dict], tools: list[dict] | None, model: str, tier: str, max_tokens: int) -> Reply:
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        output_config={"effort": EFFORT_BY_TIER.get(tier, "low")},
        messages=_anthropic_messages(messages),
    )
    if tools:
        kwargs["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    try:
        if model in FALLBACK_MODELS:
            response = await api.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            response = await api.messages.create(**kwargs)
    except anthropic.RateLimitError:
        raise  # the caller moves on to the next key
    except anthropic.APIStatusError as e:
        raise LLMError(f"Model API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the model API") from e

    if response.stop_reason == "refusal":
        raise LLMError("The model declined this request")
    text = "".join(block.text for block in response.content if block.type == "text")
    calls = [ToolCall(b.id, b.name, dict(b.input or {})) for b in response.content if b.type == "tool_use"]
    if response.stop_reason == "max_tokens":
        raise LLMTruncated("The model ran out of room before finishing")
    return Reply(text, calls)


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenAI, Gemini, Groq, custom)
# ---------------------------------------------------------------------------

def _limit_message(e: openai.APIStatusError) -> str:
    text = str(e.message).lower()
    if "per day" in text or "tpd" in text or "rpd" in text:
        return "The model's daily token limit is used up. Add more quota or switch the model in AI settings"
    return "The model is rate limited right now, try again in a moment"


def _openai_params(provider: str, model: str, tier: str, max_tokens: int, json_mode: bool = False) -> dict:
    # Gemini's compatibility layer and most other OpenAI-compatible servers take max_tokens;
    # newer OpenAI models want max_completion_tokens.
    params = {"max_tokens": max_tokens} if provider in ("gemini", "custom") else {"max_completion_tokens": max_tokens}
    if "gpt-oss" in model:
        # Reasoning models: keep thinking short so voice edits stay quick.
        params["reasoning_effort"] = EFFORT_BY_TIER.get(tier, "low")
    elif provider == "groq" and "qwen" in model:
        # Keep Qwen's <think> output out of the content we parse.
        params["extra_body"] = {"reasoning_format": "hidden"}
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    return params


def _openai_messages(system: str, messages: list[dict]) -> list[dict]:
    keep = ("role", "content", "tool_calls", "tool_call_id")
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m.get("role") in ("user", "assistant", "tool"):
            out.append({k: m[k] for k in keep if m.get(k) is not None})
    return out


async def _complete_openai(
    creds: list[dict], provider: str, fallbacks: list[str], system: str, messages: list[dict], tools: list[dict] | None,
    model: str, tier: str, max_tokens: int, json_mode: bool = False,
) -> Reply:
    # Every key on the chosen model first (same quality), then sibling models.
    models = [model] + [m for m in fallbacks if m != model]
    attempts = [(m, cred) for m in models for cred in creds]
    extra = {"tools": [{"type": "function", "function": t} for t in tools]} if tools else {}
    for i, (candidate, cred) in enumerate(attempts):
        last = i == len(attempts) - 1
        api = client(provider, cred["api_key"], cred.get("base_url", ""))
        # With another attempt left, fail over immediately instead of letting the SDK back off.
        attempt = api if last else api.with_options(max_retries=0)
        try:
            limit = _token_caps.get(candidate, max_tokens)
            try:
                response = await attempt.chat.completions.create(
                    model=candidate,
                    messages=_openai_messages(system, messages),
                    **extra,
                    **_openai_params(provider, candidate, tier, min(max_tokens, limit), json_mode),
                )
            except openai.BadRequestError as e:
                # "max_completion_tokens must be less than or equal to `16384`": a smaller model than the
                # tier expects (a fallback). Remember its cap and ask again with it.
                cap = re.search(r"max_completion_tokens.*?less than or equal to `?(\d+)", str(e))
                if not cap or int(cap.group(1)) >= min(max_tokens, limit):
                    raise
                _token_caps[candidate] = int(cap.group(1))
                response = await attempt.chat.completions.create(
                    model=candidate,
                    messages=_openai_messages(system, messages),
                    **extra,
                    **_openai_params(provider, candidate, tier, int(cap.group(1)), json_mode),
                )
            break
        except openai.RateLimitError as e:
            if last:
                raise LLMError(_limit_message(e)) from e
        except openai.APIStatusError as e:
            # Groq answers 413 when prompt + max_tokens exceeds the model's per-minute quota;
            # a sibling model has its own quota, so treat it like a rate limit.
            if e.status_code == 413 and not last:
                continue
            if e.status_code == 413:
                raise LLMError("This page is too large for the model's per-minute token limit. Try again in a minute or use a bigger model") from e
            # Groq rejects a reply whose tool call isn't valid JSON ("Failed to parse tool call arguments",
            # "failed_generation"); that's the model fumbling, so the next model gets a go.
            if e.status_code == 400 and not last and ("tool call" in str(e).lower() or "failed_generation" in str(e).lower()):
                log.info("%s fumbled a tool call, trying the next model", candidate)
                continue
            raise LLMError(f"Model API error ({e.status_code}): {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError("Could not reach the model API") from e

    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise LLMTruncated("The model ran out of room before finishing")
    calls = []
    for call in choice.message.tool_calls or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except ValueError:
            args = {}
        calls.append(ToolCall(call.id, call.function.name, args if isinstance(args, dict) else {}))
    return Reply(choice.message.content or "", calls)
