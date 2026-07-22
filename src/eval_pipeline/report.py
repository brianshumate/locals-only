"""Static self-contained HTML reports generated from the DB alone."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape

from jinja2 import Template

import math

from .analyze import (ALL_ENVIRONMENTS, _env_filter, author_judge_matrix,
                      effective_comparisons, environment_groups,
                      generation_stats, judge_agreement, score_aggregates)
from .config import Settings, load_settings
from .db import Database
from .rank import bt_ratings

PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ title }}</title>
<style>
 :root { color-scheme: light dark;
   --page: #f9f9f7; --ink: #0b0b0b; --ink-2: #52514e;
   --grid: #e1e0d9; --cell: #f2f2f2; --link: #1c5cab; }
 @media (prefers-color-scheme: dark) {
   :root { --page: #0d0d0d; --ink: #ffffff; --ink-2: #c3c2b7;
     --grid: #2c2c2a; --cell: #1a1a19; --link: #86b6ef; } }
 body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        margin: 2rem auto; max-width: 70rem; padding: 0 1rem;
        background: var(--page); color: var(--ink); }
 a { color: var(--link); }
 nav { margin-bottom: 1rem; }
 h1 { border-bottom: 2px solid var(--grid); padding-bottom: .3rem; }
 table { border-collapse: collapse; margin: 1rem 0; width: 100%; }
 th, td { border: 1px solid var(--grid); padding: .4rem .6rem; text-align: left; }
 th { background: var(--cell); }
 td.num { text-align: right; font-variant-numeric: tabular-nums; }
 .heat { text-align: right; font-variant-numeric: tabular-nums; }
 .note { color: var(--ink-2); font-size: .9rem; }
 details { margin: .5rem 0; }
</style>
</head>
<body>
<h1>{{ title }}</h1>
<p class="note">Generated {{ generated_at }} from results.sqlite.</p>
{{ body }}
</body>
</html>""")


# -- leaderboard dashboard ----------------------------------------------------

# Reference categorical palette (dataviz skill): fixed slot order, adjacent
# pairs CVD-validated in both modes. Slot follows the author (entity), never
# rank, so colors stay stable across charts and regenerations.
SERIES_LIGHT = ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a",
                "#eb6834", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#008300", "#d55181", "#c98500", "#199e70",
               "#d95926", "#9085e9", "#e66767"]

DASH_CSS = """
.viz-root { color-scheme: light;
  --surface-1:#fcfcfb; --page:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7;
  --border:rgba(11,11,11,0.10);
  %LIGHT_SLOTS% }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root { color-scheme: dark;
    --surface-1:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --baseline:#383835;
    --border:rgba(255,255,255,0.10);
    %DARK_SLOTS% } }
:root[data-theme="dark"] .viz-root { color-scheme: dark;
  --surface-1:#1a1a19; --page:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --baseline:#383835;
  --border:rgba(255,255,255,0.10);
  %DARK_SLOTS% }
.viz-root { background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.viz-root h1, .viz-root h2 { color: var(--ink); border: none; }
.tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }
.tile { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 8px; padding: .8rem 1.1rem; min-width: 9rem; }
.tile .v { font-size: 1.7rem; font-weight: 650; }
.tile .k { color: var(--ink-2); font-size: .8rem; margin-top: .15rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr));
  gap: 1rem; }
.card { background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem; overflow-x: auto; }
.card h2 { font-size: 1rem; margin: 0 0 .2rem; }
.card .sub { color: var(--ink-2); font-size: .8rem; margin: 0 0 .8rem; }
.lb table { border-collapse: collapse; width: 100%; }
.lb th { text-align: left; color: var(--ink-2); font-size: .78rem;
  font-weight: 600; border: none; border-bottom: 1px solid var(--grid);
  padding: .35rem .6rem; background: none; }
.lb td { border: none; border-bottom: 1px solid var(--grid);
  padding: .45rem .6rem; }
.lb td.num, .lb th.num { text-align: right;
  font-variant-numeric: tabular-nums; }
.lb tr:last-child td { border-bottom: none; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: .5rem; vertical-align: baseline; }
.rank1 { font-weight: 650; }
#tt { position: fixed; pointer-events: none; background: var(--surface-1);
  color: var(--ink); border: 1px solid var(--border); border-radius: 6px;
  padding: .4rem .6rem; font-size: .78rem; box-shadow: 0 2px 8px rgba(0,0,0,.18);
  display: none; z-index: 10; }
.note { color: var(--ink-2); }
"""

DASH_JS = """
<div id="tt"></div>
<script>
var tt = document.getElementById('tt');
document.querySelectorAll('[data-tt]').forEach(function (el) {
  el.addEventListener('mousemove', function (e) {
    tt.innerHTML = el.getAttribute('data-tt');
    tt.style.display = 'block';
    tt.style.left = Math.min(e.clientX + 12, window.innerWidth - 240) + 'px';
    tt.style.top = (e.clientY + 12) + 'px';
  });
  el.addEventListener('mouseleave', function () { tt.style.display = 'none'; });
});
</script>
"""


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 10000 else str(n)


def _fmt_duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.1f} min"
    return f"{seconds:.0f} s"


LABEL_PAD = 8
LABEL_W_MIN, LABEL_W_MAX = 110, 190


def _text_w(s: str, font_size: float = 12) -> float:
    """Approximate rendered width of an SVG text run.

    SVG does not reflow or measure text, so the label gutter has to be sized
    before rendering or long author ids run off the left edge of the viewBox.
    Per-character advances for the system sans at 1px, rounded up slightly —
    over-estimating costs a little plot width, under-estimating clips a label.
    """
    narrow, wide = "iljtfrI.,-'!|:;", "mwMW@"
    em = sum(0.30 if c in narrow else 0.92 if c in wide else 0.56 for c in s)
    return em * font_size


def _label_gutter(rows: list[dict]) -> float:
    """Width reserved for author labels, sized to the longest one."""
    widest = max((_text_w(r["author_id"]) for r in rows), default=0)
    return float(min(max(math.ceil(widest) + LABEL_PAD * 2, LABEL_W_MIN),
                     LABEL_W_MAX))


def _fit_label(s: str, avail: float) -> tuple[str, str]:
    """(display text, optional <title> child) for a label in `avail` px.

    Only bites at the LABEL_W_MAX clamp; the truncated name stays reachable
    as a native tooltip so identity is never lost to the ellipsis.
    """
    if _text_w(s) <= avail:
        return escape(s), ""
    trimmed = s
    while trimmed and _text_w(trimmed + "…") > avail:
        trimmed = trimmed[:-1]
    return escape(trimmed) + "…", f"<title>{escape(s)}</title>"


def _bar_chart(rows: list[dict], value_key: str, max_value: float,
               fmt=lambda v: f"{v:.1f}") -> str:
    """Horizontal bars from a zero baseline. One rect per author, 22px bars
    with 2px surface gaps, 4px rounded data-end, direct value labels in ink
    (relief rule for the sub-contrast light slots), per-mark hover tooltip.

    The label gutter sizes to the longest author id rather than a fixed
    width, so the bars start where the names end.
    """
    if not rows or max_value <= 0:
        return "<p class='note'>No data yet.</p>"
    bar_h, gap, value_w, width = 22, 2, 52, 560
    label_w = _label_gutter(rows)
    plot_w = width - label_w - value_w
    height = len(rows) * (bar_h + gap) + 6
    parts = [f"<svg viewBox='0 0 {width} {height}' width='100%' "
             f"style='max-width:{width}px' role='img'>"]
    parts.append(f"<line x1='{label_w}' y1='0' x2='{label_w}' y2='{height - 4}' "
                 "stroke='var(--baseline)' stroke-width='1'/>")
    for i, r in enumerate(rows):
        y = i * (bar_h + gap)
        v = r[value_key] or 0
        w = max(plot_w * v / max_value, 1)
        text, title = _fit_label(r["author_id"], label_w - LABEL_PAD * 2)
        parts.append(
            f"<text x='{label_w - LABEL_PAD}' y='{y + bar_h / 2 + 4}' "
            f"text-anchor='end' font-size='12' fill='var(--ink-2)'>"
            f"{title}{text}</text>")
        parts.append(
            f"<path d='M{label_w},{y} h{w - 4:.1f} a4,4 0 0 1 4,4 v{bar_h - 8} "
            f"a4,4 0 0 1 -4,4 h-{w - 4:.1f} z' fill='var({r['slot']})' "
            f"data-tt=\"{r['author_id']} · {r['model']}<br>{r['tt'][value_key]}\"/>")
        parts.append(
            f"<text x='{label_w + w + 6}' y='{y + bar_h / 2 + 4}' font-size='12' "
            f"fill='var(--ink)' style='font-variant-numeric:tabular-nums'>"
            f"{fmt(v)}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _author_slots(db: Database) -> dict[str, str]:
    """Color slot follows the author in stable (sorted) order across every
    section and regeneration, never rank."""
    authors = sorted({r["id"] for r in db.query("SELECT id FROM authors")}
                     | {r["author_id"] for r in
                        db.query("SELECT DISTINCT author_id FROM documents")})
    return {a: f"--s{i + 1}" for i, a in enumerate(authors)}


def _leaderboard_rows(db: Database, environment_id=ALL_ENVIRONMENTS,
                      slots: dict[str, str] | None = None) -> tuple[list[dict], str]:
    """Assemble per-author stats and pick the ranking basis.

    With an environment id, every stat (docs, speed, scores, BT rating) is
    computed from that environment's documents only, so authors compare on
    identical hardware/backend."""
    where, args = _env_filter(environment_id)

    gen = {g["author_id"]: g for g in generation_stats(db, environment_id)}
    if environment_id is ALL_ENVIRONMENTS:
        authors = sorted({r["id"] for r in db.query("SELECT id FROM authors")}
                         | set(gen))
    else:
        authors = sorted(gen)  # only authors with documents in this environment
    models = {r["id"]: r["model"] for r in db.query("SELECT id, model FROM authors")}

    judge_means = {r["author_id"]: r["m"] for r in db.query(
        f"""SELECT d.author_id, AVG(j.score) AS m FROM judgments j
           JOIN documents d ON d.id = j.document_id
           WHERE j.failed=0 AND j.score IS NOT NULL{where}
           GROUP BY d.author_id""", args)}
    det_means = {r["author_id"]: r["m"] for r in db.query(
        f"""SELECT d.author_id, AVG(r.score) AS m FROM det_results r
           JOIN documents d ON d.id = r.document_id
           WHERE r.score IS NOT NULL AND r.tool != 'readability'{where}
           GROUP BY d.author_id""", args)}
    det_pass = {r["author_id"]: r["p"] for r in db.query(
        f"""SELECT d.author_id, AVG(r.passed) AS p FROM det_results r
           JOIN documents d ON d.id = r.document_id
           WHERE 1=1{where} GROUP BY d.author_id""", args)}

    author_envs = {r["author_id"]: r["envs"] for r in db.query(
        f"""SELECT d.author_id, GROUP_CONCAT(DISTINCT e.backend) AS envs
           FROM documents d JOIN environments e ON e.id = d.environment_id
           WHERE 1=1{where} GROUP BY d.author_id""", args)}

    elo = {}
    comps = effective_comparisons(db, environment_id)
    if comps:
        from .rank import bt_ratings
        for r in bt_ratings(comps):
            elo[r.author_id] = 1500 + 400 / math.log(10) * r.rating

    if elo:
        basis, key = "Bradley–Terry rating (Elo-scaled)", lambda a: elo.get(a)
    elif judge_means:
        basis, key = "mean judge score", lambda a: judge_means.get(a)
    else:
        basis, key = "mean deterministic score", lambda a: det_means.get(a)

    slots = slots or _author_slots(db)

    rows = []
    for a in authors:
        g = gen.get(a, {})
        docs = g.get("docs", 0)
        total_time = g.get("total_time_s") or 0
        rows.append({
            "author_id": a,
            "model": models.get(a, "?"),
            "slot": slots[a],
            "docs": docs,
            "total_time_s": total_time,
            "time_per_doc": total_time / docs if docs else None,
            "total_tokens": g.get("total_tokens", 0),
            "tokens_per_s": g.get("tokens_per_s"),
            "judge_mean": judge_means.get(a),
            "det_mean": det_means.get(a),
            "det_pass": det_pass.get(a),
            "envs": author_envs.get(a),
            "elo": elo.get(a),
            "rank_value": key(a),
            "tt": {},
        })
    for r in rows:
        r["tt"] = {
            "judge_mean": f"mean judge score: {_opt(r['judge_mean'], '{:.2f}')} / 10",
            "det_mean": f"mean deterministic score: {_opt(r['det_mean'], '{:.2f}')} / 10",
            "tokens_per_s": (f"{_opt(r['tokens_per_s'], '{:.1f}')} completion tok/s · "
                             f"{r['docs']} doc(s) in {_fmt_duration(r['total_time_s'])}"),
        }
    ranked = sorted(rows, key=lambda r: (r["rank_value"] is None,
                                         -(r["rank_value"] or 0)))
    return ranked, basis


def _opt(v, fmt: str) -> str:
    return fmt.format(v) if v is not None else "—"


def _environments_html(db: Database) -> str:
    """Where the documents were generated: host, OS, CPU/GPU, backend."""
    rows = db.query(
        """SELECT e.hostname, e.os, e.os_version, e.arch, e.cpu, e.gpu,
                  e.backend, e.backend_version, COUNT(d.id) AS docs
           FROM environments e LEFT JOIN documents d ON d.environment_id = e.id
           GROUP BY e.id ORDER BY e.hostname, e.backend""")
    if not rows:
        return ("<p class='note'>No environment records yet; documents "
                "generated before schema v3 predate environment capture.</p>")
    body = ""
    for r in rows:
        backend = r["backend"] + (f" ({r['backend_version']})"
                                  if r["backend_version"] else "")
        body += (f"<tr><td>{r['hostname']}</td>"
                 f"<td>{r['os']} {r['os_version']}</td><td>{r['arch']}</td>"
                 f"<td>{r['cpu'] or '—'}</td><td>{r['gpu'] or '—'}</td>"
                 f"<td>{backend}</td><td class='num'>{r['docs']}</td></tr>")
    note = ""
    if len(rows) > 1:
        note = ("<p class='note'>Documents come from more than one "
                "environment, each has its own results section above; only "
                "same-environment numbers compare authors fairly.</p>")
    return ("<table><tr><th>Host</th><th>OS</th><th>Arch</th><th>CPU</th>"
            f"<th>GPU</th><th>Backend</th><th class='num'>Docs</th></tr>"
            f"{body}</table>{note}")


def _leaderboard_table(rows: list[dict]) -> str:
    header = ("<tr><th>#</th><th>Author</th><th>Model</th><th>Backend</th>"
              "<th class='num'>Rating</th><th class='num'>Judge score</th>"
              "<th class='num'>Det. score</th><th class='num'>Det. pass</th>"
              "<th class='num'>Docs</th><th class='num'>Time/doc</th>"
              "<th class='num'>Tok/s</th><th class='num'>Tokens</th></tr>")
    body = ""
    for i, r in enumerate(rows, 1):
        body += (
            f"<tr class='{'rank1' if i == 1 else ''}'><td>{i}</td>"
            f"<td><span class='swatch' style='background:var({r['slot']})'></span>"
            f"{r['author_id']}</td>"
            f"<td>{r['model']}</td>"
            f"<td>{r['envs'] or '—'}</td>"
            f"<td class='num'>{_opt(r['elo'], '{:.0f}')}</td>"
            f"<td class='num'>{_opt(r['judge_mean'], '{:.2f}')}</td>"
            f"<td class='num'>{_opt(r['det_mean'], '{:.2f}')}</td>"
            f"<td class='num'>{_opt(r['det_pass'], '{:.0%}')}</td>"
            f"<td class='num'>{r['docs']}</td>"
            f"<td class='num'>{_fmt_duration(r['time_per_doc']) if r['time_per_doc'] else '—'}</td>"
            f"<td class='num'>{_opt(r['tokens_per_s'], '{:.1f}')}</td>"
            f"<td class='num'>{_fmt_tokens(r['total_tokens'])}</td></tr>")
    return f"<table>{header}{body}</table>"


def _leaderboard_section(rows: list[dict], basis: str, title: str,
                         sub: str) -> str:
    """Charts + ranked table for one environment's documents."""
    quality_key = "judge_mean" if any(r["judge_mean"] for r in rows) else "det_mean"
    quality_title = ("Mean judge score" if quality_key == "judge_mean"
                     else "Mean deterministic score")
    quality_rows = sorted((r for r in rows if r[quality_key] is not None),
                          key=lambda r: -r[quality_key])
    speed_rows = sorted((r for r in rows if r["tokens_per_s"]),
                        key=lambda r: -r["tokens_per_s"])

    charts = f"""
<div class='cards'>
 <div class='card'><h2>{quality_title}</h2>
  <p class='sub'>0–10 scale, zero baseline</p>
  {_bar_chart(quality_rows, quality_key, 10.0, lambda v: f"{v:.2f}")}</div>
 <div class='card'><h2>Generation speed</h2>
  <p class='sub'>completion tokens per second</p>
  {_bar_chart(speed_rows, "tokens_per_s",
              max((r["tokens_per_s"] for r in speed_rows), default=0),
              lambda v: f"{v:.1f}")}</div>
</div>"""

    return f"""
<section style='margin-top:1.5rem'>
<h2>{title}</h2>
<p class='note'>{sub} Ranked by <strong>{basis}</strong>.</p>
{charts}
<div class='card lb' style='margin-top:1rem'>
<h2>Leaderboard</h2>
{_leaderboard_table(rows)}
</div>
</section>"""


def _dashboard_html(db: Database) -> str:
    slots = _author_slots(db)
    pooled_rows, pooled_basis = _leaderboard_rows(db, slots=slots)
    if not pooled_rows:
        return "<p>No authors in the database yet; run <code>eval generate</code>.</p>"

    n_docs = sum(r["docs"] for r in pooled_rows)
    total_time = sum(r["total_time_s"] for r in pooled_rows)
    total_tokens = sum(r["total_tokens"] for r in pooled_rows)
    n_judgments = db.query(
        "SELECT COUNT(*) AS n FROM judgments WHERE failed=0")[0]["n"]
    n_comps = db.query("SELECT COUNT(*) AS n FROM comparisons")[0]["n"]

    tiles = "".join(
        f"<div class='tile'><div class='v'>{v}</div><div class='k'>{k}</div></div>"
        for v, k in [
            (n_docs, "documents generated"),
            (_fmt_duration(total_time), "total authoring time"),
            (_fmt_tokens(total_tokens), "total tokens (prompt + completion)"),
            (n_judgments, "judgments"),
            (n_comps, "pairwise comparisons"),
        ])

    # One leaderboard per environment/platform: authors are only fairly
    # comparable on the same machine + backend (deployments differ across
    # backends — mlx/qat vs gguf quants). A single environment renders as one
    # section; more get per-environment sections plus a clearly-flagged
    # pooled table.
    groups = environment_groups(db)
    if len(groups) <= 1:
        label = groups[0][1] if groups else "environment unrecorded"
        sections = _leaderboard_section(
            pooled_rows, pooled_basis, f"Results: {label}",
            "All documents were authored in this environment.")
    else:
        sections = ""
        for env_id, label in groups:
            rows, basis = _leaderboard_rows(db, environment_id=env_id,
                                            slots=slots)
            if not rows:
                continue
            sections += _leaderboard_section(
                rows, basis, f"Results: {label}",
                "Stats, ratings, and charts use only documents authored in "
                "this environment, so authors compare on identical "
                "hardware and backend.")
        sections += f"""
<section style='margin-top:1.5rem'>
<h2>All environments (pooled)</h2>
<p class='note'>⚠ Pools every environment; speed columns mix hardware and
cross-backend rows compare different deployments (quantizations) of a model.
Use the per-environment sections above to compare authors fairly.
Ranked by <strong>{pooled_basis}</strong>.</p>
<div class='card lb' style='margin-top:1rem'>
{_leaderboard_table(pooled_rows)}
</div>
</section>"""

    n_slots = len(slots)
    slot_defs_light = "\n  ".join(
        f"--s{i + 1}:{SERIES_LIGHT[i % 8]};" for i in range(max(n_slots, 1)))
    slot_defs_dark = "\n    ".join(
        f"--s{i + 1}:{SERIES_DARK[i % 8]};" for i in range(max(n_slots, 1)))
    css = (DASH_CSS.replace("%LIGHT_SLOTS%", slot_defs_light)
                   .replace("%DARK_SLOTS%", slot_defs_dark))

    return f"""<style>{css}</style>
<div class='viz-root'>
<p class='note'>Rating column is the Bradley–Terry strength on an Elo-like
scale (1500 = field average); it appears once pairwise comparisons exist.</p>
<div class='tiles'>{tiles}</div>
{sections}
<div class='card' style='margin-top:1rem'>
<h2>Generation environments</h2>
<p class='sub'>machine, OS, and GPU each document was authored on</p>
{_environments_html(db)}
</div>
</div>
{DASH_JS}"""


def _heat_color(v: float, lo: float, hi: float) -> str:
    if hi <= lo:
        return "#ffffff"
    t = (v - lo) / (hi - lo)
    # white -> green
    g = int(255 - 60 * t)
    return f"rgb({int(255 - 130 * t)}, {g}, {int(255 - 130 * t)})"


def _bt_table(comps: list[tuple[str, str, str]]) -> str:
    ratings = bt_ratings(comps)
    rows = "".join(
        f"<tr><td>{i}</td><td>{r.author_id}</td>"
        f"<td class='num'>{r.rating:.3f}</td>"
        f"<td class='num'>[{r.ci_low:.3f}, {r.ci_high:.3f}]</td>"
        f"<td class='num'>{r.n_comparisons}</td></tr>"
        for i, r in enumerate(ratings, 1))
    note = ""
    if len(ratings) >= 2 and ratings[0].ci_low <= ratings[1].ci_high:
        note = "<p class='note'>⚠ Top-2 CIs overlap; no distinguishable winner.</p>"
    return ("<table><tr><th>#</th><th>Author</th><th>BT rating</th>"
            f"<th>95% CI</th><th>n</th></tr>{rows}</table>{note}")


def _leaderboard_html(db: Database) -> str:
    comps = effective_comparisons(db)
    if not comps:
        return "<p>No pairwise comparisons yet.</p>"

    per_env = []
    for env_id, label in environment_groups(db):
        env_comps = effective_comparisons(db, environment_id=env_id)
        if env_comps:
            per_env.append((label, env_comps))

    if len(per_env) <= 1:
        heading = (f"<h3>{per_env[0][0]}</h3>" if per_env else "")
        return heading + _bt_table(comps)

    parts = []
    for label, env_comps in per_env:
        parts.append(f"<h3>{label}</h3>{_bt_table(env_comps)}")
    parts.append("<h3>All environments (pooled)</h3>"
                 "<p class='note'>⚠ Mixes environments/backends; deployments "
                 "(quantization, hardware) differ; prefer the per-environment "
                 f"tables when comparing authors.</p>{_bt_table(comps)}")
    return "".join(parts)


def _matrix_html(db: Database) -> str:
    mj = author_judge_matrix(db)
    matrix = mj["matrix"]
    if not matrix:
        return "<p>No judgments yet.</p>"
    judges = sorted({j for js in matrix.values() for j in js})
    vals = [v for js in matrix.values() for v in js.values()]
    lo, hi = min(vals), max(vals)
    head = "".join(f"<th>{j}</th>" for j in judges)
    body = ""
    for author, js in sorted(matrix.items()):
        cells = ""
        for j in judges:
            v = js.get(j)
            if v is None:
                cells += "<td class='heat'>—</td>"
            else:
                cells += (f"<td class='heat' style='background:"
                          f"{_heat_color(v, lo, hi)};color:#0b0b0b'>{v:.2f}</td>")
        body += f"<tr><td>{author}</td>{cells}</tr>"
    deltas = mj["self_judging_delta"]
    delta_html = ""
    if deltas:
        drows = "".join(f"<tr><td>{a}</td><td class='num'>{d:+.3f}</td></tr>"
                        for a, d in sorted(deltas.items()))
        delta_html = ("<h3>Self-judging delta</h3><table><tr><th>Author</th>"
                      f"<th>self − others</th></tr>{drows}</table>")
    return (f"<table><tr><th>Author \\ Judge</th>{head}</tr>{body}</table>"
            + delta_html)


def _criteria_table(aggs) -> str:
    rows = "".join(
        f"<tr><td>{a.author_id}</td><td>{a.skill}</td><td class='num'>{a.n}</td>"
        f"<td class='num'>{a.mean:.2f}</td><td class='num'>{a.median:.1f}</td>"
        f"<td class='num'>{a.stddev:.2f}</td>"
        f"<td class='num'>[{a.ci_low:.2f}, {a.ci_high:.2f}]</td></tr>"
        for a in aggs)
    return ("<table><tr><th>Author</th><th>Skill</th><th>n</th><th>Mean</th>"
            f"<th>Median</th><th>SD</th><th>95% CI</th></tr>{rows}</table>")


def _criteria_html(db: Database) -> str:
    if not score_aggregates(db):
        return "<p>No judgments yet.</p>"
    groups = environment_groups(db)
    if len(groups) <= 1:
        return _criteria_table(score_aggregates(db))
    parts = []
    for env_id, label in groups:
        aggs = score_aggregates(db, environment_id=env_id)
        if aggs:
            parts.append(f"<h3>{label}</h3>{_criteria_table(aggs)}")
    return "".join(parts)


def _violations_html(db: Database) -> str:
    rows = db.query(
        """SELECT d.prompt_id, d.author_id, r.tool, r.passed, r.score,
                  r.violations_json
           FROM det_results r JOIN documents d ON d.id = r.document_id
           ORDER BY d.prompt_id, d.author_id, r.tool""")
    if not rows:
        return "<p>No deterministic results yet.</p>"
    out = ""
    for r in rows:
        violations = json.loads(r["violations_json"])
        status = "✅" if r["passed"] else "❌"
        score = f"{r['score']:.2f}" if r["score"] is not None else "—"
        items = "".join(
            f"<li><code>{v.get('rule', '')}</code> "
            f"{('line ' + str(v['line']) + ': ') if v.get('line') else ''}"
            f"{v.get('message', '')}</li>" for v in violations[:50])
        body = f"<ul>{items}</ul>" if items else "<p class='note'>clean</p>"
        out += (f"<details><summary>{status} {r['prompt_id']} / "
                f"{r['author_id']} / {r['tool']} (score {score}, "
                f"{len(violations)} finding(s))</summary>{body}</details>")
    return out


def _agreement_html(db: Database) -> str:
    agree = judge_agreement(db)
    parts = []
    if agree["alpha"]:
        rows = "".join(f"<tr><td>{s}</td><td class='num'>{a}</td></tr>"
                       for s, a in sorted(agree["alpha"].items()))
        parts.append("<h3>Krippendorff's α per skill</h3><table>"
                     f"<tr><th>Skill</th><th>α</th></tr>{rows}</table>")
    if agree["judge_deviation"]:
        rows = "".join(
            f"<tr><td>{s}</td><td>{j}</td><td class='num'>{d}</td></tr>"
            for s, js in sorted(agree["judge_deviation"].items())
            for j, d in sorted(js.items()))
        parts.append("<h3>Deviation from panel median</h3><table>"
                     f"<tr><th>Skill</th><th>Judge</th><th>|dev|</th></tr>{rows}</table>")
    if agree["swap_consistency"]:
        rows = "".join(f"<tr><td>{j}</td><td class='num'>{c}</td></tr>"
                       for j, c in sorted(agree["swap_consistency"].items()))
        parts.append("<h3>Position-swap consistency</h3><table>"
                     f"<tr><th>Judge</th><th>Agreement rate</th></tr>{rows}</table>")
    return "".join(parts) or "<p>No agreement data yet.</p>"


def write_reports(settings: Settings | None = None) -> list[str]:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    settings.reports_path.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pages = {
        "dashboard.html": ("Eval dashboard", _dashboard_html(db)),
        "leaderboard.html": ("Leaderboard (Bradley–Terry)", _leaderboard_html(db)),
        "matrix.html": ("Author × Judge matrix", _matrix_html(db)),
        "criteria.html": ("Per-criterion breakdown", _criteria_html(db)),
        "violations.html": ("Violation drill-down", _violations_html(db)),
        "agreement.html": ("Judge agreement", _agreement_html(db)),
    }
    written = []
    for fname, (title, body) in pages.items():
        nav = " | ".join(f"<a href='{f}'>{t}</a>" for f, (t, _) in pages.items())
        html = PAGE.render(title=title, body=f"<nav>{nav}</nav>{body}",
                           generated_at=now)
        out = settings.reports_path / fname
        out.write_text(html)
        written.append(str(out))
    db.close()
    return written
