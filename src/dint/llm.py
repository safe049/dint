"""Thin async wrapper around an OpenAI-compatible chat completions API.

dint talks to any provider that speaks the OpenAI ``chat.completions`` protocol
with tool/function-calling support: OpenAI itself, OpenRouter, Groq, Together,
or a local Ollama server.

Configuration is resolved at call time from :mod:`dint.settings_store`, which
merges the ``.env`` defaults with any overrides the user saved from the web UI.
This means changing the API key, base URL or model in the settings panel takes
effect on the very next message (the cached client is rebuilt automatically).
"""
from __future__ import annotations

from typing import Any, Optional

from openai import AsyncOpenAI

from . import settings_store

_client: Optional[AsyncOpenAI] = None
# Signature of the config the cached client was built from. When the effective
# settings change we rebuild rather than reuse a stale client.
_client_sig: Optional[tuple[str, str]] = None


def reset_client() -> None:
    """Drop the cached client so the next call rebuilds it from fresh settings."""
    global _client, _client_sig
    _client = None
    _client_sig = None


def get_client() -> AsyncOpenAI:
    """Return a shared :class:`AsyncOpenAI` client, rebuilt if settings changed."""
    global _client, _client_sig
    cfg = settings_store.effective()
    sig = (cfg["openai_api_key"], cfg["openai_base_url"])
    if _client is None or _client_sig != sig:
        _client = AsyncOpenAI(
            api_key=cfg["openai_api_key"], base_url=cfg["openai_base_url"]
        )
        _client_sig = sig
    return _client


async def chat_completion(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Any:
    """Run a single chat completion and return the raw response object.

    ``messages`` and ``tools`` use the OpenAI wire format. The returned object
    exposes ``.choices[0].message`` which may carry ``.content`` and/or
    ``.tool_calls``.
    """
    cfg = settings_store.effective()
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model or cfg["dint_model"],
        "messages": messages,
        "temperature": cfg["dint_temperature"] if temperature is None else temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return await client.chat.completions.create(**kwargs)


async def chat_completion_stream(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
):
    """Run a streaming chat completion, yielding delta chunks.

    Each yielded object is a raw ``ChatCompletionChunk`` from the OpenAI SDK.
    The caller is responsible for accumulating content / tool-call fragments.
    """
    cfg = settings_store.effective()
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model or cfg["dint_model"],
        "messages": messages,
        "temperature": cfg["dint_temperature"] if temperature is None else temperature,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        yield chunk


async def simple_completion(
    messages: list[dict[str, Any]],
    model: Optional[str] = None,
    temperature: float = 0.2,
) -> str:
    """A no-tools completion that returns just the assistant text.

    Used by the background reflection pass, which only needs to emit JSON. When
    ``model`` is omitted it uses the configured reflection model.
    """
    cfg = settings_store.effective()
    resp = await chat_completion(
        messages,
        tools=None,
        model=model or cfg["reflect_model"],
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""
