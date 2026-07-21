"""Point-in-time progress introspection: `eval status`.

Answers "is the run still doing work, and how much is left?" without touching
the running process. It opens the database **read-only** and never migrates or
writes, so it is safe to run while a stage is mid-flight — and it degrades
gracefully on a pre-v5 schema that lacks `created_at` (rate/ETA are simply
omitted).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import load_judges, load_settings
from .skills import load_skill

# Rows within this trailing window define "current" throughput.
_RATE_WINDOW_S = 15 * 60


def _ro_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r["name"] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def _fmt_dur(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _age(ts_utc: str) -> float | None:
    """Seconds since a `datetime('now')` UTC timestamp string, or None."""
    try:
        dt = datetime.strptime(ts_utc, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _judge_skills(judge, backend: str) -> list:
    """Skills a judge actually runs on this backend (pairwise excluded, and
    only skills that load cleanly)."""
    if judge.resolve(backend) is None:
        return []
    skills = []
    for name in judge.skills:
        if name == "pairwise-compare":
            continue
        try:
            skills.append(load_skill(name))
        except Exception:  # a broken skill must not crash read-only status
            continue
    return skills


def _server_reachable(base_url: str) -> bool:
    try:
        with httpx.Client(timeout=3) as client:
            return client.get(f"{base_url}/models").status_code == 200
    except httpx.HTTPError:
        return False


def status_report(settings=None) -> str:
    settings = settings or load_settings()
    backend = settings.resolve_backend()
    db_path = settings.db_path
    if not db_path.exists():
        return f"No database at {db_path} — nothing has run yet."

    conn = _ro_conn(db_path)
    has_ts = _has_column(conn, "judgments", "created_at")
    ndocs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]

    total, failed = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(failed), 0) f FROM judgments"
    ).fetchone()
    # The judge owning the highest-id row is whatever ran most recently.
    cur_row = conn.execute(
        "SELECT judge_id FROM judgments ORDER BY id DESC LIMIT 1").fetchone()
    current_judge = cur_row["judge_id"] if cur_row else None

    lines: list[str] = []
    schema = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    lines.append(f"Eval status — {db_path.name} "
                 f"(schema v{schema['value'] if schema else '?'}, "
                 f"backend={backend})")

    base_url = getattr(settings, backend).base_url
    reachable = _server_reachable(base_url)
    lines.append(f"Model server {base_url}: "
                 f"{'reachable' if reachable else 'UNREACHABLE'}")

    # Last activity: prefer a row timestamp, fall back to the DB file mtime.
    if has_ts:
        last = conn.execute(
            "SELECT MAX(created_at) m FROM judgments").fetchone()["m"]
        age = _age(last) if last else None
        if last and age is not None:
            lines.append(f"Last judgment: {last} UTC ({_fmt_dur(age)} ago)")
    else:
        mtime = db_path.stat().st_mtime
        lines.append(f"Last DB write: {_fmt_dur(time.time() - mtime)} ago "
                     "(no per-row timestamps until schema v5)")

    lines.append("")
    lines.append(f"Documents: {ndocs}   Judgments recorded: {total} "
                 f"({failed} failed)")
    lines.append("")

    # Per-judge progress against the work this backend implies.
    pending_total = 0
    lines.append("Per judge (done / expected):")
    for judge in load_judges():
        skills = _judge_skills(judge, backend)
        if not skills:
            lines.append(f"  {judge.id:22} —   skipped (no {backend} model "
                         "or no runnable skills)")
            continue
        expected = len(skills) * ndocs
        done = conn.execute(
            "SELECT COUNT(*) c FROM judgments WHERE judge_id=? AND failed=0",
            (judge.id,)).fetchone()["c"]
        jfailed = conn.execute(
            "SELECT COUNT(*) c FROM judgments WHERE judge_id=? AND failed=1",
            (judge.id,)).fetchone()["c"]
        pending = max(expected - done, 0)
        pending_total += pending
        marker = "  <- current" if judge.id == current_judge and pending else ""
        extra = f"  ({jfailed} failed)" if jfailed else ""
        lines.append(f"  {judge.id:22} {done:3}/{expected:<3} "
                     f"pending {pending}{extra}{marker}")

    # Throughput and ETA need timestamps.
    if has_ts:
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        recent = conn.execute(
            "SELECT created_at, latency_s FROM judgments "
            "WHERE created_at IS NOT NULL ORDER BY id DESC LIMIT 200"
        ).fetchall()
        windowed = [r for r in recent
                    if (a := _age(r["created_at"])) is not None
                    and a <= _RATE_WINDOW_S]
        lines.append("")
        # The judge loop is sequential, so mean per-judgment latency is the
        # honest basis for an ETA (robust when rows share a coarse timestamp).
        lats = [r["latency_s"] for r in windowed if r["latency_s"] is not None]
        if lats:
            per = sum(lats) / len(lats)
            lines.append(
                f"Throughput (last {_fmt_dur(_RATE_WINDOW_S)}): "
                f"{len(windowed)} judgments, ~{_fmt_dur(per)}/judgment")
            if pending_total and per:
                lines.append(
                    f"Estimated remaining: {pending_total} judgments "
                    f"-> ~{_fmt_dur(pending_total * per)}")
        else:
            lines.append("Throughput: no judgments in the last "
                         f"{_fmt_dur(_RATE_WINDOW_S)} "
                         "(idle, between judges, or not running)")

        lines.append("")
        lines.append("Recent judgments:")
        for r in conn.execute(
            "SELECT created_at, judge_id, skill, document_id, failed, latency_s "
            "FROM judgments ORDER BY id DESC LIMIT 8"
        ).fetchall():
            ok = "FAIL" if r["failed"] else "ok"
            lat = f"{r['latency_s']:.0f}s" if r["latency_s"] is not None else "-"
            lines.append(
                f"  {r['created_at'] or '?':19}  {r['judge_id']:20} "
                f"{r['skill']:14} doc#{r['document_id']:<4} {ok:4} {lat}")

    conn.close()
    return "\n".join(lines)
