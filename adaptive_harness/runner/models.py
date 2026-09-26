"""Model clients (CONTRACT.md section F).

Two real clients share one interface: `AnthropicClient` and `OpenAICompatClient`
(OpenAI, OpenRouter, and any endpoint that speaks the chat completions API). Both:

- resolve the calling role to a model through the resolved assignment (role override,
  else the default) and report the model that answered;
- send no sampling parameters;
- keep the request prefix stable (the system prompt is built by the caller from stable
  content only; nothing time-dependent is added here) so provider prompt caching works;
- report the stop reason and leave `parsed` empty when the reply was refused or cut off,
  so the caller records an error instead of reading a partial answer;
- retry twice on rate limits, server errors, and network errors (the SDKs' own retry
  policy), then raise `ModelUnavailable`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Mapping, Sequence

from ..contracts.config import ResolvedModel
from ..contracts.errors import ModelUnavailable
from ..contracts.protocol import ChatMessage, ModelResponse, ModelUsage

REQUEST_TIMEOUT_SECONDS = 300.0
MAX_RETRIES = 2

# Stop reasons after which the reply must not be read as an answer.
BAD_STOP_REASONS = frozenset(
    {"refusal", "max_tokens", "length", "content_filter", "model_context_window_exceeded"}
)


def extract_json(text: str) -> Any:
    """The first JSON object in `text` (bare, or inside a ``` fence), or None."""
    if not text:
        return None
    s = text.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    start = s.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(s, start)
            return obj
        except ValueError:
            start = s.find("{", start + 1)
    return None


def _as_dicts(messages: Sequence[ChatMessage | Mapping[str, str]]) -> list[dict[str, str]]:
    out = []
    for m in messages:
        d = m.model_dump() if isinstance(m, ChatMessage) else dict(m)
        out.append({"role": d["role"], "content": d["content"]})
    return out


class _BaseClient:
    def __init__(self, resolved: ResolvedModel) -> None:
        self.resolved = resolved

    def model_for(self, role: str) -> str:
        return self.resolved.assignment.get(role) or self.resolved.assignment["race_engineer"]

    @staticmethod
    def _response(text: str, stop_reason: str | None, usage: ModelUsage, started: float, model: str) -> ModelResponse:
        parsed = None if stop_reason in BAD_STOP_REASONS else extract_json(text)
        return ModelResponse(
            text=text,
            parsed=parsed,
            usage=usage,
            stop_reason=stop_reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            model=model,
        )


class AnthropicClient(_BaseClient):
    """Messages API. Every request streams and sets max_tokens to the model's maximum
    output, read once per model from the Models API."""

    def __init__(self, resolved: ResolvedModel, api_key: str | None, sdk_client: Any = None) -> None:
        super().__init__(resolved)
        if sdk_client is None:
            import anthropic

            kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT_SECONDS, "max_retries": MAX_RETRIES}
            if api_key:
                kwargs["api_key"] = api_key
            if resolved.base_url:
                kwargs["base_url"] = resolved.base_url
            sdk_client = anthropic.Anthropic(**kwargs)
        self.client = sdk_client
        self._max_tokens: dict[str, int] = {}
        self._lock = threading.Lock()

    def max_tokens_for(self, model: str) -> int:
        with self._lock:
            if model not in self._max_tokens:
                self._max_tokens[model] = int(self.client.models.retrieve(model).max_tokens)
            return self._max_tokens[model]

    def complete(
        self,
        role: str,
        system: str,
        messages: Sequence[ChatMessage | Mapping[str, str]],
        json_schema: Mapping[str, Any] | None = None,
    ) -> ModelResponse:
        import anthropic

        model = self.model_for(role)
        started = time.monotonic()
        try:
            max_tokens = self.max_tokens_for(model)
            with self.client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=_as_dicts(messages),
                cache_control={"type": "ephemeral"},
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.APIError as e:
            raise ModelUnavailable(f"{type(e).__name__}: {e}") from e
        stop = msg.stop_reason
        text = "" if stop == "refusal" else "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        usage = ModelUsage(
            input_tokens=(u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0),
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        return self._response(text, stop, usage, started, msg.model or model)


class OpenAICompatClient(_BaseClient):
    """Chat completions API (OpenAI, OpenRouter, and compatible endpoints via base_url).
    No max_tokens is sent."""

    def __init__(self, resolved: ResolvedModel, api_key: str | None, sdk_client: Any = None) -> None:
        super().__init__(resolved)
        if sdk_client is None:
            import openai

            sdk_client = openai.OpenAI(
                api_key=api_key or None,
                base_url=resolved.base_url or None,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        self.client = sdk_client

    def complete(
        self,
        role: str,
        system: str,
        messages: Sequence[ChatMessage | Mapping[str, str]],
        json_schema: Mapping[str, Any] | None = None,
    ) -> ModelResponse:
        import openai

        model = self.model_for(role)
        started = time.monotonic()
        try:
            resp = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}] + _as_dicts(messages),
            )
        except openai.APIError as e:
            raise ModelUnavailable(f"{type(e).__name__}: {e}") from e
        if not resp.choices:
            raise ModelUnavailable("the endpoint returned no choices")
        choice = resp.choices[0]
        stop = choice.finish_reason
        if getattr(choice.message, "refusal", None):
            stop = "refusal"
        text = "" if stop == "refusal" else (choice.message.content or "")
        u = resp.usage
        cached = 0
        if u is not None and getattr(u, "prompt_tokens_details", None) is not None:
            cached = getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0
        usage = ModelUsage(
            input_tokens=(u.prompt_tokens or 0) if u else 0,
            output_tokens=(u.completion_tokens or 0) if u else 0,
            cache_read_tokens=cached,
        )
        return self._response(text, stop, usage, started, resp.model or model)


def build_client(resolved: ResolvedModel, env: Mapping[str, str] | None = None):
    """The real client for the resolved provider. The key is read from MODEL_API_KEY."""
    env = os.environ if env is None else env
    api_key = env.get("MODEL_API_KEY") or None
    provider = resolved.provider.strip().lower()
    if provider == "anthropic":
        return AnthropicClient(resolved, api_key)
    if provider in ("openai", "openrouter", "openai_compatible", "openai-compatible", "compatible"):
        return OpenAICompatClient(resolved, api_key)
    raise ValueError(f"unknown MODEL_PROVIDER {resolved.provider!r} (use anthropic or openai_compatible)")
