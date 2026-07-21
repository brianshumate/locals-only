"""Model discovery: enumerate what each backend can actually serve and
propose the missing `config/authors.yaml` / `config/judges.yaml` entries.

Two sources, one per backend:

- ``lmstudio``  — `lms ls --json`, which lists every *downloaded* model with
  its quantization, parameter count, and max context length (unlike
  `/v1/models`, which only reports what is loaded right now).
- ``llamacpp``  — the gguf files in the model directory, listed on the host
  when it is reachable and otherwise inside the running container.

Discovery is advisory: `eval discover` prints proposals and writes nothing
until `--apply`. Writes are line-oriented text edits rather than a YAML
round-trip, so the heavily commented config files keep their comments.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import (CONFIG_DIR, AuthorConfig, DiscoverySettings, JudgeConfig,
                     LlamaCppSettings, Settings, load_authors, load_judges,
                     load_settings)

log = logging.getLogger(__name__)

JUDGE_ID_PREFIX = "judge-"

# Tokens that describe *how a model was built* rather than which model it is.
# Truncating an identifier at the first of these turns a filename or model key
# into a stable id: `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf` -> `gemma-4-26b`.
_NOISE_TOKENS = frozenset({
    "it", "instruct", "chat", "mlx", "mtp", "qat", "ud", "gguf", "safetensors",
    "bf16", "f16", "fp16", "f32", "base", "pt",
})
# Quantization-shaped tokens: Q4_K_M, q8_0, IQ3_XXS, 6bit, MXFP4.
_QUANT_TOKEN = re.compile(r"^(i?q\d+(_\w+)*|\d+bit|mxfp4)$", re.IGNORECASE)
# Mixture-of-experts active-parameter tokens: A3B, A4B, A1B.
_ACTIVE_PARAMS_TOKEN = re.compile(r"^a\d+(\.\d+)?b$", re.IGNORECASE)
# The quantization label embedded in a gguf filename, with its qat-/UD-
# decorations. The `_`-suffixed form is tried first so `Q4_K_M` wins over `Q4`.
_QUANT_LABEL = re.compile(
    r"(?:qat-)?(?:UD-)?(?:I?Q\d+(?:_[A-Z0-9]+)+|I?Q\d+|BF16|F16|MXFP4)",
    re.IGNORECASE)
# Sharded gguf: keep only the first part, which names the whole model.
_SHARD = re.compile(r"-(\d{5})-of-\d{5}$")


@dataclass(frozen=True)
class DiscoveredModel:
    """A model a backend can serve, as reported by that backend."""
    backend: str
    model: str                      # identity the backend loads by
    suggested_id: str               # derived config id
    quantization: str | None = None
    context_length: int | None = None
    params: str | None = None

    def describe(self) -> str:
        bits = [b for b in (self.params, self.quantization) if b]
        return f"{self.model}" + (f"  ({', '.join(bits)})" if bits else "")


@dataclass
class Proposal:
    """One config change discovery wants to make."""
    kind: str                       # "new-author" | "new-judge" | "add-backend"
    role: str                       # "author" | "judge"
    entry_id: str
    found: DiscoveredModel

    @property
    def summary(self) -> str:
        if self.kind == "add-backend":
            return (f"{self.entry_id}: add `{self.found.backend}` backend "
                    f"-> {self.found.model}")
        return f"{self.entry_id}: new {self.role} -> {self.found.describe()}"


# --------------------------------------------------------------------------
# identifier derivation
# --------------------------------------------------------------------------


def _strip_publisher(name: str) -> str:
    """`google/gemma-4-12b-qat` -> `gemma-4-12b-qat`; `Qwen_Qwen3.6-35B` ->
    `Qwen3.6-35B` (HuggingFace-style repo prefixes only, not `LFM2.5_8B`)."""
    name = name.rsplit("/", 1)[-1]
    head, sep, tail = name.partition("_")
    if sep and tail.lower().startswith(head.lower()):
        return tail
    return name


def derive_id(model: str) -> str:
    """Stable config id for a backend model identity.

    Drops the publisher, the file extension, and every build-detail suffix,
    so the two deployments of one model converge on the same id:

        google/gemma-4-12b-qat                   -> gemma-4-12b
        gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf   -> gemma-4-26b
        Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf         -> qwen3.6-35b
        lfm2.5-8b-a1b-mlx                        -> lfm2.5-8b
    """
    stem = _strip_publisher(model)
    if stem.lower().endswith(".gguf"):
        stem = stem[: -len(".gguf")]
    stem = _SHARD.sub("", stem)
    kept: list[str] = []
    for token in re.split(r"[-\s]+", stem):
        if not token:
            continue
        low = token.lower()
        if low in _NOISE_TOKENS or _QUANT_TOKEN.match(low) or \
                _ACTIVE_PARAMS_TOKEN.match(low):
            break
        kept.append(low)
    return "-".join(kept) or stem.lower()


def _gguf_quantization(filename: str) -> str | None:
    matches = list(_QUANT_LABEL.finditer(filename))
    if not matches:
        return None
    label = matches[-1].group(0)
    # Config spells pure float formats lowercase (`bf16`) and integer quants
    # in their canonical uppercase (`Q4_K_M`, `qat-UD-Q4_K_XL`).
    return label.lower() if re.fullmatch(r"(BF16|F16)", label, re.I) else label


# --------------------------------------------------------------------------
# backend enumeration
# --------------------------------------------------------------------------


def discover_lmstudio(settings: Settings | None = None) -> list[DiscoveredModel]:
    """Downloaded LM Studio LLMs, per `lms ls --json`."""
    settings = settings or load_settings()
    try:
        proc = subprocess.run(["lms", "ls", "--json"], capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("lmstudio discovery: `lms ls` unavailable (%s)", e)
        return []
    if proc.returncode != 0:
        log.warning("lmstudio discovery: `lms ls` failed: %s",
                    proc.stderr.strip()[:300])
        return []
    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        log.warning("lmstudio discovery: unparsable `lms ls --json`: %s", e)
        return []

    found = []
    for entry in entries:
        # Embedding and reranker models answer /v1/embeddings, not /v1/chat.
        if entry.get("type") != "llm":
            continue
        model = entry.get("modelKey") or entry.get("indexedModelIdentifier")
        if not model:
            continue
        found.append(DiscoveredModel(
            backend="lmstudio",
            model=model,
            suggested_id=derive_id(model),
            quantization=(entry.get("quantization") or {}).get("name"),
            context_length=entry.get("maxContextLength"),
            params=entry.get("paramsString"),
        ))
    return found


def _list_gguf_via_docker(settings: LlamaCppSettings) -> list[str]:
    """Fall back to listing the model volume inside the running container."""
    try:
        proc = subprocess.run(
            ["docker", "compose", "exec", "-T", settings.compose_service,
             "ls", "-1", settings.container_model_dir],
            cwd=settings.compose_dir, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("llamacpp discovery: docker exec unavailable (%s)", e)
        return []
    if proc.returncode != 0:
        log.warning("llamacpp discovery: could not list %s in container: %s",
                    settings.container_model_dir, proc.stderr.strip()[:300])
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def discover_llamacpp(settings: Settings | None = None) -> list[DiscoveredModel]:
    """gguf files the llama.cpp server can be pointed at."""
    settings = settings or load_settings()
    cfg = settings.llamacpp
    host_dir = Path(cfg.host_model_dir)
    if host_dir.is_dir():
        names = [p.name for p in host_dir.iterdir() if p.is_file()]
    else:
        log.info("llamacpp discovery: %s not present on this host, listing "
                 "the model volume inside the container instead", host_dir)
        names = _list_gguf_via_docker(cfg)

    found = []
    for name in sorted(names):
        if not name.lower().endswith(".gguf"):
            continue
        shard = _SHARD.search(name[: -len(".gguf")])
        if shard and shard.group(1) != "00001":
            continue  # non-leading shard of a split model
        found.append(DiscoveredModel(
            backend="llamacpp",
            model=name,
            suggested_id=derive_id(name),
            quantization=_gguf_quantization(name),
        ))
    return found


def discover(backend: str, settings: Settings | None = None) -> list[DiscoveredModel]:
    settings = settings or load_settings()
    if backend == "lmstudio":
        return discover_lmstudio(settings)
    return discover_llamacpp(settings)


# --------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------


def _excluded(model: DiscoveredModel, cfg: DiscoverySettings) -> bool:
    hay = f"{model.model} {model.suggested_id}".lower()
    return any(pat.lower() in hay for pat in cfg.exclude)


def _propose_for_role(found: list[DiscoveredModel],
                      entries: list[AuthorConfig] | list[JudgeConfig],
                      role: str, backend: str) -> list[Proposal]:
    by_id = {e.id: e for e in entries}
    # A judge id is conventionally the author id with a `judge-` prefix.
    def entry_id(model_id: str) -> str:
        return f"{JUDGE_ID_PREFIX}{model_id}" if role == "judge" else model_id

    # Models already named by *some* entry, so a second gguf of the same model
    # (a different quant, say) does not propose a duplicate.
    configured = {
        (e.id, bm.model)
        for e in entries
        for bm in [e.resolve(backend)] if bm is not None
    }

    proposals: list[Proposal] = []
    seen_ids: set[str] = set()
    for model in found:
        eid = entry_id(model.suggested_id)
        existing = by_id.get(eid)
        if existing is not None:
            if existing.resolve(backend) is not None:
                continue  # already runnable here
            proposals.append(Proposal("add-backend", role, eid, model))
            continue
        if eid in seen_ids or any(mid == model.model for _, mid in configured):
            continue
        seen_ids.add(eid)
        proposals.append(
            Proposal("new-author" if role == "author" else "new-judge",
                     role, eid, model))
    return proposals


def plan(backend: str, roles: tuple[str, ...] = ("author",),
         settings: Settings | None = None,
         authors_path: Path | None = None,
         judges_path: Path | None = None) -> list[Proposal]:
    """Everything discovery would add for `backend`, in file order."""
    settings = settings or load_settings()
    found = [m for m in discover(backend, settings)
             if not _excluded(m, settings.discovery)]
    proposals: list[Proposal] = []
    if "author" in roles:
        proposals += _propose_for_role(
            found, load_authors(authors_path), "author", backend)
    if "judge" in roles:
        proposals += _propose_for_role(
            found, load_judges(judges_path), "judge", backend)
    return proposals


# --------------------------------------------------------------------------
# rendering + applying
# --------------------------------------------------------------------------


def _model_block(found: DiscoveredModel, indent: str,
                 with_quantization: bool = True) -> list[str]:
    """The `model:`/`quantization:` pair as written for a backend. Judges
    carry no quantization — `JudgeConfig` has no such field."""
    lines = [f"{indent}model: {found.model}"]
    if with_quantization and found.quantization:
        lines.append(f"{indent}quantization: {found.quantization}")
    return lines


def render_entry(p: Proposal, cfg: DiscoverySettings) -> str:
    """A complete new authors.yaml / judges.yaml list item."""
    quant = p.role == "author"
    lines = [f"  - id: {p.entry_id}"]
    if p.found.backend == "lmstudio":
        lines += _model_block(p.found, "    ", quant)
    else:
        lines += ["    backends:", f"      {p.found.backend}:"]
        lines += _model_block(p.found, "        ", quant)
    if p.role == "judge":
        lines.append(f"    temperature: {cfg.judge_temperature}")
        lines.append(f"    max_tokens: {cfg.judge_max_tokens}")
        lines.append(f"    skills: [{', '.join(cfg.judge_skills)}]")
    else:
        ctx = cfg.context_length
        if p.found.context_length:
            ctx = min(ctx, p.found.context_length)
        lines.append(f"    temperature: {cfg.temperature}")
        lines.append(f"    seed: {cfg.seed}")
        lines.append(f"    max_tokens: {cfg.max_tokens}")
        lines.append(f"    context_length: {ctx}")
    return "\n".join(lines) + "\n"


def _entry_bounds(lines: list[str], entry_id: str) -> tuple[int, int]:
    """Half-open line range of the `- id: <entry_id>` list item."""
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"\s*-\s+id:\s+{re.escape(entry_id)}\s*$", line):
            start = i
            break
    if start is None:
        raise KeyError(f"no entry with id {entry_id!r} in config")
    for j in range(start + 1, len(lines)):
        if re.match(r"\s*-\s+id:\s", lines[j]):
            return start, j
    return start, len(lines)


def _insert_backend(lines: list[str], p: Proposal) -> list[str]:
    """Add a `backends: <name>:` mapping to an entry that lacks one."""
    start, end = _entry_bounds(lines, p.entry_id)
    block = _model_block(p.found, "        ", p.role == "author")
    for i in range(start, end):
        if re.match(r"\s*backends:\s*$", lines[i]):
            # Existing `backends:` map — append this backend to it.
            insert_at = i + 1
            while insert_at < end and re.match(r"\s{6,}\S", lines[insert_at]):
                insert_at += 1
            return (lines[:insert_at] + [f"      {p.found.backend}:"] + block
                    + lines[insert_at:])
    # No `backends:` yet: open one after the entry's own scalar keys, keeping
    # it above temperature/seed/... the way the hand-written entries read.
    insert_at = start + 1
    while insert_at < end and re.match(
            r"\s*(model|quantization):\s", lines[insert_at]):
        insert_at += 1
    return (lines[:insert_at] + ["    backends:", f"      {p.found.backend}:"]
            + block + lines[insert_at:])


def apply_proposals(proposals: list[Proposal],
                    settings: Settings | None = None,
                    authors_path: Path | None = None,
                    judges_path: Path | None = None) -> dict[str, int]:
    """Write `proposals` into the config files. Returns per-file counts."""
    settings = settings or load_settings()
    cfg = settings.discovery
    paths = {
        "author": authors_path or CONFIG_DIR / "authors.yaml",
        "judge": judges_path or CONFIG_DIR / "judges.yaml",
    }
    counts = {"author": 0, "judge": 0}
    for role, path in paths.items():
        mine = [p for p in proposals if p.role == role]
        if not mine:
            continue
        lines = path.read_text().splitlines()
        for p in [q for q in mine if q.kind == "add-backend"]:
            lines = _insert_backend(lines, p)
        appended = [render_entry(p, cfg) for p in mine if p.kind != "add-backend"]
        text = "\n".join(lines).rstrip("\n") + "\n"
        if appended:
            text += "\n" + "\n".join(appended)
        path.write_text(text)
        counts[role] = len(mine)
    return counts
