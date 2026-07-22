import json

import pytest

from eval_pipeline.generate import UnknownIdError, _check_ids

KNOWN = ["gemma-4-12b", "qwen3.5-9b", "qwen3.6-27b"]


def test_check_ids_passes_on_known_ids():
    _check_ids("author", ["qwen3.5-9b"], KNOWN, "config/authors.yaml")


def test_check_ids_rejects_unknown_with_suggestion():
    with pytest.raises(UnknownIdError) as exc:
        _check_ids("author", ["qwen3.5-9b-mtp"], KNOWN, "config/authors.yaml")
    msg = str(exc.value)
    assert "unknown author id 'qwen3.5-9b-mtp'" in msg
    assert "did you mean 'qwen3.5-9b'?" in msg
    assert "config/authors.yaml" in msg


def test_check_ids_lists_valid_ids_without_close_match():
    with pytest.raises(UnknownIdError) as exc:
        _check_ids("prompt", ["nope"], ["tut-python-json"], "datasets/prompts/")
    msg = str(exc.value)
    assert "did you mean" not in msg
    assert "tut-python-json" in msg


def test_backfill_environments(seeded_db, tmp_path, monkeypatch):
    from eval_pipeline import envinfo
    from eval_pipeline import generate as gen_mod
    from eval_pipeline.config import Settings

    db, doc_a, doc_b = seeded_db  # fixture docs: no manifest -> stay skipped

    env = {"hostname": "mac", "os": "macOS", "os_version": "26",
           "arch": "arm64", "cpu": "Apple M5", "gpu": "", "backend": "lmstudio",
           "backend_version": ""}
    d1 = tmp_path / "runs" / "p1" / "author-a"
    d1.mkdir(parents=True)
    (d1 / "output.md").write_text("x")
    (d1 / "manifest.json").write_text(json.dumps({"environment": env}))
    doc_c = db.register_document("p1", "author-a", str(d1 / "output.md"), "hash-c")

    d2 = tmp_path / "runs" / "p1" / "author-b"
    d2.mkdir(parents=True)
    (d2 / "output.md").write_text("y")
    (d2 / "manifest.json").write_text(json.dumps({"prompt_id": "p1"}))
    doc_d = db.register_document("p1", "author-b", str(d2 / "output.md"), "hash-d")

    class FakeBackend:
        name = "lmstudio"

        def version(self):
            return "0.4"

    monkeypatch.setattr(gen_mod, "get_backend", lambda settings=None: FakeBackend())
    monkeypatch.setattr(
        gen_mod.envinfo, "collect",
        lambda backend, backend_version="": envinfo.EnvInfo(
            "mac2", "macOS", "26", "arm64", "Apple M5", "", backend,
            backend_version))
    monkeypatch.setattr(Settings, "db_path", property(lambda self: db.path))
    settings = Settings(database=str(db.path))

    counts = gen_mod.backfill_environments(settings=settings)
    assert counts == {"from_manifest": 1, "assumed": 0, "skipped": 3}

    counts = gen_mod.backfill_environments(assume_current=True,
                                           settings=settings)
    assert counts == {"from_manifest": 0, "assumed": 1, "skipped": 2}

    envs = {r["id"]: r for r in db.query(
        """SELECT d.id, e.id AS env_id, e.hostname, e.backend_version
           FROM documents d JOIN environments e ON e.id = d.environment_id""")}
    # Manifest env and assumed-current env are the same hardware and backend,
    # differing only in hostname pseudonym and probed version — one row.
    assert envs[doc_c]["env_id"] == envs[doc_d]["env_id"]
    assert envs[doc_c]["hostname"] == "mac"          # first sighting kept
    assert envs[doc_c]["backend_version"] == "0.4"   # blank probe backfilled
    assert doc_a not in envs and doc_b not in envs

    # the assumption is persisted to the manifest, and marked as such
    manifest = json.loads((d2 / "manifest.json").read_text())
    assert manifest["environment"]["hostname"] == "mac2"
    assert manifest["environment_assumed"] is True
