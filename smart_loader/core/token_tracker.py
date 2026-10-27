"""
core/token_tracker.py
───────────────────────
Per-LLM-call token usage logging.

This module is referenced by resolver_agent.py, axiom_agent.py, and
connector_agent.py (each wraps the import in try/except so the pipeline
never crashes without it), and by cli.py's token-usage summary and the
`axiom dashboard` command. The README documents it as a real component
("core/token_tracker.py — Per-LLM-call token log (JSONL)") but the file
itself was missing from the codebase, so both features were silently
dead — `axiom run` prints nothing for "Token Usage (this run)" and
`axiom dashboard` fails to start.

Design mirrors CodeAugur's lean-log pattern used elsewhere in this
project: append-only JSONL, one line per LLM call, cheap to write and
to scan. get_summary() aggregates the CURRENT PROCESS's calls only —
each `axiom run` / `axiom scan-dir` invocation is a fresh Python
process, so "this run" naturally means "since this process started"
without needing to filter by session_id for the common case. A
session_id filter is still accepted for the dashboard, which reads
across runs from the JSONL file on disk.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

LOGS_DIR = Path(os.environ.get("AXIOM_LOGS_DIR", "./axiom_logs"))
TOKEN_LOG_PATH = LOGS_DIR / "token_usage.jsonl"

_lock = threading.Lock()

# In-process accumulator for "this run"'s summary — avoids re-reading the
# JSONL file (which may contain prior runs' calls too) every time cli.py
# wants to print a summary at the end of a single `axiom run` invocation.
_this_process_calls: list[dict] = []


@dataclass
class TokenUsageRecord:
    timestamp:     float
    session_id:    str
    agent:         str
    model:         str
    provider:      str
    input_tokens:  int
    output_tokens: int
    source_file:   str = ""
    latency_ms:    float = 0.0


def record_usage(
    session_id:    str,
    agent:         str,
    model:         str,
    provider:      str,
    input_tokens:  int,
    output_tokens: int,
    source_file:   str = "",
    latency_ms:    float = 0.0,
) -> None:
    """Append one LLM call's token usage to the JSONL log and the
    in-process accumulator used by get_summary()."""
    record = TokenUsageRecord(
        timestamp=time.time(),
        session_id=session_id,
        agent=agent,
        model=model,
        provider=provider,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        source_file=source_file,
        latency_ms=float(latency_ms or 0.0),
    )
    row = asdict(record)

    with _lock:
        _this_process_calls.append(row)
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(TOKEN_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            # Logging failures should never break the pipeline — token
            # tracking is observability, not a correctness requirement.
            pass


def get_summary(session_id: str | None = None) -> dict:
    """
    Aggregate token usage. With no session_id, summarizes every call made
    by THIS process (i.e. the current `axiom run` invocation) — this is
    what cli.py's "Token Usage (this run)" section calls. Pass a
    session_id to instead summarize a specific historical session by
    re-reading the JSONL log (used by the dashboard).
    """
    if session_id is None:
        rows = list(_this_process_calls)
    else:
        rows = [r for r in _read_all_rows() if r.get("session_id") == session_id]

    return _summarize(rows)


def get_all_summary() -> dict:
    """Summarize every call ever logged to the JSONL file (dashboard-wide view)."""
    return _summarize(_read_all_rows())


def _read_all_rows() -> list[dict]:
    if not TOKEN_LOG_PATH.exists():
        return []
    rows = []
    with open(TOKEN_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _summarize(rows: list[dict]) -> dict:
    by_agent: dict[str, dict] = {}
    total_input = 0
    total_output = 0

    for row in rows:
        agent = row.get("agent", "unknown")
        stats = by_agent.setdefault(agent, {"input": 0, "output": 0, "calls": 0})
        stats["input"]  += row.get("input_tokens", 0)
        stats["output"] += row.get("output_tokens", 0)
        stats["calls"]  += 1
        total_input  += row.get("input_tokens", 0)
        total_output += row.get("output_tokens", 0)

    return {
        "total_calls":  len(rows),
        "total_input":  total_input,
        "total_output": total_output,
        "by_agent":     by_agent,
    }


def reset_process_accumulator() -> None:
    """Clear the in-process accumulator. Mainly useful for tests."""
    with _lock:
        _this_process_calls.clear()
