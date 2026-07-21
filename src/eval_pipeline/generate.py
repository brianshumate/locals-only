"""Generation pipeline: authors x prompts -> runs/<prompt>/<author>/output.md.

Author-major iteration: each model is loaded once, all its prompts generated,
then it is unloaded before the next model.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from . import envinfo
from .backends import get_backend, model_session
from .config import (AuthorConfig, BackendModel, Settings, load_authors,
                     load_settings)
from .db import Database, resolve_path
from .prompts import PromptTask, load_prompts

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a professional technical writer. You produce clear, accurate, "
    "well-structured Markdown documentation that follows the given "
    "specification exactly."
)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class UnknownIdError(ValueError):
    """Raised when a requested author/prompt id does not exist."""


def _check_ids(kind: str, requested: list[str], known: list[str],
               config_hint: str) -> None:
    unknown = [r for r in requested if r not in known]
    if not unknown:
        return
    lines = []
    for u in unknown:
        line = f"unknown {kind} id {u!r}"
        close = difflib.get_close_matches(u, known, n=1)
        if close:
            line += f" — did you mean {close[0]!r}?"
        lines.append(line)
    lines.append(f"valid {kind} ids ({config_hint}): {', '.join(sorted(known))}")
    raise UnknownIdError("\n".join(lines))


def register_configs(db: Database, authors: list[AuthorConfig],
                     tasks: list[PromptTask]) -> None:
    for a in authors:
        db.upsert_author(a.id, a.display_model(), a.quantization,
                         a.temperature, a.seed, a.model_dump())
    for t in tasks:
        db.upsert_prompt(t.id, t.doc_type, t.audience, t.prompt_hash(),
                         f"datasets/prompts/{t.id}.yaml")


def generate_all(author_ids: list[str] | None = None,
                 prompt_ids: list[str] | None = None,
                 force: bool = False,
                 settings: Settings | None = None) -> int:
    """Returns the number of documents newly generated."""
    settings = settings or load_settings()
    db = Database(settings.db_path)
    authors = load_authors()
    tasks = load_prompts()
    if author_ids:
        _check_ids("author", author_ids, [a.id for a in authors],
                   "config/authors.yaml")
        authors = [a for a in authors if a.id in author_ids]
    if prompt_ids:
        _check_ids("prompt", prompt_ids, [t.id for t in tasks],
                   "datasets/prompts/")
        tasks = [t for t in tasks if t.id in prompt_ids]
    register_configs(db, authors, tasks)

    backend = get_backend(settings)
    env = envinfo.collect(backend.name)
    generated = 0
    env_id: int | None = None
    for author in authors:
        resolved = author.resolve(backend.name)
        if resolved is None:
            log.warning("%s: no model configured for backend %r — skipping "
                        "(add a `backends: %s:` entry in config/authors.yaml)",
                        author.id, backend.name, backend.name)
            continue
        pending = [t for t in tasks
                   if force or not _already_generated(db, settings, t, author)]
        if not pending:
            log.info("%s: all prompts up to date, skipping model load", author.id)
            continue
        log.info("%s: generating %d document(s) on %s (%s)", author.id,
                 len(pending), backend.name, resolved.model)
        with model_session(resolved.model, settings=settings,
                           temperature=author.temperature,
                           max_tokens=author.max_tokens, seed=author.seed,
                           context_length=author.context_length) as client:
            if env_id is None or not env.backend_version:
                # backend version is only queryable once the server is up
                env.backend_version = backend.version() or env.backend_version
                env_id = db.upsert_environment(env.as_dict())
            for task in pending:
                generated += _generate_one(db, settings, client, author, task,
                                           resolved, backend.name, env, env_id)
    db.close()
    return generated


def backfill_environments(assume_current: bool = False,
                          settings: Settings | None = None) -> dict[str, int]:
    """Attribute documents that have no recorded environment.

    Manifests written since environment capture (schema v3) name their
    environment, so those documents attribute exactly. Older manifests carry
    none: with assume_current, they are attributed to *this* machine's
    environment — run it on the machine that actually authored them — and the
    environment is written back into the manifest with an
    `environment_assumed: true` marker, so the attribution is visibly an
    assumption and survives a DB rebuild from runs/.
    """
    settings = settings or load_settings()
    db = Database(settings.db_path)
    rows = db.query(
        "SELECT id, prompt_id, author_id, path FROM documents "
        "WHERE environment_id IS NULL ORDER BY id")
    counts = {"from_manifest": 0, "assumed": 0, "skipped": 0}
    current_env: dict | None = None
    for r in rows:
        manifest_path = resolve_path(r["path"]).parent / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("%s/%s: no readable manifest at %s — skipping",
                        r["prompt_id"], r["author_id"], manifest_path)
            counts["skipped"] += 1
            continue
        env = manifest.get("environment")
        if env:
            db.set_document_environment(r["id"], db.upsert_environment(env))
            counts["from_manifest"] += 1
            continue
        if not assume_current:
            counts["skipped"] += 1
            continue
        if current_env is None:
            backend = get_backend(settings)
            current_env = envinfo.collect(backend.name,
                                          backend.version()).as_dict()
            log.info("assuming environment: %s (%s)",
                     current_env["hostname"], current_env["backend"])
        db.set_document_environment(r["id"], db.upsert_environment(current_env))
        manifest["environment"] = current_env
        manifest["environment_assumed"] = True
        manifest.setdefault("backend", current_env["backend"])
        manifest_path.write_text(json.dumps(manifest, indent=2))
        log.info("%s/%s: attributed to %s (assumed)", r["prompt_id"],
                 r["author_id"], current_env["hostname"])
        counts["assumed"] += 1
    db.close()
    return counts


def _out_dir(settings: Settings, task: PromptTask, author: AuthorConfig):
    return settings.runs_path / task.id / author.id


def _already_generated(db: Database, settings: Settings, task: PromptTask,
                       author: AuthorConfig) -> bool:
    """Skip when output.md exists and its manifest matches the current prompt
    hash (prompt edits force regeneration)."""
    out = _out_dir(settings, task, author)
    manifest_path = out / "manifest.json"
    output_path = out / "output.md"
    if not (manifest_path.exists() and output_path.exists()):
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return False
    if manifest.get("prompt_hash") != task.prompt_hash():
        return False
    # Ensure the document is registered even if the DB was wiped, keeping the
    # environment it was originally generated in (recorded in the manifest).
    env = manifest.get("environment")
    env_id = db.upsert_environment(env) if env else None
    db.register_document(task.id, author.id, str(output_path),
                         content_hash(output_path.read_text()),
                         manifest.get("gen_time_s"), manifest.get("completion_tokens"),
                         manifest.get("prompt_tokens"), environment_id=env_id)
    return True


def _generate_one(db: Database, settings: Settings, client, author: AuthorConfig,
                  task: PromptTask, resolved: BackendModel, backend_name: str,
                  env: envinfo.EnvInfo, env_id: int) -> int:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task.render()},
    ]
    start = time.monotonic()
    try:
        text, usage = client.chat(messages)
    except Exception as e:
        log.error("%s/%s generation failed: %s", task.id, author.id, e)
        return 0
    elapsed = time.monotonic() - start

    out = _out_dir(settings, task, author)
    out.mkdir(parents=True, exist_ok=True)
    output_path = out / "output.md"
    output_path.write_text(text)
    manifest = {
        "prompt_id": task.id,
        "author_id": author.id,
        "model": resolved.model,
        "quantization": resolved.quantization or author.quantization,
        "backend": backend_name,
        "environment": env.as_dict(),
        "temperature": author.temperature,
        "seed": author.seed,
        "max_tokens": author.max_tokens,
        "prompt_hash": task.prompt_hash(),
        "content_hash": content_hash(text),
        "gen_time_s": round(elapsed, 2),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    db.register_document(task.id, author.id, str(output_path),
                         manifest["content_hash"], elapsed, usage.completion_tokens,
                         usage.prompt_tokens, environment_id=env_id)
    log.info("%s/%s done in %.1fs (%d tokens)", task.id, author.id, elapsed,
             usage.completion_tokens)
    return 1
