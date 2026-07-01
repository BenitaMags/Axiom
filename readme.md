# AXIOM — Adaptive eXecution & Import Optimization Module

> Multi-Agent OS-Loader Simulation · LangGraph + Ollama/Claude/NVIDIA

---

## What Is AXIOM?

AXIOM is a multi-agent AI system that simulates the behavior of an **OS-level dynamic loader** — but for Python source files instead of binary executables.

When an OS loader runs a binary, it reads the dependency manifest, checks available libraries, resolves which shared libraries serve the same purpose, measures load costs, and patches the binary to use the optimal one. AXIOM does the exact same thing for Python:

| OS Loader Phase               | AXIOM Agent        | Method                          |
|-------------------------------|--------------------|---------------------------------|
| Read ELF header / .dynamic    | `parser_agent`     | Python `ast` module             |
| Deterministic library checks  | `rules_agent`      | importlib + metadata            |
| Security validation           | `security_agent`   | OSV API + PyPI                  |
| Symbol resolution             | `resolver_agent`   | LLM reasoning                   |
| Library load cost profiling   | `profiler_agent`   | subprocess timing               |
| Human review gate             | `approval_gate`    | LangGraph `interrupt()` + diff  |
| GOT patching / relocation     | `axiom_agent`      | AST rewriting                   |
| Compatibility shim generation | `connector_agent`  | LLM-generated adapter code      |

Inspired by **CodeAugur** (Professor Agadakos's binary semantic equivalence analyzer): AXIOM determines functional equivalence between Python packages the same way CodeAugur determines binary similarity across compiler optimization levels — capability/task equivalence, not syntactic API compatibility.

---

## Pipeline

```
  [parser_agent]
        │         Reads the .py file, extracts every import statement
        │         and all call sites via AST. No LLM.
        │
        ├── no imports or file error ──► END
        │
  [rules_agent]
        │         Deterministic checks: is_installed, has_metadata,
        │         license_permissive, api_surface_overlap,
        │         not_transitive_dependency (never groups parent/child
        │         packages like FastAPI/Starlette as alternatives).
        │         Disqualifies unavailable packages before any LLM call.
        │
  [security_agent]
        │         Queries OSV API for CVEs per package+version.
        │         Queries PyPI for release freshness and yanked versions.
        │         Parallel HTTP — no LLM, no extra dependencies.
        │
  [resolver_agent]
        │         LLM call #1. Groups packages into equivalence clusters:
        │         sets of imports that serve the same functional role.
        │         Identifies migration pitfalls between equivalents.
        │         Uses core/llm_json.py to parse output — correctly
        │         strips <think> blocks from reasoning models (NVIDIA
        │         Nemotron, DeepSeek-R1) before JSON extraction.
        │
        ├── no equivalence groups found ──► END
        │
  [profiler_agent]
        │         Benchmarks each candidate in a fresh subprocess:
        │         import time (ms, averaged over 3 runs) + memory (KB) +
        │         runtime API benchmarks where snippets are available.
        │
  [axiom_agent]
        │         LLM call #2. Scores all candidates across speed, memory,
        │         security, API compatibility. Picks winner per group.
        │         Applies crypto-veto correctly: only blocks non-crypto
        │         hash packages (xxhash, mmh3) when the resolver
        │         explicitly identifies a security-sensitive role — NOT
        │         just because hashlib.md5 appears in the import list.
        │
        ├── (require_approval=True) ──► [approval_gate]
        │                                     │
        │         LangGraph interrupt() — graph genuinely pauses here.
        │         Shows proposed changes table + per-file unified diff.
        │         Human types a/r/1,2,3 to accept/reject before ANY
        │         source file is touched. MemorySaver checkpointer
        │         persists state so resume_loader() continues from
        │         exactly this point.
        │                                     │
        ├── (no approval gate) ──────────────►│
        │                                     │
  [connector_agent]
        │         LLM call #3. For each replaced package whose API
        │         differs from the original, generates a thin inline
        │         wrapper function injected directly into the patched
        │         source. Call sites are untouched — they call the
        │         wrapper, which handles the API translation.
        │
       END
```

---

## Project Structure

```
axiom_v2/
├── pyproject.toml
├── .env                           ← API keys (never commit this)
└── smart_loader/
    ├── cli.py                     Typer CLI entry point
    ├── __main__.py
    │
    ├── core/
    │   ├── state.py               All dataclasses + LoaderState TypedDict
    │   ├── graph.py               LangGraph StateGraph builder + run_loader()
    │   ├── llm_factory.py         Single source of truth for LLM instantiation
    │   ├── llm_json.py            ← NEW: robust JSON extraction for reasoning models
    │   ├── feedback_loop.py       ← REWRITTEN: all human-in-the-loop logic
    │   │                            run_feedback_loop()      single-file post-pipeline review
    │   │                            approval_gate_node()     LangGraph interrupt() node
    │   │                            scan_dir_approval_prompt() per-file diffs + y/n gate
    │   ├── diff_reporter.py       DiffReporter + PerfReporter + AxiomReport
    │   │                            (diff/perf superclass — professor's concept)
    │   ├── constraints.py         Crypto-veto + connector-required inference
    │   ├── ast_rewrite.py         ImportRewriter AST node transformer
    │   ├── rules_engine.py        Deterministic rule registry (@rule decorator)
    │   ├── token_tracker.py       ← NEW: per-LLM-call JSONL token log
    │   └── telemetry.py           Session lean log + content-addressed detail store
    │
    ├── agents/
    │   ├── parser_agent.py        Node 1 — AST import + call-site extraction
    │   ├── rules_agent.py         Node 2 — deterministic validation
    │   ├── security_agent.py      Node 3 — OSV + PyPI CVE scan
    │   ├── resolver_agent.py      Node 4 — LLM equivalence clustering
    │   ├── profiler_agent.py      Node 5 — subprocess benchmarking
    │   ├── axiom_agent.py         Node 6 — scoring + deferred AST rewrite
    │   └── connector_agent.py     Node 7 — inline API adapter codegen
    │
    ├── dashboard/
        └── app.py                 FastAPI token dashboard (port 7788)
```

---

## Installation

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running locally (for the default local LLM)
- Git

### 1. Clone and install

```bash
git clone <your-repo-url>
cd axiom_v2
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
pip install -e .
pip install python-dotenv        # required for .env key loading
```

### 2. Set your API key

Create a `.env` file in the project root — this is the reliable way to set API keys regardless of which terminal or tool launches axiom:

```bash
# For Claude
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# For NVIDIA NIM
echo 'NVIDIA_API_KEY=nvapi-...' >> .env
```

Never export keys in one terminal and expect them to appear in another session, VS Code's integrated terminal, or tools like Cursor/Antigravity. The `.env` file is read by the Python process itself on every run.

### 3. Pull a local model (optional)

```bash
ollama serve
ollama pull mistral        # fast, works for most experiments
ollama pull qwen3-coder-next  # stronger reasoning, slower
```

---

## Supported LLM Providers

| Provider | `--llm` flag | Required env var | Notes |
|---|---|---|---|
| Ollama (local) | `ollama` | — | Default. `ollama pull <model>` first |
| Anthropic Claude | `claude` | `ANTHROPIC_API_KEY` | Recommended for best results |
| NVIDIA NIM | `nvidia` | `NVIDIA_API_KEY` | Supports Nemotron reasoning models |
| OpenAI | `openai` | `OPENAI_API_KEY` | |
| Any OpenAI-compatible | `openai-compat` | `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL` | vLLM, LM Studio, etc. |

**Reasoning models** (Nemotron Ultra/Super, DeepSeek-R1, QwQ) are automatically detected by name. AXIOM raises their `max_tokens` budget to 16,384 and passes `enable_thinking: False` to NVIDIA's endpoint — preventing chain-of-thought from leaking into the JSON output and causing "all imports already optimal" false negatives.

---

## CLI Reference

### Analyze a single file

```bash
axiom run path/to/script.py

# Full options
axiom run path/to/script.py \
  --llm claude \
  --model claude-sonnet-4-6 \
  --save \                        # write patched file + versioned diff/perf report
  --feedback \                    # pause for human accept/reject before saving
  --non-interactive \             # auto-accept (for CI)
  --no-patch \                    # suppress patched source printout
  --no-security \                 # skip OSV/PyPI network calls (faster)
  --no-connector \                # skip adapter shim generation
  --output path/to/output.py      # custom output path
```

### Scan a multi-file project

```bash
axiom scan-dir path/to/project/

# Full options
axiom scan-dir path/to/project/ \
  --llm nvidia \
  --model nvidia/nemotron-3-ultra-550b-a55b \
  --save \                        # write all modified files after approval
  --no-approve \                  # skip approval gate (use after dry-run review)
  --non-interactive \             # auto-accept without prompting (CI)
  --exclude "test_*.py,*_optimized.py" \
  --no-security \
  --no-connector

# Dry run (no --save): see all decisions without touching any file
axiom scan-dir path/to/project/ --llm claude --model claude-sonnet-4-6
```

`scan-dir` runs the full pipeline per file, then reconciles decisions **project-wide** — if `pandas → polars` wins in `file_a.py`, the same replacement is applied in `file_b.py` too, so the project converges on one consistent dependency choice.

With `--save` (the default has `--approve` ON), AXIOM shows:
1. The project-wide decisions table
2. A per-file unified diff for every file that would be modified
3. A single `y/n` prompt — nothing is written until you type `y`

### Version history and diffs

```bash
# List all saved versions of a file
axiom versions path/to/script.py

# Show unified diff between any two versions
axiom diff path/to/script.py --from 1 --to 2

# Two identical versions produce an empty diff (null diff)
# — mirrors `diff` Linux behavior, as per the diff/perf superclass design
```

### Security scan

```bash
axiom security requests      # scan one package against OSV + PyPI
axiom security numpy
```

### Other commands

```bash
axiom experiment             # run all experiment_libs files in sequence
axiom verify <session_id>    # check session log integrity (tamper detection)
axiom dashboard              # token usage dashboard at http://localhost:7788
axiom visualize              # print pipeline topology
```

---

## Human-in-the-Loop (Feedback Loop)

AXIOM implements human-in-the-loop at two levels, both backed by the same `core/feedback_loop.py` module.

### For `axiom run` — `--feedback` flag

```bash
axiom run myfile.py --llm claude --model claude-sonnet-4-6 --save --feedback
```

After scoring, before writing anything, AXIOM shows:
- A table of all proposed changes (role, original package, replacement, confidence, rationale)
- A coloured unified diff of the full source change
- A prompt: `a` accept all, `r` reject all, `1,2,3` reject specific changes by number

Then runs an **output equivalence check**: executes both the original and patched source in isolated subprocesses, compares stdout/stderr. If they produce identical output, prints `✓ EQUIVALENT` (null diff — same behavior as the professor's `diff original patched → empty`). If they differ, shows the output diff so you can decide.

### For `axiom scan-dir` — `--approve` flag (default ON)

The approval gate fires once for the whole project after all files have been analyzed. Nothing is written until you approve. It shows:
- The project-wide decisions table
- A full per-file unified diff for every file that would be modified
- `Write these N file(s)? [y/n]` — defaults to `n` (the safe default)

This is implemented as a genuine **LangGraph `interrupt()`** with `MemorySaver` checkpointing — not a UI prompt that runs after writes. The graph genuinely pauses before the AST rewrite node; `resume_loader()` with `Command(resume=...)` continues from the exact checkpoint.

---

## Diff and Version Tracking

AXIOM implements **diff/perf superclass** concept: `AxiomReport` inherits conceptually from both `diff` (structural change tracking) and `perf stat` (benchmark delta reporting).

Every `--save` run writes versioned records under `axiom_logs/versions/{filename}/`:

```
axiom_logs/
├── versions/
│   └── {filename}/
│       ├── v1_original.py           always the untouched source
│       ├── v{n}_patched.py          each --save creates a new version
│       ├── diff_v{n-1}_to_v{n}.txt  unified diff between consecutive versions
│       └── perf_report.json         import-time speedup ratios
├── sessions/
│   ├── {session_id}.json            lean log: decisions, hashes, integrity field
│   └── details/sha256_{h}.json      full decision content, keyed by hash
└── token_usage.jsonl                per-LLM-call token log (input, output, latency)
```

```bash
axiom versions myfile.py       # list all versions with +/- line counts
axiom diff myfile.py --from 1 --to 3    # compare any two versions
axiom verify <session_id>      # recompute integrity hash to detect tampering
```

---

## Scoring Formula

| Signal            | Weight | Source                              |
|-------------------|--------|-------------------------------------|
| Import speed      | 35%    | Profiler — fastest import wins      |
| Availability      | 20%    | Rules engine                        |
| Security          | 20%    | OSV overall score                   |
| API compatibility | 15%    | LLM-rated drop-in compatibility     |
| Memory footprint  | 10%    | Profiler — smallest peak memory     |

A package with a CRITICAL CVE gets security score ≈ 0.0 — a 20-point penalty that overrides speed advantages.

**Confidence levels:**
- `HIGH` — winner leads by > 0.15 score gap
- `MEDIUM` — winner leads but margin is close
- `LOW` — only one available candidate

**Active role separation**: if both `requests` and `httpx` have distinct active call sites in the same file (different functions use each), AXIOM correctly identifies them as serving complementary roles, not as alternatives — neither will be replaced.

**Crypto-veto**: non-cryptographic hash packages (`xxhash`, `mmh3`, `farmhash`) are excluded from groups where the resolver identifies a security-sensitive role. The veto only fires when the role is genuinely crypto-sensitive — it does NOT fire when the resolver explicitly notes the hash usage is non-security-sensitive (e.g. cache-key generation, deduplication checksums).

---

## Security Scanning

AXIOM queries two external APIs (no LLM, no extra dependencies):

- **OSV API** (`https://api.osv.dev`) — CVE lookup per package and installed version
- **PyPI JSON API** — release freshness, yanked version detection

Risk levels: `CRITICAL` · `HIGH` · `MEDIUM` · `LOW`

All scans run in parallel. Results feed into the scoring formula and appear in a dedicated summary table. A package with HIGH or CRITICAL risk has its score penalized and the resolver is told to prefer safer alternatives in the same equivalence group.

---

## Adapter Shims (Connector Agent)

When AXIOM replaces `fastapi` with `starlette`, existing code still calls `FastAPI(title=..., version=...)`. The connector agent generates a thin wrapper function — injected **inline** into the patched source — that bridges the API difference so existing call sites work unchanged.

```python
# AXIOM CONNECTOR: fastapi → starlette
# Injected inline by the connector agent
def _axiom_create_starlette_app(title: str = "", version: str = "", **kwargs):
    """Wraps Starlette() to accept FastAPI's constructor signature."""
    from starlette.applications import Starlette
    starlette_keys = {"debug", "routes", "middleware",
                      "exception_handlers", "on_startup", "on_shutdown"}
    return Starlette(**{k: v for k, v in kwargs.items() if k in starlette_keys})

app = _axiom_create_starlette_app(title="My App", version="1.0")
```

The connector agent also documents what would still break after the migration (e.g. `@app.get()` decorators, Pydantic body parsing, `Depends()` injection) so you know exactly what manual work remains.

---

## Telemetry & Audit

Every session produces three artefacts:

| File | Content |
|------|---------|
| `axiom_logs/sessions/{id}.json` | Lean log: event sequence, hashes, verdict, token usage |
| `axiom_logs/details/sha256_{h}.json` | Full decision content keyed by hash — includes `crypto_vetoed`, `used_apis`, `requires_connector` |
| `axiom_logs/token_usage.jsonl` | Per-LLM-call log: agent, model, provider, input/output tokens, latency |

The `integrity` field in the lean log is a SHA-256 computed over all referenced detail hashes. Recomputing it is sufficient to detect any post-hoc tampering.

```bash
axiom verify <session_id>
```

---

## Experiment Files

### Built-in single-file experiments (`experiment_libs/`)

| File | Equivalence Groups |
|------|-------------------|
| `http_experiment.py` | requests ≡ httpx |
| `json_experiment.py` | json ≡ ujson ≡ orjson |
| `dataframe_experiment.py` | pandas ≡ polars |
| `mixed_experiment.py` | all of the above combined |

### Multi-file project experiments (`multiple_experiment/`)

A realistic multi-module FastAPI project with files spread across `api/`, `models/`, `services/`, `utils/`, `config/` — demonstrating that AXIOM analyzes an entire project, not just one entry point. Use `axiom scan-dir` to analyze the whole project with one command.

### `cache_lib/` experiment

A purpose-built 4-file library (`client.py`, `storage.py`, `config.py`, `cli.py`) designed to exercise specific AXIOM behaviors:
- `client.py` — single-role `requests` → `httpx` replacement target
- `storage.py` — non-crypto hash veto test (`hashlib.md5` for cache keys, not security)
- `config.py` / `cli.py` — stdlib-only control files (AXIOM must leave these untouched)

```bash
cd cache_lib && pip install -e ".[candidates]"   # installs httpx, orjson, xxhash
axiom scan-dir cache_lib/ --llm claude --model claude-sonnet-4-6 --save
```

---

## Adding a Custom Rule

Rules run before any LLM call and never raise exceptions — always return `PASS`, `FAIL`, or `UNKNOWN`.

```python
from smart_loader.core.rules_engine import rule

@rule("my_rule", confidence="HIGH")
def _my_rule(package: str) -> tuple[str, dict]:
    try:
        return "PASS", {"detail": "..."}
    except Exception as e:
        return "UNKNOWN", {"error": str(e)}
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for `--llm claude` |
| `NVIDIA_API_KEY` | — | Required for `--llm nvidia` |
| `OPENAI_API_KEY` | — | Required for `--llm openai` |
| `AXIOM_LOGS_DIR` | `./axiom_logs` | Directory for all log output |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server address |

All variables are also read from a `.env` file in the project root (via `python-dotenv`). This is more reliable than shell `export` commands, which only live in one terminal session.

---

## Related Research

| Paper | Relation to AXIOM |
|---|---|
| PLLM (arXiv:2501.16191) | LLM dependency resolution — AXIOM optimizes, PLLM fixes |
| SMT-LLM (arXiv:2605.11772) | AST-based Python analysis, same approach |
| MemRes (arXiv:2604.16941) | Skips LLM for known packages — AXIOM does same in rules |
| COMPILOT (arXiv:2511.00592) | LLM + feedback loop optimization — same pattern |
| CodeAugur (Agadakos et al.) | Primary architectural reference — binary semantic equivalence; AXIOM applies the same reasoning to Python packages |
