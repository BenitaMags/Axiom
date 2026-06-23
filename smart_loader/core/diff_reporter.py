"""
core/diff_reporter.py
──────────────────────
AXIOM diff + perf subsystem.

Professor's concept: AXIOM as a superclass of `diff` and `perf` Linux tools.

  diff  → structural diff between original and patched source (version-tracked)
  perf  → performance delta report comparing old vs new import benchmarks

In Python OOP terms:
  DiffReporter   — mirrors `diff` behaviour
  PerfReporter   — mirrors `perf stat` behaviour  
  AxiomReport    — superclass combining both (as professor suggested)

Versioned storage:
  axiom_logs/versions/{session_id}/
    v1_original.py         ← always the untouched source
    v{n}_patched.py        ← each --save creates a new version
    versions.json          ← index: [{version, timestamp, session_id, hash}]
    diff_{n-1}_to_{n}.txt  ← unified diff between consecutive versions
    perf_report.json       ← benchmark delta
"""

from __future__ import annotations
import difflib
import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


LOGS_DIR = Path(os.environ.get("AXIOM_LOGS_DIR", "./axiom_logs"))
VERSIONS_DIR = LOGS_DIR / "versions"


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class VersionRecord:
    version:    int
    timestamp:  str
    session_id: str
    source_file: str
    sha256:     str
    lines_added:   int = 0
    lines_removed: int = 0
    packages_replaced: list[str] = field(default_factory=list)


@dataclass 
class PerfDelta:
    package:       str
    original_ms:   float
    patched_ms:    float
    speedup:       float        # patched_ms / original_ms  (<1.0 = faster)
    original_kb:   float = 0.0
    patched_kb:    float = 0.0
    memory_delta:  float = 0.0  # patched_kb - original_kb (<0 = smaller)


@dataclass
class AxiomReport:
    """
    Superclass of diff + perf.
    Combines structural diff output with performance delta report.
    """
    session_id:    str
    source_file:   str
    timestamp:     str
    version:       int
    diff_lines:    list[str]             # unified diff
    perf_deltas:   list[PerfDelta]
    version_index: list[VersionRecord]
    identical:     bool = False          # True when diff produces no output
    total_speedup: float = 1.0


# ── DiffReporter ───────────────────────────────────────────────────────────────

class DiffReporter:
    """
    Mirrors `diff -u original patched`.
    Stores every version so `axiom --save` always has a trail.
    """

    def __init__(self, source_file: str):
        self.source_file = source_file
        slug = Path(source_file).stem
        self.version_dir = VERSIONS_DIR / slug
        self.version_dir.mkdir(parents=True, exist_ok=True)
        self.index_path  = self.version_dir / "versions.json"

    def _load_index(self) -> list[VersionRecord]:
        if not self.index_path.exists():
            return []
        try:
            raw = json.loads(self.index_path.read_text())
            return [VersionRecord(**r) for r in raw]
        except Exception:
            return []

    def _save_index(self, index: list[VersionRecord]):
        self.index_path.write_text(
            json.dumps([asdict(r) for r in index], indent=2)
        )

    def _sha256(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def save_version(
        self,
        original_code: str,
        patched_code: str,
        session_id: str,
        packages_replaced: list[str] | None = None,
    ) -> tuple[VersionRecord, list[str]]:
        """
        Save both the original and patched source, compute unified diff,
        update the version index.
        Returns (VersionRecord, diff_lines).
        """
        index = self._load_index()
        version_num = len(index) + 1
        ts = datetime.now(timezone.utc).isoformat()

        # Always save original as v1 if this is the first run
        if version_num == 1:
            (self.version_dir / "v1_original.py").write_text(original_code)

        # Save patched version
        patched_path = self.version_dir / f"v{version_num}_patched.py"
        patched_path.write_text(patched_code)

        # Unified diff
        original_lines = original_code.splitlines(keepends=True)
        patched_lines  = patched_code.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            original_lines, patched_lines,
            fromfile=f"v{max(1, version_num-1)}_original.py",
            tofile=f"v{version_num}_patched.py",
            lineterm="",
        ))

        # Save diff file
        diff_path = self.version_dir / f"diff_v{max(1,version_num-1)}_to_v{version_num}.txt"
        diff_path.write_text("\n".join(diff) if diff else "(no changes)\n")

        # Count additions/removals
        added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

        rec = VersionRecord(
            version=version_num,
            timestamp=ts,
            session_id=session_id,
            source_file=self.source_file,
            sha256=self._sha256(patched_code),
            lines_added=added,
            lines_removed=removed,
            packages_replaced=packages_replaced or [],
        )
        index.append(rec)
        self._save_index(index)

        return rec, diff

    def diff_between(self, v_from: int, v_to: int) -> list[str]:
        """Return unified diff between any two saved versions."""
        def _read(n):
            p = self.version_dir / f"v{n}_patched.py"
            if not p.exists():
                p = self.version_dir / "v1_original.py"
            return p.read_text().splitlines(keepends=True) if p.exists() else []

        return list(difflib.unified_diff(
            _read(v_from), _read(v_to),
            fromfile=f"v{v_from}", tofile=f"v{v_to}", lineterm="",
        ))

    def get_index(self) -> list[VersionRecord]:
        return self._load_index()


# ── PerfReporter ───────────────────────────────────────────────────────────────

class PerfReporter:
    """
    Mirrors `perf stat` — computes import benchmark deltas
    between the original and patched dependency set.
    """

    @staticmethod
    def compute_deltas(
        decisions: list,           # list[LoadDecision]
        benchmarks: dict,          # dict[str, BenchmarkResult]
    ) -> tuple[list[PerfDelta], float]:
        """
        For each replacement decision, compute the speedup ratio.
        Returns (list_of_deltas, overall_geometric_mean_speedup).
        """
        import math
        deltas = []
        speedups = []

        for decision in decisions:
            if decision.winner == decision.original:
                continue

            orig_bench = benchmarks.get(decision.original)
            win_bench  = benchmarks.get(decision.winner)

            if not orig_bench or not win_bench:
                continue

            orig_ms = orig_bench.import_time_ms or 0.001
            win_ms  = win_bench.import_time_ms  or 0.001
            speedup = win_ms / orig_ms   # < 1.0 = faster

            delta = PerfDelta(
                package=f"{decision.original} → {decision.winner}",
                original_ms=round(orig_ms, 3),
                patched_ms=round(win_ms, 3),
                speedup=round(speedup, 4),
                original_kb=round(orig_bench.memory_kb, 2),
                patched_kb=round(win_bench.memory_kb, 2),
                memory_delta=round(win_bench.memory_kb - orig_bench.memory_kb, 2),
            )
            deltas.append(delta)
            speedups.append(speedup)

        total_speedup = (
            math.exp(sum(math.log(s) for s in speedups) / len(speedups))
            if speedups else 1.0
        )
        return deltas, round(total_speedup, 4)


# ── AxiomReport factory ────────────────────────────────────────────────────────

def build_report(
    session_id:    str,
    source_file:   str,
    original_code: str,
    patched_code:  str,
    decisions:     list,
    benchmarks:    dict,
) -> AxiomReport:
    """
    Build the combined diff+perf report and save versioned files.
    Called by the CLI on --save.
    """
    diff_reporter = DiffReporter(source_file)
    perf_reporter = PerfReporter()

    packages_replaced = [d.winner for d in decisions if d.winner != d.original]

    version_rec, diff_lines = diff_reporter.save_version(
        original_code=original_code,
        patched_code=patched_code,
        session_id=session_id,
        packages_replaced=packages_replaced,
    )

    perf_deltas, total_speedup = perf_reporter.compute_deltas(decisions, benchmarks)

    # Save perf report alongside diffs
    perf_path = diff_reporter.version_dir / "perf_report.json"
    perf_path.write_text(json.dumps(
        {
            "session_id": session_id,
            "timestamp":  version_rec.timestamp,
            "total_speedup": total_speedup,
            "deltas": [asdict(d) for d in perf_deltas],
        },
        indent=2,
    ))

    return AxiomReport(
        session_id=session_id,
        source_file=source_file,
        timestamp=version_rec.timestamp,
        version=version_rec.version,
        diff_lines=diff_lines,
        perf_deltas=perf_deltas,
        version_index=diff_reporter.get_index(),
        identical=len(diff_lines) == 0,
        total_speedup=total_speedup,
    )