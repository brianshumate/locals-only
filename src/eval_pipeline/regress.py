"""Drift detection: run every skill against frozen fixture docs and fail if
any judge leaves its expected score band.

fixtures/regression.yaml:
    fixtures:
      - doc: fixtures/golden.md
        skill: style-guide
        min_score: 8
        max_score: 10
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .backends import model_session
from .config import PROJECT_ROOT, Settings, load_judges, load_settings
from .judge import judge_document, _extra_context
from .skills import load_skill

log = logging.getLogger(__name__)

REGRESSION_FILE = PROJECT_ROOT / "fixtures" / "regression.yaml"


def run_regression(judge_ids: list[str] | None = None,
                   settings: Settings | None = None,
                   regression_file: Path | None = None) -> tuple[bool, str]:
    """Returns (ok, report)."""
    settings = settings or load_settings()
    spec = yaml.safe_load((regression_file or REGRESSION_FILE).read_text())
    fixtures = spec["fixtures"]
    judges = load_judges()
    if judge_ids:
        judges = [j for j in judges if j.id in judge_ids]

    backend_name = settings.resolve_backend()
    failures = []
    lines = []
    for judge in judges:
        wanted = [f for f in fixtures if f["skill"] in judge.skills]
        if not wanted:
            continue
        resolved = judge.resolve(backend_name)
        if resolved is None:
            log.warning("%s: no model configured for backend %r — skipping",
                        judge.id, backend_name)
            continue
        with model_session(resolved.model, settings=settings,
                           temperature=judge.temperature,
                           max_tokens=judge.max_tokens) as client:
            for fx in wanted:
                skill = load_skill(fx["skill"])
                doc_path = PROJECT_ROOT / fx["doc"]
                extra = {}
                if "reference_facts" in fx:
                    extra["reference_facts"] = fx["reference_facts"]
                data, _, error = judge_document(client, skill,
                                                doc_path.read_text(), **extra)
                if data is None:
                    failures.append(f"{judge.id}/{fx['skill']}/{fx['doc']}: "
                                    f"invalid output ({error[:100]})")
                    continue
                score = data.get("score")
                lo, hi = fx["min_score"], fx["max_score"]
                status = "OK" if (score is not None and lo <= score <= hi) else "DRIFT"
                lines.append(f"{status:<6}{judge.id:<22}{fx['skill']:<18}"
                             f"{fx['doc']:<28}score={score} band=[{lo},{hi}]")
                if status == "DRIFT":
                    failures.append(
                        f"{judge.id}/{fx['skill']}/{fx['doc']}: "
                        f"score {score} outside [{lo}, {hi}]")
    report = "\n".join(lines)
    if failures:
        report += "\n\nFAILURES:\n" + "\n".join(f"  - {f}" for f in failures)
    return (not failures, report)
