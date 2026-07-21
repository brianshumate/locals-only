"""Prompt dataset: datasets/prompts/*.yaml + datasets/reference/*.yaml."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "datasets" / "prompts"
REFERENCE_DIR = PROJECT_ROOT / "datasets" / "reference"

DocType = Literal["tutorial", "how-to", "reference", "conceptual"]


class PromptTask(BaseModel):
    id: str
    title: str
    doc_type: DocType
    audience: str
    source_material: str
    required_sections: list[str] = Field(min_length=1)
    target_length_words: int = Field(gt=0)
    requires_code: bool = False
    code_languages: list[str] = []

    def render(self) -> str:
        """The exact prompt every author model receives."""
        sections = "\n".join(f"- {s}" for s in self.required_sections)
        code_req = ""
        if self.requires_code:
            langs = ", ".join(self.code_languages) or "any appropriate language"
            code_req = (
                f"\nInclude runnable, self-contained code examples ({langs}). "
                "Every code block must execute successfully as written, with no "
                "placeholders, no network access, and no undefined variables."
            )
        return f"""Write a {self.doc_type} document in Markdown.

Title: {self.title}
Audience: {self.audience}
Target length: about {self.target_length_words} words.

Source material / specification:
{self.source_material}

The document must contain these sections:
{sections}
{code_req}
Output only the Markdown document, no preamble or commentary."""

    def prompt_hash(self) -> str:
        return hashlib.sha256(self.render().encode()).hexdigest()[:16]


class ReferenceFact(BaseModel):
    claim: str
    truth: str  # the correct statement of the fact


class ReferenceFacts(BaseModel):
    prompt_id: str
    facts: list[ReferenceFact] = Field(min_length=1)


def load_prompts(prompts_dir: Path | None = None) -> list[PromptTask]:
    prompts_dir = prompts_dir or PROMPTS_DIR
    tasks = []
    for path in sorted(prompts_dir.glob("*.yaml")):
        tasks.append(PromptTask.model_validate(yaml.safe_load(path.read_text())))
    return tasks


def load_reference(prompt_id: str, reference_dir: Path | None = None) -> ReferenceFacts | None:
    reference_dir = reference_dir or REFERENCE_DIR
    path = reference_dir / f"{prompt_id}.yaml"
    if not path.exists():
        return None
    return ReferenceFacts.model_validate(yaml.safe_load(path.read_text()))
