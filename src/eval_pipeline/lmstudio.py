"""LM Studio orchestration: `lms load/unload/ps` plus an OpenAI-compatible
chat client with retries, timeouts, token accounting, and JSON mode.

Usage:
    with model_session("qwen3.5-9b-mtp") as m:
        text, usage = m.chat([{"role": "user", "content": "hi"}])
        data, usage = m.chat_json(messages, schema=schema_dict)
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from .config import LMStudioSettings, load_settings

log = logging.getLogger(__name__)


class LMStudioError(RuntimeError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class TokenAccount:
    """Cumulative token usage across a session."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, usage: Usage) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.calls += 1


def _run_lms(*args: str, timeout: float = 300) -> str:
    cmd = ["lms", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise LMStudioError(f"`{' '.join(cmd)}` failed: {proc.stderr.strip()}")
    return proc.stdout


def loaded_models() -> list[str]:
    """Identifiers of models currently loaded, per `lms ps --json`."""
    try:
        out = _run_lms("ps", "--json")
        return [m.get("identifier") or m.get("modelKey", "") for m in json.loads(out)]
    except (json.JSONDecodeError, LMStudioError):
        # Fall back to plain `lms ps` text parsing (no models -> message text).
        out = _run_lms("ps")
        if "No models" in out:
            return []
        return [line.split()[0] for line in out.splitlines()[1:] if line.strip()]


def load_model(model: str, context_length: int | None = None,
               timeout: float = 300) -> None:
    args = ["load", model, "--yes"]
    if context_length:
        args += ["--context-length", str(context_length)]
    _run_lms(*args, timeout=timeout)


def unload_model(model: str) -> None:
    try:
        _run_lms("unload", model)
    except LMStudioError as e:
        log.warning("unload %s failed: %s", model, e)


def unload_all() -> None:
    try:
        _run_lms("unload", "--all")
    except LMStudioError as e:
        log.warning("unload --all failed: %s", e)


@dataclass
class ModelClient:
    model: str
    settings: LMStudioSettings
    temperature: float = 0.7
    max_tokens: int = 4096
    seed: int | None = None
    account: TokenAccount = field(default_factory=TokenAccount)

    def _post_chat(self, payload: dict) -> dict:
        last_err: Exception | None = None
        for attempt in range(self.settings.retries + 1):
            try:
                with httpx.Client(timeout=self.settings.request_timeout) as client:
                    resp = client.post(
                        f"{self.settings.base_url}/chat/completions", json=payload
                    )
                    resp.raise_for_status()
                    return resp.json()
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                last_err = e
                log.warning("chat attempt %d failed: %s", attempt + 1, e)
                time.sleep(2 ** attempt)
        raise LMStudioError(f"chat failed after retries: {last_err}")

    def _base_payload(self, messages: list[dict], **overrides: Any) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        payload.update(overrides)
        return payload

    def _extract(self, data: dict) -> tuple[str, Usage]:
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError) as e:
            raise LMStudioError(f"malformed chat response: {data}") from e
        if not (content or "").strip():
            # Reasoning models (qwen3.5/3.6) emit a thinking phase that LM
            # Studio routes into `reasoning_content`. Under a structured-output
            # grammar the model can never emit `</think>`, so the entire
            # completion — including the final answer — lands there with
            # `content` empty. Recover it when the model finished cleanly.
            reasoning = (message.get("reasoning_content") or "").strip()
            if choice.get("finish_reason") == "stop" and reasoning:
                content = reasoning
            else:
                raise LMStudioError(
                    "empty completion "
                    f"(finish_reason={choice.get('finish_reason')!r}): the "
                    "model likely exhausted max_tokens while thinking — "
                    "raise max_tokens for this model")
        u = data.get("usage") or {}
        usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                      u.get("total_tokens", 0))
        self.account.add(usage)
        return content, usage

    def chat(self, messages: list[dict], **overrides: Any) -> tuple[str, Usage]:
        data = self._post_chat(self._base_payload(messages, **overrides))
        return self._extract(data)

    def chat_json(self, messages: list[dict], schema: dict | None = None,
                  **overrides: Any) -> tuple[dict, Usage]:
        """JSON-mode chat. Uses LM Studio structured output when a schema is
        given; falls back to json_object mode otherwise. Raises LMStudioError
        if the reply is not parseable JSON."""
        if schema is not None:
            fmt = {"type": "json_schema",
                   "json_schema": {"name": "response", "strict": True, "schema": schema}}
        else:
            fmt = {"type": "json_object"}
        data = self._post_chat(
            self._base_payload(messages, response_format=fmt, **overrides)
        )
        content, usage = self._extract(data)
        try:
            return json.loads(_strip_fences(content)), usage
        except json.JSONDecodeError as e:
            raise LMStudioError(f"model returned invalid JSON: {content[:500]}") from e


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text


@contextmanager
def model_session(model: str, settings: LMStudioSettings | None = None,
                  temperature: float = 0.7, max_tokens: int = 4096,
                  seed: int | None = None, context_length: int | None = None,
                  keep_loaded: bool = False) -> Iterator[ModelClient]:
    """Load a model (unloading everything else first — Apple Silicon memory
    is the constraint), yield a client, unload on exit."""
    settings = settings or load_settings().lmstudio
    current = loaded_models()
    if model not in current:
        if current:
            unload_all()
        load_model(model, context_length=context_length,
                   timeout=settings.load_timeout)
    client = ModelClient(model=model, settings=settings, temperature=temperature,
                         max_tokens=max_tokens, seed=seed)
    try:
        yield client
    finally:
        if not keep_loaded:
            unload_model(model)


def server_reachable(settings: LMStudioSettings | None = None) -> bool:
    settings = settings or load_settings().lmstudio
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{settings.base_url}/models")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
