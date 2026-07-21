"""Skill loading + schema enforcement (WU-3.1)."""

import jsonschema
import pytest

from eval_pipeline.skills import list_skills, load_skill

EXPECTED = {"audience-fit", "code-quality", "completeness", "factuality",
            "length-check", "pairwise-compare", "style-guide"}


def test_all_skills_present():
    assert set(list_skills()) == EXPECTED


def test_all_skills_load_and_render():
    for name in EXPECTED:
        skill = load_skill(name)
        assert skill.version
        rendered = skill.render(document="DOC-SENTINEL",
                                document_a="A-SENTINEL", document_b="B-SENTINEL",
                                reference_facts=[{"claim": "c", "truth": "t"}])
        if name == "pairwise-compare":
            assert "A-SENTINEL" in rendered and "B-SENTINEL" in rendered
        else:
            assert "DOC-SENTINEL" in rendered


def test_schema_validation_accepts_and_rejects():
    skill = load_skill("style-guide")
    good = {"score": 8.0, "confidence": 0.9, "summary": "ok",
            "violations": [{"severity": "minor", "rule": "tone",
                            "location": "intro", "message": "m"}]}
    skill.validate_output(good)
    with pytest.raises(jsonschema.ValidationError):
        skill.validate_output({"score": 11, "confidence": 0.9,
                               "violations": [], "summary": "x"})
    with pytest.raises(jsonschema.ValidationError):
        skill.validate_output({"score": 5})


def test_factuality_template_lists_facts():
    skill = load_skill("factuality")
    rendered = skill.render(document="d", reference_facts=[
        {"claim": "a", "truth": "TRUTH-ONE"}, {"claim": "b", "truth": "TRUTH-TWO"}])
    assert "TRUTH-ONE" in rendered and "2. TRUTH-TWO" in rendered
