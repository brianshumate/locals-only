"""Deterministic-validator tests (WU-2.x done-criteria)."""

import shutil

import pytest

from eval_pipeline.config import CodeRunnerSettings, Settings
from eval_pipeline.validate import (extract_code_blocks, run_code_blocks,
                                    run_codespell, run_markdownlint,
                                    run_readability, run_vale)
from conftest import FIXTURES

GOLDEN = FIXTURES / "golden.md"
BAD = FIXTURES / "bad.md"
CODE = FIXTURES / "code-blocks.md"

needs = lambda tool: pytest.mark.skipif(shutil.which(tool) is None,
                                        reason=f"{tool} not installed")


def test_extract_code_blocks():
    blocks = extract_code_blocks(CODE.read_text())
    assert [lang for lang, _ in blocks] == ["python", "python", "python", "rust"]


def test_code_runner_pass_fail_timeout():
    settings = Settings(code_runner=CodeRunnerSettings(timeout_seconds=3))
    res = run_code_blocks(CODE, settings)
    assert not res.passed
    rules = sorted(v["rule"] for v in res.violations)
    assert rules == ["code-fail", "code-timeout"]  # rust block skipped
    assert res.score == pytest.approx(10 * 1 / 3, abs=0.01)


def test_code_runner_no_blocks(tmp_path):
    doc = tmp_path / "prose.md"
    doc.write_text("# Title\n\nNo code here.\n")
    res = run_code_blocks(doc, Settings())
    assert res.passed and res.score is None


@needs("codespell")
def test_codespell_fixtures():
    assert run_codespell(GOLDEN).passed
    bad = run_codespell(BAD)
    assert not bad.passed
    assert any("enviroment" in v["message"] for v in bad.violations)


@needs("markdownlint")
def test_markdownlint_runs():
    res = run_markdownlint(BAD)
    assert res.tool == "markdownlint"
    assert isinstance(res.violations, list)


@needs("vale")
def test_vale_golden_vs_bad():
    golden = run_vale(GOLDEN)
    bad = run_vale(BAD)
    # Golden may pick up suggestions but no errors; bad must flag seeded
    # terminology violations at error level.
    assert golden.passed
    assert not bad.passed
    assert len(bad.violations) > len(golden.violations)
    assert any("LocalDocs" in v["rule"] for v in bad.violations)


def test_readability_metrics():
    res = run_readability(GOLDEN)
    assert res.passed
    import json
    metrics = json.loads(res.violations[0]["message"])
    assert metrics["word_count"] > 100
    assert metrics["heading_count"] >= 6
    assert 0 < metrics["avg_sentence_length"] < 40
