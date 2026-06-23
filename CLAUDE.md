# AXIOM — Claude Code Reference

**Adaptive eXecution & Import Optimization Module**
A multi-agent LangGraph pipeline that analyzes Python source files, identifies redundant or dead imports, benchmarks equivalent packages, checks security vulnerabilities, and rewrites the AST to use the best-performing alternative.

---

## Architecture

The pipeline is a LangGraph `StateGraph` where every agent reads from and writes to a single shared `LoaderState` dict. The final state IS the output — fully auditable.

```
[parser_agent]        AST walk — no LLM
      │
[rules_agent]         deterministic checks — no LLM
      │
[security_agent]      OSV + PyPI CVE scan — no LLM, parallel HTTP
      │
[resolver_agent]      LLM call #1 — equivalence clustering
      │
      ├── no groups → END
      │
[profiler_agent]      subprocess benchmarking — no LLM
      │
[axiom_agent]         LLM call #2 — weighted scoring + AST rewrite
      │
[connector_agent]     LLM call #3 + #4 — API adapter shim generation
      │
     END
```

Entry point: `smart_loader/core/graph.py → run_loader()`

---

## Project Layout

```
smart_loader/
├── agents/
│   ├── parser_agent.py       Node 1 — AST import + call site extraction
│   ├── rules_agent.py        Node 2 — deterministic package validation
│   ├── security_agent.py     Node 3 — OSV/PyPI vulnerability scanning
│   ├── resolver_agent.py     Node 4 — LLM equivalence grouping
│   ├── profiler_agent.py     Node 5 — subprocess import benchmarking
│   ├── axiom_agent.py        Node 6 — scoring, decision, AST rewrite
│   └── connector_agent.py    Node 7 — API adapter shim codegen
├── core/
│   ├── graph.py              LangGraph StateGraph builder + run_loader()
│   ├── state.py              All dataclasses: ImportInfo, EquivalenceGroup,
│   │                         BenchmarkResult, RuleResult, LoadDecision,
│   │                         AgentEvent, LoaderState
│   ├── rules_engine.py       Rule registry + built-in rules
│   ├── token_tracker.py      JSONL token usage log + get_summary()
│   └── telemetry.py          Session lean log + content-addressed detail store
├── dashboard/
│   └── app.py                FastAPI dashboard (localhost:7788)
cli.py                        Typer CLI — run / dashboard / security / verify / experiment / visualize
experiment_libs/              Test .py files: http, json, dataframe, mixed
axiom_logs/                   Runtime artefacts (gitignore this)
axiom_connectors/             Generated adapter shims (gitignore this)
```

---

## Core Data Models (`smart_loader/core/state.py`)

These flow through every agent via `LoaderState`. Never rename fields — agents key into the dict by name.

| Class | Produced by | Key fields |
|-------|-------------|------------|
| `ImportInfo` | parser_agent | `module`, `alias`, `names`, `line`, `api_calls` |
| `EquivalenceGroup` | resolver_agent | `role`, `candidates`, `used_apis`, `reasoning`, `pitfalls` |
| `BenchmarkResult` | profiler_agent | `package`, `import_time_ms`, `memory_kb`, `available`, `error` |
| `RuleResult` | rules_agent | `rule`, `package`, `result` (PASS/FAIL/UNKNOWN), `confidence` |
| `SecurityResult` | security_agent | `package`, `version`, `vulnerabilities`, `overall_score`, `risk_level` |
| `LoadDecision` | axiom_agent | `role`, `winner`, `original`, `score`, `rationale`, `confidence` |
| `AgentEvent` | all agents | `stage`, `event`, `detail` |

**`LoaderState` keys** (the full dict passed through the graph):
```
source_file, llm_provider, model, _session_id,
source_code, imports, rule_results, security_results,
equivalence_groups, benchmarks, decisions, patched_code,
connectors, agent_trace, messages, error, done
```

---

## Adding a New Agent

1. Create `smart_loader/agents/my_agent.py` — function signature must be `def my_agent(state: dict) -> dict`
2. Return only the keys your agent writes; LangGraph merges them into state
3. Always append to `agent_trace`:
   ```python
   trace = list(state.get("agent_trace", []))
   trace.append(AgentEvent(stage="my_agent", event="started"))
   # ... work ...
   trace.append(AgentEvent(stage="my_agent", event="completed", detail={...}))
   return {"my_output": result, "agent_trace": trace, "messages": [...]}
   ```
4. Register the node in `smart_loader/core/graph.py`:
   ```python
   graph.add_node("my_agent", my_agent)
   graph.add_edge("axiom", "my_agent")   # insert at the right position
   graph.add_edge("my_agent", END)
   ```
5. Add the new output key to `run_loader()`'s initial state dict

---

## Adding a New Rule (`smart_loader/core/rules_engine.py`)

Rules run deterministically before any LLM. They never crash the pipeline — always return PASS/FAIL/UNKNOWN.

```python
@rule("my_rule", confidence="HIGH")
def _my_rule(package: str) -> tuple[str, dict]:
    # return ("PASS" | "FAIL" | "UNKNOWN", detail_dict)
    try:
        ...
        return "PASS", {"detail": "..."}
    except Exception as e:
        return "UNKNOWN", {"error": str(e)}
```

Built-in rules: `is_installed`, `has_metadata`, `license_permissive`, `api_surface_overlap`

Packages that FAIL `is_installed` are disqualified before the resolver LLM call.

---

## Scoring Formula (axiom_agent.py)

```
total_score = 0.35 × speed_score        # import_time_ms — fastest = 1.0
            + 0.10 × memory_score       # memory_kb — smallest = 1.0
            + 0.20 × availability_score # always 1.0 if available
            + 0.15 × api_match_score    # LLM-rated drop-in compatibility
            + 0.20 × security_score     # OSV overall_score (1.0=clean, 0.0=critical CVE)
```

Confidence: `HIGH` if winner gap > 0.15, `MEDIUM` if close, `LOW` if only one available candidate.

To adjust weights, edit the `W_*` constants at the top of `axiom_agent.py`. They must sum to 1.0.

---

## LLM Providers

Set in `run_loader()` via `llm_provider` and `model` args, or via CLI `--llm` / `--model`.

| Provider | `llm_provider` value | Notes |
|----------|---------------------|-------|
| Anthropic | `"claude"` | Requires `ANTHROPIC_API_KEY` env var |
| Ollama | `"ollama"` | Default; requires local Ollama running |

LLM factory is in both `resolver_agent.py` and `axiom_agent.py` — `_get_llm(provider, model)`. To add a provider, update both.

Default model: `qwen3-coder-next` (Ollama), `claude-sonnet-4-20250514` (Claude fallback).

---

## Token Tracking

Every LLM call records to `axiom_logs/token_usage.jsonl` via `smart_loader/core/token_tracker.py`.

```python
from smart_loader.core.token_tracker import record_usage, get_summary

# After an LLM call:
usage = getattr(response, "usage_metadata", None) or {}
record_usage(
    session_id=session_id, agent="my_agent",
    model=model, provider=provider,
    input_tokens=usage.get("input_tokens", 0),
    output_tokens=usage.get("output_tokens", 0),
    latency_ms=latency_ms,
)

# Get aggregated stats:
summary = get_summary()
# → {"total_tokens": ..., "by_agent": {"resolver": {"input": ..., "output": ..., "calls": ...}}}
```

**Any new LLM call must call `record_usage`** — otherwise it won't appear in the dashboard.

Agents tracked: `resolver`, `axiom`, `connector`

---

## Security Agent

Queries two external APIs (no LLM, no new pip deps — stdlib `urllib.request` only):
- **OSV API** (`https://api.osv.dev/v1/querybatch`) — CVE lookup by package + version
- **PyPI JSON API** (`https://pypi.org/pypi/{package}/json`) — version, freshness, yanked status

`SecurityResult.overall_score` = `0.7 × vuln_score + 0.3 × freshness_score`

Risk levels: `CRITICAL` (<0.30 or any CVSS CRITICAL), `HIGH` (<0.55), `MEDIUM` (<0.75), `LOW` (≥0.75)

Disable per-run: `axiom run file.py --no-security` or `run_loader(..., enable_security=False)`

---

## Connector Agent

Generates a Python adapter shim when `winner != original`. Two LLM calls per replacement:
1. **Shim generation** — produces `axiom_{original}_compat.py` matching the original's exact call signatures
2. **Source transform** — rewrites the target file's imports to use the shim

Both calls are attributed to agent `"connector"` in the token log (not separate entries).

Disable: `axiom run file.py --no-connector` or `run_loader(..., enable_connector=False)`

Shims saved to disk: `axiom run file.py --save-connectors [--connector-dir ./path]`

---

## Dashboard

```bash
axiom dashboard            # http://localhost:7788
axiom dashboard --port 9000
```

The URL is only printed at the end of `axiom run` when `--dashboard` flag is passed.

Charts: token timeline, input/output by agent, token distribution doughnut, calls per agent, tokens per session, input/output ratio radar.

API endpoints: `GET /api/tokens`, `GET /api/sessions`, `GET /health`

---

## CLI Reference

```bash
axiom run <file>                          # full pipeline
  --llm ollama|claude                     # LLM provider (default: ollama)
  --model <name>                          # model name
  --no-security                           # skip OSV/PyPI scan
  --no-connector                          # skip adapter shim generation
  --save-connectors                       # write shim .py files to disk
  --connector-dir ./path                  # where to save shims (default: ./axiom_connectors)
  --dashboard                             # print dashboard URL after run
  --save / -s                             # save patched source file
  --output / -o <path>                    # output path for saved file
  --no-patch                              # suppress patched source printout

axiom security <package>                  # one-off CVE scan
axiom dashboard [--port N] [--host H]    # start dashboard server
axiom verify <session_id>                 # verify session integrity
axiom experiment [--llm] [--model]        # batch run all experiment_libs/*.py
axiom visualize                           # print pipeline topology
```

---

## Telemetry & Audit

Two-layer storage (mirrors CodeAugur's design):

```
axiom_logs/
├── sessions/{id}.json          lean log: event sequence + hashes + verdict
├── sessions/{id}_patched.py    the rewritten source code
├── details/sha256_{h}.json     full decision content, keyed by hash
└── token_usage.jsonl           append-only per-LLM-call token log
```

The `integrity` field in the lean log is SHA-256 over all detail hashes — recomputing it detects tampering.

```bash
axiom verify <session_id>
```

---

## Environment Variables

| Variable | Default | Required for |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | `--llm claude` |
| `AXIOM_LOGS_DIR` | `./axiom_logs` | All log output |

---

## Dependencies

Core: `langgraph`, `langchain-core`, `langchain-anthropic`, `langchain-ollama`, `rich`, `typer`
Dashboard: `fastapi`, `uvicorn`
Security: stdlib only (`urllib.request`)
Benchmarking: stdlib only (`subprocess`, `tracemalloc`)

---

## Conventions

- **Agent functions** always accept `state: dict` and return `dict` — never mutate state in place
- **`agent_trace`** must be copied (`list(state.get("agent_trace", []))`) before appending — LangGraph merges by replacement not mutation
- **Rules** return `("PASS"|"FAIL"|"UNKNOWN", detail_dict)` — never raise exceptions
- **Token tracking** — every LLM call gets a `record_usage()` call immediately after, attributed to the correct agent name
- **Connector agent** — both internal LLM calls use `agent="connector"` (not `"connector_transform"`)
- **Security scores** flow from `security_results` dict in state, keyed by top-level package name (e.g. `"requests"` not `"requests.adapters"`)
- **`_session_id`** is set by `run_loader()` at graph invocation and passed through state — use it in token tracking calls
