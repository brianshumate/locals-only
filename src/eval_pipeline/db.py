"""Sqlite persistence layer.

Idempotency rules:
- authors/judges/prompts upsert on their natural id.
- documents are keyed on (prompt_id, author_id, content_hash): re-registering
  the same content is a no-op returning the existing row id.
- det_results/judgments/comparisons insert-or-ignore on a uniqueness key so
  re-running a stage never duplicates rows.

Document paths are stored repo-relative (`runs/<prompt>/<author>/output.md`)
so one database works on every machine the eval runs on (the Mac and the
Linux server keep the checkout at different absolute paths); `documents()`
resolves them back to local absolute paths.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

SCHEMA_VERSION = 6

# What makes two generation environments the same environment. Deliberately
# *excludes* hostname: a host's name is not a property of the machine. It
# moves with DHCP, differs inside containers, and — because we only ever store
# it pseudonymized — changes outright whenever the local HMAC key is
# regenerated. Keying identity on it split one box's results across two report
# sections. Identity is now the machine and the deployment on it: OS, CPU,
# GPU, and backend. Hostname is kept alongside as a provenance breadcrumb.
IDENTITY_FIELDS = ("os", "os_version", "arch", "cpu", "gpu",
                   "backend", "backend_version")
ENV_FIELDS = ("hostname", *IDENTITY_FIELDS)

ENVIRONMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS environments (
    id INTEGER PRIMARY KEY,
    env_hash TEXT NOT NULL UNIQUE,
    hostname TEXT NOT NULL,
    os TEXT NOT NULL,
    os_version TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    cpu TEXT NOT NULL DEFAULT '',
    gpu TEXT NOT NULL DEFAULT '',
    backend TEXT NOT NULL,
    backend_version TEXT NOT NULL DEFAULT ''
);
"""

# Statements to move an existing DB from version N-1 to N. Fresh DBs are
# created at the latest shape by SCHEMA directly.
MIGRATIONS: dict[int, list[str]] = {
    2: ["ALTER TABLE documents ADD COLUMN prompt_tokens INTEGER"],
    3: [ENVIRONMENTS_TABLE,
        "ALTER TABLE documents ADD COLUMN environment_id INTEGER "
        "REFERENCES environments(id)"],
    # v4: document paths become repo-relative so the DB is portable between
    # machines. Rewrites absolute paths from any checkout to start at runs/.
    4: ["UPDATE documents SET path = substr(path, instr(path, '/runs/') + 1) "
        "WHERE path LIKE '/%' AND instr(path, '/runs/') > 0"],
    # v5: per-row timestamps on the long LLM stages so progress is
    # introspectable (rate, ETA, last-activity) via `eval status`. ADD COLUMN
    # cannot carry a non-constant default, so existing rows stay NULL; every
    # new write sets created_at explicitly.
    5: ["ALTER TABLE judgments ADD COLUMN created_at TEXT",
        "ALTER TABLE comparisons ADD COLUMN created_at TEXT"],
    # v6: environment identity drops hostname (see IDENTITY_FIELDS). Rows that
    # only ever differed by hostname described one machine, so they are merged
    # rather than left as parallel report sections.
    6: [lambda conn: _merge_environments_by_identity(conn)],
}


def _merge_environments_by_identity(conn: sqlite3.Connection) -> None:
    """Recompute env_hash without hostname, folding rows that collide.

    Collisions are the whole point: pre-v6 the same box could hold several
    rows, one per hostname it reported. The survivor is the lowest id, except
    that a row with a known backend_version outranks one whose probe failed,
    so merging never discards the more specific record.

    Blank-backend_version rows fold into a matching known row, mirroring the
    fallback in ``upsert_environment``. Pre-v6 that fallback could still leave
    a stranded blank row behind — it only fires when the known row already
    exists, so a blank written first stayed forever.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM environments ORDER BY id")]
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(environment_hash(row), []).append(row)

    # Absorb each blank-version group into a known one that agrees on all the
    # other identity fields, if exactly one such group exists. More than one
    # and the blank is genuinely ambiguous — which deployment produced it is
    # unknowable, so it stays its own row rather than being assigned a version
    # it may not have run.
    def without_version(row: dict) -> tuple:
        return tuple(str(row.get(f) or "")
                     for f in IDENTITY_FIELDS if f != "backend_version")

    for env_hash, members in list(groups.items()):
        if members[0]["backend_version"]:
            continue
        candidates = [h for h, m in groups.items()
                      if h != env_hash and m[0]["backend_version"]
                      and without_version(m[0]) == without_version(members[0])]
        if len(candidates) == 1:
            groups[candidates[0]].extend(members)
            del groups[env_hash]

    # env_hash is UNIQUE; park every row on a scratch value first so no
    # intermediate state of the rewrite can collide with a row not yet moved.
    for row in rows:
        conn.execute("UPDATE environments SET env_hash=? WHERE id=?",
                     (f"migrating-{row['id']}", row["id"]))

    for env_hash, members in groups.items():
        survivor = sorted(
            members, key=lambda r: (not r["backend_version"], r["id"]))[0]
        for other in members:
            if other["id"] == survivor["id"]:
                continue
            conn.execute(
                "UPDATE documents SET environment_id=? WHERE environment_id=?",
                (survivor["id"], other["id"]))
            conn.execute("DELETE FROM environments WHERE id=?", (other["id"],))
        conn.execute("UPDATE environments SET env_hash=? WHERE id=?",
                     (env_hash, survivor["id"]))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS authors (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    quantization TEXT NOT NULL,
    temperature REAL NOT NULL,
    seed INTEGER NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS judges (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS prompts (
    id TEXT PRIMARY KEY,
    doc_type TEXT NOT NULL,
    audience TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    file TEXT NOT NULL
);
""" + ENVIRONMENTS_TABLE + """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompts(id),
    author_id TEXT NOT NULL REFERENCES authors(id),
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    gen_time_s REAL,
    tokens INTEGER,
    prompt_tokens INTEGER,
    environment_id INTEGER REFERENCES environments(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (prompt_id, author_id, content_hash)
);

CREATE TABLE IF NOT EXISTS det_results (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    tool TEXT NOT NULL,
    passed INTEGER NOT NULL,
    score REAL,
    violations_json TEXT NOT NULL DEFAULT '[]',
    tool_version TEXT NOT NULL DEFAULT '',
    UNIQUE (document_id, tool)
);

CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    judge_id TEXT NOT NULL REFERENCES judges(id),
    skill TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    score REAL,
    confidence REAL,
    violations_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}',
    latency_s REAL,
    failed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_id, judge_id, skill, skill_version)
);

CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY,
    prompt_id TEXT NOT NULL REFERENCES prompts(id),
    doc_a INTEGER NOT NULL REFERENCES documents(id),
    doc_b INTEGER NOT NULL REFERENCES documents(id),
    judge_id TEXT NOT NULL REFERENCES judges(id),
    skill TEXT NOT NULL,
    winner TEXT NOT NULL CHECK (winner IN ('a', 'b', 'tie')),
    confidence REAL,
    position_swapped INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (doc_a, doc_b, judge_id, skill, position_swapped)
);

CREATE TABLE IF NOT EXISTS human_scores (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    reviewer TEXT NOT NULL,
    skill TEXT NOT NULL,
    score REAL NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE (document_id, reviewer, skill)
);
"""


def to_stored_path(path: str | Path) -> str:
    """Storage form of a document path: repo-relative when under the project
    root, so the same DB works wherever the checkout lives."""
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(p)
    return str(p)


def resolve_path(stored: str) -> Path:
    """Local absolute path for a stored document path. Relative paths anchor
    at the project root; absolute paths written by a pre-v4 schema on another
    machine are rebased onto the local runs/ tree if they don't exist here."""
    p = Path(stored)
    if not p.is_absolute():
        return PROJECT_ROOT / p
    if p.exists():
        return p
    parts = p.parts
    if "runs" in parts:
        return PROJECT_ROOT.joinpath(*parts[parts.index("runs"):])
    return p


def environment_hash(env: dict) -> str:
    """Stable identity for a generation environment (see IDENTITY_FIELDS)."""
    canon = {k: str(env.get(k) or "") for k in IDENTITY_FIELDS}
    return hashlib.sha256(
        json.dumps(canon, sort_keys=True).encode()).hexdigest()[:16]


def machine_label(env_hash: str) -> str:
    """Short human-facing name for an environment row.

    Derived from the identity hash rather than the hostname so reports never
    carry a machine name — not even a pseudonymized one — and so the label
    stays put when a host is renamed.
    """
    return f"env-{env_hash[:6]}"


@dataclass
class Document:
    id: int
    prompt_id: str
    author_id: str
    path: str
    content_hash: str
    gen_time_s: float | None = None
    tokens: int | None = None


class Database:
    def __init__(self, path: Path | str = "results.sqlite"):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        # A brand-new DB has no tables yet; create it at the latest shape.
        has_meta = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone() is not None
        if not has_meta:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()
            return
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 1
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema v{current} is newer than code v{SCHEMA_VERSION}"
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for step in MIGRATIONS.get(version, []):
                # Steps are SQL, or a callable for migrations that have to
                # read a row before deciding what to write.
                if callable(step):
                    step(self.conn)
                else:
                    self.conn.execute(step)
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- upserts ------------------------------------------------------------

    def upsert_author(self, id: str, model: str, quantization: str,
                      temperature: float, seed: int, config: dict | None = None) -> None:
        self.conn.execute(
            """INSERT INTO authors (id, model, quantization, temperature, seed, config_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET model=excluded.model,
                 quantization=excluded.quantization, temperature=excluded.temperature,
                 seed=excluded.seed, config_json=excluded.config_json""",
            (id, model, quantization, temperature, seed, json.dumps(config or {})),
        )
        self.conn.commit()

    def upsert_judge(self, id: str, model: str, config: dict | None = None) -> None:
        self.conn.execute(
            """INSERT INTO judges (id, model, config_json) VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET model=excluded.model,
                 config_json=excluded.config_json""",
            (id, model, json.dumps(config or {})),
        )
        self.conn.commit()

    def upsert_prompt(self, id: str, doc_type: str, audience: str,
                      prompt_hash: str, file: str) -> None:
        self.conn.execute(
            """INSERT INTO prompts (id, doc_type, audience, prompt_hash, file)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET doc_type=excluded.doc_type,
                 audience=excluded.audience, prompt_hash=excluded.prompt_hash,
                 file=excluded.file""",
            (id, doc_type, audience, prompt_hash, file),
        )
        self.conn.commit()

    def upsert_environment(self, env: dict) -> int:
        """Register a generation environment; identical machines share one
        row. Returns the row id.

        Identity is ``IDENTITY_FIELDS`` — hostname is recorded but does not
        participate, so the same box keeps one row across renames, container
        restarts, and pseudonymization-key changes.

        A backend whose version probe failed reports an empty
        ``backend_version``. That is absence of knowledge, not a distinct
        deployment, so it attaches to an otherwise-identical known
        environment rather than forking a second row for the same machine —
        reports group per row, and the fork would split one host's results
        into two incomparable sections.
        """
        canon = {k: str(env.get(k) or "") for k in ENV_FIELDS}
        env_hash = environment_hash(canon)
        row = self.conn.execute(
            "SELECT id FROM environments WHERE env_hash=?", (env_hash,)
        ).fetchone()
        if row:
            return row["id"]
        if not canon["backend_version"]:
            known = [f for f in IDENTITY_FIELDS if f != "backend_version"]
            row = self.conn.execute(
                "SELECT id FROM environments WHERE "
                + " AND ".join(f"{f}=?" for f in known)
                + " ORDER BY id LIMIT 1",
                tuple(canon[f] for f in known),
            ).fetchone()
            if row:
                return row["id"]
        cur = self.conn.execute(
            """INSERT INTO environments (env_hash, hostname, os, os_version,
                 arch, cpu, gpu, backend, backend_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (env_hash, *(canon[k] for k in ENV_FIELDS)),
        )
        self.conn.commit()
        return cur.lastrowid

    def register_document(self, prompt_id: str, author_id: str, path: str,
                          content_hash: str, gen_time_s: float | None = None,
                          tokens: int | None = None,
                          prompt_tokens: int | None = None,
                          environment_id: int | None = None) -> int:
        """Insert a document; identical (prompt, author, hash) is a no-op.

        Returns the row id either way.
        """
        cur = self.conn.execute(
            "SELECT id FROM documents WHERE prompt_id=? AND author_id=? AND content_hash=?",
            (prompt_id, author_id, content_hash),
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            """INSERT INTO documents (prompt_id, author_id, path, content_hash,
                 gen_time_s, tokens, prompt_tokens, environment_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (prompt_id, author_id, to_stored_path(path), content_hash,
             gen_time_s, tokens, prompt_tokens, environment_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_document_environment(self, document_id: int,
                                 environment_id: int) -> None:
        """Attribute an existing document to an environment (backfill)."""
        self.conn.execute("UPDATE documents SET environment_id=? WHERE id=?",
                          (environment_id, document_id))
        self.conn.commit()

    def record_det_result(self, document_id: int, tool: str, passed: bool,
                          score: float | None, violations: list[dict],
                          tool_version: str = "") -> None:
        self.conn.execute(
            """INSERT INTO det_results (document_id, tool, passed, score, violations_json, tool_version)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id, tool) DO UPDATE SET passed=excluded.passed,
                 score=excluded.score, violations_json=excluded.violations_json,
                 tool_version=excluded.tool_version""",
            (document_id, tool, int(passed), score, json.dumps(violations), tool_version),
        )
        self.conn.commit()

    def record_judgment(self, document_id: int, judge_id: str, skill: str,
                        skill_version: str, score: float | None,
                        confidence: float | None, violations: list[dict],
                        raw: dict, latency_s: float | None,
                        failed: bool = False) -> None:
        self.conn.execute(
            """INSERT INTO judgments (document_id, judge_id, skill, skill_version,
                 score, confidence, violations_json, raw_json, latency_s, failed,
                 created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(document_id, judge_id, skill, skill_version)
               DO UPDATE SET score=excluded.score, confidence=excluded.confidence,
                 violations_json=excluded.violations_json, raw_json=excluded.raw_json,
                 latency_s=excluded.latency_s, failed=excluded.failed,
                 created_at=excluded.created_at""",
            (document_id, judge_id, skill, skill_version, score, confidence,
             json.dumps(violations), json.dumps(raw), latency_s, int(failed)),
        )
        self.conn.commit()

    def record_comparison(self, prompt_id: str, doc_a: int, doc_b: int,
                          judge_id: str, skill: str, winner: str,
                          confidence: float | None, position_swapped: bool,
                          raw: dict | None = None) -> None:
        self.conn.execute(
            """INSERT INTO comparisons (prompt_id, doc_a, doc_b, judge_id, skill,
                 winner, confidence, position_swapped, raw_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(doc_a, doc_b, judge_id, skill, position_swapped)
               DO UPDATE SET winner=excluded.winner, confidence=excluded.confidence,
                 raw_json=excluded.raw_json, created_at=excluded.created_at""",
            (prompt_id, doc_a, doc_b, judge_id, skill, winner, confidence,
             int(position_swapped), json.dumps(raw or {})),
        )
        self.conn.commit()

    def record_human_score(self, document_id: int, reviewer: str, skill: str,
                           score: float, notes: str = "") -> None:
        self.conn.execute(
            """INSERT INTO human_scores (document_id, reviewer, skill, score, notes)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(document_id, reviewer, skill)
               DO UPDATE SET score=excluded.score, notes=excluded.notes""",
            (document_id, reviewer, skill, score, notes),
        )
        self.conn.commit()

    # -- queries ------------------------------------------------------------

    def documents(self, prompt_id: str | None = None,
                  author_id: str | None = None) -> list[Document]:
        sql = "SELECT * FROM documents WHERE 1=1"
        args: list[Any] = []
        if prompt_id:
            sql += " AND prompt_id=?"
            args.append(prompt_id)
        if author_id:
            sql += " AND author_id=?"
            args.append(author_id)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        return [Document(row["id"], row["prompt_id"], row["author_id"],
                         str(resolve_path(row["path"])),
                         row["content_hash"], row["gen_time_s"], row["tokens"])
                for row in rows]

    def document_exists(self, prompt_id: str, author_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM documents WHERE prompt_id=? AND author_id=? LIMIT 1",
            (prompt_id, author_id),
        )
        return cur.fetchone() is not None

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()
