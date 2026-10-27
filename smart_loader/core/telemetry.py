"""
core/telemetry.py
──────────────────
Session telemetry and audit logging.

DIRECTLY INSPIRED BY CodeAugur's telemetry system:
  CodeAugur produces three artefacts per analysis session:
    1. analysis_logs/sessions/{id}.json  — lean log: event sequence + hashes
    2. analysis_logs/details/sha256_{h}.json — full content per decision
    3. analysis_logs/workspace.db — SQLite index

  The lean log references detail files ONLY by hash. An auditor can scan
  the event sequence quickly without being buried in data, then resolve
  individual hashes when they want full content.

  The integrity field is SHA-256 over all referenced detail hashes —
  recomputing it detects any post-hoc tampering.

We implement the same two-layer system for AXIOM:
  - A lean session log (event types + summary)
  - A full detail JSON per session
  - Integrity hash over all events
"""

from __future__ import annotations
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from dataclasses import asdict
from datetime import datetime

from smart_loader.core.state import AgentEvent, LoadDecision


LOGS_DIR = Path(os.environ.get("AXIOM_LOGS_DIR", "./axiom_logs"))


def _ensure_dirs():
    (LOGS_DIR / "sessions").mkdir(parents=True, exist_ok=True)
    (LOGS_DIR / "details").mkdir(parents=True, exist_ok=True)


def _hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]


def _collect_metrics(state: dict | None, agent_trace: list) -> dict:
    """Per-run outcome metrics, read from state AXIOM already produces.

    Everything here was previously visible only as console output during a
    run and then discarded: the session log recorded which packages changed
    and which stages fired, but not whether the result was *verified*, nor
    how far the diversified variant moved. A client running AXIOM on their
    own code has no evaluation harness to fall back on, so the dashboard is
    the only place these can surface — which means they have to be persisted
    here, at the one point that already writes a durable record.

    Every field degrades to None rather than a default. A run with the
    verifier disabled genuinely has no verification verdict, and writing
    ``False`` there would assert something the run never established.
    """
    state = state or {}
    stages = [getattr(e, "stage", "") for e in agent_trace]
    events = [(getattr(e, "stage", ""), getattr(e, "event", "")) for e in agent_trace]

    verdicts = [ev for st, ev in events if st == "verifier"]
    if not verdicts:
        verified = None                       # the verifier never ran
    elif any(v in ("accepted", "verified", "completed") for v in verdicts):
        verified = True
    elif any(v in ("rejected", "failed", "exhausted") for v in verdicts):
        verified = False
    else:
        verified = None

    report = state.get("isomorph_report") or {}
    diversity = report.get("diversity") or {}
    applied = report.get("applied") or []

    failing = next((st for st, ev in events
                    if ev in ("rejected", "failed", "blocked", "error")), None)

    return {
        # ── did the migration hold up ──────────────────────────────────────
        "verified":          verified,
        "failing_stage":     failing,
        "repair_cycles":     int(state.get("_repair_cycle_count", 0) or 0),
        "connector_retries": int(state.get("_connector_retry_count", 0) or 0),
        "axiom_retries":     int(state.get("_axiom_retry_count", 0) or 0),
        # ── was first-party logic diversified, and by how much ─────────────
        "diversified":       bool(applied),
        "transformations":   applied,
        "diversity":         {k: round(v, 4) for k, v in diversity.items()
                              if isinstance(v, (int, float))} or None,
        "lines_before":      report.get("original_lines"),
        "lines_after":       report.get("achieved_lines"),
        "equivalence":       report.get("verification"),
        "equivalence_detail": report.get("oracle_detail"),
        # ── supply chain ───────────────────────────────────────────────────
        "vulnerabilities":   state.get("vulnerability_count"),
        "stages_run":        sorted(set(s for s in stages if s)),
    }


def save_session(
    source_file: str,
    decisions: list[LoadDecision],
    agent_trace: list[AgentEvent],
    patched_code: str,
    llm_provider: str,
    model: str,
    session_id: str | None = None,
    state: dict | None = None,
) -> str:
    """
    Save a complete session to disk.
    Returns the session_id.

    Structure mirrors CodeAugur:
      sessions/{id}.json  → lean log with hashes
      details/{hash}.json → full decision content

    session_id: pass the pipeline's existing run-scoped session id (the one
    already used by token_tracker and diff_reporter, threaded through
    LoaderState as `_session_id`) so a single AXIOM run produces ONE
    consistent session id across telemetry, token usage, and version
    history instead of three different identifiers for the same run.
    Falls back to a freshly generated id only if none was supplied
    (e.g. when save_session is called outside the normal graph pipeline).
    """
    _ensure_dirs()
    session_id = session_id or uuid.uuid4().hex[:8]
    timestamp  = datetime.utcnow().isoformat()

    # ── Build detail entries ───────────────────────────────────────────────────
    detail_hashes = []
    for decision in decisions:
        content = json.dumps({
            "role":                decision.role,
            "winner":               decision.winner,
            "original":             decision.original,
            "score":                decision.score,
            "rationale":            decision.rationale,
            "confidence":           decision.confidence,
            # The fields below are stashed dynamically via __dict__ by
            # axiom_agent (LoadDecision itself stays a plain dataclass with
            # only the original five fields, since score/rationale/etc are
            # consumed elsewhere as strict types). Persisting them here is
            # what makes the crypto veto and connector requirement actually
            # auditable after the fact, instead of only visible as a
            # transient console.print during the run.
            "used_apis":            getattr(decision, "used_apis", []),
            "requires_connector":   getattr(decision, "requires_connector", False),
            "crypto_vetoed":        getattr(decision, "crypto_vetoed", []),
            "needs_install":        getattr(decision, "needs_install", False),
        }, indent=2)
        h = _hash(content)
        detail_hashes.append(h)
        detail_path = LOGS_DIR / "details" / f"{h.replace(':', '_')}.json"
        detail_path.write_text(content)

    # ── Integrity hash over all detail hashes (tamper detection) ──────────────
    integrity = _hash("".join(detail_hashes))

    # ── Lean session log ───────────────────────────────────────────────────────
    lean_log = {
        "session_id":   session_id,
        "timestamp":    timestamp,
        "source_file":  source_file,
        "llm_provider": llm_provider,
        "model":        model,
        "verdict": {
            "decisions_made":    len(decisions),
            "packages_replaced": sum(1 for d in decisions if d.winner != d.original),
            "summary": "; ".join(f"{d.role}: {d.original}→{d.winner}" for d in decisions),
        },
        "detail_hashes": detail_hashes,
        "integrity":     integrity,
        # Outcome metrics for this run. Absent on sessions written before this
        # existed, so every reader must treat a missing "metrics" key as
        # "not recorded" rather than as zeros.
        "metrics":       _collect_metrics(state, agent_trace),
        "events": [
            {"stage": e.stage, "event": e.event}
            for e in agent_trace
        ],
    }

    session_path = LOGS_DIR / "sessions" / f"{session_id}.json"
    session_path.write_text(json.dumps(lean_log, indent=2))

    # ── Full patched code ──────────────────────────────────────────────────────
    if patched_code:
        (LOGS_DIR / "sessions" / f"{session_id}_patched.py").write_text(patched_code)

    return session_id


def verify_session(session_id: str) -> dict:
    """
    Verify a session's integrity — recompute the hash over detail files
    and compare to the stored integrity value.
    Mirrors CodeAugur's /sessions/{id}/verify endpoint.
    """
    session_path = LOGS_DIR / "sessions" / f"{session_id}.json"
    if not session_path.exists():
        return {"integrity_ok": False, "error": "Session not found"}

    log = json.loads(session_path.read_text())
    detail_hashes = log.get("detail_hashes", [])
    recomputed = _hash("".join(detail_hashes))
    ok = recomputed == log.get("integrity")
    return {
        "integrity_ok": ok,
        "stored":       log.get("integrity"),
        "recomputed":   recomputed,
        "failures":     [] if ok else ["integrity mismatch"],
    }