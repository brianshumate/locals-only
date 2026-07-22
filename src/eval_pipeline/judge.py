"""Judge runner: judges x skills x documents -> judgments table.

JSON discipline: model output is validated against the skill's schema; one
repair retry (the validation error is fed back); a second failure is recorded
as failed=1, never coerced.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import jsonschema

from .backends import model_session
from .config import JudgeConfig, Settings, load_judges, load_settings
from .db import Database
from .lmstudio import LMStudioError, LMStudioTimeout, ModelClient
from .prompts import load_reference
from .skills import Skill, load_skill

log = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "You are a strict, impartial technical-documentation evaluator. "
    "You respond with a single JSON object matching the requested schema — "
    "no prose, no markdown fences."
)


def render_judge_messages(skill: Skill, document: str, **extra) -> list[dict]:
    user = skill.render(document=document, **extra)
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def judge_document(client: ModelClient, skill: Skill, document: str,
                   **extra) -> tuple[dict | None, float, str]:
    """Returns (validated_json_or_None, latency_s, error). One repair retry."""
    messages = render_judge_messages(skill, document, **extra)
    start = time.monotonic()
    error = ""
    for attempt in range(2):
        try:
            data, _ = client.chat_json(messages, schema=skill.schema)
            skill.validate_output(data)
            return data, time.monotonic() - start, ""
        except LMStudioTimeout as e:
            # Nothing to repair — the model never answered. Feeding back an
            # "invalid output" correction would just cost another timeout.
            log.warning("judge timed out: %s", e)
            return None, time.monotonic() - start, str(e)
        except (LMStudioError, jsonschema.ValidationError) as e:
            error = str(e)
            log.warning("judge output invalid (attempt %d): %s", attempt + 1,
                        error[:200])
            messages = messages + [
                {"role": "assistant", "content": "(invalid output)"},
                {"role": "user", "content":
                    f"Your previous output was invalid: {error[:500]}\n"
                    "Return ONLY a JSON object that satisfies the schema."},
            ]
    return None, time.monotonic() - start, error


def _extra_context(skill_name: str, prompt_id: str) -> dict:
    """Skill-specific extra template context (e.g. reference facts)."""
    if skill_name == "factuality":
        ref = load_reference(prompt_id)
        if ref is None:
            return {"reference_facts": []}
        return {"reference_facts": [f.model_dump() for f in ref.facts]}
    return {}


def judge_all(judge_ids: list[str] | None = None,
              skill_names: list[str] | None = None,
              force: bool = False,
              settings: Settings | None = None) -> int:
    """Judge-major iteration: load each judge model once, run all its
    (skill, document) work, unload. Returns judgments recorded."""
    settings = settings or load_settings()
    backend_name = settings.resolve_backend()
    db = Database(settings.db_path)
    judges = load_judges()
    if judge_ids:
        judges = [j for j in judges if j.id in judge_ids]

    count = 0
    for judge in judges:
        resolved = judge.resolve(backend_name)
        if resolved is None:
            log.warning("%s: no model configured for backend %r — skipping",
                        judge.id, backend_name)
            continue
        db.upsert_judge(judge.id, judge.display_model(), judge.model_dump())
        skills = [load_skill(s) for s in judge.skills
                  if s != "pairwise-compare"
                  and (not skill_names or s in skill_names)]
        work = _pending_work(db, judge, skills, force)
        if not work:
            log.info("%s: nothing to judge", judge.id)
            continue
        total = len(work)
        log.info("%s: %d judgments to run", judge.id, total)
        with model_session(resolved.model, settings=settings,
                           temperature=judge.temperature,
                           max_tokens=judge.max_tokens,
                           context_length=judge.context_length) as client:
            for i, (doc, skill) in enumerate(work, 1):
                text = Path(doc.path).read_text()
                extra = _extra_context(skill.name, doc.prompt_id)
                data, latency, error = judge_document(client, skill, text, **extra)
                if data is None:
                    db.record_judgment(doc.id, judge.id, skill.name, skill.version,
                                       None, None, [], {"error": error}, latency,
                                       failed=True)
                    log.warning("%s [%d/%d] %s doc#%d FAILED in %.1fs: %s",
                                judge.id, i, total, skill.name, doc.id, latency,
                                error[:120])
                else:
                    db.record_judgment(doc.id, judge.id, skill.name, skill.version,
                                       data.get("score"), data.get("confidence"),
                                       data.get("violations", []), data, latency)
                    log.info("%s [%d/%d] %s doc#%d score=%s in %.1fs",
                             judge.id, i, total, skill.name, doc.id,
                             data.get("score"), latency)
                count += 1
    db.close()
    return count


def _pending_work(db: Database, judge: JudgeConfig, skills: list[Skill],
                  force: bool) -> list[tuple]:
    docs = db.documents()
    work = []
    for skill in skills:
        for doc in docs:
            if not force:
                rows = db.query(
                    """SELECT 1 FROM judgments WHERE document_id=? AND judge_id=?
                       AND skill=? AND skill_version=? AND failed=0""",
                    (doc.id, judge.id, skill.name, skill.version))
                if rows:
                    continue
            work.append((doc, skill))
    return work
