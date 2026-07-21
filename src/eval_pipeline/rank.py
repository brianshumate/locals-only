"""Bradley-Terry ranking from pairwise comparisons (via choix), with
bootstrap CIs. Ties count as half a win for each side."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .analyze import effective_comparisons, environment_groups
from .config import Settings, load_settings
from .db import Database


@dataclass
class Rating:
    author_id: str
    rating: float
    ci_low: float
    ci_high: float
    n_comparisons: int


def _fit_bt(wins: list[tuple[int, int]], n_items: int,
            alpha: float = 0.01) -> np.ndarray:
    import choix
    return choix.ilsr_pairwise(n_items, wins, alpha=alpha)


def bt_ratings(comparisons: list[tuple[str, str, str]],
               n_boot: int = 500, seed: int = 0) -> list[Rating]:
    """comparisons: (author_a, author_b, winner in {'a','b','tie'})."""
    authors = sorted({a for a, _, _ in comparisons} | {b for _, b, _ in comparisons})
    idx = {a: i for i, a in enumerate(authors)}

    def to_wins(comps) -> list[tuple[int, int]]:
        wins = []
        for a, b, w in comps:
            if w == "a":
                wins.append((idx[a], idx[b]))
            elif w == "b":
                wins.append((idx[b], idx[a]))
            else:  # tie -> half win each way (append both directions once)
                wins.append((idx[a], idx[b]))
                wins.append((idx[b], idx[a]))
        return wins

    point = _fit_bt(to_wins(comparisons), len(authors))

    rng = np.random.default_rng(seed)
    comps_arr = np.array(comparisons, dtype=object)
    boots = []
    for _ in range(n_boot):
        sample = comps_arr[rng.integers(0, len(comps_arr), len(comps_arr))]
        try:
            boots.append(_fit_bt(to_wins([tuple(r) for r in sample]), len(authors)))
        except Exception:
            continue
    boots_arr = np.array(boots) if boots else point.reshape(1, -1)
    lo = np.quantile(boots_arr, 0.025, axis=0)
    hi = np.quantile(boots_arr, 0.975, axis=0)

    counts = {a: 0 for a in authors}
    for a, b, _ in comparisons:
        counts[a] += 1
        counts[b] += 1

    ratings = [Rating(a, float(point[i]), float(lo[i]), float(hi[i]), counts[a])
               for a, i in idx.items()]
    return sorted(ratings, key=lambda r: -r.rating)


def _render_table(comps: list[tuple[str, str, str]], heading: str) -> list[str]:
    ratings = bt_ratings(comps)
    lines = [heading,
             f"{'rank':<6}{'author':<18}{'rating':>8}{'95% CI':>20}{'n':>6}"]
    for i, r in enumerate(ratings, 1):
        lines.append(f"{i:<6}{r.author_id:<18}{r.rating:>8.3f}"
                     f"   [{r.ci_low:.3f}, {r.ci_high:.3f}]{r.n_comparisons:>6}")
    # Refuse to declare a winner when top CIs overlap.
    if len(ratings) >= 2 and ratings[0].ci_low <= ratings[1].ci_high:
        lines.append("NOTE: top-2 confidence intervals overlap — "
                     "no statistically distinguishable winner.")
    elif len(ratings) >= 2:
        lines.append(f"Winner: {ratings[0].author_id} "
                     "(CI separated from runner-up).")
    return lines


def leaderboard(settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    comps = effective_comparisons(db)
    if not comps:
        db.close()
        return "No pairwise comparisons in DB yet — run `eval compare` first."

    # One leaderboard per environment: only same-environment pairs count, so
    # authors are compared on identical hardware/backend (and deployment).
    per_env = []
    for env_id, label in environment_groups(db):
        env_comps = effective_comparisons(db, environment_id=env_id)
        if env_comps:
            per_env.append((label, env_comps))
    db.close()

    if len(per_env) <= 1:
        label = per_env[0][0] if per_env else None
        heading = (f"== Bradley-Terry leaderboard — {label} =="
                   if label else "== Bradley-Terry leaderboard ==")
        return "\n".join(_render_table(comps, heading))

    lines: list[str] = []
    for label, env_comps in per_env:
        lines += _render_table(env_comps,
                               f"== Bradley-Terry leaderboard — {label} ==")
        lines.append("")
    n_cross = len(comps) - sum(len(c) for _, c in per_env)
    lines += _render_table(comps, "== Bradley-Terry leaderboard — all "
                                  "environments pooled ==")
    note = ("NOTE: pooled table mixes environments/backends; deployments "
            "(quantization, hardware) differ, so prefer the per-environment "
            "tables when comparing authors.")
    if n_cross:
        note += f" ({n_cross} cross-environment pair(s) appear only here.)"
    lines.append(note)
    return "\n".join(lines)
