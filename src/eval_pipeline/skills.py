"""Skill loading: skills/<name>/{SKILL.md, rubric.yaml, schema.json}.

A skill is one evaluation function: SKILL.md is the judge prompt (Jinja2
template), rubric.yaml carries criteria/weights/version, schema.json is the
required JSON output shape enforced with jsonschema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml
from jinja2 import Template

from .config import PROJECT_ROOT

SKILLS_DIR = PROJECT_ROOT / "skills"

# The templates wrap untrusted document text in <document>/<document_a>/
# <document_b> tags. A document containing one of those *closing* tags — which
# is entirely plausible in this corpus, e.g. a doc about prompt formatting —
# would end the wrapper early and have its remaining text read as judge
# instructions. Breaking the slash keeps the text readable to the judge while
# making it impossible to match the real delimiter.
_CLOSING_DELIMITER = re.compile(r"</\s*(document(?:_[ab])?)\s*>", re.IGNORECASE)


def sanitize_document(text: str) -> str:
    """Neutralize closing document-delimiter tags in untrusted document text."""
    return _CLOSING_DELIMITER.sub(r"<\\/\1>", text)


def _is_document_key(key: str) -> bool:
    return key == "document" or key.startswith("document_")


@dataclass
class Skill:
    name: str
    version: str
    instructions: str  # SKILL.md contents (jinja2 template)
    rubric: dict
    schema: dict

    def render(self, **context) -> str:
        safe = {k: sanitize_document(v)
                if _is_document_key(k) and isinstance(v, str) else v
                for k, v in context.items()}
        return Template(self.instructions).render(rubric=self.rubric, **safe)

    def validate_output(self, data: dict) -> None:
        """Raises jsonschema.ValidationError on mismatch."""
        jsonschema.validate(data, self.schema)

    def overall_score(self, data: dict) -> float | None:
        """The score to record for a judgment.

        When a skill elicits per-criterion scores, the overall score is the
        weighted mean computed here from rubric.yaml weights — small local
        judges do this arithmetic unreliably, so their own `score` field is
        ignored in that case. Skills without `criterion_scores` keep the
        model's holistic score as-is.
        """
        per = data.get("criterion_scores")
        if not isinstance(per, dict) or not per:
            return data.get("score")
        total = weight_sum = 0.0
        for criterion in self.rubric.get("criteria", []):
            name = criterion.get("name")
            if name in per:
                weight = float(criterion.get("weight", 0.0))
                total += weight * float(per[name])
                weight_sum += weight
        if weight_sum == 0:
            return data.get("score")
        return round(total / weight_sum, 3)


def load_skill(name: str, skills_dir: Path | None = None) -> Skill:
    root = (skills_dir or SKILLS_DIR) / name
    if not root.is_dir():
        raise FileNotFoundError(f"no such skill: {root}")
    rubric = yaml.safe_load((root / "rubric.yaml").read_text())
    schema = json.loads((root / "schema.json").read_text())
    version = str(rubric.get("version", "0"))
    return Skill(name=name, version=version,
                 instructions=(root / "SKILL.md").read_text(),
                 rubric=rubric, schema=schema)


def list_skills(skills_dir: Path | None = None) -> list[str]:
    root = skills_dir or SKILLS_DIR
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "SKILL.md").exists()
                  and (p / "schema.json").exists())
