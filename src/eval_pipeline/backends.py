"""Inference-backend abstraction.

Two backends serve the same OpenAI-compatible chat API but differ in how a
model gets loaded:

- ``lmstudio``  — LM Studio on the Mac; models are swapped with the `lms` CLI.
- ``llamacpp``  — `llama-server` (llama.cpp) in Docker on the Linux box;
  models are swapped by recreating the compose service with a different
  ``LLAMA_MODEL`` (one model at a time on the single RTX 3090).

``model_session`` is the backend-aware replacement for
``lmstudio.model_session``: it resolves the active backend from settings,
ensures the requested model is being served, and yields the shared
``ModelClient``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

import httpx

from . import lmstudio
from .config import (LlamaCppSettings, LMStudioSettings, Settings,
                     load_settings)
from .lmstudio import ModelClient

log = logging.getLogger(__name__)


class BackendError(RuntimeError):
    pass


class LMStudioBackend:
    name = "lmstudio"

    def __init__(self, settings: LMStudioSettings):
        self.settings = settings

    @property
    def chat_settings(self) -> LMStudioSettings:
        return self.settings

    def ensure_loaded(self, model: str, context_length: int | None = None) -> None:
        current = lmstudio.loaded_models()
        if model not in current:
            if current:
                lmstudio.unload_all()
            lmstudio.load_model(model, context_length=context_length,
                                timeout=self.settings.load_timeout)

    def release(self, model: str, keep_loaded: bool = False) -> None:
        if not keep_loaded:
            lmstudio.unload_model(model)

    def reachable(self) -> bool:
        return lmstudio.server_reachable(self.settings)

    def version(self) -> str:
        # `lms version` prints an ANSI-colored ASCII-art banner; the version
        # identity is a "... version ..." or "CLI commit: ..." line.
        try:
            out = lmstudio._run_lms("version", timeout=10)
        except Exception:  # noqa: BLE001 - best effort only
            return ""
        clean = re.sub(r"\x1b\[[0-9;]*m", "", out)
        for line in clean.splitlines():
            line = line.strip()
            if re.search(r"(version|commit)[:\s]", line, re.IGNORECASE):
                return line
        return ""


class LlamaCppBackend:
    name = "llamacpp"

    def __init__(self, settings: LlamaCppSettings):
        self.settings = settings

    @property
    def chat_settings(self) -> LlamaCppSettings:
        return self.settings

    # -- http helpers ---------------------------------------------------

    @property
    def _root(self) -> str:
        # base_url points at .../v1; server endpoints like /health live above it
        return self.settings.base_url.rsplit("/v1", 1)[0]

    def _get(self, path: str, timeout: float = 5) -> httpx.Response:
        with httpx.Client(timeout=timeout) as client:
            return client.get(f"{self._root}{path}")

    def reachable(self) -> bool:
        try:
            return self._get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def current_model(self) -> str | None:
        """Basename of the gguf the server is currently serving, if any."""
        try:
            data = self._get("/v1/models").json()
            for m in data.get("data", []):
                if m.get("id"):
                    return PurePosixPath(m["id"]).name
        except (httpx.HTTPError, ValueError):
            pass
        return None

    def version(self) -> str:
        try:
            props = self._get("/props").json()
            return str(props.get("build_info") or "")
        except (httpx.HTTPError, ValueError):
            return ""

    # -- lifecycle --------------------------------------------------------

    def _model_path(self, model: str) -> str:
        if model.startswith("/"):
            return model
        return f"{self.settings.container_model_dir.rstrip('/')}/{model}"

    def ensure_loaded(self, model: str, context_length: int | None = None) -> None:
        target = self._model_path(model)
        want = PurePosixPath(target).name
        if self.reachable() and self.current_model() == want:
            log.info("llamacpp: %s already being served, reusing", want)
            return
        compose_file = Path(self.settings.compose_dir) / "docker-compose.yml"
        if not compose_file.exists():
            raise BackendError(f"compose file not found: {compose_file}")
        env = {"LLAMA_MODEL": target}
        if context_length:
            env["LLAMA_CTX_SIZE"] = str(context_length)
        log.info("llamacpp: (re)starting %s with %s — loading into VRAM "
                 "takes a while", self.settings.compose_service, want)
        proc = subprocess.run(
            ["docker", "compose", "up", "-d", "--force-recreate",
             self.settings.compose_service],
            cwd=self.settings.compose_dir, env={**os.environ, **env},
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise BackendError(
                f"docker compose up failed: {proc.stderr.strip()[-500:]}")
        deadline = time.monotonic() + self.settings.load_timeout
        while time.monotonic() < deadline:
            if self.reachable():
                served = self.current_model()
                if served and served != want:
                    raise BackendError(
                        f"server came up serving {served!r}, expected {want!r}")
                log.info("llamacpp: %s ready", want)
                return
            time.sleep(3)
        raise BackendError(
            f"llama-server not healthy after {self.settings.load_timeout:.0f}s — "
            f"inspect with: docker compose -f {compose_file} logs "
            f"{self.settings.compose_service}")

    def release(self, model: str, keep_loaded: bool = False) -> None:
        # Leaving the container running is harmless (and keeps the model warm
        # for the next stage); ensure_loaded swaps it when the model changes.
        log.debug("llamacpp: leaving %s loaded", model)


Backend = LMStudioBackend | LlamaCppBackend


def get_backend(settings: Settings | None = None) -> Backend:
    settings = settings or load_settings()
    name = settings.resolve_backend()
    if name == "lmstudio":
        return LMStudioBackend(settings.lmstudio)
    return LlamaCppBackend(settings.llamacpp)


@contextmanager
def model_session(model: str, settings: Settings | None = None,
                  temperature: float = 0.7, max_tokens: int = 4096,
                  seed: int | None = None, context_length: int | None = None,
                  keep_loaded: bool = False) -> Iterator[ModelClient]:
    """Backend-aware model session: ensure `model` is served on the active
    backend, yield a chat client, release on exit."""
    settings = settings or load_settings()
    backend = get_backend(settings)
    backend.ensure_loaded(model, context_length=context_length)
    client = ModelClient(model=model, settings=backend.chat_settings,
                         temperature=temperature, max_tokens=max_tokens,
                         seed=seed)
    try:
        yield client
    finally:
        backend.release(model, keep_loaded)
