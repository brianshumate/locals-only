import json
import subprocess

import pytest

from eval_pipeline import discover as disc
from eval_pipeline.config import Settings, load_authors, load_judges

AUTHORS_YAML = """\
# Author models.
authors:
  # LM Studio only.
  - id: gemma-4-12b
    model: google/gemma-4-12b-qat
    quantization: qat
    temperature: 0.7
    seed: 42
    max_tokens: 32768
    context_length: 65536

  - id: lfm2.5-8b
    model: lfm2.5-8b-a1b-mlx
    quantization: mlx
    backends:
      llamacpp:
        model: LFM2.5-8B-A1B-BF16.gguf
        quantization: bf16
    temperature: 0.7
    seed: 42
    max_tokens: 32768
    context_length: 65536
"""

JUDGES_YAML = """\
judges:
  - id: judge-gemma-4-12b
    model: google/gemma-4-12b-qat
    temperature: 0.0
    max_tokens: 32768
    skills: [style-guide, factuality]
"""


@pytest.fixture
def config_files(tmp_path):
    a = tmp_path / "authors.yaml"
    j = tmp_path / "judges.yaml"
    a.write_text(AUTHORS_YAML)
    j.write_text(JUDGES_YAML)
    return a, j


@pytest.mark.parametrize("model,expected", [
    ("google/gemma-4-12b-qat", "gemma-4-12b"),
    ("gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf", "gemma-4-26b"),
    ("Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf", "qwen3.6-35b"),
    ("LFM2.5-8B-A1B-BF16.gguf", "lfm2.5-8b"),
    ("lfm2.5-8b-a1b-mlx", "lfm2.5-8b"),
    ("liquid/lfm2-24b-a2b", "lfm2-24b"),
    ("nvidia/nemotron-3-nano-4b", "nemotron-3-nano-4b"),
    ("qwen3.5-9b-mtp", "qwen3.5-9b"),
    ("gemma-4-31B-it-qat-UD-Q4_K_XL.gguf", "gemma-4-31b"),
])
def test_derive_id(model, expected):
    assert disc.derive_id(model) == expected


def test_derive_id_converges_across_backends():
    """The two deployments of one model must land on the same config id, or
    discovery would propose a duplicate entry instead of a backend mapping."""
    assert disc.derive_id("lfm2.5-8b-a1b-mlx") == \
        disc.derive_id("LFM2.5-8B-A1B-BF16.gguf")


@pytest.mark.parametrize("filename,expected", [
    ("Qwen3.6-27B-MTP-Q4_K_M.gguf", "Q4_K_M"),
    ("gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf", "qat-UD-Q4_K_XL"),
    ("LFM2.5-8B-A1B-BF16.gguf", "bf16"),
    ("some-model.gguf", None),
])
def test_gguf_quantization(filename, expected):
    assert disc._gguf_quantization(filename) == expected


def test_discover_lmstudio_parses_lms_output(monkeypatch):
    payload = [
        {"type": "llm", "modelKey": "google/gemma-4-12b-qat",
         "quantization": {"name": "Q4_0"}, "paramsString": "12B",
         "maxContextLength": 262144},
        {"type": "embedding", "modelKey": "text-embedding-nomic-embed-text",
         "quantization": {"name": "Q4_K_M"}, "maxContextLength": 2048},
    ]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, stdout=json.dumps(payload), stderr=""))
    found = disc.discover_lmstudio(Settings())
    assert [m.model for m in found] == ["google/gemma-4-12b-qat"]
    assert found[0].suggested_id == "gemma-4-12b"
    assert found[0].quantization == "Q4_0"


def test_discover_lmstudio_survives_missing_cli(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("lms")
    monkeypatch.setattr(subprocess, "run", boom)
    assert disc.discover_lmstudio(Settings()) == []


def test_discover_llamacpp_lists_ggufs_and_skips_shards(tmp_path):
    for name in ["Qwen3.6-27B-MTP-Q4_K_M.gguf", "notes.txt",
                 "big-00001-of-00003.gguf", "big-00002-of-00003.gguf"]:
        (tmp_path / name).write_text("")
    settings = Settings()
    settings.llamacpp.host_model_dir = str(tmp_path)
    found = disc.discover_llamacpp(settings)
    assert sorted(m.model for m in found) == [
        "Qwen3.6-27B-MTP-Q4_K_M.gguf", "big-00001-of-00003.gguf"]


def _plan(found, config_files, roles=("author",), backend="llamacpp",
          monkeypatch=None):
    a, j = config_files
    monkeypatch.setattr(disc, "discover", lambda b, s: found)
    return disc.plan(backend, roles, Settings(), authors_path=a, judges_path=j)


def test_plan_skips_already_configured(config_files, monkeypatch):
    found = [disc.DiscoveredModel("llamacpp", "LFM2.5-8B-A1B-BF16.gguf",
                                  "lfm2.5-8b", "bf16")]
    assert _plan(found, config_files, monkeypatch=monkeypatch) == []


def test_plan_adds_backend_to_existing_entry(config_files, monkeypatch):
    """gemma-4-12b exists but is LM Studio only; a matching gguf should extend
    it rather than create a second entry."""
    found = [disc.DiscoveredModel("llamacpp", "gemma-4-12b-it-Q4_K_M.gguf",
                                  "gemma-4-12b", "Q4_K_M")]
    (p,) = _plan(found, config_files, monkeypatch=monkeypatch)
    assert p.kind == "add-backend"
    assert p.entry_id == "gemma-4-12b"


def test_plan_proposes_new_author(config_files, monkeypatch):
    found = [disc.DiscoveredModel("llamacpp", "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf",
                                  "qwen3.6-35b", "Q4_K_M")]
    (p,) = _plan(found, config_files, monkeypatch=monkeypatch)
    assert (p.kind, p.entry_id) == ("new-author", "qwen3.6-35b")


def test_plan_judge_role_uses_judge_prefix(config_files, monkeypatch):
    found = [disc.DiscoveredModel("llamacpp", "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf",
                                  "qwen3.6-35b", "Q4_K_M")]
    (p,) = _plan(found, config_files, roles=("judge",), monkeypatch=monkeypatch)
    assert (p.kind, p.entry_id, p.role) == ("new-judge", "judge-qwen3.6-35b",
                                            "judge")


def test_plan_honours_exclude_patterns(config_files, monkeypatch):
    found = [disc.DiscoveredModel("llamacpp", "nomic-embed-text-Q4_K_M.gguf",
                                  "nomic-embed-text", "Q4_K_M")]
    a, j = config_files
    monkeypatch.setattr(disc, "discover", lambda b, s: found)
    assert disc.plan("llamacpp", ("author",), Settings(),
                     authors_path=a, judges_path=j) == []


def test_apply_appends_author_and_preserves_comments(config_files, monkeypatch):
    a, _ = config_files
    found = [disc.DiscoveredModel("llamacpp", "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf",
                                  "qwen3.6-35b", "Q4_K_M")]
    proposals = _plan(found, config_files, monkeypatch=monkeypatch)
    disc.apply_proposals(proposals, Settings(), authors_path=a,
                         judges_path=config_files[1])

    text = a.read_text()
    assert "# LM Studio only." in text          # comments survive
    authors = {e.id: e for e in load_authors(a)}
    assert set(authors) == {"gemma-4-12b", "lfm2.5-8b", "qwen3.6-35b"}
    new = authors["qwen3.6-35b"]
    resolved = new.resolve("llamacpp")
    assert resolved.model == "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert resolved.quantization == "Q4_K_M"
    assert new.resolve("lmstudio") is None
    assert new.context_length == 65536


def test_apply_adds_backend_block_in_place(config_files, monkeypatch):
    a, _ = config_files
    found = [disc.DiscoveredModel("llamacpp", "gemma-4-12b-it-Q4_K_M.gguf",
                                  "gemma-4-12b", "Q4_K_M")]
    proposals = _plan(found, config_files, monkeypatch=monkeypatch)
    disc.apply_proposals(proposals, Settings(), authors_path=a,
                         judges_path=config_files[1])

    authors = {e.id: e for e in load_authors(a)}
    assert set(authors) == {"gemma-4-12b", "lfm2.5-8b"}   # no duplicate row
    entry = authors["gemma-4-12b"]
    assert entry.resolve("llamacpp").model == "gemma-4-12b-it-Q4_K_M.gguf"
    # The pre-existing LM Studio identity is untouched.
    assert entry.resolve("lmstudio").model == "google/gemma-4-12b-qat"
    assert entry.temperature == 0.7 and entry.context_length == 65536


def test_apply_extends_existing_backends_map(config_files, monkeypatch):
    """lfm2.5-8b already has a `backends:` map; a new backend joins it."""
    a, _ = config_files
    found = [disc.DiscoveredModel("lmstudio", "lfm2.5-8b-a1b-mlx",
                                  "lfm2.5-8b", "8bit")]
    # Pretend the entry has no lmstudio identity by discovering for a config
    # whose lfm2.5-8b is llamacpp-only.
    a.write_text(AUTHORS_YAML.replace(
        "    model: lfm2.5-8b-a1b-mlx\n    quantization: mlx\n", ""))
    proposals = _plan(found, config_files, backend="lmstudio",
                      monkeypatch=monkeypatch)
    (p,) = proposals
    assert p.kind == "add-backend"
    disc.apply_proposals(proposals, Settings(), authors_path=a,
                         judges_path=config_files[1])

    entry = {e.id: e for e in load_authors(a)}["lfm2.5-8b"]
    assert entry.resolve("lmstudio").model == "lfm2.5-8b-a1b-mlx"
    assert entry.resolve("llamacpp").model == "LFM2.5-8B-A1B-BF16.gguf"


def test_apply_judge_entry_gets_skills(config_files, monkeypatch):
    _, j = config_files
    found = [disc.DiscoveredModel("llamacpp", "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf",
                                  "qwen3.6-35b", "Q4_K_M")]
    proposals = _plan(found, config_files, roles=("judge",),
                      monkeypatch=monkeypatch)
    disc.apply_proposals(proposals, Settings(), authors_path=config_files[0],
                         judges_path=j)

    judges = {e.id: e for e in load_judges(j)}
    new = judges["judge-qwen3.6-35b"]
    assert new.temperature == 0.0
    assert "pairwise-compare" in new.skills
    assert new.resolve("llamacpp").model == "Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf"
    # JudgeConfig has no quantization field; emitting one would be dead text.
    assert "quantization" not in j.read_text()


def test_render_author_caps_context_to_model_maximum():
    found = disc.DiscoveredModel("lmstudio", "tiny-model", "tiny", "Q8_0",
                                 context_length=8192)
    text = disc.render_entry(
        disc.Proposal("new-author", "author", "tiny", found),
        Settings().discovery)
    assert "context_length: 8192" in text
