"""Telemetry for the isomorphism agent — LLM features AND program metrics.

Deliberately mirrors ``core/token_tracker.py``'s shape (append-only JSONL,
one record per event, flat JSON-safe fields) so both streams can be loaded
and joined with the same tooling. AXIOM already records the LLM half —
session, agent, model, provider, token counts, latency — via
``token_tracker.record_usage``. This module adds the program half, which the
migration pipeline never needed:

  * what was requested (target length) and what was achieved;
  * which transformations were applied, in order;
  * every registered diversity metric;
  * whether the equivalence oracle passed, and how expensive it was;
  * search economics — candidates generated vs candidates that survived.

Keeping these in one queryable log is what makes the trade-off curve (length
target vs achieved diversity vs equivalence rate) a matter of loading a file
rather than re-running the experiment.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field

_lock = threading.Lock()
DEFAULT_LOG = os.environ.get("AXIOM_ISOMORPH_LOG", "axiom_isomorph_telemetry.jsonl")


@dataclass
class VariantRecord:
    """One generated variant, with everything needed to reproduce and rank it."""

    timestamp: float
    session_id: str
    module: str
    # ── request vs outcome ────────────────────────────────────────────────
    original_lines: int
    requested_lines: int
    achieved_lines: int
    length_error: int
    # ── what produced it ─────────────────────────────────────────────────
    applied_transformations: list[str]
    transformation_count: int
    # ── program metrics (pluggable — whatever is registered) ─────────────
    diversity: dict[str, float]
    headline_diversity: float
    # ── equivalence ──────────────────────────────────────────────────────
    oracle_passed: bool
    oracle_detail: str
    # ── search economics ─────────────────────────────────────────────────
    iterations: int
    candidates_generated: int
    candidates_equivalent: int
    duration_s: float
    # ── LLM attribution (empty in the deterministic-catalogue mode) ──────
    llm_provider: str = ""
    llm_model: str = ""
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_calls: int = 0
    notes: dict = field(default_factory=dict)


_records: list[VariantRecord] = []


def record_variant(result, session_id: str = "", log_path: str | None = None,
                   **llm_attribution) -> VariantRecord:
    """Append one ``SearchResult`` to the telemetry log and the accumulator."""
    record = VariantRecord(
        timestamp=time.time(),
        session_id=session_id,
        module=result.module,
        original_lines=result.original_lines,
        requested_lines=result.requested_lines,
        achieved_lines=result.achieved_lines,
        length_error=result.length_error,
        applied_transformations=list(result.applied),
        transformation_count=len(result.applied),
        diversity=dict(result.diversity),
        headline_diversity=float(result.headline_diversity),
        oracle_passed=bool(result.oracle_passed),
        oracle_detail=str(result.oracle_detail)[:400],
        iterations=int(result.iterations),
        candidates_generated=int(result.candidates_generated),
        candidates_equivalent=int(result.candidates_equivalent),
        duration_s=round(float(result.duration_s), 3),
        llm_provider=llm_attribution.get("provider", ""),
        llm_model=llm_attribution.get("model", ""),
        llm_input_tokens=int(llm_attribution.get("input_tokens", 0)),
        llm_output_tokens=int(llm_attribution.get("output_tokens", 0)),
        llm_calls=int(llm_attribution.get("calls", 0)),
        notes=llm_attribution.get("notes", {}),
    )
    path = log_path or DEFAULT_LOG
    with _lock:
        _records.append(record)
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record)) + "\n")
        except OSError:
            # Telemetry must never take down a run.
            pass
    return record


def get_summary() -> dict:
    """Aggregate view over this process's records."""
    if not _records:
        return {"variants": 0}
    passed = [r for r in _records if r.oracle_passed]
    on_target = [r for r in _records if abs(r.length_error) <= 1]
    return {
        "variants": len(_records),
        "oracle_pass_rate": round(len(passed) / len(_records), 4),
        "on_target_rate": round(len(on_target) / len(_records), 4),
        "mean_headline_diversity": round(
            sum(r.headline_diversity for r in passed) / len(passed), 4
        ) if passed else 0.0,
        "mean_abs_length_error": round(
            sum(abs(r.length_error) for r in _records) / len(_records), 2
        ),
        "total_candidates_generated": sum(r.candidates_generated for r in _records),
        "total_llm_calls": sum(r.llm_calls for r in _records),
    }


def reset() -> None:
    with _lock:
        _records.clear()
