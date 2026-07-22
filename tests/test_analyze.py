"""Analysis + ranking on synthetic data (WU-4.x)."""

import numpy as np
import pytest

from eval_pipeline.analyze import (author_judge_matrix, bootstrap_ci,
                                   effective_comparisons, environment_groups,
                                   generation_stats, judge_agreement,
                                   score_aggregates)
from eval_pipeline.rank import bt_ratings


def _seed_judgments(db, doc_a, doc_b):
    # author-a consistently better; judge-1 shares author-a's model (self-judge)
    for i, (doc, j1, j2) in enumerate([(doc_a, 9.5, 8.0), (doc_b, 5.0, 4.0)]):
        db.record_judgment(doc, "judge-1", "style-guide", "1.0", j1, 0.9, [], {}, 1)
        db.record_judgment(doc, "judge-2", "style-guide", "1.0", j2, 0.9, [], {}, 1)


def test_bootstrap_ci_contains_mean():
    vals = np.array([5.0, 6.0, 7.0, 8.0, 9.0])
    lo, hi = bootstrap_ci(vals)
    assert lo <= vals.mean() <= hi


def test_score_aggregates(seeded_db):
    db, doc_a, doc_b = seeded_db
    _seed_judgments(db, doc_a, doc_b)
    aggs = score_aggregates(db)
    by_author = {a.author_id: a for a in aggs}
    assert by_author["author-a"].mean > by_author["author-b"].mean
    assert by_author["author-a"].n == 2


def test_author_judge_matrix_and_self_delta(seeded_db):
    db, doc_a, doc_b = seeded_db
    _seed_judgments(db, doc_a, doc_b)
    mj = author_judge_matrix(db)
    assert mj["matrix"]["author-a"]["judge-1"] == 9.5
    # judge-1 model == author-a model -> positive self-judging delta
    assert mj["self_judging_delta"]["author-a"] == pytest.approx(1.5)


def test_judge_agreement_swap_consistency(seeded_db):
    db, doc_a, doc_b = seeded_db
    _seed_judgments(db, doc_a, doc_b)
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare", "a", 0.9, False)
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare", "a", 0.9, True)
    db.record_comparison("p1", doc_a, doc_b, "judge-2", "pairwise-compare", "a", 0.9, False)
    db.record_comparison("p1", doc_a, doc_b, "judge-2", "pairwise-compare", "b", 0.9, True)
    agree = judge_agreement(db)
    assert agree["swap_consistency"]["judge-1"] == 1.0
    assert agree["swap_consistency"]["judge-2"] == 0.0
    assert "style-guide" in agree["judge_deviation"]


def test_effective_comparisons_disagreement_is_tie(seeded_db):
    db, doc_a, doc_b = seeded_db
    db.record_comparison("p1", doc_a, doc_b, "judge-2", "pairwise-compare", "a", 0.9, False)
    db.record_comparison("p1", doc_a, doc_b, "judge-2", "pairwise-compare", "b", 0.9, True)
    comps = effective_comparisons(db)
    assert comps == [("author-a", "author-b", "tie")]


def _two_env_db(seeded_db):
    """seeded_db plus a second environment: doc_a/doc_b are unrecorded (NULL
    environment), doc_c/doc_d were authored on a recorded machine."""
    db, doc_a, doc_b = seeded_db
    env = db.upsert_environment({
        "hostname": "srv", "os": "Linux", "os_version": "6", "arch": "x86_64",
        "cpu": "x", "gpu": "RTX 3090", "backend": "llamacpp",
        "backend_version": "b1"})
    doc_c = db.register_document("p1", "author-a", "runs/p1/a2.md", "hash-c",
                                 gen_time_s=10.0, tokens=100,
                                 environment_id=env)
    doc_d = db.register_document("p1", "author-b", "runs/p1/b2.md", "hash-d",
                                 gen_time_s=20.0, tokens=100,
                                 environment_id=env)
    return db, env, doc_a, doc_b, doc_c, doc_d


def test_environment_groups_include_unrecorded(seeded_db):
    db, env, *_ = _two_env_db(seeded_db)
    groups = environment_groups(db)
    assert [g[0] for g in groups] == [env, None]
    assert "srv" in groups[0][1] and "llamacpp" in groups[0][1]


def test_generation_stats_env_filter(seeded_db):
    db, env, *_ = _two_env_db(seeded_db)
    per_env = {g["author_id"]: g for g in generation_stats(db, environment_id=env)}
    assert per_env["author-a"]["docs"] == 1
    assert per_env["author-a"]["tokens_per_s"] == 10.0
    pooled = {g["author_id"]: g for g in generation_stats(db)}
    assert pooled["author-a"]["docs"] == 2


def test_score_aggregates_env_filter(seeded_db):
    db, env, doc_a, _, doc_c, _ = _two_env_db(seeded_db)
    db.record_judgment(doc_a, "judge-1", "style-guide", "1.0", 9.0, 0.9, [], {}, 1)
    db.record_judgment(doc_c, "judge-1", "style-guide", "1.0", 5.0, 0.9, [], {}, 1)
    by_env = {a.author_id: a for a in score_aggregates(db, environment_id=env)}
    assert by_env["author-a"].mean == 5.0 and by_env["author-a"].n == 1
    unrecorded = {a.author_id: a for a in score_aggregates(db, environment_id=None)}
    assert unrecorded["author-a"].mean == 9.0


def test_effective_comparisons_env_filter(seeded_db):
    db, env, doc_a, doc_b, doc_c, doc_d = _two_env_db(seeded_db)
    # same-env pair (recorded), same-env pair (unrecorded), cross-env pair
    db.record_comparison("p1", doc_c, doc_d, "judge-1", "pairwise-compare", "a", 0.9, False)
    db.record_comparison("p1", doc_a, doc_b, "judge-1", "pairwise-compare", "b", 0.9, False)
    db.record_comparison("p1", doc_a, doc_d, "judge-1", "pairwise-compare", "a", 0.9, False)
    assert effective_comparisons(db, environment_id=env) == [
        ("author-a", "author-b", "a")]
    assert effective_comparisons(db, environment_id=None) == [
        ("author-a", "author-b", "b")]
    assert len(effective_comparisons(db)) == 3  # pooled keeps the cross-env pair


def test_bt_ratings_orders_winner_first():
    rng = np.random.default_rng(0)
    comps = []
    for _ in range(60):
        comps.append(("strong", "weak", "a" if rng.random() < 0.85 else "b"))
        comps.append(("strong", "mid", "a" if rng.random() < 0.7 else "b"))
        comps.append(("mid", "weak", "a" if rng.random() < 0.7 else "b"))
    ratings = bt_ratings(comps, n_boot=50)
    assert [r.author_id for r in ratings] == ["strong", "mid", "weak"]
    top = ratings[0]
    assert top.ci_low <= top.rating <= top.ci_high
