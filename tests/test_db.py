"""Round-trip and idempotency tests for every table (WU-0.4)."""

import json


def test_author_roundtrip(db):
    db.upsert_author("a1", "m1", "q4", 0.5, 7, {"k": 1})
    row = db.query("SELECT * FROM authors WHERE id='a1'")[0]
    assert row["model"] == "m1"
    assert json.loads(row["config_json"]) == {"k": 1}
    # upsert overwrites
    db.upsert_author("a1", "m2", "q8", 0.9, 8)
    row = db.query("SELECT * FROM authors WHERE id='a1'")[0]
    assert row["model"] == "m2"
    assert len(db.query("SELECT * FROM authors")) == 1


def test_document_reinsert_is_noop(seeded_db):
    db, doc_a, _ = seeded_db
    again = db.register_document("p1", "author-a", "elsewhere.md", "hash-a")
    assert again == doc_a
    assert len(db.documents(prompt_id="p1", author_id="author-a")) == 1


def test_new_hash_makes_new_document(seeded_db):
    db, doc_a, _ = seeded_db
    new = db.register_document("p1", "author-a", "x.md", "hash-new")
    assert new != doc_a


def test_det_result_upsert(seeded_db):
    db, doc_a, _ = seeded_db
    db.record_det_result(doc_a, "vale", True, 9.0, [{"rule": "x"}], "3.15")
    db.record_det_result(doc_a, "vale", False, 4.0, [], "3.15")
    rows = db.query("SELECT * FROM det_results WHERE document_id=?", (doc_a,))
    assert len(rows) == 1
    assert rows[0]["passed"] == 0
    assert rows[0]["score"] == 4.0


def test_judgment_roundtrip(seeded_db):
    db, doc_a, _ = seeded_db
    db.record_judgment(doc_a, "judge-1", "style-guide", "1.0", 8.5, 0.9,
                       [{"severity": "minor"}], {"score": 8.5}, 1.2)
    db.record_judgment(doc_a, "judge-1", "style-guide", "1.0", 7.0, 0.8, [], {}, 1.0)
    rows = db.query("SELECT * FROM judgments")
    assert len(rows) == 1
    assert rows[0]["score"] == 7.0
    # new skill version -> new row
    db.record_judgment(doc_a, "judge-1", "style-guide", "2.0", 6.0, 0.8, [], {}, 1.0)
    assert len(db.query("SELECT * FROM judgments")) == 2


def test_comparison_roundtrip(seeded_db):
    db, doc_a, doc_b = seeded_db
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare",
                         "a", 0.9, False)
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare",
                         "a", 0.8, True)
    assert len(db.query("SELECT * FROM comparisons")) == 2
    # same key updates in place
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare",
                         "tie", 0.5, False)
    rows = db.query("SELECT * FROM comparisons WHERE position_swapped=0")
    assert len(rows) == 1 and rows[0]["winner"] == "tie"


def test_prompt_tokens_roundtrip(db):
    db.upsert_author("a1", "m1", "q4", 0.7, 42)
    db.upsert_prompt("p1", "tutorial", "x", "h", "f")
    db.register_document("p1", "a1", "x.md", "h1", 12.5, 900, 350)
    row = db.query("SELECT * FROM documents")[0]
    assert row["tokens"] == 900 and row["prompt_tokens"] == 350


_JUDGMENTS_CREATED_AT = (
    "    failed INTEGER NOT NULL DEFAULT 0,\n"
    "    created_at TEXT NOT NULL DEFAULT (datetime('now')),\n")
_COMPARISONS_CREATED_AT = (
    "    raw_json TEXT NOT NULL DEFAULT '{}',\n"
    "    created_at TEXT NOT NULL DEFAULT (datetime('now')),\n")


def _strip_v5(schema: str) -> str:
    """Drop the v5 created_at columns from judgments and comparisons. Matches
    each via its preceding line so the unrelated documents.created_at (present
    since v1) is left intact."""
    schema = schema.replace(_JUDGMENTS_CREATED_AT,
                            "    failed INTEGER NOT NULL DEFAULT 0,\n")
    schema = schema.replace(_COMPARISONS_CREATED_AT,
                            "    raw_json TEXT NOT NULL DEFAULT '{}',\n")
    return schema


def _old_schema(version: int) -> str:
    """Reconstruct the v1/v2 schema by stripping later additions."""
    import re
    from eval_pipeline import db as db_mod

    schema = re.sub(r"CREATE TABLE IF NOT EXISTS environments.*?;\n", "",
                    db_mod.SCHEMA, flags=re.S)
    schema = schema.replace(
        "    environment_id INTEGER REFERENCES environments(id),\n", "")
    if version < 2:
        schema = schema.replace("    prompt_tokens INTEGER,\n", "")
    if version < 5:
        schema = _strip_v5(schema)
    return schema


def _make_old_db(path, version: int):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(_old_schema(version))
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                 (str(version),))
    conn.execute("INSERT INTO authors VALUES ('a1','m1','q4',0.7,42,'{}')")
    conn.execute("INSERT INTO prompts VALUES ('p1','tutorial','x','h','f')")
    conn.execute("""INSERT INTO documents (prompt_id, author_id, path, content_hash)
                    VALUES ('p1','a1','x.md','h1')""")
    conn.commit()
    conn.close()


def test_migration_from_v1(tmp_path):
    """A v1 database (no prompt_tokens column) upgrades in place."""
    from eval_pipeline import db as db_mod

    path = tmp_path / "old.sqlite"
    _make_old_db(path, 1)
    upgraded = db_mod.Database(path)
    row = upgraded.query("SELECT value FROM meta WHERE key='schema_version'")[0]
    assert int(row["value"]) == db_mod.SCHEMA_VERSION
    doc = upgraded.query("SELECT * FROM documents")[0]
    assert doc["prompt_tokens"] is None  # column exists, old rows null
    assert doc["environment_id"] is None
    upgraded.close()


def test_migration_from_v2(tmp_path):
    """A v2 database gains the environments table and column."""
    from eval_pipeline import db as db_mod

    path = tmp_path / "old.sqlite"
    _make_old_db(path, 2)
    upgraded = db_mod.Database(path)
    assert upgraded.query("SELECT * FROM environments") == []
    doc = upgraded.query("SELECT * FROM documents")[0]
    assert doc["environment_id"] is None
    upgraded.close()


ENV = {"hostname": "beast", "os": "Linux", "os_version": "Pop!_OS 22.04",
       "arch": "x86_64", "cpu": "AMD Ryzen 9", "gpu": "RTX 3090 (24576 MiB)",
       "backend": "llamacpp", "backend_version": "b1234"}


def test_environment_upsert_is_idempotent(db):
    e1 = db.upsert_environment(ENV)
    e2 = db.upsert_environment(dict(ENV))
    assert e1 == e2
    assert len(db.query("SELECT * FROM environments")) == 1
    e3 = db.upsert_environment({**ENV, "backend": "lmstudio"})
    assert e3 != e1


def test_empty_backend_version_joins_known_environment(db):
    """A failed version probe reports an empty backend_version. That is
    absence of knowledge, not a second deployment, so it must attach to the
    known row — a fork would split one host's results into two report
    sections."""
    known = db.upsert_environment(ENV)
    probed_blank = db.upsert_environment({**ENV, "backend_version": ""})
    assert probed_blank == known
    assert len(db.query("SELECT * FROM environments")) == 1
    # The known row keeps its version; the blank does not erase it.
    assert db.query("SELECT backend_version FROM environments"
                    )[0]["backend_version"] == "b1234"


def test_empty_backend_version_still_distinguishes_machines(db):
    """The empty-version fallback matches on the remaining hardware fields, so
    it never merges genuinely different hardware or backends."""
    db.upsert_environment(ENV)
    other_gpu = db.upsert_environment(
        {**ENV, "gpu": "RTX 4090 (24576 MiB)", "backend_version": ""})
    other_backend = db.upsert_environment(
        {**ENV, "backend": "lmstudio", "backend_version": ""})
    assert len({other_gpu, other_backend}) == 2
    assert len(db.query("SELECT * FROM environments")) == 3


def test_hostname_drift_reuses_environment(db):
    """The hostname pseudonym is display metadata, not identity: the same box
    reporting a different pseudonym (renamed host, regenerated machine secret)
    reuses its row instead of forking a second, incomparable report section.
    The first-seen hostname is kept."""
    original = db.upsert_environment(ENV)
    drifted = db.upsert_environment({**ENV, "hostname": "renamed-box"})
    assert drifted == original
    rows = db.query("SELECT * FROM environments")
    assert len(rows) == 1
    assert rows[0]["hostname"] == "beast"


def test_blank_environment_is_created_when_nothing_matches(db):
    """With no prior row to attach to, an unknown version still registers."""
    env_id = db.upsert_environment({**ENV, "backend_version": ""})
    assert env_id is not None
    assert len(db.query("SELECT * FROM environments")) == 1


def test_version_change_reuses_environment(db):
    """Updating the backend (a new llama.cpp build, an LM Studio CLI bump) is
    not a new machine. Forking on it split one host's results into a fresh
    incomparable section after every routine upgrade, so the version is
    display metadata and the row is reused."""
    e1 = db.upsert_environment(ENV)
    e2 = db.upsert_environment({**ENV, "backend_version": "b9999"})
    assert e1 == e2
    rows = db.query("SELECT * FROM environments")
    assert len(rows) == 1
    assert rows[0]["backend_version"] == "b1234"  # first sighting is kept


def test_blank_backend_version_is_backfilled(db):
    """A row first registered from a failed version probe learns the version
    once a later run reports one."""
    env_id = db.upsert_environment({**ENV, "backend_version": ""})
    assert db.upsert_environment(ENV) == env_id
    assert db.query("SELECT backend_version FROM environments"
                    )[0]["backend_version"] == "b1234"


def test_document_records_environment(db):
    db.upsert_author("a1", "m1", "q4", 0.7, 42)
    db.upsert_prompt("p1", "tutorial", "x", "h", "f")
    env_id = db.upsert_environment(ENV)
    db.register_document("p1", "a1", "x.md", "h1", environment_id=env_id)
    row = db.query("SELECT * FROM documents")[0]
    assert row["environment_id"] == env_id


def test_paths_stored_relative_resolved_absolute(db):
    """Paths under the project root are stored repo-relative (portable DB)
    and come back as local absolute paths from documents()."""
    from eval_pipeline.config import PROJECT_ROOT

    db.upsert_author("a1", "m1", "q4", 0.7, 42)
    db.upsert_prompt("p1", "tutorial", "x", "h", "f")
    absolute = PROJECT_ROOT / "runs" / "p1" / "a1" / "output.md"
    db.register_document("p1", "a1", str(absolute), "h1")
    stored = db.query("SELECT path FROM documents")[0]["path"]
    assert stored == "runs/p1/a1/output.md"
    assert db.documents()[0].path == str(absolute)


def test_resolve_path_rebases_foreign_absolute():
    """A pre-v4 absolute path from another machine's checkout resolves onto
    the local runs/ tree."""
    from eval_pipeline.config import PROJECT_ROOT
    from eval_pipeline.db import resolve_path

    foreign = "/Users/example/evals/x/runs/p1/a1/output.md"
    assert resolve_path(foreign) == PROJECT_ROOT / "runs/p1/a1/output.md"


def test_migration_from_v3_relativizes_paths(tmp_path):
    """v3 -> v4 rewrites absolute document paths to start at runs/."""
    import sqlite3

    from eval_pipeline import db as db_mod

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    # v3 and v4 share the same shape; strip the v5 created_at columns so the
    # v5 migration has them to add.
    conn.executescript(_strip_v5(db_mod.SCHEMA))
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '3')")
    conn.execute("INSERT INTO authors VALUES ('a1','m1','q4',0.7,42,'{}')")
    conn.execute("INSERT INTO prompts VALUES ('p1','tutorial','x','h','f')")
    conn.execute("""INSERT INTO documents (prompt_id, author_id, path, content_hash)
                    VALUES ('p1','a1','/Users/example/evals/x/runs/p1/a1/output.md','h1')""")
    conn.execute("""INSERT INTO documents (prompt_id, author_id, path, content_hash)
                    VALUES ('p1','a1','runs/p1/a1/other.md','h2')""")
    conn.commit()
    conn.close()
    upgraded = db_mod.Database(path)
    paths = [r["path"] for r in upgraded.query(
        "SELECT path FROM documents ORDER BY id")]
    assert paths == ["runs/p1/a1/output.md", "runs/p1/a1/other.md"]
    upgraded.close()


def test_migration_from_v4_adds_created_at(tmp_path):
    """v4 -> v5 adds created_at to judgments and comparisons; existing rows
    (written before the column existed) stay NULL."""
    import sqlite3

    from eval_pipeline import db as db_mod

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(_strip_v5(db_mod.SCHEMA))
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '4')")
    conn.execute("INSERT INTO authors VALUES ('a1','m1','q4',0.7,42,'{}')")
    conn.execute("INSERT INTO judges VALUES ('j1','m1','{}')")
    conn.execute("INSERT INTO prompts VALUES ('p1','tutorial','x','h','f')")
    conn.execute("""INSERT INTO documents (prompt_id, author_id, path, content_hash)
                    VALUES ('p1','a1','x.md','h1')""")
    conn.execute("""INSERT INTO judgments
                    (document_id, judge_id, skill, skill_version)
                    VALUES (1,'j1','style-guide','1')""")
    conn.commit()
    conn.close()

    upgraded = db_mod.Database(path)
    cols = {r["name"] for r in upgraded.query("PRAGMA table_info(judgments)")}
    assert "created_at" in cols
    assert "created_at" in {
        r["name"] for r in upgraded.query("PRAGMA table_info(comparisons)")}
    # The pre-existing row predates the column, so it is NULL.
    assert upgraded.query("SELECT created_at FROM judgments")[0]["created_at"] is None
    upgraded.close()


def test_record_judgment_sets_created_at(seeded_db):
    """Freshly recorded judgments carry a timestamp for progress tracking."""
    db, doc_a, _ = seeded_db
    db.record_judgment(doc_a, "judge-1", "style-guide", "1.0", 8.0, 0.9, [], {}, 2.0)
    ts = db.query("SELECT created_at FROM judgments")[0]["created_at"]
    assert ts is not None and len(ts) == 19  # 'YYYY-MM-DD HH:MM:SS'


def test_human_score_roundtrip(seeded_db):
    db, doc_a, _ = seeded_db
    db.record_human_score(doc_a, "alex", "style-guide", 8.0, "solid")
    db.record_human_score(doc_a, "alex", "style-guide", 7.0)
    rows = db.query("SELECT * FROM human_scores")
    assert len(rows) == 1 and rows[0]["score"] == 7.0
