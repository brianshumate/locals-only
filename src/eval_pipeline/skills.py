"""Skill loading: skills/<name>/{SKILL.md, rubric.yaml, schema.json}.

A skill is one evaluation function: SKILL.md is the judge prompt (Jinja2
template), rubric.yaml carries criteria/weights/version, schema.json is the
required JSON output shape enforced with jsonschema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml
from jinja2 import Template

from .config import PROJECT_ROOT

SKILLS_DIR = PROJECT_ROOT / "skills"


@dataclass
class Skill:
    name: str
    version: str
    instructions: str  # SKILL.md contents (jinja2 template)
    rubric: dict
    schema: dict

    def render(self, **context) -> str:
        return Template(self.instructions).render(rubric=self.rubric, **context)

    def validate_output(self, data: dict) -> None:
        """Raises jsonschema.ValidationError on mismatch."""
        jsonschema.validate(data, self.schema)


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
