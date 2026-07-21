"""Deterministic validators. Each adapter returns a normalized
DetResult(tool, passed, score, violations, tool_version); scores are 0-10
where 10 = no violations, decreasing with violation density.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROJECT_ROOT, Settings, load_settings
from .db import Database

log = logging.getLogger(__name__)

# Tool output is stored verbatim in det_results and rendered into the HTML
# reports. Tracebacks and linter messages quote absolute paths, which carry
# the account name of whoever ran the pipeline; collapse those to `~` on the
# way in so reports never republish it.
_HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s\"'<>:;,)\]]+")


def redact_paths(text: str) -> str:
    """Replace any user's home directory prefix with `~`."""
    return _HOME_PATH.sub("~", text)


@dataclass
class DetResult:
    tool: str
    passed: bool
    score: float | None
    violations: list[dict] = field(default_factory=list)
    tool_version: str = ""


def _tool_version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (subprocess.SubprocessError, FileNotFoundError, IndexError):
        return "unknown"


def _density_score(n_violations: int, n_words: int) -> float:
    """10 for zero violations, decaying with violations per 100 words."""
    if n_violations == 0:
        return 10.0
    per100 = n_violations / max(n_words, 1) * 100
    return round(max(0.0, 10.0 * math.exp(-per100 / 2)), 2)


def _word_count(text: str) -> int:
    return len(text.split())


# -- markdownlint -----------------------------------------------------------

def run_markdownlint(path: Path) -> DetResult:
    proc = subprocess.run(
        ["markdownlint", "--json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    violations = []
    if proc.stderr.strip():
        try:
            for item in json.loads(proc.stderr):
                violations.append({
                    "rule": "/".join(item.get("ruleNames", [])),
                    "line": item.get("lineNumber"),
                    "message": item.get("ruleDescription", ""),
                    "detail": item.get("errorDetail") or "",
                })
        except json.JSONDecodeError:
            # Non-JSON stderr means the tool itself failed.
            return DetResult("markdownlint", False, None,
                             [{"rule": "tool-error",
                               "message": redact_paths(proc.stderr[:500])}],
                             _tool_version(["markdownlint", "--version"]))
    words = _word_count(path.read_text())
    return DetResult("markdownlint", len(violations) == 0,
                     _density_score(len(violations), words), violations,
                     _tool_version(["markdownlint", "--version"]))


# -- codespell ---------------------------------------------------------------

def run_codespell(path: Path) -> DetResult:
    proc = subprocess.run(
        ["codespell", "--disable-colors", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    violations = []
    for line in proc.stdout.splitlines():
        m = re.match(r"^(.*?):(\d+):\s*(\S+)\s*==>\s*(.+)$", line)
        if m:
            violations.append({"rule": "spelling", "line": int(m.group(2)),
                               "message": f"{m.group(3)} ==> {m.group(4)}"})
    words = _word_count(path.read_text())
    return DetResult("codespell", len(violations) == 0,
                     _density_score(len(violations), words), violations,
                     _tool_version(["codespell", "--version"]))


# -- lychee (links) -----------------------------------------------------------

def run_lychee(path: Path, settings: Settings | None = None) -> DetResult:
    settings = settings or load_settings()
    cmd = ["lychee", "--format", "json", "--no-progress"]
    if settings.lychee.offline:
        cmd.append("--offline")
    for pattern in settings.lychee.allowlist:
        cmd += ["--exclude", pattern]
    cmd.append(str(path))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    violations = []
    try:
        data = json.loads(proc.stdout)
        for source, fails in (data.get("error_map") or {}).items():
            for f in fails:
                violations.append({"rule": "dead-link", "message": f.get("url", ""),
                                   "detail": str(f.get("status", ""))})
    except json.JSONDecodeError:
        if proc.returncode != 0:
            return DetResult("lychee", False, None,
                             [{"rule": "tool-error",
                               "message": redact_paths(
                                   (proc.stderr or proc.stdout)[:500])}],
                             _tool_version(["lychee", "--version"]))
    passed = len(violations) == 0
    return DetResult("lychee", passed, 10.0 if passed else
                     _density_score(len(violations), _word_count(path.read_text())),
                     violations, _tool_version(["lychee", "--version"]))


# -- vale ----------------------------------------------------------------------

def run_vale(path: Path, config: Path | None = None) -> DetResult:
    config = config or PROJECT_ROOT / "styles" / ".vale.ini"
    proc = subprocess.run(
        ["vale", "--config", str(config), "--output", "JSON", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    violations = []
    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
        for file_alerts in data.values():
            for a in file_alerts:
                violations.append({
                    "rule": a.get("Check", ""),
                    "severity": a.get("Severity", ""),
                    "line": a.get("Line"),
                    "message": a.get("Message", ""),
                })
    except json.JSONDecodeError:
        return DetResult("vale", False, None,
                         [{"rule": "tool-error",
                           "message": redact_paths(
                               (proc.stderr or proc.stdout)[:500])}],
                         _tool_version(["vale", "--version"]))
    errors = [v for v in violations if v.get("severity") == "error"]
    words = _word_count(path.read_text())
    return DetResult("vale", len(errors) == 0,
                     _density_score(len(violations), words), violations,
                     _tool_version(["vale", "--version"]))


# -- code-block runner ----------------------------------------------------------

FENCE_RE = re.compile(r"^```(\w+)?[^\n]*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)


def extract_code_blocks(markdown: str) -> list[tuple[str, str]]:
    """[(language, code), ...] for every fenced block with a language tag."""
    return [(m.group(1) or "", m.group(2)) for m in FENCE_RE.finditer(markdown)]


def run_code_blocks(path: Path, settings: Settings | None = None) -> DetResult:
    settings = settings or load_settings()
    allowed = set(settings.code_runner.allowed_languages)
    timeout = settings.code_runner.timeout_seconds
    blocks = extract_code_blocks(path.read_text())
    violations = []
    ran = 0
    passed_blocks = 0
    for i, (lang, code) in enumerate(blocks):
        if lang not in allowed:
            continue
        ran += 1
        status, detail = _run_block(lang, code, timeout)
        if status == "PASS":
            passed_blocks += 1
        else:
            violations.append({"rule": f"code-{status.lower()}", "block": i,
                               "language": lang, "message": detail[:500]})
    if ran == 0:
        return DetResult("code-runner", True, None, [],
                         f"python/{_tool_version(['python3', '--version'])}")
    score = round(10.0 * passed_blocks / ran, 2)
    return DetResult("code-runner", passed_blocks == ran, score, violations,
                     f"python/{_tool_version(['python3', '--version'])}")


def _run_block(lang: str, code: str, timeout: int) -> tuple[str, str]:
    """Run one block in a tempdir with no network (best-effort via env) and a
    hard timeout. Returns (PASS|FAIL|TIMEOUT, detail)."""
    with tempfile.TemporaryDirectory() as tmp:
        if lang == "python":
            cmd = ["python3", "-I", "-c", code]
        else:  # bash / sh
            cmd = ["bash", "--noprofile", "--norc", "-c", code]
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": tmp,
            "TMPDIR": tmp,
            # Common proxy vars pointed at a dead port to block accidental network use.
            "http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
            "no_proxy": "", "NO_PROXY": "",
        }
        try:
            proc = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "TIMEOUT", f"exceeded {timeout}s"
        if proc.returncode == 0:
            return "PASS", ""
        return "FAIL", redact_paths((proc.stderr or proc.stdout).strip())


# -- readability -------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"[.!?]+(?:\s|$)")
_VOWEL_GROUPS = re.compile(r"[aeiouy]+")


def _syllables(word: str) -> int:
    word = word.lower().strip(".,;:!?\"'()[]")
    if not word:
        return 0
    count = len(_VOWEL_GROUPS.findall(word))
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _strip_markdown(text: str) -> str:
    text = FENCE_RE.sub("", text)                      # code blocks
    text = re.sub(r"`[^`]*`", "", text)                # inline code
    text = re.sub(r"^#+\s*", "", text, flags=re.M)     # heading markers
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links
    text = re.sub(r"[*_>#|-]", " ", text)
    return text


def run_readability(path: Path) -> DetResult:
    raw = path.read_text()
    prose = _strip_markdown(raw)
    words = [w for w in prose.split() if any(c.isalpha() for c in w)]
    sentences = [s for s in _SENT_SPLIT.split(prose) if s.strip()]
    n_words, n_sents = len(words), max(len(sentences), 1)
    n_syll = sum(_syllables(w) for w in words)

    if n_words == 0:
        return DetResult("readability", False, None,
                         [{"rule": "empty", "message": "no prose found"}])

    fk_grade = 0.39 * (n_words / n_sents) + 11.8 * (n_syll / n_words) - 15.59
    headings = re.findall(r"^(#+)\s", raw, flags=re.M)
    code_chars = sum(len(c) for _, c in extract_code_blocks(raw))
    metrics = {
        "flesch_kincaid_grade": round(fk_grade, 2),
        "avg_sentence_length": round(n_words / n_sents, 2),
        "word_count": n_words,
        "heading_count": len(headings),
        "max_heading_depth": max((len(h) for h in headings), default=0),
        "code_prose_ratio": round(code_chars / max(len(prose), 1), 3),
    }
    # Stored as a single "violation" entry so metrics live in violations_json.
    return DetResult("readability", True, round(fk_grade, 2),
                     [{"rule": "metrics", "message": json.dumps(metrics)}])


# -- orchestration ---------------------------------------------------------------

ALL_TOOLS = ["markdownlint", "codespell", "lychee", "vale", "code-runner", "readability"]


def validate_document(path: Path, tools: list[str] | None = None,
                      settings: Settings | None = None) -> list[DetResult]:
    settings = settings or load_settings()
    tools = tools or ALL_TOOLS
    runners = {
        "markdownlint": lambda: run_markdownlint(path),
        "codespell": lambda: run_codespell(path),
        "lychee": lambda: run_lychee(path, settings),
        "vale": lambda: run_vale(path),
        "code-runner": lambda: run_code_blocks(path, settings),
        "readability": lambda: run_readability(path),
    }
    results = []
    for tool in tools:
        if tool not in runners:
            raise ValueError(f"unknown tool {tool}")
        binary = tool if tool not in ("code-runner", "readability") else None
        if binary and shutil.which(binary) is None:
            log.warning("%s not installed, skipping", binary)
            continue
        try:
            results.append(runners[tool]())
        except Exception as e:
            log.error("%s failed on %s: %s", tool, path, e)
            results.append(DetResult(tool, False, None,
                                     [{"rule": "tool-error", "message": str(e)[:500]}]))
    return results


def validate_all(tools: list[str] | None = None,
                 settings: Settings | None = None) -> int:
    settings = settings or load_settings()
    db = Database(settings.db_path)
    count = 0
    for doc in db.documents():
        path = Path(doc.path)
        if not path.exists():
            log.warning("missing file for document %d: %s", doc.id, doc.path)
            continue
        for res in validate_document(path, tools, settings):
            db.record_det_result(doc.id, res.tool, res.passed, res.score,
                                 res.violations, res.tool_version)
            count += 1
    db.close()
    return count
