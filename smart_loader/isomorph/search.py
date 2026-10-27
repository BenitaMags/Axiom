"""Length-targeted beam search over semantics-preserving transformations.

The search skeleton is Auto-SPT's Algorithm 1 — maintain a beam of the B best
candidates; each iteration *expands* (apply every SPT to every beam member),
*filters* (keep only syntactically valid, oracle-equivalent candidates), and
*selects* (keep the top B) — with one substantive change.

Auto-SPT selects the candidates that maximise distance from a clone detector,
because its goal is an adversarial example. We have no clone detector and no
adversary. Our goal is a variant of a *requested size*, so selection is
ordered by:

    1. distance from the requested line count  (primary — the user's ask)
    2. structural diversity, rename-insensitive (secondary — the tie-break)

That substitution is the whole reason the objective is honest. A trivially
"longer" program can be produced by appending dead code; here the only way to
move the line count is to apply a transformation from the catalogue, and every
one of those is load-bearing — it rewrites computation that actually runs.
Length is therefore a *best-effort* target rather than a guarantee, and the
result records requested vs achieved so a missed target is reported as data
rather than hidden.

Which diversity metric plays role (2) is a PARAMETER (``ranking_metric``),
defaulting to ``ast_structural``. The search needs exactly one scalar to sort
by, and picking one is a real choice with a measurable consequence:
``ast_structural`` rewards statement-level padding over control-flow
restructuring, and the sweep showed the effect plainly — the variant that won
on ``protective_relay`` (ast 0.1261) left the branching structure completely
unchanged (cfg_shape 0.0000), while the genuinely restructured
``pump_controller`` variant (cfg_shape 0.1429) ranked near last. So
``composite`` is available as an ablation.

Two things follow, and both are honesty constraints rather than style:

  * ``ranking_metric="ast_structural"`` must reproduce the earlier results
    exactly. If parameterising the choice moved any number, the
    parameterisation is buggy, not the metric.
  * Diversity values are NOT comparable across ranking modes. They are
    different scales measuring different things; an ast_structural of 0.0940
    and a composite of 0.065 stand in no order, any more than 20 C is colder
    than 68 F. The columns that ARE comparable are the length miss, the
    on-target rate and the oracle pass rate — compare those, and report both
    modes whichever way it comes out.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field

from smart_loader.isomorph import metrics as metrics_mod
from smart_loader.isomorph.oracle import CorpusOracle, is_valid_python
from smart_loader.isomorph.spt import SPT, normalize, spts


@dataclass
class Candidate:
    source: str
    history: list[str] = field(default_factory=list)

    @property
    def lines(self) -> int:
        return len(self.source.splitlines())


@dataclass
class SearchResult:
    module: str
    original_source: str
    variant_source: str
    requested_lines: int
    achieved_lines: int
    original_lines: int
    applied: list[str]
    diversity: dict[str, float]
    headline_diversity: float
    ranking_metric: str
    oracle_passed: bool
    oracle_detail: str
    iterations: int
    candidates_generated: int
    candidates_equivalent: int
    duration_s: float

    @property
    def length_error(self) -> int:
        """Signed miss against the request (0 = exactly on target)."""
        return self.achieved_lines - self.requested_lines

    def summary(self) -> str:
        direction = "grew" if self.achieved_lines > self.original_lines else (
            "shrank" if self.achieved_lines < self.original_lines else "held")
        return (
            f"{self.module}: {self.original_lines} -> {self.achieved_lines} lines "
            f"({direction}; requested {self.requested_lines}, miss "
            f"{self.length_error:+d}) | diversity[{self.ranking_metric}] "
            f"{self.headline_diversity:.4f} | "
            f"{len(self.applied)} transformation(s): {', '.join(self.applied) or 'none'}"
        )


class LengthTargetedSearch:
    """Beam search for an equivalent variant close to a requested line count."""

    def __init__(
        self,
        oracle: CorpusOracle,
        beam_size: int = 4,
        max_iterations: int = 6,
        tolerance_ratio: float = 0.10,
        catalogue: list[SPT] | None = None,
        ranking_metric: str = metrics_mod.DEFAULT_RANKING_METRIC,
        artifact_dir: str | None = None,
    ) -> None:
        self.oracle = oracle
        self.beam_size = beam_size
        self.max_iterations = max_iterations
        self.tolerance_ratio = tolerance_ratio
        self.catalogue = catalogue or spts()
        # Where to persist oracle-passing candidates. None (the default) keeps
        # normal runs fast: a full sweep produces well over a thousand of them.
        # A claim of the form "1,496 candidates were verified equivalent" is
        # only checkable if the candidates still exist, so the sweep that backs
        # a paper should set this even though the default does not.
        self.artifact_dir = artifact_dir
        # Which of the rename-insensitive metrics breaks ties. Parameterised so
        # the alternative can be reported as an ablation rather than swapped in
        # silently; the default reproduces every previously published number.
        # Validated eagerly so a typo fails at construction rather than after a
        # sweep has been running for minutes.
        metrics_mod.headline("x = 1\n", "x = 1\n", ranking_metric)
        self.ranking_metric = ranking_metric

    def _score(self, cand: Candidate, original: str,
               target: int | None) -> tuple[float, float]:
        """Sort key: (line-count error, negative diversity).

        Primary term is the user's actual request. Diversity only breaks ties
        between candidates that are equally close to the target, so the search
        can never trade away the requested size in pursuit of a prettier
        diversity number. Changing ``ranking_metric`` therefore changes only
        which candidates win among equally-close ones — it cannot change how
        well the length target is met except through those tie-breaks.

        ``target=None`` means the caller expressed no size preference. The
        length term is then held constant so diversity alone orders the beam.
        Previously a missing target silently became 1.15x the current size, so
        a caller who only wanted a diverse variant got a search optimising a
        size they never asked for — with diversity demoted to a tie-break on
        that invented objective.
        """
        if target is None:
            return (
                0.0,
                -metrics_mod.headline(original, cand.source, self.ranking_metric),
            )
        return (
            abs(cand.lines - target),
            -metrics_mod.headline(original, cand.source, self.ranking_metric),
        )

    # ── artefact persistence ─────────────────────────────────────────────
    #
    # Every candidate the oracle accepts is a verified data point, and until
    # now all of them were thrown away — the sweeps produced 690 and 1,496
    # equivalent programs that existed only inside this loop. A reader cannot
    # audit a number they cannot open, so when ``artifact_dir`` is set the
    # accepted candidates are written out alongside a manifest recording how
    # each one was produced and what it measured.

    @staticmethod
    def _chain_slug(history: list[str], limit: int = 120) -> str:
        slug = re.sub(r"[^A-Za-z0-9_+-]", "-", "+".join(history)) or "unchanged"
        if len(slug) > limit:
            # Keep the name readable AND unique: a long chain would otherwise
            # collide with every other chain sharing its first 120 characters.
            digest = hashlib.sha256(slug.encode()).hexdigest()[:8]
            slug = f"{slug[:limit]}--{digest}"
        return slug

    def _open_artifacts(self, module_name: str, label: str) -> str | None:
        if not self.artifact_dir:
            return None
        root = os.path.join(self.artifact_dir, module_name, label)
        os.makedirs(root, exist_ok=True)
        return root

    def run(self, module_name: str, target_lines: int | None,
            label: str | None = None,
            seed_source: str | None = None) -> SearchResult:
        """Search for a variant of ``module_name`` near ``target_lines``.

        ``seed_source`` is the text the search starts from and measures
        diversity against. It defaults to the oracle's copy of the module,
        which is right for a standalone sweep over a pristine corpus.

        Inside the migration pipeline it is emphatically NOT right: the
        caller has just produced a patched version of the module, and
        seeding from the oracle's pristine copy silently discards that
        patch — the variant that ships is then a diversified *original*,
        with the library substitution thrown away. It also decouples the
        line target (computed by the caller from the patched text) from the
        text actually being searched, which is how a 25-line module came to
        be given a 167-line target and reported a 138-line miss.
        """
        started = time.time()
        original = (seed_source if seed_source is not None
                    else self.oracle.module_source(module_name))
        origin = Candidate(source=original)
        artifacts = self._open_artifacts(
            module_name, label or f"target_{target_lines}",
        )
        manifest: list[dict] = []

        beam: list[Candidate] = [origin]
        best = origin
        best_key = self._score(origin, original, target_lines)
        generated = equivalent = 0
        # Tolerance is relative to the requested CHANGE, not to the absolute
        # file size. Sizing it off the absolute count makes it meaninglessly
        # loose — a 10% band on a 97-line target is ±9 lines, so a search
        # asked to add 8 lines would stop satisfied having added none.
        # With no requested size there is nothing to be within tolerance OF,
        # so the early stop is disabled and the search runs its full iteration
        # budget, ranking purely on diversity.
        if target_lines is None:
            tolerance = None
        else:
            requested_delta = abs(target_lines - origin.lines)
            tolerance = max(1, int(round(requested_delta * self.tolerance_ratio)))
        iterations = 0

        for iterations in range(1, self.max_iterations + 1):
            # ── expansion ────────────────────────────────────────────────
            expanded: list[Candidate] = []
            seen = {c.source for c in beam}
            for cand in beam:
                for spt in self.catalogue:
                    produced = normalize(spt(cand.source))
                    if produced == cand.source or produced in seen:
                        continue
                    seen.add(produced)
                    generated += 1
                    expanded.append(Candidate(
                        source=produced, history=cand.history + [spt.name],
                    ))
            if not expanded:
                break

            # ── filtering: validity first (cheap), then equivalence ──────
            survivors: list[Candidate] = []
            for cand in expanded:
                if not is_valid_python(cand.source):
                    continue
                verdict = self.oracle.check(module_name, cand.source)
                if verdict.equivalent:
                    equivalent += 1
                    survivors.append(cand)
                    if artifacts is not None:
                        manifest.append(self._persist(
                            artifacts, equivalent, cand, original, verdict,
                        ))
            if not survivors:
                break

            # ── selection ────────────────────────────────────────────────
            survivors.sort(key=lambda c: self._score(c, original, target_lines))
            beam = survivors[: self.beam_size]

            top_key = self._score(beam[0], original, target_lines)
            if top_key < best_key:
                best, best_key = beam[0], top_key

            if tolerance is not None and abs(best.lines - target_lines) <= tolerance:
                break

        # When no transformation survived, `best` is the seed. The old code
        # hardcoded oracle_passed=True here on the reasoning that unchanged
        # code is trivially equivalent -- true with respect to the SEED, but
        # the oracle compares against the pristine ORIGINAL, and in
        # reimplement mode the seed is the First-Party Reimplementation
        # Agent's rewrite, which may not be equivalent to the original at all.
        # A C1 run shipped a PID whose constructor raised AttributeError on
        # every call, reported as "Equivalence oracle PASS", because of exactly
        # this. The seed gets checked like anything else now.
        final = self.oracle.check(module_name, best.source)
        result = SearchResult(
            module=module_name,
            original_source=original,
            variant_source=best.source,
            # No request means the achieved count IS the outcome, so recording
            # it as the request keeps length_error at 0 rather than inventing a
            # miss against a target the caller never set.
            requested_lines=(best.lines if target_lines is None else target_lines),
            achieved_lines=best.lines,
            original_lines=origin.lines,
            applied=best.history,
            diversity=metrics_mod.measure_all(original, best.source),
            headline_diversity=metrics_mod.headline(
                original, best.source, self.ranking_metric),
            ranking_metric=self.ranking_metric,
            oracle_passed=final.equivalent,
            oracle_detail=(
                final.detail if best.history else
                f"unchanged (no transformation applied); seed "
                f"{'passes' if final.equivalent else 'FAILS'} the oracle: "
                f"{final.detail}"
            ),
            iterations=iterations,
            candidates_generated=generated,
            candidates_equivalent=equivalent,
            duration_s=time.time() - started,
        )
        if artifacts is not None:
            self._write_manifest(artifacts, result, manifest)
        return result

    def _persist(self, root: str, index: int, cand: Candidate, original: str,
                 verdict) -> dict:
        """Write one oracle-passing candidate and return its manifest row."""
        slug = self._chain_slug(cand.history)
        filename = f"{index:03d}_{slug}.py"
        try:
            with open(os.path.join(root, filename), "w", encoding="utf-8") as handle:
                handle.write(cand.source)
        except OSError as exc:
            # Persisting artefacts is diagnostic, never load-bearing: a full
            # disk must not invalidate a search that is otherwise correct.
            return {"index": index, "file": None, "write_error": str(exc),
                    "chain": list(cand.history)}
        return {
            "index": index,
            "file": filename,
            "chain": list(cand.history),
            "lines": cand.lines,
            "line_delta": cand.lines - len(original.splitlines()),
            "metrics": {k: round(v, 6)
                        for k, v in metrics_mod.measure_all(original, cand.source).items()},
            "oracle_passed": True,
            "oracle_detail": verdict.detail,
            "oracle_script_checked": verdict.script_checked,
            "oracle_duration_s": round(verdict.duration_s, 3),
        }

    def _write_manifest(self, root: str, result: SearchResult,
                        rows: list[dict]) -> None:
        winner = os.path.join(root, "winner.py")
        try:
            with open(winner, "w", encoding="utf-8") as handle:
                handle.write(result.variant_source)
            payload = {
                "module": result.module,
                "original_lines": result.original_lines,
                "requested_lines": result.requested_lines,
                "achieved_lines": result.achieved_lines,
                "length_error": result.length_error,
                "ranking_metric": result.ranking_metric,
                "beam_size": self.beam_size,
                "max_iterations": self.max_iterations,
                "iterations_used": result.iterations,
                "candidates_generated": result.candidates_generated,
                "candidates_equivalent": result.candidates_equivalent,
                "duration_s": round(result.duration_s, 3),
                "winner": {
                    "file": "winner.py",
                    "chain": list(result.applied),
                    "lines": result.achieved_lines,
                    "metrics": {k: round(v, 6) for k, v in result.diversity.items()},
                    "oracle_passed": result.oracle_passed,
                    "oracle_detail": result.oracle_detail,
                },
                "candidates": rows,
            }
            with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1)
        except OSError:
            pass
