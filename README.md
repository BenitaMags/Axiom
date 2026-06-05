# AXIOM - Adaptive eXecution & Import Optimization Module

> Multi-Agent OS-Loader Simulation · LangGraph + Ollama/Claude

---

## What Is AXIOM?

AXIOM is a multi-agent AI system that simulates the behavior of an **OS-level dynamic loader** — but for Python source files instead of binary executables.

When an OS loader runs a binary, it reads the dependency manifest, checks available libraries, resolves which shared libraries serve the same purpose, measures load costs, and patches the binary to use the optimal one. AXIOM does the exact same thing for Python:

| OS Loader Phase                  | AXIOM Agent          | Method                        |
|----------------------------------|----------------------|-------------------------------|
| Read ELF header / .dynamic       | `parser_agent`       | Python `ast` module           |
| Deterministic library checks     | `rules_agent`        | importlib + metadata          |
| Security validation              | `security_agent`     | OSV API + PyPI                |
| Symbol resolution                | `resolver_agent`     | LLM reasoning                 |
| Library load cost profiling      | `profiler_agent`     | subprocess timing              |
| GOT patching / relocation        | `axiom_agent`        | AST rewriting                 |
| Compatibility shim generation    | `connector_agent`    | LLM-generated adapter code    |

Given a Python file, AXIOM will tell you which of your imports are redundant, which alternatives are faster and safer, and rewrite your source to use them — with generated adapter code so existing call sites work unchanged.

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
        │         Runs deterministic checks on every package:
        │         is it installed? does it have metadata? permissive license?
        │         API surface overlap? Disqualifies unavailable packages.
        │
  [security_agent]
        │         Queries OSV API for CVEs per package+version.
        │         Queries PyPI for release freshness and yanked versions.
        │         Parallel HTTP — no LLM, no new dependencies.
        │
  [resolver_agent]
        │         LLM call #1. Groups packages into equivalence clusters:
        │         sets of imports that serve the same functional role.
        │         Also identifies migration pitfalls between equivalents.
        │
        ├── no equivalence groups found ──► END
        │
  [profiler_agent]
        │         Benchmarks each candidate in a fresh subprocess:
        │         import time (ms, averaged over 3 runs) + memory (KB).
        │
  [axiom_agent]
        │         LLM call #2. Scores all candidates with a weighted formula
        │         across speed, memory, availability, API compatibility,
        │         and security. Picks a winner per group with a confidence
        │         level. Rewrites the source AST. Saves a telemetry session.
        │
  [connector_agent]
        │         LLM calls #3 and #4. For each replaced package, generates
        │         a thin Python adapter module (axiom_{pkg}_compat.py) that
        │         wraps the new package to match the original's call signatures.
        │         Then rewrites the source imports to use the adapter.
        │
       END
```

---

## Project Structure

```
axiom_v2/
├── pyproject.toml
└── smart_loader/
    ├── cli.py                         Typer CLI entry point
    ├── __main__.py
    │
    ├── core/
    │   ├── state.py                   All dataclasses + LoaderState
    │   ├── graph.py                   LangGraph StateGraph builder
    │   ├── rules_engine.py            Deterministic rule registry
    │   ├── token_tracker.py           Per-LLM-call token log (JSONL)
    │   └── telemetry.py               Session lean log + integrity hash
    │
    ├── agents/
    │   ├── parser_agent.py            Node 1 — AST import extraction
    │   ├── rules_agent.py             Node 2 — deterministic validation
    │   ├── security_agent.py          Node 3 — OSV + PyPI CVE scan
    │   ├── resolver_agent.py          Node 4 — LLM equivalence clustering
    │   ├── profiler_agent.py          Node 5 — subprocess benchmarking
    │   ├── axiom_agent.py             Node 6 — scoring + AST rewrite
    │   └── connector_agent.py         Node 7 — API adapter shim codegen
    │
    ├── dashboard/
    │   └── app.py                     FastAPI token dashboard (port 7788)
    │
    └── experiment_libs/
        ├── http_experiment.py         requests vs httpx vs urllib3
        ├── json_experiment.py         json vs ujson vs orjson
        ├── dataframe_experiment.py    pandas vs polars
        └── mixed_experiment.py        all categories combined
```

---

## Installation

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running locally (for the default local LLM)
- Git

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd axiom_v2
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows
```

### 3. Install AXIOM

```bash
pip install .
```

This registers the `axiom` command globally in your environment. Verify:

```bash
axiom --help
```

### 4. Install the default LLM model (Ollama)

```bash
# Start Ollama if it isn't already running
ollama serve

# Pull the default model
ollama pull qwen3-coder-next
```

Any Ollama model that supports tool calling works (`llama3.1`, `llama3.3`, `mistral`, `qwen2.5`).

### 5. (Optional) Set up Claude

If you want to use Anthropic's Claude instead of Ollama:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or pass it directly: `axiom run file.py --llm claude --api-key sk-ant-...`

### 6. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Usage

### Analyze a Python file

```bash
axiom run path/to/script.py
```

```bash
# Save the rewritten file
axiom run path/to/script.py --save

# Specify output path
axiom run path/to/script.py --save --output path/to/optimized.py

# Write generated adapter shims to disk
axiom run path/to/script.py --save-connectors --connector-dir ./my_connectors

# Show the dashboard URL after the run
axiom run path/to/script.py --dashboard

# Or use this command to see the dashboard after the run
axiom dashboard

# Disable security scan (faster, skips OSV/PyPI network calls)
axiom run path/to/script.py --no-security

# Disable connector generation (skips adapter shim codegen)
axiom run path/to/script.py --no-connector

# Use Claude instead of Ollama
axiom run path/to/script.py --llm claude --model claude-sonnet-4-20250514
```

### One-off security scan

```bash
axiom security requests
axiom security numpy
axiom security pillow
```

### Token usage dashboard

```bash
# Start the dashboard server
axiom dashboard               # http://localhost:7788
axiom dashboard --port 9000   # custom port
```

### Verify a session's integrity

```bash
axiom verify <session_id>
```

### View pipeline topology

```bash
axiom visualize
```

---

## Scoring Formula

Every candidate package in an equivalence group receives a weighted score:

| Signal              | Weight | Source                         |
|---------------------|--------|--------------------------------|
| Import speed        | 35%    | Profiler — fastest import wins |
| Availability        | 20%    | Rules engine                   |
| Security            | 20%    | OSV overall score              |
| API compatibility   | 15%    | LLM-rated drop-in score        |
| Memory footprint    | 10%    | Profiler — smallest wins       |

A package with a CRITICAL CVE gets a security score near 0.0 — a 20-point penalty that overrides speed advantages.

**Confidence levels:**
- `HIGH` — winner leads by more than 0.15 score gap
- `MEDIUM` — winner leads but margin is close
- `LOW` — only one available candidate

---

## Security Scanning

AXIOM queries two external APIs (no LLM, no extra dependencies):

- **OSV API** (`https://api.osv.dev`) — CVE lookup per package and version
- **PyPI JSON API** — release freshness, yanked versions

Risk levels: `CRITICAL` · `HIGH` · `MEDIUM` · `LOW`

All package scans run in parallel. Results are factored into the scoring formula and printed in a dedicated summary table at the end of each run.

---

## Telemetry & Audit

Every run saves two artefacts:

```
axiom_logs/
├── sessions/{session_id}.json        lean log: event sequence + hashes + verdict
├── sessions/{session_id}_patched.py  the rewritten source
├── details/sha256_{hash}.json        full decision content per equivalence group
└── token_usage.jsonl                 per-LLM-call token log
```

The `integrity` field in the session log is a SHA-256 computed over all detail hashes. Recomputing it detects any post-hoc tampering.

```bash
axiom verify <session_id>
```

---

## Adding a Custom Rule

Rules run before any LLM call. They never raise exceptions — always return `PASS`, `FAIL`, or `UNKNOWN`.

```python
from smart_loader.core.rules_engine import rule

@rule("my_rule", confidence="HIGH")
def _my_rule(package: str) -> tuple[str, dict]:
    try:
        # your check here
        return "PASS", {"detail": "..."}
    except Exception as e:
        return "UNKNOWN", {"error": str(e)}
```

---

## Environment Variables

| Variable            | Default          | Description                        |
|---------------------|------------------|------------------------------------|
| `ANTHROPIC_API_KEY` | —                | Required when using `--llm claude` |
| `AXIOM_LOGS_DIR`    | `./axiom_logs`   | Directory for all log output       |

---

## Related Research

| Paper                       | Relation to AXIOM                                        |
|-----------------------------|----------------------------------------------------------|
| PLLM (arXiv:2501.16191)     | LLM dependency resolution — AXIOM optimizes, PLLM fixes  |
| SMT-LLM (arXiv:2605.11772)  | AST-based Python analysis, same approach                 |
| MemRes (arXiv:2604.16941)   | Skips LLM for known packages — AXIOM does same in rules  |
| COMPILOT (arXiv:2511.00592) | LLM + feedback loop optimization — same pattern          |
