"""Dataset schema checks (WU-1.1 done-criteria)."""

from collections import Counter

from eval_pipeline.prompts import load_prompts, load_reference


def test_ten_valid_prompts():
    tasks = load_prompts()
    assert len(tasks) == 10
    assert len({t.id for t in tasks}) == 10


def test_doc_type_coverage():
    counts = Counter(t.doc_type for t in load_prompts())
    for doc_type in ("tutorial", "how-to", "reference", "conceptual"):
        assert counts[doc_type] >= 2, f"need >=2 {doc_type} prompts"


def test_code_prompt_coverage():
    code_tasks = [t for t in load_prompts() if t.requires_code]
    assert len(code_tasks) >= 3
    for t in code_tasks:
        assert t.code_languages, f"{t.id} requires code but lists no languages"
        assert set(t.code_languages) <= {"python", "bash", "sh"}


def test_every_prompt_has_reference_facts():
    for t in load_prompts():
        ref = load_reference(t.id)
        assert ref is not None, f"missing datasets/reference/{t.id}.yaml"
        assert ref.prompt_id == t.id
        assert len(ref.facts) >= 3


def test_prompt_render_and_hash_stable():
    t = load_prompts()[0]
    assert t.title in t.render()
    assert t.prompt_hash() == t.prompt_hash()
