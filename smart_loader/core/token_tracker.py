"""
core/token_tracker.py
──────────────────────
Tracks LLM token usage across all agents and pipeline runs.

INSPIRED BY CodeAugur's token_usage field in results:
  CodeAugur records per-stage token counts (tool_caller, analyst, verdict)
  in every session result. We extend this into a persistent time-series
  store that powers the /dashboard endpoint.

Storage: append-only JSONL file — one record per LLM call.
The dashboard endpoint reads this file and serves aggregated stats.
"""

from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict

LOGS_DIR     = Path(os.environ.get("AXIOM_LOGS_DIR", "./axiom_logs"))
TOKEN_LOG    = LOGS_DIR / "token_usage.jsonl"
_lock        = threading.Lock()


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class TokenRecord:
    timestamp:    str        # ISO UTC
    session_id:   str
    agent:        str        # "resolver" | "axiom" | "security"
    model:        str
    provider:     str
    input_tokens: int
    output_tokens: int
    source_file:  str = ""
    latency_ms:   float = 0.0


# ── Public API ─────────────────────────────────────────────────────────────────

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
    """Append one token-usage record to the JSONL log. Thread-safe."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    rec = TokenRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        agent=agent,
        model=model,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        source_file=source_file,
        latency_ms=latency_ms,
    )
    with _lock:
        with TOKEN_LOG.open("a") as f:
            f.write(json.dumps(asdict(rec)) + "\n")


def load_records(limit: int = 2000) -> list[dict]:
    """Load the most recent N records from the JSONL log."""
    if not TOKEN_LOG.exists():
        return []
    lines = TOKEN_LOG.read_text().strip().splitlines()
    recent = lines[-limit:]
    records = []
    for line in recent:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records


def get_summary() -> dict:
    """Return aggregated token stats for the dashboard."""
    records = load_records()
    if not records:
        return {
            "total_input": 0, "total_output": 0, "total_calls": 0,
            "by_agent": {}, "by_session": {}, "timeline": [],
        }

    by_agent: dict[str, dict] = {}
    by_session: dict[str, dict] = {}
    timeline: list[dict] = []

    for r in records:
        agent   = r["agent"]
        sid     = r["session_id"]
        inp     = r["input_tokens"]
        out     = r["output_tokens"]
        ts      = r["timestamp"][:16]   # minute-level bucket

        # by_agent
        if agent not in by_agent:
            by_agent[agent] = {"input": 0, "output": 0, "calls": 0}
        by_agent[agent]["input"]  += inp
        by_agent[agent]["output"] += out
        by_agent[agent]["calls"]  += 1

        # by_session
        if sid not in by_session:
            by_session[sid] = {
                "input": 0, "output": 0, "calls": 0,
                "source_file": r.get("source_file", ""),
                "timestamp": r["timestamp"],
            }
        by_session[sid]["input"]  += inp
        by_session[sid]["output"] += out
        by_session[sid]["calls"]  += 1

        # timeline (per-call data points for charting)
        timeline.append({
            "timestamp": r["timestamp"],
            "agent":     agent,
            "input":     inp,
            "output":    out,
            "session":   sid,
            "model":     r.get("model", ""),
        })

    total_input  = sum(r["input_tokens"]  for r in records)
    total_output = sum(r["output_tokens"] for r in records)

    return {
        "total_input":   total_input,
        "total_output":  total_output,
        "total_tokens":  total_input + total_output,
        "total_calls":   len(records),
        "by_agent":      by_agent,
        "by_session":    dict(list(by_session.items())[-50:]),   # last 50 sessions
        "timeline":      timeline[-500:],                         # last 500 data points
    }