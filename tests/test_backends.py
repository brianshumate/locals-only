"""Backend selection, per-backend model resolution, and llama.cpp
orchestration logic (no docker/server required)."""

import pydantic
import pytest

from eval_pipeline import config as config_mod
from eval_pipeline.backends import LlamaCppBackend
from eval_pipeline.config import (AuthorConfig, JudgeConfig, LlamaCppSettings,
                                  Settings)


# -- Settings.resolve_backend -------------------------------------------------

def test_explicit_backend_wins(monkeypatch):
    monkeypatch.delenv("EVAL_BACKEND", raising=False)
    assert Settings(backend="lmstudio").resolve_backend() == "lmstudio"
    assert Settings(backend="llamacpp").resolve_backend() == "llamacpp"


def test_auto_backend_follows_platform(monkeypatch):
    monkeypatch.delenv("EVAL_BACKEND", raising=False)
    monkeypatch.setattr(config_mod.platform, "system", lambda: "Darwin")
    assert Settings(backend="auto").resolve_backend() == "lmstudio"
    monkeypatch.setattr(config_mod.platform, "system", lambda: "Linux")
    assert Settings(backend="auto").resolve_backend() == "llamacpp"


def test_env_var_overrides_config(monkeypatch):
    monkeypatch.setenv("EVAL_BACKEND", "llamacpp")
    assert Settings(backend="lmstudio").resolve_backend() == "llamacpp"


def test_unknown_backend_rejected(monkeypatch):
    monkeypatch.delenv("EVAL_BACKEND", raising=False)
    with pytest.raises(ValueError):
        Settings(backend="ollama").resolve_backend()


# -- per-backend model resolution ---------------------------------------------

AUTHOR = dict(id="a", model="lms-model", quantization="mlx",
              backends={"llamacpp": {"model": "a.gguf", "quantization": "q8"}})


def test_author_resolves_per_backend():
    a = AuthorConfig(**AUTHOR)
    lms = a.resolve("lmstudio")
    assert (lms.model, lms.quantization) == ("lms-model", "mlx")
    lcpp = a.resolve("llamacpp")
    assert (lcpp.model, lcpp.quantization) == ("a.gguf", "q8")


def test_author_without_backend_entry_is_unavailable():
    a = AuthorConfig(id="a", model="lms-only")
    assert a.resolve("llamacpp") is None
    llamacpp_only = AuthorConfig(id="b",
                                 backends={"llamacpp": {"model": "b.gguf"}})
    assert llamacpp_only.resolve("lmstudio") is None
    assert llamacpp_only.display_model() == "b.gguf"


def test_backend_quantization_falls_back_to_top_level():
    a = AuthorConfig(id="a", model="m", quantization="qat",
                     backends={"llamacpp": {"model": "a.gguf"}})
    assert a.resolve("llamacpp").quantization == "qat"


def test_judge_resolves_per_backend():
    j = JudgeConfig(id="j", model="lms-model",
                    backends={"llamacpp": {"model": "j.gguf"}})
    assert j.resolve("llamacpp").model == "j.gguf"
    assert j.resolve("lmstudio").model == "lms-model"


def test_entry_requires_some_model():
    with pytest.raises(pydantic.ValidationError):
        AuthorConfig(id="empty")
    with pytest.raises(pydantic.ValidationError):
        AuthorConfig(id="bad", backends={"vllm": {"model": "x"}})


def test_shipped_configs_load_and_cover_llamacpp():
    """The real authors/judges configs parse, and the llama.cpp backend keeps
    >=2 judges for every subjective skill (design principle 4)."""
    authors = config_mod.load_authors()
    assert any(a.resolve("llamacpp") for a in authors)
    judges = [j for j in config_mod.load_judges() if j.resolve("llamacpp")]
    skills = {s for j in judges for s in j.skills}
    for skill in skills:
        n = sum(1 for j in judges if skill in j.skills)
        assert n >= 2, f"{skill} has {n} llamacpp judge(s)"


# -- LlamaCppBackend ----------------------------------------------------------

def _backend() -> LlamaCppBackend:
    return LlamaCppBackend(LlamaCppSettings())


def test_model_path_resolution():
    b = _backend()
    assert b._model_path("x.gguf") == "/models/x.gguf"
    assert b._model_path("/abs/y.gguf") == "/abs/y.gguf"


def test_ensure_loaded_reuses_running_model(monkeypatch):
    b = _backend()
    monkeypatch.setattr(b, "reachable", lambda: True)
    monkeypatch.setattr(b, "current_model", lambda: "x.gguf")
    monkeypatch.setattr("eval_pipeline.backends.subprocess.run",
                        lambda *a, **k: pytest.fail("must not restart docker"))
    b.ensure_loaded("x.gguf")  # no exception, no docker call


def test_ensure_loaded_requires_compose_file(monkeypatch, tmp_path):
    b = LlamaCppBackend(LlamaCppSettings(compose_dir=str(tmp_path / "nope")))
    monkeypatch.setattr(b, "reachable", lambda: False)
    from eval_pipeline.backends import BackendError
    with pytest.raises(BackendError, match="compose file"):
        b.ensure_loaded("x.gguf")
