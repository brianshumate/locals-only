from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def db(tmp_path):
    from eval_pipeline.db import Database
    d = Database(tmp_path / "test.sqlite")
    yield d
    d.close()


@pytest.fixture
def seeded_db(db):
    """A db with one author, judge, prompt, and document registered."""
    db.upsert_author("author-a", "model-a", "q4", 0.7, 42)
    db.upsert_author("author-b", "model-b", "q4", 0.7, 42)
    db.upsert_judge("judge-1", "model-a")
    db.upsert_judge("judge-2", "model-c")
    db.upsert_prompt("p1", "tutorial", "beginners", "abc123", "datasets/prompts/p1.yaml")
    doc_a = db.register_document("p1", "author-a", str(FIXTURES / "golden.md"), "hash-a")
    doc_b = db.register_document("p1", "author-b", str(FIXTURES / "bad.md"), "hash-b")
    return db, doc_a, doc_b
