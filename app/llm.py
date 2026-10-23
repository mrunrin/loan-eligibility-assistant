"""Groq chat-completions client: one async call, one sync stream, both retrying 429s.

Every call is a Langfuse generation with Groq's reported token usage, and adds that usage to the
request's token meter (see start_usage_meter) so the borrower budget counts real tokens.
"""

import asyncio
import json
import time
from collections.abc import Iterator
from contextvars import ContextVar

import httpx
import requests

from app.config import GROQ_API_KEY, GROQ_MODEL
from app.tracing import langfuse

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MAX_RETRIES = 3

# Per-request running total of Groq tokens. A mutable list so calls made in worker threads
# (which run on a copy of the context) still add to the same counter.
_usage_meter: ContextVar[list[int] | None] = ContextVar("groq_usage_meter", default=None)


def start_usage_meter() -> list[int]:
    """Start counting Groq tokens for the current request; read meter[0] when it is done."""
    meter = [0]
    _usage_meter.set(meter)
    return meter


def _record_usage(generation, usage: dict | None) -> None:
    if not usage:
        return
    generation.update(usage_details={
        "input": int(usage.get("prompt_tokens") or 0), "output": int(usage.get("completion_tokens") or 0),
    })
    meter = _usage_meter.get()
    if meter is not None:
        meter[0] += int(usage.get("total_tokens") or 0)


def _retry_delay(headers, attempt: int) -> float:
    """Honour Groq's retry-after on 429 (capped), else exponential backoff."""
    try:
        return min(float(headers.get("retry-after", "")), 10.0)
    except ValueError:
        return float(2 ** attempt)


async def groq_chat(messages: list[dict], model: str = GROQ_MODEL, name: str = "llm-generation", **extra) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0, **extra}
    with langfuse.start_as_current_observation(name=name, as_type="generation", model=model, input=messages) as generation:
        async with httpx.AsyncClient(timeout=120) as client:
            for attempt in range(GROQ_MAX_RETRIES + 1):
                response = await client.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json=payload)
                if response.status_code != 429 or attempt == GROQ_MAX_RETRIES:
                    break
                await asyncio.sleep(_retry_delay(response.headers, attempt))
            response.raise_for_status()
            body = response.json()
        content = body["choices"][0]["message"]["content"]
        generation.update(output=content)
        _record_usage(generation, body.get("usage"))
        return content


async def complete(prompt: str) -> str:
    return await groq_chat([{"role": "user", "content": prompt}], reasoning_effort="low")


def stream_completion(prompt: str) -> Iterator[str]:
    messages = [{"role": "user", "content": prompt}]
    generation = langfuse.start_observation(name="llm-generation", as_type="generation", model=GROQ_MODEL, input=messages)
    output = ""
    try:
        for attempt in range(GROQ_MAX_RETRIES + 1):
            response = requests.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": GROQ_MODEL,
                    "messages": messages,
                    "temperature": 0,
                    "stream": True,
                    "stream_options": {"include_usage": True},  # final chunk carries token usage
                },
                stream=True,
                timeout=120,
            )
            if response.status_code != 429 or attempt == GROQ_MAX_RETRIES:
                break
            response.close()
            time.sleep(_retry_delay(response.headers, attempt))
        response.raise_for_status()
        response.encoding = "utf-8"  # Groq sends text/event-stream with no charset; requests defaults to ISO-8859-1
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            # Groq reports usage on the last chunk, as "usage" or inside its "x_groq" extension.
            _record_usage(generation, chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage"))
            choices = chunk.get("choices") or []
            content = choices[0].get("delta", {}).get("content", "") if choices else ""
            if content:
                output += content
                yield content
    finally:
        generation.update(output=output)
        generation.end()


def provider_ready() -> tuple[bool, str]:
    if not GROQ_API_KEY:
        return False, "Groq API key is not configured. Add GROQ_API_KEY to your .env file."
    return True, ""
