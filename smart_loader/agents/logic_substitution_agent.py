"""
agents/logic_substitution_agent.py
──────────────────────────────────
Logic Substitution Agent — produces a program-isomorphic variant of the
Connector's patched source.

Position in the pipeline: immediately after ``connector_agent``. Connector has
by then produced ``patched_code`` (the migrated file); this agent produces a
variant of that file which is structurally different but behaviourally
identical, at (approximately) a user-requested size. The two are then merged
into a single deliverable — the migration removes *known* vulnerabilities, the
diversification hardens against *unknown* ones.

This module is the LangGraph node only. The machinery it drives lives in
``smart_loader/isomorph/`` and is deliberately separate, mirroring how
``connector_agent`` drives ``core/ast_rewrite.py`` and ``core/aci_tools.py``:

    isomorph/spt.py        the transformation catalogue (pluggable registry)
    isomorph/metrics.py    the diversity metrics (pluggable registry)
    isomorph/oracle.py     the equivalence gate A(x, x')
    isomorph/search.py     length-targeted beam search
    isomorph/telemetry.py  program-metric + LLM-feature recording

Design provenance: the generate/validate/measure loop and the beam search
follow Auto-SPT (Hooda et al., arXiv:2512.06042, 2025), with its
clone-detector objective replaced by a size objective — see
``isomorph/search.py`` for why that substitution is what keeps the length
control honest.

State contract
──────────────
Reads:
    patched_code            source to diversify (falls back to source_code)
    source_file             path, used for reporting and telemetry
    isomorph_target_lines   absolute line target, OR
    isomorph_target_ratio   target as a multiple of the current size
    isomorph_interactive    ask the human for a line target (needs a real tty;
                            ignored when isomorph_target_lines is set)
    isomorph_corpus_dir     directory holding the equivalence oracle
    isomorph_ranking_metric rename-insensitive metric the search ranks by
    isomorph_artifact_dir   persist oracle-passing candidates here (None = off)
    enable_isomorph         master switch (default False — opt-in)

Writes:
    isomorph_variant_code   the accepted variant (empty when none was found)
    isomorph_report         requested/achieved/diversity/oracle detail
    agent_trace             one AgentEvent, as every other agent does
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.table import Table
from langchain_core.messages import AIMessage

from smart_loader.core.state import AgentEvent, LoaderState
from smart_loader.isomorph import telemetry
from smart_loader.isomorph.metrics import DEFAULT_RANKING_METRIC, METRIC_REGISTRY
from smart_loader.isomorph import llm_variant
from smart_loader.isomorph.oracle import CorpusOracle, NullOracle
from smart_loader.isomorph.search import LengthTargetedSearch
from smart_loader.isomorph.spt import SPT_REGISTRY

console = Console()

DEFAULT_TARGET_RATIO = 1.15


def parse_length_request(reply: str, current_lines: int) -> int | None:
    """Turn what the human typed into an absolute line target.

    Accepts an absolute count (``120``) or a change relative to the current
    size (``+20``, ``-15``). The relative form is the one people reach for —
    "make it about twenty lines longer" is the natural way to think about a
    variant of a file you are looking at — and it is why the sign has to be
    honoured rather than stripped.

    Returns None when the input is empty or unparseable, which the caller
    treats as "keep the default" rather than as an error.
    """
    text = (reply or "").strip().replace(",", "")
    if not text:
        return None
    try:
        if text[0] in "+-":
            return max(1, current_lines + int(text))
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _is_interactive(state: dict) -> bool:
    """Whether it is safe to block on a human.

    A batch sweep must never stop waiting for input it will never get, so the
    prompt requires BOTH an explicit opt-in and a real terminal on stdin. The
    ``stdin.isatty()`` half is what protects a piped or CI run that forgot to
    unset the flag.
    """
    if not state.get("isomorph_interactive", False):
        return False
    try:
        import sys

        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _prompt_for_target(current_lines: int) -> int | None:
    """The second human-in-the-loop gate: how long should the variant be?

    Distinct from ``run_feedback_loop()``, which sits after the verifier and
    asks whether to accept a migration. This one sits after the Connector and
    asks for a size, because the size is the one thing the search cannot infer
    — it is a preference, not a property of the code.
    """
    console.print(
        f"\n[bold]How many lines should the isomorphic variant be?[/bold]\n"
        f"[dim]Current size: {current_lines} lines. Enter an absolute count "
        f"(e.g. {int(current_lines * 1.15)}), a relative change (+20, -15), "
        f"or press Enter for the default "
        f"({int(round(current_lines * DEFAULT_TARGET_RATIO))}, "
        f"{DEFAULT_TARGET_RATIO:g}x).[/dim]"
    )
    try:
        reply = input("target lines> ")
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]No input — using the default target.[/dim]")
        return None
    target = parse_length_request(reply, current_lines)
    if target is None and reply.strip():
        console.print(f"[yellow]Could not read {reply.strip()!r} as a line "
                      f"count; using the default.[/yellow]")
    return target


def _resolve_target(state: dict, current_lines: int) -> int | None:
    """Absolute line target from an explicit count, a prompt, or a ratio.

    Four entry points, in strict precedence order. ``isomorph_target_lines``
    wins outright: it is what the CLI sets, and a batch run that named a size
    must never be second-guessed by a prompt. Failing that, an interactive
    session asks the human. Failing that, an explicit ratio drives it, so a
    sweep of many sizes needs no human present.

    Failing all three, the answer is **None — no size was requested**, and the
    search ranks on diversity alone. This used to fall back to
    ``DEFAULT_TARGET_RATIO`` (1.15x), which meant a caller who never mentioned
    size still got a search optimising for a 15% growth target, with diversity
    demoted to a tie-break on an objective nobody set. On the migration corpus,
    where no size constraint exists, that was the whole search budget spent on
    the wrong thing. A default is not a request.
    """
    explicit = state.get("isomorph_target_lines")
    if isinstance(explicit, int) and explicit > 0:
        return explicit

    if _is_interactive(state):
        chosen = _prompt_for_target(current_lines)
        if chosen is not None:
            return chosen

    ratio = state.get("isomorph_target_ratio")
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return None
    # A zero ratio is "no ratio given", NOT "target zero lines". graph.py
    # defaults this field to 0.0 rather than leaving it unset, so the previous
    # ``ratio in (None, "")`` guard let 0.0 through to
    # ``max(1, round(lines * 0.0))`` == 1 — every module in the pipeline was
    # searched against an unreachable one-line target. Length then dominated
    # the lexicographic sort key and diversity fell to a tie-break, which is
    # precisely the defect the None-target work was meant to remove: the
    # migrated file came back "none applicable" with diversity 0.0000 while
    # modules reached by a caller that passed no ratio diversified normally.
    if ratio <= 0.0:
        return None
    return max(1, int(round(current_lines * ratio)))


def _render(result) -> None:
    table = Table(title="Logic Substitution — isomorphic variant")
    table.add_column("Property")
    table.add_column("Value", overflow="fold")
    table.add_row("Module", result.module)
    table.add_row("Lines (original → variant)",
                  f"{result.original_lines} → {result.achieved_lines}")
    # Requested vs achieved, spelled out rather than left to be inferred: the
    # length target is best-effort, so the miss is a result to report, not a
    # failure to hide. A signed miss says which way it went; "on target" is
    # only claimed at exactly zero.
    verdict = ("ON TARGET" if result.length_error == 0 else
               f"{abs(result.length_error)} line(s) "
               f"{'over' if result.length_error > 0 else 'short'}")
    table.add_row("Requested lines", str(result.requested_lines))
    table.add_row("Achieved lines", str(result.achieved_lines))
    table.add_row("Miss (achieved − requested)",
                  f"{result.length_error:+d}   [{verdict}]")
    table.add_row("Transformations",
                  ", ".join(result.applied) or "none applicable")
    table.add_row("Equivalence oracle",
                  "PASS" if result.oracle_passed else f"FAIL — {result.oracle_detail}")
    for name, value in result.diversity.items():
        marker = "" if METRIC_REGISTRY[name].rename_sensitive else "  (rename-insensitive)"
        ranked = "  ← ranking signal" if name == result.ranking_metric else ""
        table.add_row(f"diversity: {name}", f"{value:.4f}{marker}{ranked}")
    table.add_row("Search", f"{result.candidates_equivalent}/"
                            f"{result.candidates_generated} candidates equivalent, "
                            f"{result.iterations} iterations, {result.duration_s:.1f}s")
    console.print(table)


def logic_substitution_agent(state: LoaderState) -> dict:
    """LangGraph node: generate an isomorphic variant of the patched source."""
    # Silent, allocation-free pass-through when the feature is off. This node
    # sits on the main pipeline edge, so every ordinary migration run executes
    # it; printing a banner or rewriting agent_trace here would change the
    # output of runs that asked for none of this. Returning {} leaves the
    # state byte-identical.
    if not state.get("enable_isomorph", False):
        return {}

    console.rule("[bold cyan]🧬 Logic Substitution Agent")
    trace = list(state.get("agent_trace", []))

    source = state.get("patched_code") or state.get("source_code") or ""
    if not source.strip():
        console.print("[yellow]Nothing to diversify — no patched source.[/yellow]")
        trace.append(AgentEvent(stage="logic_substitution", event="skipped",
                                detail={"reason": "no source"}))
        return {"agent_trace": trace}

    # ── Verification opt-out ────────────────────────────────────────────────
    # Normally the absence of an oracle corpus is fatal: an unverified variant
    # is worthless for a dependability claim, so the agent refuses rather than
    # emitting one. The ``proteus_no_verif`` ablation in the evaluation
    # protocol needs precisely the forbidden behaviour — the agents running
    # with the equivalence oracle switched off — so that the paper can report
    # what verification is worth.
    #
    # This is deliberately a separate, explicitly-named flag rather than
    # "corpus missing means skip the check". A missing corpus is usually a
    # misconfiguration, and silently downgrading it to unverified output is how
    # an unverified variant reaches a user who believed it was checked. Opting
    # out has to be a decision someone typed.
    #
    # Every unverified variant is marked in three places — console, agent
    # trace, and ``isomorph_report['verification']`` — so a downstream consumer
    # cannot mistake it for a checked one.
    skip_verification = bool(state.get("isomorph_skip_verification", False))

    corpus_dir = state.get("isomorph_corpus_dir", "")
    # Read program_dir BEFORE the refusal check. It was read further down, so
    # the guard below fired first and blocked every C/D run even with a fully
    # populated ProgramOracle corpus available — the new path was unreachable.
    program_dir = str(state.get("isomorph_program_dir", "") or "")
    if not corpus_dir and not program_dir and not skip_verification:
        console.print("[red]No equivalence corpus configured "
                      "(isomorph_corpus_dir); refusing to emit an unverified "
                      "variant. Pass isomorph_skip_verification=True only if "
                      "you are deliberately running the no-verification "
                      "ablation.[/red]")
        trace.append(AgentEvent(stage="logic_substitution", event="blocked",
                                detail={"reason": "no oracle corpus"}))
        return {"agent_trace": trace}

    if skip_verification:
        console.print("[bold yellow]⚠ EQUIVALENCE ORACLE DISABLED — this "
                      "variant is NOT verified and must not be used outside "
                      "the no-verification ablation.[/bold yellow]")
        trace.append(AgentEvent(stage="logic_substitution", event="unverified",
                                detail={"reason": "isomorph_skip_verification"}))

    module = state.get("isomorph_module") or _module_name(state.get("source_file", ""))

    # Two oracle shapes are supported. The ICS layout (golden.json +
    # test_corpus.py + oracle.py in one directory) is the original; a
    # ProgramOracle covers a corpus whose programs ship pytest suites instead,
    # recording probe values per program in a shared golden.json. Selection is
    # by what the directory actually contains rather than by a flag, so a
    # caller cannot point at the wrong kind and get a silently weaker check.
    if program_dir and not skip_verification:
        import sys as _sys

        equiv_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "equivalence")
        if equiv_root not in _sys.path:
            _sys.path.insert(0, equiv_root)
        try:
            from oracle import ProgramOracle  # type: ignore

            oracle = ProgramOracle(program_dir,
                                   state.get("isomorph_program_name", ""))
            console.print(f"[dim]Using ProgramOracle for "
                          f"{os.path.basename(program_dir)} "
                          f"(golden vectors + invariants + tests + imports).[/dim]")
        except Exception as exc:
            console.print(f"[red]ProgramOracle unusable: {exc}[/red]")
            trace.append(AgentEvent(stage="logic_substitution", event="blocked",
                                    detail={"error": str(exc)}))
            return {"agent_trace": trace}
    elif skip_verification:
        # NullOracle accepts every candidate, so the search still explores and
        # still reports diversity — it simply never rejects. Seeded with the
        # incoming source because there is no corpus to read the original from.
        oracle = NullOracle(source, module)
    else:
        try:
            oracle = CorpusOracle(corpus_dir)
        except FileNotFoundError as exc:
            console.print(f"[red]Equivalence corpus unusable: {exc}[/red]")
            trace.append(AgentEvent(stage="logic_substitution", event="blocked",
                                    detail={"error": str(exc)}))
            return {"agent_trace": trace}

    if state.get("isomorph_use_generated", False):
        from smart_loader.isomorph.design import register_generated
        added = register_generated(state.get("isomorph_generated_path"))
        if added:
            console.print(f"[dim]Registered {added} LLM-designed transformation(s) "
                          f"from the generated catalogue.[/dim]")

    current_lines = len(source.splitlines())
    target = _resolve_target(state, current_lines)
    console.print(
        f"[dim]{len(SPT_REGISTRY)} transformations available; "
        + (f"targeting {target} lines (currently {current_lines})."
           if target is not None else
           f"no size requested (currently {current_lines} lines) — "
           f"ranking on diversity alone.")
        + "[/dim]"
    )

    search = LengthTargetedSearch(
        oracle,
        beam_size=int(state.get("isomorph_beam_size", 4) or 4),
        max_iterations=int(state.get("isomorph_max_iterations", 8) or 8),
        ranking_metric=(state.get("isomorph_ranking_metric")
                        or DEFAULT_RANKING_METRIC),
        artifact_dir=state.get("isomorph_artifact_dir") or None,
    )
    # Seed from the source this agent was actually handed — the Connector's
    # patched module — not from the oracle's pristine copy. Without this the
    # migration is discarded and the shipped variant is a diversified
    # ORIGINAL, while the line target (derived from the patched text above)
    # describes a file the search never sees.
    # ── candidate source: LLM first, SPT search as fallback ───────────────
    # The catalogue is thirteen peephole rewrites inside a function body. It
    # cannot introduce a value object, collapse duplicated accessors into a
    # table, or extract a module -- and those are the restructurings that read
    # as a different design rather than as churn. Measured on D1 against a
    # hand-written reference: ast_structural 0.4548 for the reference against
    # 0.2864 for the search, while the search's cfg_shape ran to 0.5556 against
    # the reference's 0.2000, because four of the transformations manufacture
    # branches.
    #
    # The gate does not move. A candidate from either source ships only if
    # ``oracle.check`` accepts it, so the LLM is a proposer and A(x, x') is
    # still the thing being trusted. When the LLM's attempts are all rejected
    # the search runs as before, which is why this is a fallback rather than a
    # replacement: a verified small variant beats no variant.
    generator = str(state.get("isomorph_generator", "llm") or "llm").lower()
    result = None
    if generator == "llm":
        provider = state.get("llm_provider", "ollama")
        model = state.get("model", "qwen3-coder-next")

        def _ask(prompt: str) -> str:
            from smart_loader.core.llm_factory import _get_llm
            from smart_loader.core.llm_json import coerce_llm_text
            # disable_thinking, for the reason llm_factory documents: extended
            # thinking can spend the whole output budget before a single text
            # block is emitted, and the caller sees an empty reply. Measured
            # here -- a D1 attempt returned 0 bytes, which the diagnostics
            # caught only because they write the reply to disk. This call wants
            # a long single-shot artifact (a code file), which is exactly the
            # case that note says to turn thinking off for. Raising max_tokens
            # would only have bought more room to think.
            client = _get_llm(provider, model, disable_thinking=True)
            reply = client.invoke(prompt)
            return coerce_llm_text(getattr(reply, "content", reply))

        def _note(attempt, kind, detail):
            console.print(f"[dim]  llm variant attempt {attempt}: "
                          f"{kind} — {str(detail)[:110]}[/dim]")

        llm_result = llm_variant.generate(
            oracle, module, source, _ask, target_lines=target,
            attempts=int(state.get("isomorph_llm_attempts", 3) or 3),
            ranking_metric=(state.get("isomorph_ranking_metric")
                            or DEFAULT_RANKING_METRIC),
            siblings=_sibling_modules(program_dir),
            on_event=_note,
        )
        if llm_result.oracle_passed:
            result = llm_result
        else:
            console.print("[yellow]LLM variant not accepted by the oracle; "
                          "falling back to the transformation search.[/yellow]")
            trace.append(AgentEvent(stage="logic_substitution",
                                    event="llm_fallback",
                                    detail={"reason": llm_result.oracle_detail}))

    if result is None:
        result = search.run(module, target, seed_source=source)
    _render(result)

    telemetry.record_variant(
        result, session_id=str(state.get("_session_id", "") or ""),
    )

    if not result.oracle_passed:
        console.print("[red]Variant rejected: equivalence oracle failed.[/red]")
        trace.append(AgentEvent(stage="logic_substitution", event="rejected",
                                detail={"oracle": result.oracle_detail}))
        return {"agent_trace": trace, "isomorph_variant_code": ""}

    trace.append(AgentEvent(stage="logic_substitution", event="completed", detail={
        "module": result.module,
        "requested_lines": result.requested_lines,
        "achieved_lines": result.achieved_lines,
        "transformations": result.applied,
        "headline_diversity": result.headline_diversity,
    }))
    return {
        "isomorph_variant_code": result.variant_source,
        # Modules the variant EXTRACTED. The runner writes the variant over one
        # file; without these the clone imports a module that is not there.
        "isomorph_extra_files": dict(getattr(result, "extra_files", {}) or {}),
        "isomorph_report": {
            "module": result.module,
            "original_lines": result.original_lines,
            "requested_lines": result.requested_lines,
            "achieved_lines": result.achieved_lines,
            "length_error": result.length_error,
            "applied": list(result.applied),
            "diversity": dict(result.diversity),
            "headline_diversity": result.headline_diversity,
            "ranking_metric": result.ranking_metric,
            # oracle_passed alone is ambiguous once the ablation exists: the
            # NullOracle returns True for everything, so True would mean
            # "checked and equivalent" in one config and "never checked" in the
            # other. ``verification`` disambiguates, and the downstream CSV
            # writer keys ``oracle_equiv`` off it rather than off oracle_passed.
            "oracle_passed": True,
            "verification": "skipped" if skip_verification else "full",
            "oracle_detail": result.oracle_detail,
            "candidates_generated": result.candidates_generated,
            "candidates_equivalent": result.candidates_equivalent,
        },
        "agent_trace": trace,
        "messages": [AIMessage(content=(
            f"Logic Substitution: produced "
            f"{'an UNVERIFIED' if skip_verification else 'an equivalence-verified'} "
            f"variant of {result.module} at {result.achieved_lines} lines "
            f"(requested {result.requested_lines}) using "
            f"{len(result.applied)} transformation(s)."
        ))],
    }



def _sibling_modules(program_dir: str) -> set:
    """The program's own module names, which are first-party by definition."""
    try:
        return {n[:-3] for n in os.listdir(program_dir)
                if n.endswith(".py") and not n.startswith("test_")}
    except OSError:
        return set()


def _module_name(source_file: str) -> str:
    import os

    base = os.path.basename(source_file or "")
    return base[:-3] if base.endswith(".py") else base
