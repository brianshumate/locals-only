"""Human calibration: stratified sampling, a markdown scoring form, import of
completed forms, and human<->judge Spearman correlation per judge."""

from __future__ import annotations

import random
import re
from pathlib import Path

from scipy.stats import spearmanr

from .config import PROJECT_ROOT, Settings, load_settings
from .db import Database

CALIBRATION_DIR = PROJECT_ROOT / "calibration"

FORM_TEMPLATE = """# Calibration form — document {doc_id}

- prompt: {prompt_id}
- author: {author_id}
- file: {path}

Score each criterion 0-10. Fill in the number after the colon.

<!-- doc_id: {doc_id} -->
- style-guide:
- factuality:
- completeness:
- audience-fit:
- code-quality:

Notes:

"""


def sample(pct: float = 10.0, reviewer: str = "human", seed: int = 0,
           settings: Settings | None = None) -> list[Path]:
    """Stratified by doc_type: sample ~pct% of documents per type, min 1.
    Writes one markdown form per sampled document."""
    settings = settings or load_settings()
    db = Database(settings.db_path)
    rows = db.query(
        """SELECT d.id, d.prompt_id, d.author_id, d.path, p.doc_type
           FROM documents d JOIN prompts p ON p.id = d.prompt_id""")
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["doc_type"], []).append(r)
    rng = random.Random(seed)
    CALIBRATION_DIR.mkdir(exist_ok=True)
    written = []
    for doc_type, docs in sorted(by_type.items()):
        k = max(1, round(len(docs) * pct / 100))
        for r in rng.sample(docs, min(k, len(docs))):
            form = CALIBRATION_DIR / f"form-{r['id']:04d}.md"
            form.write_text(FORM_TEMPLATE.format(
                doc_id=r["id"], prompt_id=r["prompt_id"],
                author_id=r["author_id"], path=r["path"]))
            written.append(form)
    db.close()
    return written


SCORE_RE = re.compile(r"^-\s*([\w-]+):\s*([\d.]+)\s*$", re.MULTILINE)
DOC_ID_RE = re.compile(r"<!--\s*doc_id:\s*(\d+)\s*-->")


def import_forms(reviewer: str = "human",
                 settings: Settings | None = None) -> int:
    """Parse filled-in forms in calibration/ into human_scores."""
    settings = settings or load_settings()
    db = Database(settings.db_path)
    count = 0
    for form in sorted(CALIBRATION_DIR.glob("form-*.md")):
        text = form.read_text()
        m = DOC_ID_RE.search(text)
        if not m:
            continue
        doc_id = int(m.group(1))
        notes_match = re.search(r"Notes:\s*\n(.*)", text, re.DOTALL)
        notes = (notes_match.group(1).strip() if notes_match else "")
        for skill, score in SCORE_RE.findall(text):
            db.record_human_score(doc_id, reviewer, skill, float(score), notes)
            count += 1
    db.close()
    return count


def correlation_report(settings: Settings | None = None) -> str:
    """Spearman correlation between each judge's scores and human scores,
    per skill, over documents both have scored."""
    settings = settings or load_settings()
    db = Database(settings.db_path)
    rows = db.query(
        """SELECT h.document_id, h.skill, h.score AS human, j.judge_id,
                  j.score AS judge
           FROM human_scores h
           JOIN judgments j ON j.document_id = h.document_id AND j.skill = h.skill
           WHERE j.failed = 0 AND j.score IS NOT NULL""")
    db.close()
    grouped: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for r in rows:
        grouped.setdefault((r["judge_id"], r["skill"]), []).append(
            (r["human"], r["judge"]))
    if not grouped:
        return "No overlapping human + judge scores yet."
    lines = ["== Human <-> judge Spearman correlation ==",
             f"{'judge':<22}{'skill':<18}{'n':>4}{'rho':>8}{'p':>8}"]
    for (judge, skill), pairs in sorted(grouped.items()):
        if len(pairs) < 3:
            lines.append(f"{judge:<22}{skill:<18}{len(pairs):>4}    (need >=3)")
            continue
        h, j = zip(*pairs)
        rho, p = spearmanr(h, j)
        lines.append(f"{judge:<22}{skill:<18}{len(pairs):>4}{rho:>8.3f}{p:>8.3f}")
    return "\n".join(lines)
