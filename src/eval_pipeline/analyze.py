"""Statistics over judgments and comparisons: aggregates, bootstrap CIs,
author x judge matrix, self-judging deltas, Krippendorff's alpha, and
position-swap consistency.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import krippendorff as _krippendorff
except ImportError:  # analysis still usable without it
    _krippendorff = None

from .config import Settings, load_settings
from .db import Database, machine_label

# Sentinel: no environment filtering — pool documents from every environment.
ALL_ENVIRONMENTS = object()


def environment_groups(db: Database) -> list[tuple[int | None, str]]:
    """Environments documents were authored in, as (environment_id, label).

    Documents that predate environment capture (schema < v3) group under a
    trailing (None, "unrecorded environment") entry. Authors are only fairly
    comparable within one group: hardware, backend, and usually the model
    weights themselves (mlx/qat vs gguf quants) differ across groups.
    """
    rows = db.query(
        """SELECT e.id, e.env_hash, e.os, e.arch, e.backend
           FROM environments e
           WHERE EXISTS (SELECT 1 FROM documents d WHERE d.environment_id = e.id)
           ORDER BY e.env_hash, e.backend""")
    groups = [(r["id"], f"{machine_label(r['env_hash'])} · "
                        f"{r['os']}/{r['arch']} · {r['backend']}")
              for r in rows]
    unrecorded = db.query(
        "SELECT COUNT(*) AS n FROM documents WHERE environment_id IS NULL")[0]["n"]
    if unrecorded:
        groups.append((None, "unrecorded environment"))
    return groups


def _env_filter(environment_id, alias: str = "d") -> tuple[str, tuple]:
    """SQL fragment restricting documents (aliased) to one environment.

    `IS ?` (not `= ?`) so binding None matches the NULL environment_id of
    pre-v3 documents.
    """
    if environment_id is ALL_ENVIRONMENTS:
        return "", ()
    return f" AND {alias}.environment_id IS ?", (environment_id,)


@dataclass
class Aggregate:
    author_id: str
    skill: str
    n: int
    mean: float
    median: float
    stddev: float
    ci_low: float
    ci_high: float


def bootstrap_ci(values: np.ndarray, n_boot: int = 2000,
                 alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    if len(values) == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(n_boot, len(values)), replace=True).mean(axis=1)
    return (float(np.quantile(means, alpha / 2)),
            float(np.quantile(means, 1 - alpha / 2)))


def generation_stats(db: Database,
                     environment_id=ALL_ENVIRONMENTS) -> list[dict]:
    """Per-author authoring cost: document count, total wall-clock time,
    prompt/completion token totals, and completion tokens per second.

    Pass an environment id (or None for unrecorded) to restrict to documents
    authored in that environment — speed numbers only compare fairly within
    one machine/backend."""
    where, args = _env_filter(environment_id)
    rows = db.query(
        f"""SELECT d.author_id, a.model, COUNT(*) AS docs,
                  SUM(d.gen_time_s) AS total_time_s,
                  SUM(d.tokens) AS completion_tokens,
                  SUM(d.prompt_tokens) AS prompt_tokens
           FROM documents d JOIN authors a ON a.id = d.author_id
           WHERE 1=1{where}
           GROUP BY d.author_id ORDER BY d.author_id""", args)
    out = []
    for r in rows:
        total_time = r["total_time_s"] or 0.0
        completion = r["completion_tokens"] or 0
        out.append({
            "author_id": r["author_id"],
            "model": r["model"],
            "docs": r["docs"],
            "total_time_s": round(total_time, 1),
            "prompt_tokens": r["prompt_tokens"] or 0,
            "completion_tokens": completion,
            "total_tokens": (r["prompt_tokens"] or 0) + completion,
            "tokens_per_s": round(completion / total_time, 1) if total_time else None,
        })
    return out


def score_aggregates(db: Database,
                     environment_id=ALL_ENVIRONMENTS) -> list[Aggregate]:
    where, args = _env_filter(environment_id)
    rows = db.query(
        f"""SELECT d.author_id, j.skill, j.score FROM judgments j
           JOIN documents d ON d.id = j.document_id
           WHERE j.failed = 0 AND j.score IS NOT NULL{where}""", args)
    grouped: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        grouped.setdefault((r["author_id"], r["skill"]), []).append(r["score"])
    out = []
    for (author, skill), vals in sorted(grouped.items()):
        arr = np.array(vals, dtype=float)
        lo, hi = bootstrap_ci(arr)
        out.append(Aggregate(author, skill, len(arr), float(arr.mean()),
                             float(np.median(arr)), float(arr.std(ddof=1)) if len(arr) > 1 else 0.0,
                             lo, hi))
    return out


def author_judge_matrix(db: Database, skill: str | None = None) -> dict:
    """{author_id: {judge_id: mean_score}} plus self-judging deltas.

    A judge 'self-judges' when its underlying model equals the author's model.
    """
    where = "AND j.skill = ?" if skill else ""
    args = (skill,) if skill else ()
    rows = db.query(
        f"""SELECT d.author_id, j.judge_id, AVG(j.score) AS mean_score
            FROM judgments j JOIN documents d ON d.id = j.document_id
            WHERE j.failed = 0 AND j.score IS NOT NULL {where}
            GROUP BY d.author_id, j.judge_id""", args)
    matrix: dict[str, dict[str, float]] = {}
    for r in rows:
        matrix.setdefault(r["author_id"], {})[r["judge_id"]] = round(r["mean_score"], 3)

    author_models = {r["id"]: r["model"] for r in db.query("SELECT id, model FROM authors")}
    judge_models = {r["id"]: r["model"] for r in db.query("SELECT id, model FROM judges")}
    deltas = {}
    for author, judge_scores in matrix.items():
        self_scores = [s for j, s in judge_scores.items()
                       if judge_models.get(j) == author_models.get(author)]
        other_scores = [s for j, s in judge_scores.items()
                        if judge_models.get(j) != author_models.get(author)]
        if self_scores and other_scores:
            deltas[author] = round(float(np.mean(self_scores) - np.mean(other_scores)), 3)
    return {"matrix": matrix, "self_judging_delta": deltas}


def judge_agreement(db: Database) -> dict:
    """Per-skill Krippendorff's alpha across judges + per-judge deviation from
    the panel median + pairwise position-swap consistency per judge."""
    out: dict = {"alpha": {}, "judge_deviation": {}, "swap_consistency": {}}

    skills = [r["skill"] for r in db.query(
        "SELECT DISTINCT skill FROM judgments WHERE failed=0")]
    for skill in skills:
        rows = db.query(
            """SELECT document_id, judge_id, score FROM judgments
               WHERE failed=0 AND score IS NOT NULL AND skill=?""", (skill,))
        judges = sorted({r["judge_id"] for r in rows})
        docs = sorted({r["document_id"] for r in rows})
        if len(judges) < 2 or not docs:
            continue
        table = np.full((len(judges), len(docs)), np.nan)
        jidx = {j: i for i, j in enumerate(judges)}
        didx = {d: i for i, d in enumerate(docs)}
        for r in rows:
            table[jidx[r["judge_id"]], didx[r["document_id"]]] = r["score"]
        if _krippendorff is not None:
            try:
                out["alpha"][skill] = round(float(_krippendorff.alpha(
                    reliability_data=table, level_of_measurement="interval")), 3)
            except (ValueError, ZeroDivisionError):
                out["alpha"][skill] = None
        # deviation from panel median per judge
        medians = np.nanmedian(table, axis=0)
        for j, i in jidx.items():
            dev = np.nanmean(np.abs(table[i] - medians))
            out["judge_deviation"].setdefault(skill, {})[j] = round(float(dev), 3)

    # position-swap consistency: fraction of pairs where both orderings agree
    rows = db.query(
        """SELECT doc_a, doc_b, judge_id, skill,
                  MAX(CASE WHEN position_swapped=0 THEN winner END) AS w0,
                  MAX(CASE WHEN position_swapped=1 THEN winner END) AS w1
           FROM comparisons GROUP BY doc_a, doc_b, judge_id, skill
           HAVING w0 IS NOT NULL AND w1 IS NOT NULL""")
    per_judge: dict[str, list[int]] = {}
    for r in rows:
        per_judge.setdefault(r["judge_id"], []).append(int(r["w0"] == r["w1"]))
    for j, agreements in per_judge.items():
        out["swap_consistency"][j] = round(float(np.mean(agreements)), 3)
    return out


def effective_comparisons(db: Database,
                          environment_id=ALL_ENVIRONMENTS) -> list[tuple[str, str, str]]:
    """Resolve position-swapped duplicate comparisons into effective outcomes:
    (author_a, author_b, winner) where winner in {'a','b','tie'}.
    Disagreement across orderings -> tie.

    With an environment id, only pairs where *both* documents were authored in
    that environment count — the basis for per-environment rankings."""
    where_a, args_a = _env_filter(environment_id, "da")
    where_b, args_b = _env_filter(environment_id, "db_")
    rows = db.query(
        f"""SELECT c.doc_a, c.doc_b, c.judge_id, c.skill,
                  da.author_id AS author_a, db_.author_id AS author_b,
                  MAX(CASE WHEN position_swapped=0 THEN winner END) AS w0,
                  MAX(CASE WHEN position_swapped=1 THEN winner END) AS w1
           FROM comparisons c
           JOIN documents da ON da.id = c.doc_a
           JOIN documents db_ ON db_.id = c.doc_b
           WHERE 1=1{where_a}{where_b}
           GROUP BY c.doc_a, c.doc_b, c.judge_id, c.skill""",
        args_a + args_b)
    out = []
    for r in rows:
        w0, w1 = r["w0"], r["w1"]
        if w0 is not None and w1 is not None:
            winner = w0 if w0 == w1 else "tie"
        else:
            winner = w0 or w1
        if winner is None:
            continue
        out.append((r["author_a"], r["author_b"], winner))
    return out


def print_summary(settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    lines = []

    # Break cost/score tables out per environment when documents span more
    # than one machine/backend — pooled numbers would compare unlike hardware
    # (and usually unlike quantizations).
    groups = environment_groups(db)
    if len(groups) <= 1:
        env_sections = [(ALL_ENVIRONMENTS, groups[0][1] if groups else None)]
    else:
        env_sections = groups

    for env_id, label in env_sections:
        gen = generation_stats(db, environment_id=env_id)
        if not gen:
            continue
        suffix = f" — {label}" if label else ""
        lines.append(f"== Generation cost (per author){suffix} ==")
        lines.append(f"{'author':<16}{'docs':>5}{'time_s':>9}{'prompt_tok':>12}"
                     f"{'compl_tok':>11}{'tok/s':>8}")
        for g in gen:
            tps = f"{g['tokens_per_s']:.1f}" if g["tokens_per_s"] else "—"
            lines.append(f"{g['author_id']:<16}{g['docs']:>5}{g['total_time_s']:>9.1f}"
                         f"{g['prompt_tokens']:>12}{g['completion_tokens']:>11}{tps:>8}")
        lines.append("")

    for env_id, label in env_sections:
        env_aggs = score_aggregates(db, environment_id=env_id)
        if not env_aggs:
            continue
        suffix = f" — {label}" if label else ""
        lines.append(f"== Score aggregates (author x skill){suffix} ==")
        lines.append(f"{'author':<16}{'skill':<18}{'n':>4}{'mean':>7}{'med':>6}"
                     f"{'sd':>6}{'95% CI':>18}")
        for a in env_aggs:
            lines.append(f"{a.author_id:<16}{a.skill:<18}{a.n:>4}{a.mean:>7.2f}"
                         f"{a.median:>6.1f}{a.stddev:>6.2f}"
                         f"   [{a.ci_low:.2f}, {a.ci_high:.2f}]")
        lines.append("")

    aggs = score_aggregates(db)  # pooled: feeds the self-judging CI flag

    mj = author_judge_matrix(db)
    if mj["matrix"]:
        lines.append("\n== Author x Judge mean-score matrix ==")
        judges = sorted({j for js in mj["matrix"].values() for j in js})
        lines.append("author".ljust(16) + "".join(j[:18].rjust(20) for j in judges))
        for author, js in sorted(mj["matrix"].items()):
            lines.append(author.ljust(16) +
                         "".join(f"{js.get(j, float('nan')):>20.2f}" for j in judges))
        if mj["self_judging_delta"]:
            lines.append("\nSelf-judging delta (self mean - others mean):")
            aggs_by_author = {}
            for a in aggs:
                aggs_by_author.setdefault(a.author_id, []).append(a)
            for author, delta in sorted(mj["self_judging_delta"].items()):
                # Flag when delta exceeds the widest CI half-width for that author.
                widths = [(x.ci_high - x.ci_low) / 2 for x in aggs_by_author.get(author, [])]
                flag = "  ⚠ exceeds CI width" if widths and delta > max(widths) else ""
                lines.append(f"  {author}: {delta:+.3f}{flag}")

    agree = judge_agreement(db)
    if agree["alpha"] or agree["swap_consistency"]:
        lines.append("\n== Judge agreement ==")
        for skill, alpha in sorted(agree["alpha"].items()):
            lines.append(f"  Krippendorff alpha[{skill}] = {alpha}")
        for skill, devs in sorted(agree["judge_deviation"].items()):
            for j, d in sorted(devs.items()):
                lines.append(f"  |dev from median|[{skill}][{j}] = {d}")
        for j, c in sorted(agree["swap_consistency"].items()):
            lines.append(f"  swap-consistency[{j}] = {c}")

    db.close()
    return "\n".join(lines) if lines else "No data yet — run generate/judge first."
