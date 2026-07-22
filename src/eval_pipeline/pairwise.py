"""Pairwise A/B comparisons with position-bias control.

Every pair is judged twice, once in each order. If the two orderings disagree
on the winner, both rows stay in the DB (position_swapped 0/1) and analysis
treats the pair as a tie / judge-noise signal.
"""

from __future__ import annotations

import itertools
import logging
import time
from pathlib import Path

from .backends import model_session
from .config import Settings, load_judges, load_settings
from .db import Database
from .judge import judge_document
from .skills import load_skill

log = logging.getLogger(__name__)

SKILL_NAME = "pairwise-compare"


def all_pairs(db: Database) -> list[tuple]:
    """(prompt_id, doc_a, doc_b) for every unordered author pair per prompt."""
    pairs = []
    prompt_ids = [r["id"] for r in db.query("SELECT id FROM prompts ORDER BY id")]
    for pid in prompt_ids:
        docs = db.documents(prompt_id=pid)
        # One document per author (latest wins if regenerated).
        by_author = {d.author_id: d for d in docs}
        for a, b in itertools.combinations(sorted(by_author), 2):
            pairs.append((pid, by_author[a], by_author[b]))
    return pairs


def compare_all(judge_ids: list[str] | None = None, force: bool = False,
                settings: Settings | None = None) -> int:
    settings = settings or load_settings()
    backend_name = settings.resolve_backend()
    db = Database(settings.db_path)
    skill = load_skill(SKILL_NAME)
    judges = [j for j in load_judges() if SKILL_NAME in j.skills]
    if judge_ids:
        judges = [j for j in judges if j.id in judge_ids]

    pairs = all_pairs(db)
    count = 0
    for judge in judges:
        resolved = judge.resolve(backend_name)
        if resolved is None:
            log.warning("%s: no model configured for backend %r — skipping",
                        judge.id, backend_name)
            continue
        db.upsert_judge(judge.id, judge.display_model(), judge.model_dump())
        work = []
        for pid, doc_a, doc_b in pairs:
            for swapped in (False, True):
                if not force and db.query(
                        """SELECT 1 FROM comparisons WHERE doc_a=? AND doc_b=?
                           AND judge_id=? AND skill=? AND position_swapped=?""",
                        (doc_a.id, doc_b.id, judge.id, SKILL_NAME, int(swapped))):
                    continue
                work.append((pid, doc_a, doc_b, swapped))
        if not work:
            log.info("%s: no comparisons pending", judge.id)
            continue
        log.info("%s: %d comparisons to run", judge.id, len(work))
        with model_session(resolved.model, settings=settings,
                           temperature=judge.temperature,
                           max_tokens=judge.max_tokens,
                           context_length=judge.context_length) as client:
            for pid, doc_a, doc_b, swapped in work:
                text_a = Path(doc_a.path).read_text()
                text_b = Path(doc_b.path).read_text()
                first, second = (text_b, text_a) if swapped else (text_a, text_b)
                data, latency, error = judge_document(
                    client, skill, document="",  # unused by this skill
                    document_a=first, document_b=second)
                if data is None:
                    log.error("comparison failed %s %s/%s: %s", judge.id,
                              doc_a.author_id, doc_b.author_id, error[:200])
                    continue
                winner = data["winner"]  # 'a'|'b'|'tie' in *presented* order
                if swapped and winner in ("a", "b"):
                    winner = "b" if winner == "a" else "a"
                db.record_comparison(pid, doc_a.id, doc_b.id, judge.id,
                                     SKILL_NAME, winner, data.get("confidence"),
                                     swapped, data)
                count += 1
    db.close()
    return count
