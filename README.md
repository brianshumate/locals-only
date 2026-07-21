<!-- markdownlint-disable MD013 MD014 MD060 -->
<!-- Wide tables and console transcripts    -->
<!-- These rules stay ON for evaluated docs -->

# Locals Only

An eval for **technical-writing quality** of local open-weight LLMs. Every
author model writes the same documents from identical prompts; a battery of
deterministic validators and LLM judges scores the results; rankings come from
pairwise A/B comparisons aggregated with Bradley–Terry, calibrated against
periodic human review.

Everything runs locally, on either of two machines/backends:

- **`lmstudio`**: [LM Studio](https://lmstudio.ai) (`lms` CLI +
  OpenAI-compatible server) on the MacBook Air (M5, unified memory).

- **`llamacpp`**: llama.cpp's `llama-server` in Docker (compose project in
  `~/lcpp-docker`) on the Linux server (RTX 3090), serving gguf
  files from `/mnt/data-one/llama-models`.

The backend is auto-detected per machine (macOS → `lmstudio`, Linux →
`llamacpp`) and both expose the same OpenAI-compatible chat API, so every
pipeline stage runs unchanged on either box. Every generation and judgment is
stored on disk and in sqlite with full provenance (model, temperature, seed,
prompt hash, skill version, **and the machine/OS/GPU/backend it ran on**), so
runs are reproducible, auditable, and comparable across hardware.

## Design principles

1. **Four independent concerns.** Author models → deterministic validators →
   judge models → human calibration. Each evolves separately.

1. **Skills are evaluation functions.** Each skill answers exactly one question
   (style, factuality, completeness, …) and must return strict JSON against a
   schema.

1. **Deterministic first.** Anything objectively checkable (markdown validity,
   links, spelling, code execution, readability) never goes to an LLM judge.

1. **Never trust one judge.** Every subjective criterion is scored by ≥2 judge
   models; the full author×judge matrix makes self-judging bias measurable.

1. **Pairwise > absolute.** Absolute 0–10 scores feed diagnostics; rankings
   come from A/B comparisons (both orderings) fed to Bradley–Terry.

1. **Everything on disk, reproducible.** Re-running any stage is idempotent;
   `--force` regenerates.

## Architecture

```text
                         datasets/prompts/*.yaml
                                   │
                        ┌──── eval generate ───┐       (loads each author model
                        ▼          ▼           ▼        sequentially on the active
                    gemma-26b  qwen3.6-27b  lfm2.5-8b   backend: LM Studio on the
                        │          │           │        Mac, llama.cpp/Docker on
                        │          │           │        the Linux/3090 server)
                        ▼          ▼           ▼
              runs/<prompt-id>/<author>/output.md + manifest.json
                        │
        ┌───────────────┴────────────────────┐
        ▼                                    ▼
  Deterministic layer                 LLM judge layer
  (eval validate)                     (eval judge / eval compare)
  ├─ markdownlint                     ├─ style-guide skill
  ├─ vale (Google + LocalDocs)        ├─ factuality skill
  ├─ codespell                        ├─ completeness skill
  ├─ lychee (offline links)           ├─ audience-fit skill
  ├─ code-block runner (sandboxed)    ├─ code-quality skill
  └─ readability metrics              └─ pairwise-compare skill (A vs B, both orders)
        │                                    │
        └────────────► results.sqlite ◄──────┘
                            │
                 eval analyze / eval rank
              (means, bootstrap CIs, Krippendorff's α,
               Bradley–Terry ratings, author×judge grid,
               self-judging deltas, swap consistency)
                            │
                      eval report → reports/*.html   (dashboard, drill-downs)
                            │
              eval calibrate … / eval regress        (human anchor, drift guard)
```

### Repository layout

```text
├── config/
│   ├── settings.yaml         # backend selection, LM Studio + llama.cpp settings,
│   │                         #   timeouts, code-runner sandbox, lychee
│   ├── authors.yaml          # author models: id, model, temperature, seed, max_tokens
│   └── judges.yaml           # judge models + which skills each runs
├── datasets/
│   ├── prompts/*.yaml        # 10 tasks (Diátaxis: tutorial/how-to/reference/conceptual)
│   └── reference/*.yaml      # checkable ground-truth facts per prompt (factuality)
├── skills/<name>/            # one evaluator each:
│   ├── SKILL.md              #   judge prompt (Jinja2 template)
│   ├── rubric.yaml           #   criteria, weights, version
│   └── schema.json           #   required JSON output shape (enforced)
├── styles/                   # Vale config: Google package + custom LocalDocs rules
├── fixtures/                 # golden/bad/code-block docs + regression.yaml bands
├── src/eval_pipeline/        # the pipeline (see module map below)
├── scripts/doctor.sh         # environment verification
├── runs/                     # generated documents (gitignored)
├── reports/                  # generated HTML (gitignored)
├── calibration/              # human scoring forms
└── results.sqlite            # all results (gitignored)
```

### Module map (`src/eval_pipeline/`)

| Module | Responsibility |
|---|---|
| `config.py` | Typed (pydantic) loading of the three config YAMLs; backend resolution (`auto`/`EVAL_BACKEND`); per-backend model resolution for authors/judges |
| `backends.py` | Backend abstraction: `LMStudioBackend` (swaps models via `lms`) and `LlamaCppBackend` (recreates the Docker compose service with `LLAMA_MODEL`/`LLAMA_CTX_SIZE`); backend-aware `model_session()` |
| `envinfo.py` | Best-effort capture of hostname, OS, CPU, GPU, backend + version for provenance |
| `lmstudio.py` | `lms load/unload/ps` wrapper; OpenAI-compat chat client with retries, JSON-schema mode, token accounting (the client is shared by both backends) |
| `db.py` | Sqlite schema + versioned migrations; idempotent, hash-keyed inserts |
| `discover.py` | Enumerates models each backend can serve (`lms ls`, gguf directory) and proposes the missing authors/judges entries; comment-preserving config writes |
| `prompts.py` | Prompt/reference-fact dataset schema and loading |
| `generate.py` | Authors × prompts → `runs/` + DB, author-major (one model load each) |
| `validate.py` | Deterministic adapters, all normalized to `{tool, passed, score, violations[]}` |
| `skills.py` | Skill loader + jsonschema output enforcement |
| `judge.py` | Judges × skills × documents → judgments (one repair retry, failures recorded) |
| `pairwise.py` | A/B sampling, both orderings, swap-aware winner normalization |
| `analyze.py` | Aggregates, bootstrap CIs, author×judge matrix, Krippendorff's α, generation cost |
| `rank.py` | Bradley–Terry fit (`choix`) + bootstrap CIs; refuses overlapping-CI winners |
| `report.py` | Static HTML: dashboard/leaderboard, heatmap, drill-downs, agreement |
| `calibrate.py` | Stratified human sampling, form import, human↔judge Spearman ρ |
| `regress.py` | Drift detection against frozen fixtures + score bands |
| `cli.py` | The `eval` command |

### Database schema (`results.sqlite`, schema v4)

```text
authors(id, model, quantization, temperature, seed, config_json)
judges(id, model, config_json)
prompts(id, doc_type, audience, prompt_hash, file)
environments(id, env_hash, hostname, os, os_version, arch, cpu, gpu,
             backend, backend_version)
documents(id, prompt_id, author_id, path, content_hash,
          gen_time_s, tokens, prompt_tokens, environment_id, created_at)
det_results(id, document_id, tool, passed, score, violations_json, tool_version)
judgments(id, document_id, judge_id, skill, skill_version, score, confidence,
          violations_json, raw_json, latency_s, failed)
comparisons(id, prompt_id, doc_a, doc_b, judge_id, skill, winner, confidence,
            position_swapped, raw_json)
human_scores(id, document_id, reviewer, skill, score, notes)
meta(key, value)                      -- schema_version; migrations in db.py
```

Idempotency: documents are unique on `(prompt_id, author_id, content_hash)`;
det_results on `(document_id, tool)`; judgments on
`(document_id, judge_id, skill, skill_version)`; comparisons on
`(doc_a, doc_b, judge_id, skill, position_swapped)`. Re-running a stage
updates rather than duplicates. Environments deduplicate on a hash of their
fields, so each (machine, backend version) pair is one row that documents
point at.

The database is **portable between machines**: document paths are stored
repo-relative (`runs/<prompt>/<author>/output.md`, schema v4 migrates older
absolute paths) and resolve against the local checkout, so you can rsync
`results.sqlite` + `runs/` between the Mac and the server and continue any
stage there.

### Key mechanics

- **JSON discipline.** Judge calls use LM Studio structured output with the
  skill's `schema.json`; the reply is re-validated with `jsonschema`. Invalid
  output gets exactly one repair retry (the validation error is fed back);
  a second failure is stored with `failed=1` but never coerced.

- **Model orchestration.** Neither machine can hold two large models at once
  (unified memory on the Mac, a single RTX 3090 on the server), so
  `model_session()` serves exactly one model at a time and the pipeline
  batches *all* work for that model before switching (author-major
  generation, judge-major judging). On `lmstudio` a swap is `lms unload` +
  `lms load`; on `llamacpp` it recreates the compose service with
  `LLAMA_MODEL=/models/<file>.gguf` (and `LLAMA_CTX_SIZE` from the author's
  `context_length`) and waits for `/health`. If the requested model is
  already being served, the running server is reused.

- **Backend selection.** `backend: auto` in `config/settings.yaml` picks
  `lmstudio` on macOS and `llamacpp` elsewhere; override with
  `eval --backend lmstudio|llamacpp` or `EVAL_BACKEND=...`. Authors and
  judges declare per-backend model identities; entries with no model for the
  active backend are skipped on that machine with a warning.

- **Environment provenance.** Every document records the environment it was
  authored in (hostname, OS, CPU, GPU, backend + version) in its
  `manifest.json` and in the `environments` table and every results view
  (dashboard, `eval analyze`, `eval rank`, the leaderboard and criteria
  pages) is broken out per environment: rankings, score aggregates, and
  speed metrics are computed from same-environment documents only, with the
  cross-environment pool shown separately and clearly flagged. Note that
  cross-backend weights usually differ (mlx/qat on the Mac vs gguf quants on
  the 3090), so cross-machine rows compare *deployments* of a model, not
  identical weights, which is why only same-environment numbers compare
  authors fairly.

- **Position-bias control.** Every pair is judged twice with A/B swapped.
  Winners are normalized back to the canonical order; if the two orderings
  disagree, analysis counts the pair as a tie and the disagreement feeds the
  per-judge swap-consistency metric.

- **Self-judging allowed but tracked.** A judge whose underlying model equals
  the author's model is a self-judge; the dashboard reports the delta between
  self-scores and other-judge scores per author.

- **Versioning.** Judgments store the skill version (from `rubric.yaml`).
  Editing a rubric bumps the version, and old scores never mix with new ones.
  Editing a prompt changes its hash, which forces regeneration.

## Configuration

### `config/settings.yaml`

```yaml
backend: auto                          # lmstudio | llamacpp | auto
                                       # (auto: macOS→lmstudio, else llamacpp)
lmstudio:
  base_url: http://localhost:1234/v1   # doctor.sh checks this
  request_timeout: 1800                # seconds per chat call (27B is slow)
  load_timeout: 300
  retries: 2
llamacpp:
  base_url: http://localhost:8080/v1   # llama-server inside the container
  compose_dir: ~/lcpp-docker # docker-compose.yml lives here
  compose_service: llama
  container_model_dir: /models         # where the compose file mounts
                                       #   /mnt/data-one/llama-models
  request_timeout: 1800
  load_timeout: 900                    # container start + gguf load → VRAM
  retries: 2
database: results.sqlite
runs_dir: runs
reports_dir: reports
code_runner:
  allowed_languages: [python, bash, sh]  # everything else is skipped
  timeout_seconds: 30
lychee:
  offline: true                        # no network during link checks
  allowlist: []
```

### `config/authors.yaml`

One entry per author configuration. Sweeps (temperature, quantization) are
just additional rows with new ids; they become separate `authors` in the DB.

`model`/`quantization` are the LM Studio identity; a `backends: llamacpp:`
entry maps the same author to a gguf filename in `/mnt/data-one/llama-models`
(resolved under the container's `/models` mount). An author with no model for
the active backend is skipped on that machine, so one authors.yaml drives
both boxes, including llama.cpp-only authors that are too big for the Mac.

```yaml
authors:
  - id: qwen3.6-27b          # DB id: stable name for this configuration
    model: qwen3.6-27b-mtp   # LM Studio model identifier (MacBook Air)
    quantization: mtp
    backends:
      llamacpp:              # same author on the Linux/3090 server
        model: Qwen3.6-27B-MTP-Q4_K_M.gguf
        quantization: Q4_K_M
    temperature: 0.7
    seed: 42
    max_tokens: 4096
    context_length: 8192

  - id: qwen3.6-35b          # llama.cpp only: no `model:` key, so the
    backends:                #   Mac skips it
      llamacpp:
        model: Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf
        quantization: Q4_K_M
    temperature: 0.7
    seed: 42
    max_tokens: 8192
    context_length: 16384
```

Entries do not have to be written by hand. `eval discover` proposes them from
whatever the active backend can actually serve (see
[Discovering new models](#discovering-new-models)).

### `config/judges.yaml`

Judges run at temperature 0 in JSON mode. Every subjective skill should be
listed under ≥2 judges (design principle 4). Backends work exactly as in
authors.yaml. Keep ≥2 judges per skill available on *each* backend.

```yaml
judges:
  - id: judge-qwen3.6-27b
    model: qwen3.6-27b-mtp
    backends:
      llamacpp:
        model: Qwen3.6-27B-MTP-Q4_K_M.gguf
    temperature: 0.0
    max_tokens: 2048
    skills: [style-guide, factuality, completeness, audience-fit,
             code-quality, pairwise-compare]
```

### Prompts (`datasets/prompts/<id>.yaml`)

```yaml
id: tut-python-json
title: Working with JSON in Python
doc_type: tutorial            # tutorial | how-to | reference | conceptual
audience: Beginner Python programmers comfortable with dictionaries and lists.
source_material: |            # the spec the model writes from
  Teach the standard-library `json` module: ...
required_sections: [Introduction, Prerequisites, ...]
target_length_words: 800
requires_code: true
code_languages: [python]      # must be on the code-runner allowlist
```

Each prompt has a matching `datasets/reference/<id>.yaml` with ≥3 checkable
facts; the factuality skill grades documents against these:

```yaml
prompt_id: tut-python-json
facts:
  - claim: null mapping
    truth: JSON `null` maps to Python `None`.
```

### Skills (`skills/<name>/`)

A skill is one evaluator: `SKILL.md` (Jinja2 judge prompt), `rubric.yaml`
(criteria, weights, **version**), `schema.json` (enforced output shape).
Shipped skills: `style-guide`, `factuality`, `completeness`, `audience-fit`,
`code-quality`, `pairwise-compare`, and `length-check` (a trivial smoke-test
skill). All scoring skills return
`{score: 0–10, confidence: 0–1, violations: [...], summary}`;
`pairwise-compare` returns `{winner: a|b|tie, confidence, reason}`.

To add a skill: create the three files, list the skill under the judges that
should run it in `judges.yaml`, add fixture bands to `fixtures/regression.yaml`,
and run `eval regress`.

## Operation

### Setup (once per machine)

Common to both machines:

Install Python dependencies and the `eval` CLI.

```shell
uv sync 
```

Pull the Google style Vale package.

```shell
cd styles && vale sync
```

**MacBook Air (LM Studio backend):**

Install dependencies with Home Brew.

```shell
brew install lychee codespell vale
```

Install `markdownlint` CLI.

```shell
npm install -g markdownlint-cli
```

Start the LM Studio server using the `lms` CLI.

```shell
lms server start --port 1234
```

**Linux server (llama.cpp/Docker backend):**

On Linux you need Docker + the NVIDIA container toolkit.

You can use the official llama.cpp Docker project to build the
`llama.cpp:full-cuda` and place your gguf models in a shared volume.

Install the deterministic validators at the user level with tools like
`mise`, `npm`, and `uv`.

Install lychee for link checking.

```shell
mise use -g vale@latest lychee@latest
```

Install markdownlint with `npm`.

```shell
npm install -g markdownlint-cli
```

Install codespell with `uv`.

```shell
uv tool install codespell
```

The llama-server container does **not** need to be running; the pipeline
starts and swaps it on demand via `docker compose`.

Then on either machine, verify everything.

```shell
bash ./scripts/doctor.sh
```

```plaintext
== External tools ==
  ok    python3        Python 3.14.3
  ok    vale           vale version 3.15.1
  ...
== Backend: llamacpp ==                  # (or "Backend: lmstudio" on the Mac)
  ok    docker         Docker version 29.5.3
  ok    nvidia-smi     NVIDIA GeForce RTX 3090
  ok    compose file    ~/lcpp-docker/docker-compose.yml
  ok    models         6 gguf file(s) in /mnt/data-one/llama-models
```

`doctor.sh` exits non-zero if anything is missing run it after any
environment change. It resolves the backend the same way the pipeline does
(`EVAL_BACKEND` → `config/settings.yaml` → auto by OS).

### Test suite

Run the unit tests and live LM Studio integration test.

```shell
uv run pytest
```

Run the unit tests only (no inference server needed).

```shell
$ uv run pytest -m "not integration"
```

The integration test loads the smallest model, verifies a chat and a
JSON-schema completion, and confirms the model is unloaded afterwards.

### Pipeline stages

Each stage is idempotent; run them in order and freely re-run as needed.

```shell
uv run eval discover [--role author|judge|both] [--apply]   # optional
uv run eval generate [--authors a,b] [--prompts p1,p2] [--force]
uv run eval validate [--tools markdownlint,vale,...]
uv run eval judge    [--judges j1] [--skills style-guide,...] [--force]
uv run eval compare  [--judges j1] [--force]
uv run eval analyze          # console: cost, aggregates, matrix, agreement
uv run eval rank             # console: Bradley–Terry leaderboard + CIs
uv run eval report           # regenerates reports/*.html from the DB alone
```

Add `-v` before the subcommand for debug logging (`uv run eval -v generate`).

The backend is chosen automatically per machine; override it per invocation
when needed:

```shell
uv run eval --backend llamacpp generate     # Force the Docker backend
EVAL_BACKEND=lmstudio uv run eval judge     # Environment variable works too
```

### Discovering new models

After pulling a model in LM Studio or dropping a gguf on the server, let
discovery write the config entry instead of hand-editing YAML:

Preview what's available but not configured.

```shell
uv run eval discover    
```

Append the proposed author entries.

```shell
uv run eval discover --apply
```

Append both authors *and* judges.

```shell
uv run eval discover --role both --apply
```

Discovery asks the active backend what it can serve: `lms ls --json` on the
Mac (every *downloaded* model, not just the loaded one), the gguf files in
`llamacpp.host_model_dir` on the server (falling back to listing the volume
inside the container when run from elsewhere) and diffs that against
`config/`. Embedding and re-ranker models are filtered out, as is anything
matching `discovery.exclude` in settings.yaml.

Each model gets an id derived by dropping the publisher, the extension, and
the build-detail suffixes, so both deployments of one model converge on the
same id:

```text
google/gemma-4-12b-qat                          -> gemma-4-12b
gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf          -> gemma-4-26b
Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf                -> qwen3.6-35b
lfm2.5-8b-a1b-mlx  ==  LFM2.5-8B-A1B-BF16.gguf  ->  lfm2.5-8b
```

That convergence is what makes the cross-machine case work: a gguf matching an
author that already exists but can't run on this backend gets a `backends:`
mapping added to the existing entry, rather than a duplicate row.

```shell
uv run eval discover
```

```text
llamacpp: 2 proposal(s)

  gemma-4-12b: add `llamacpp` backend -> gemma-4-12b-it-qat-UD-Q4_K_XL.gguf
  kimi-linear-49b: new author -> Kimi-Linear-49B-A3B-Q4_K_M.gguf  (Q4_K_M)
```

New entries are stamped with the `discovery:` defaults from settings.yaml
(`context_length` capped to the model's own maximum when the backend reports
it). Writes are line-oriented, so the config files keep their comments and
existing entries are never rewritten.

Judges are opt-in (`--role judge|both`) rather than automatic: the judge panel
determines self-judging bias and the ≥2-judges-per-skill invariant, so adding
one is a deliberate call. Review the diff either way: ids and quantization
labels are inferred from model names, and a 1-bit quant or a 4B model may not
belong in the panel at all.

### Running across both machines

One `results.sqlite` + `runs/` tree serves both boxes; document paths are
stored repo-relative, so syncing them is enough:

Author with an LM Studio model.

```shell
uv run eval generate
```

Validate, judge, and compare.

```shell
uv run eval validate && uv run eval judge && uv run eval compare
```

Dashboard shows both environments.

```shell
uv run eval report
```

Stages are idempotent, so the order is flexible. Each machine only
generates for authors that have a model on its backend, and judging/
validation run wherever the documents happen to be.

Documents that predate environment capture (schema < v3) land in an
"unrecorded environment" bucket. `eval backfill-env` attributes them: it
uses the environment recorded in each document's manifest when present, and
with `--assume-current` it attributes the rest to the machine it runs on
(run it on the machine that authored them). Writing the environment back
into the manifest with an `environment_assumed: true` marker so the
attribution is visibly an assumption and survives a DB rebuild. The dashboard
shows a separate results section (charts, ranking, speed) per machine/backend,
so authors are compared within one environment; the "Generation environments"
table shows which machine authored what.

### Reports

`eval report` writes five self-contained pages (light/dark aware, no external
assets) to `reports/`:

| Page | Contents |
|---|---|
| `dashboard.html` | **The leaderboard.** Stat tiles (docs, authoring time, tokens, judgments, comparisons), then **one results section per environment/platform**: quality and tokens/sec bar charts plus a ranked table (model name, Elo-scaled BT rating, judge/deterministic scores, pass rate, time/doc, tok/s, tokens) computed from that environment's documents only, so authors compare on identical hardware and backend. Ranks by BT when comparisons exist, else judge mean, else deterministic mean; each section states which. When documents span environments, a flagged **All environments (pooled)** table follows. A **Generation environments** table lists each machine (hostname, OS, CPU, GPU, backend + version, doc count). |
| `leaderboard.html` | Raw Bradley–Terry ratings with bootstrap CIs, per environment (same-environment pairs only) plus the flagged pooled table |
| `matrix.html` | Author×judge heatmap + self-judging deltas |
| `criteria.html` | Per-author × per-skill mean/median/SD/CI, per environment |
| `violations.html` | Per-document deterministic violation drill-down |
| `agreement.html` | Krippendorff's α, deviation from panel median, swap consistency |

### Human calibration

```shell
$ uv run eval calibrate sample --pct 10    # stratified forms → calibration/
# fill in the 0–10 scores in calibration/form-*.md, then:
$ uv run eval calibrate import --reviewer alex
$ uv run eval calibrate correlate          # Spearman ρ per judge × skill
```

Low correlation for a judge means its scores shouldn't be trusted for that
skill. Drop it from `judges.yaml` or fix the skill prompt.

### Drift detection

`fixtures/regression.yaml` freezes documents (a hand-written golden doc, a
seeded-bad doc) with expected score bands per skill. After **any** change to
a skill, rubric, judge model, or LM Studio itself:

```shell
$ uv run eval regress          # exits 1 if any judge leaves its band
OK    judge-qwen3.6-27b   style-guide    fixtures/golden.md   score=9 band=[8,10]
DRIFT judge-qwen3.5-9b    factuality     fixtures/bad.md      score=7 band=[0,4]
```

## End-to-end example run

A complete walk-through (shown on the Mac/LM Studio backend. On the Linux
server the flow is identical minus the `lms server start`, since the
pipeline manages the llama.cpp container itself), from clean checkout to
leaderboard. Model-load times dominate; the full author × 10-prompt matrix
is an unattended multi-hour run, so start small:

```shell
# 0. Environment
$ lms server start --port 1234 && ./scripts/doctor.sh
$ uv run pytest -m "not integration"       # 36 passed

# 1. Generate one document with the smallest author
$ uv run eval generate --authors lfm2.5-8b --prompts tut-python-json
... INFO eval_pipeline.generate: lfm2.5-8b: generating 1 document(s)
... INFO eval_pipeline.generate: tut-python-json/lfm2.5-8b done in 50.3s (3042 tokens)
Generated 1 document(s).

# The artifact and its provenance:
$ ls runs/tut-python-json/lfm2.5-8b/
manifest.json  output.md
$ cat runs/tut-python-json/lfm2.5-8b/manifest.json
{
  "prompt_id": "tut-python-json",
  "author_id": "lfm2.5-8b",
  "model": "lfm2.5-8b-a1b-mlx",
  "backend": "lmstudio",
  "environment": {
    "hostname": "…", "os": "macOS", "os_version": "…",
    "arch": "arm64", "cpu": "Apple M5", "gpu": "Apple M5 GPU (unified memory)",
    "backend": "lmstudio", "backend_version": "…"
  },
  "temperature": 0.7,
  "seed": 42,
  "prompt_hash": "…",
  "content_hash": "…",
  "gen_time_s": 50.32,
  "prompt_tokens": 267,
  "completion_tokens": 3042,
  ...
}

# Idempotency: re-running is a no-op (hash-matched)
$ uv run eval generate --authors lfm2.5-8b --prompts tut-python-json
... lfm2.5-8b: all prompts up to date, skipping model load
Generated 0 document(s).

# 2. Deterministic validation (real output from this document):
$ uv run eval validate
Recorded 6 deterministic result(s).
# per-tool rows now in det_results:
#   markdownlint  passed=0  score=2.61   (heading/format violations)
#   codespell     passed=1  score=10.0
#   lychee        passed=1  score=10.0
#   vale          passed=0  score=1.92   (style-guide violations)
#   code-runner   passed=0  score=1.11   (most python blocks fail to execute!)
#   readability   passed=1  score=14.23  (Flesch-Kincaid grade)

# 3. LLM judging + pairwise comparisons (loads each judge model once):
$ uv run eval judge
$ uv run eval compare

# 4. Analysis
$ uv run eval analyze
== Generation cost (per author) ==
author           docs   time_s  prompt_tok  compl_tok   tok/s
lfm2.5-8b           1     50.3         267       3042    60.4
== Score aggregates (author x skill) ==
...
$ uv run eval rank
== Bradley-Terry leaderboard ==
rank  author            rating              95% CI     n
...
NOTE: top-2 confidence intervals overlap with no statistically
distinguishable winner.        # ← it refuses to over-claim

# 5. Reports
$ uv run eval report
wrote reports/dashboard.html   # ← open this one
...

# 6. Scale up to the full matrix (unattended, hours):
$ uv run eval generate && uv run eval validate && uv run eval judge && uv run eval compare
$ uv run eval report

# 7. Guard rails
$ uv run eval regress                        # judge drift check
$ uv run eval calibrate sample --pct 10      # human anchor
```

Cost accounting for step 1, as recorded: 50.3 s wall clock, 267 prompt
tokens, 3,042 completion tokens (60.4 tok/s) visible in `eval analyze`
and on the dashboard tiles.

## Notes & gotchas

- **LM Studio port.** This pipeline expects **1234** (`config/settings.yaml`).
  Start with `--port 1234` or change the config. `doctor.sh` catches
  the mismatch.

- **llama.cpp port.** The compose file publishes llama-server on **8080**;
  that's also LM Studio's default port, another reason the Mac must run LM
  Studio on 1234.

- **Models drive.** `/mnt/data-one/llama-models` must be a mounted volume
  with the gguf files in it. An empty mount point makes every llamacpp
  model swap fail. `doctor.sh` warns when the directory is empty and
  nothing is mounted there.

- **Model swaps recreate the container.** On llamacpp each model change is a
  `docker compose up -d --force-recreate` plus a gguf load into VRAM
  (`--mlock --no-mmap`), which can take minutes from spinning storage, 
  hence `load_timeout: 900`. Anything else using the GPU or port 8080 will
  fight with it.

- **Memory.** The big models need the machine to themselves (unified memory
  on the Mac, 24 GB VRAM on the 3090). The pipeline handles this
  (sequential load/swap), but don't run two stages in parallel.

- **Code-runner sandbox** is best-effort: tempdir cwd/HOME, hard timeout, and
  proxy variables black-holed to block accidental network use. It is not a
  container; do not put untrusted prompts in the dataset. Docker isolation is
  on the backlog.

- **`runs/`, `reports/`, `results.sqlite` are gitignored**: The DB is
  rebuildable from `runs/` manifests with `eval generate` (it re-registers
  existing documents).

- The original content-authoring skills that predate this pipeline live in
  `authoring-skills/` (the `skills/` directory is reserved for evaluators).

## Roadmap

Remaining work: full-matrix run, one human calibration round (WU-5.1),
then scale-out (25–30 prompts, temperature/quantization sweeps, WU-5.3).
Backlog: Docker-isolated code execution, embedding similarity to reference
docs, Elo over time, a cloud judge (Claude) as calibration anchor,
doc-type-conditional leaderboards.

## AI use disclaimer

The author builds and manages this codebase with the support of coding agents.

## License

[BSD 2-Clause](LICENSE)
