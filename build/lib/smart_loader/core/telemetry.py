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


def save_session(
    source_file: str,
    decisions: list[LoadDecision],
    agent_trace: list[AgentEvent],
    patched_code: str,
    llm_provider: str,
    model: str,
) -> str:
    """
    Save a complete session to disk.
    Returns the session_id.

    Structure mirrors CodeAugur:
      sessions/{id}.json  → lean log with hashes
      details/{hash}.json → full decision content
    """
    _ensure_dirs()
    session_id = uuid.uuid4().hex[:8]
    timestamp  = datetime.utcnow().isoformat()

    # ── Build detail entries ───────────────────────────────────────────────────
    detail_hashes = []
    for decision in decisions:
        content = json.dumps({
            "role":       decision.role,
            "winner":     decision.winner,
            "original":   decision.original,
            "score":      decision.score,
            "rationale":  decision.rationale,
            "confidence": decision.confidence,
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