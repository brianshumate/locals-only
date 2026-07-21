"""`eval` CLI — one subcommand per pipeline stage."""

from __future__ import annotations

import os
import sys

import click


def _split(value: str | None) -> list[str] | None:
    if not value or value == "all":
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


@click.group()
@click.option("-v", "--verbose", is_flag=True)
@click.option("--backend", type=click.Choice(["auto", "lmstudio", "llamacpp"]),
              default=None,
              help="inference backend (overrides config/settings.yaml; "
                   "auto: macOS→lmstudio, Linux→llamacpp)")
@click.option("--log-file", type=click.Path(dir_okay=False), default=None,
              help="also write logs here (default: logs/eval.log)")
def main(verbose: bool, backend: str | None, log_file: str | None) -> None:
    if backend:
        os.environ["EVAL_BACKEND"] = backend
    from .logging_setup import configure_logging
    configure_logging(verbose=verbose, log_file=log_file)


@main.command()
@click.option("--authors", default="all", help="comma-separated author ids")
@click.option("--prompts", default="all", help="comma-separated prompt ids")
@click.option("--force", is_flag=True, help="regenerate even if up to date")
def generate(authors: str, prompts: str, force: bool) -> None:
    """Generate documents: authors x prompts."""
    from .generate import UnknownIdError, generate_all
    try:
        n = generate_all(_split(authors), _split(prompts), force)
    except UnknownIdError as e:
        raise click.UsageError(str(e)) from e
    click.echo(f"Generated {n} document(s).")


@main.command()
@click.option("--tools", default="all",
              help="comma-separated: markdownlint,codespell,lychee,vale,code-runner,readability")
def validate(tools: str) -> None:
    """Run deterministic validators over all documents."""
    from .validate import validate_all
    n = validate_all(_split(tools))
    click.echo(f"Recorded {n} deterministic result(s).")


@main.command()
@click.option("--judges", default="all", help="comma-separated judge ids")
@click.option("--skills", default="all", help="comma-separated skill names")
@click.option("--force", is_flag=True)
def judge(judges: str, skills: str, force: bool) -> None:
    """Run LLM judge skills over all documents."""
    from .judge import judge_all
    n = judge_all(_split(judges), _split(skills), force)
    click.echo(f"Recorded {n} judgment(s).")


@main.command()
@click.option("--judges", default="all", help="comma-separated judge ids")
@click.option("--force", is_flag=True)
def compare(judges: str, force: bool) -> None:
    """Run pairwise A/B comparisons (both orderings)."""
    from .pairwise import compare_all
    n = compare_all(_split(judges), force)
    click.echo(f"Recorded {n} comparison(s).")


@main.command("backfill-env")
@click.option("--assume-current", is_flag=True,
              help="attribute documents whose manifest has no environment to "
                   "THIS machine — run it on the machine that authored them")
def backfill_env(assume_current: bool) -> None:
    """Attribute documents that predate environment capture.

    Uses the environment recorded in each document's manifest when present;
    with --assume-current, manifest-less-environment documents get the
    current machine's environment (marked as assumed in the manifest).
    """
    from .generate import backfill_environments
    c = backfill_environments(assume_current)
    click.echo(f"{c['from_manifest']} attributed from manifest(s), "
               f"{c['assumed']} assumed to be this machine, "
               f"{c['skipped']} left unattributed.")
    if c["skipped"] and not assume_current:
        click.echo("Re-run with --assume-current on the authoring machine "
                   "to attribute the rest.")


@main.command()
@click.option("--role", type=click.Choice(["author", "judge", "both"]),
              default="author",
              help="propose discovered models as authors (default), judges, "
                   "or both")
@click.option("--apply", "apply_", is_flag=True,
              help="write the proposals into config/ (default: preview only)")
def discover(role: str, apply_: bool) -> None:
    """List models the active backend can serve that config/ doesn't know.

    Reads LM Studio's downloaded-model list (`lms ls`) or the llama.cpp model
    directory, derives a config id per model, and proposes the missing
    entries. A model that matches an existing entry which cannot yet run on
    this backend gets a `backends:` mapping added instead of a duplicate row.
    """
    from .config import load_settings
    from .discover import apply_proposals, plan, render_entry

    settings = load_settings()
    backend = settings.resolve_backend()
    roles = ("author", "judge") if role == "both" else (role,)
    proposals = plan(backend, roles, settings)
    if not proposals:
        click.echo(f"{backend}: no new models — config is up to date.")
        return

    click.echo(f"{backend}: {len(proposals)} proposal(s)\n")
    for p in proposals:
        click.echo(f"  {p.summary}")
    if not apply_:
        click.echo("\nEntries that would be added:\n")
        for p in proposals:
            if p.kind != "add-backend":
                click.echo(render_entry(p, settings.discovery))
        click.echo("Re-run with --apply to write these to config/.")
        return

    counts = apply_proposals(proposals, settings)
    click.echo(f"\nWrote {counts['author']} author entr(ies) and "
               f"{counts['judge']} judge entr(ies).")
    click.echo("Review the diff — ids and quantization labels are derived "
               "from model names and may want editing.")


@main.command()
def status() -> None:
    """Show progress of the current/last run (read-only; safe mid-run)."""
    from .status import status_report
    click.echo(status_report())


@main.command()
def analyze() -> None:
    """Print score aggregates, author x judge matrix, judge agreement."""
    from .analyze import print_summary
    click.echo(print_summary())


@main.command()
def rank() -> None:
    """Print the Bradley-Terry leaderboard."""
    from .rank import leaderboard
    click.echo(leaderboard())


@main.command()
def report() -> None:
    """Regenerate HTML reports from the DB."""
    from .report import write_reports
    for path in write_reports():
        click.echo(f"wrote {path}")


@main.group()
def calibrate() -> None:
    """Human calibration workflow."""


@calibrate.command("sample")
@click.option("--pct", default=10.0, type=float)
@click.option("--seed", default=0, type=int)
def calibrate_sample(pct: float, seed: int) -> None:
    from .calibrate import sample
    forms = sample(pct=pct, seed=seed)
    for f in forms:
        click.echo(f"wrote {f}")
    click.echo(f"{len(forms)} form(s) — fill in scores, then run "
               "`eval calibrate import`.")


@calibrate.command("import")
@click.option("--reviewer", default="human")
def calibrate_import(reviewer: str) -> None:
    from .calibrate import import_forms
    n = import_forms(reviewer=reviewer)
    click.echo(f"Imported {n} human score(s).")


@calibrate.command("correlate")
def calibrate_correlate() -> None:
    from .calibrate import correlation_report
    click.echo(correlation_report())


@main.command()
@click.option("--judges", default="all", help="comma-separated judge ids")
def regress(judges: str) -> None:
    """Run drift-detection fixtures; exit 1 on any band violation."""
    from .regress import run_regression
    ok, rep = run_regression(_split(judges))
    click.echo(rep)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
