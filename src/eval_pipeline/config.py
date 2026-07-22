"""Typed loading of config/{settings,authors,judges}.yaml."""

from __future__ import annotations

import os
import platform
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

BACKEND_NAMES = ("lmstudio", "llamacpp")


class LMStudioSettings(BaseModel):
    base_url: str = "http://localhost:1234/v1"
    request_timeout: float = 600
    load_timeout: float = 300
    retries: int = 2


class LlamaCppSettings(BaseModel):
    """llama.cpp `llama-server` running in Docker (compose project)."""
    base_url: str = "http://localhost:8080/v1"
    # `~` is expanded on read, so the default stays correct without naming a
    # user. Override in config/settings.local.yaml when the compose project
    # lives elsewhere.
    compose_dir: str = "~/lcpp-docker"
    compose_service: str = "llama"
    # Path of the model volume *inside* the container (compose mounts the
    # host model directory there); bare gguf filenames are resolved under it.
    container_model_dir: str = "/models"
    # The same directory as seen from the host. Only `eval discover` uses it,
    # to enumerate gguf files; when it is absent (e.g. running on the Mac)
    # discovery falls back to listing the directory inside the container.
    host_model_dir: str = "/mnt/data-one/llama-models"
    request_timeout: float = 1800
    load_timeout: float = 900
    retries: int = 2

    @model_validator(mode="after")
    def _expand_paths(self):
        for field in ("compose_dir", "host_model_dir"):
            setattr(self, field,
                    os.path.expanduser(getattr(self, field)))
        return self


class CodeRunnerSettings(BaseModel):
    allowed_languages: list[str] = ["python", "bash", "sh"]
    timeout_seconds: int = 30


class LycheeSettings(BaseModel):
    offline: bool = True
    allowlist: list[str] = []


class DiscoverySettings(BaseModel):
    """Defaults applied to config entries proposed by `eval discover`."""
    # Case-insensitive substrings; a model whose identity contains any of them
    # is never proposed (embedding models are filtered by type already).
    exclude: list[str] = ["-embed", "embedding", "reranker", "whisper"]
    temperature: float = 0.7
    seed: int = 42
    max_tokens: int = 32768
    context_length: int = 65536
    # Skills assigned to a judge entry proposed with `--role judge|both`.
    judge_skills: list[str] = ["style-guide", "factuality", "completeness",
                               "audience-fit", "code-quality",
                               "pairwise-compare"]
    judge_temperature: float = 0.0
    judge_max_tokens: int = 32768
    # Judges only ever see one document plus a rubric, so they need far less
    # context than authors. Left unset the backend loads the model at its
    # maximum context, which on Apple Silicon sizes the KV cache into swap.
    judge_context_length: int = 16384


class Settings(BaseModel):
    # Which inference backend to drive: "lmstudio", "llamacpp", or "auto"
    # (macOS -> lmstudio, everything else -> llamacpp). The EVAL_BACKEND
    # environment variable overrides the config value.
    backend: str = "auto"
    lmstudio: LMStudioSettings = LMStudioSettings()
    llamacpp: LlamaCppSettings = LlamaCppSettings()
    database: str = "results.sqlite"
    runs_dir: str = "runs"
    reports_dir: str = "reports"
    code_runner: CodeRunnerSettings = CodeRunnerSettings()
    lychee: LycheeSettings = LycheeSettings()
    discovery: DiscoverySettings = DiscoverySettings()

    @property
    def db_path(self) -> Path:
        return PROJECT_ROOT / self.database

    @property
    def runs_path(self) -> Path:
        return PROJECT_ROOT / self.runs_dir

    @property
    def reports_path(self) -> Path:
        return PROJECT_ROOT / self.reports_dir

    def resolve_backend(self) -> str:
        name = os.environ.get("EVAL_BACKEND") or self.backend
        if name == "auto":
            return "lmstudio" if platform.system() == "Darwin" else "llamacpp"
        if name not in BACKEND_NAMES:
            raise ValueError(
                f"unknown backend {name!r} — expected one of "
                f"{', '.join(BACKEND_NAMES)}, or 'auto'")
        return name


class BackendModel(BaseModel):
    """Per-backend model identity: an LM Studio model key or a gguf filename."""
    model: str
    quantization: str | None = None


class _ModelEntry(BaseModel):
    """Shared shape of author/judge configs: a top-level LM Studio `model`
    and/or per-backend overrides under `backends:`. An entry with no model
    for the active backend is simply skipped on that machine."""
    id: str
    model: str = ""  # LM Studio identifier (the historical default backend)
    backends: dict[str, BackendModel] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _needs_some_model(self):
        if not self.model and not self.backends:
            raise ValueError(f"{self.id}: needs `model` or a `backends:` entry")
        unknown = set(self.backends) - set(BACKEND_NAMES)
        if unknown:
            raise ValueError(f"{self.id}: unknown backend(s) {sorted(unknown)}")
        return self

    def resolve(self, backend: str) -> BackendModel | None:
        """Model identity for `backend`, or None if not runnable there."""
        if backend in self.backends:
            entry = self.backends[backend]
            quant = entry.quantization or getattr(self, "quantization", None)
            return BackendModel(model=entry.model, quantization=quant)
        if backend == "lmstudio" and self.model:
            return BackendModel(model=self.model,
                                quantization=getattr(self, "quantization", None))
        return None

    def display_model(self) -> str:
        """Canonical model name for DB registration / reports."""
        if self.model:
            return self.model
        return next(iter(self.backends.values())).model


class AuthorConfig(_ModelEntry):
    quantization: str = "unknown"
    temperature: float = 0.7
    seed: int = 42
    max_tokens: int = 4096
    context_length: int = 8192


class JudgeConfig(_ModelEntry):
    temperature: float = 0.0
    max_tokens: int = 2048
    context_length: int = 16384
    skills: list[str] = Field(default_factory=list)


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(path: Path | None = None) -> Settings:
    """settings.yaml, with settings.local.yaml merged over it when present.

    The local file is untracked, so machine-specific paths stay out of the
    repository instead of being committed as defaults.
    """
    path = path or CONFIG_DIR / "settings.yaml"
    data = yaml.safe_load(path.read_text()) or {}
    local = path.with_name(f"{path.stem}.local{path.suffix}")
    if local.exists():
        data = _deep_merge(data, yaml.safe_load(local.read_text()) or {})
    return Settings.model_validate(data)


def load_authors(path: Path | None = None) -> list[AuthorConfig]:
    path = path or CONFIG_DIR / "authors.yaml"
    data = yaml.safe_load(path.read_text())
    return [AuthorConfig.model_validate(a) for a in data["authors"]]


def load_judges(path: Path | None = None) -> list[JudgeConfig]:
    path = path or CONFIG_DIR / "judges.yaml"
    data = yaml.safe_load(path.read_text())
    return [JudgeConfig.model_validate(j) for j in data["judges"]]
