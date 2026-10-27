"""LLM-driven design and implementation of new SPTs — Auto-SPT Steps 1 and 2.

This is the *generative* half of the hybrid. The hand-written catalogue in
``spt.py`` is deliberately small and its limits are visible in the telemetry:
length targets are consistently undershot and ``protective_relay`` cannot grow
at all, because only a few of the twelve transformations meaningfully change
size. Auto-SPT (Hooda et al., arXiv:2512.06042) measures exactly this effect —
their four hand-written transformations reach a worst-case distance of 0.243,
while twenty LLM-*designed* ones reach 0.947. More transformation TYPES, not
more applications of the same type, is what unlocks the search.

Two steps, following the paper:

  Step 1 — DESIGN. Prompt the LLM for a transformation description that is
    explicitly distinct from everything designed so far. The "be distinct from
    this list" instruction is load-bearing: the paper notes LLMs lack
    randomness and will otherwise re-propose near-identical transformations.

  Step 2 — IMPLEMENT. Prompt for a concrete LibCST implementation, sample
    several candidates at higher temperature, and keep the best by a reward
    that combines correctness with usefulness. Auto-SPT's reward is
    ``R(T) = mean_i A(x_i, T(x_i)) * L(x_i, T(x_i))`` — equivalence times
    strength. Ours keeps the equivalence factor exactly (the corpus oracle IS
    ``A``) and replaces "strength against a clone detector" with what we
    actually want: applicability, structural diversity, and the ability to
    move line count.

The critical economic property, straight from the paper: this runs ONCE. The
resulting transformations are ordinary deterministic Python, cached to disk,
and applied thereafter with no LLM calls at all. That is what keeps the search
at ~4 seconds per module rather than minutes of inference.

SAFETY — this module executes LLM-generated code. That is inherent to the
technique (Auto-SPT generates program mutators too), but it is contained:
every candidate runs in a subprocess with a timeout, is only ever applied to
corpus files, and is rejected outright unless it produces valid Python that
survives the full three-layer oracle. Nothing is registered into the live
catalogue until it has passed on every module.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import tokenize
from dataclasses import asdict, dataclass, field

from smart_loader.isomorph import metrics as metrics_mod
from smart_loader.isomorph.oracle import CorpusOracle, is_valid_python

DEFAULT_CATALOGUE = os.path.join(os.path.dirname(__file__), "generated_spts.json")

# ── Step 1 prompt ─────────────────────────────────────────────────────────────

DESIGN_SYSTEM = """You are a Python program-transformation expert working on \
software diversification for safety-critical industrial control code.

Design ONE semantics-preserving transformation (SPT): a rewrite that changes a \
program's syntactic structure while leaving its observable behaviour exactly \
identical.

HARD REQUIREMENTS — a transformation violating any of these is useless to us:
1. BEHAVIOUR-PRESERVING. Same outputs for all inputs. The code controls \
physical processes; a behavioural change is a safety failure, not a bug.
2. BIT-EXACT ON FLOATS. Never reassociate, redistribute or reorder floating- \
point arithmetic. (a+b)+c and a+(b+c) differ in IEEE-754. Extracting a \
subexpression into a variable is fine; regrouping it is not.
3. NO DEAD CODE. Every line the transformation adds must actually execute and \
contribute to the result. Padding with unused variables is explicitly \
forbidden — it would make the size control meaningless.
4. EVALUATION ORDER PRESERVED. Do not duplicate an expression that could have \
side effects, and do not change short-circuit order.

Return ONLY a JSON object:
{"name": "snake_case_name",
 "direction": "grow" | "shrink" | "neutral",
 "description": "precise description, implementable without ambiguity"}

"direction" states whether the transformation reliably increases, decreases, \
or roughly preserves the program's line count."""

DESIGN_USER = """Here are example programs from the corpus:

{programs}

Transformations already designed (yours MUST be materially different from \
every one of these — a different mechanism, not a variation):
{existing}

We especially need transformations with direction "{wanted}".{guidance}"""

# Direction-specific guidance appended to the design prompt.
#
# Without this the design step was direction-blind in practice: the cycle asked
# for "shrink" one turn in four, but nothing in the prompt explained what a
# line-REDUCING transformation looks like, and all four accepted designs came
# back grow or neutral. Measured consequence — only two transformations in the
# whole catalogue could remove a line, ``pid_governor`` and ``protective_relay``
# had no shrink path at all, and every shrink request in the sweep landed at
# exactly -1.5 mean lines regardless of how much reduction was asked for.
DIRECTION_GUIDANCE = {
    "shrink": """

This is the direction the catalogue is weakest in, so be concrete about how \
your transformation REMOVES lines while preserving behaviour exactly.

A shrink transformation must reduce the physical line count of programs shaped \
like the ones above. Mechanisms worth considering (do not simply pick one — \
they are here to calibrate what "shrink" means):
* merging adjacent statements that are compatible and independent;
* collapsing 'if cond: return True' / 'return False' into 'return cond';
* folding a single-use temporary into its one consumer;
* combining sequential augmented assignments to the same target;
* collapsing a redundant 'else' block after a branch that always returns;
* merging two adjacent guards on the same variable into one condition.

Constraints that still bind, and are the usual reason a shrink attempt is \
rejected:
* Folding a temporary is only safe when it is used EXACTLY ONCE and nothing \
runs between its definition and that use.
* When you substitute an expression into a larger one, parenthesise it. \
't * 2' with 't = a + b' becomes '(a + b) * 2', never 'a + b * 2'.
* Parenthesising groups, it does not reassociate — 'a + b' and '(a + b)' are \
bit-identical. Actually REORDERING float operands is still forbidden.
* Do not merge statements across a branch, loop, or return boundary.
* Deleting a line that has an observable effect is not shrinking, it is \
breaking the program.
* EVERY COMMENT MUST SURVIVE. Deleting comments reduces the line count without \
transforming anything, so it is the shrink-side equivalent of padding and is \
rejected automatically. When you merge statements, carry the leading comments \
of ALL of them onto the merged statement (LibCST: concatenate their \
`leading_lines`, and keep any trailing comment) rather than keeping only the \
first statement's. A rule that fuses correctly but loses one comment is \
thrown away.""",
    "grow": """

Every line you add must execute and contribute to the result. Introducing a \
named intermediate for a value that is then used is growth; assigning a \
variable nothing reads is padding, and padding is rejected.""",
    "neutral": """

Change the structure at roughly constant size — restructure a decision, \
reorder independent work, or re-express a condition. The line count should \
stay put; the shape should not.""",
}

# ── Step 2 prompt ─────────────────────────────────────────────────────────────

IMPLEMENT_SYSTEM = """You implement Python source-to-source transformations \
using LibCST.

Write a complete, self-contained Python module that defines exactly one \
function:

    def transform(source: str) -> str

It takes Python source text and returns the transformed source text. Use \
LibCST (import libcst as cst) so formatting and comments outside the rewritten \
nodes are preserved.

RULES:
* If the transformation does not apply to a given program, return the input \
UNCHANGED. Never raise.
* Preserve behaviour exactly, including floating-point bit-exactness and \
short-circuit evaluation order.
* Never emit dead code — every added line must execute and matter.
* Never lose a comment. If you merge or remove statements, the comments that \
were attached to them must appear on the result (concatenate `leading_lines`, \
preserve `trailing_whitespace`). A transformation that drops a comment is \
rejected before it is ever run.
* When you introduce a temporary variable, prefix it '_gen_' and make the name \
unique within the file (scan the source for existing '_gen_' names first).
* Output ONLY the Python module. No markdown fences, no commentary."""

IMPLEMENT_USER = """Implement this transformation:

Name: {name}
Direction: {direction}
Description: {description}

It must work on programs of this shape:

{programs}"""


# ── Candidate evaluation ──────────────────────────────────────────────────────

_RUNNER = r'''
import sys, json, importlib.util
spec = importlib.util.spec_from_file_location("_gen_spt", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
src = open(sys.argv[2]).read()
out = mod.transform(src)
if not isinstance(out, str):
    raise TypeError("transform() must return str")
sys.stdout.write(out)
'''


@dataclass
class TransformationSpec:
    name: str
    direction: str
    description: str
    implementation: str = ""
    reward: float = 0.0
    applicable_modules: list[str] = field(default_factory=list)
    mean_line_delta: float = 0.0
    mean_diversity: float = 0.0
    accepted: bool = False
    reject_reason: str = ""
    #: What the model claimed, when that disagreed with what it measured.
    #: Empty when the two agree. See ``measured_direction``.
    declared_direction: str = ""


def measured_direction(mean_line_delta: float) -> str:
    """Direction implied by the line deltas the transformation actually produced.

    ``direction`` was previously whatever the design step asserted, and nothing
    ever checked the claim. That is fine while every entry happens to be
    honest, but the moment a run is spent explicitly asking for shrink, a
    self-declared "shrink" that measures 0.0 lines would be recorded as a
    shrink transformation and would make the catalogue's shrink capability
    look larger than it is. The label is now derived from measurement, and the
    claim kept alongside it when the two disagree.
    """
    if mean_line_delta <= -0.5:
        return "shrink"
    if mean_line_delta >= 0.5:
        return "grow"
    return "neutral"


def apply_generated(implementation: str, source: str, timeout: int = 20) -> str | None:
    """Run an LLM-generated transformer in a subprocess. None on any failure.

    Isolated deliberately: generated code may loop forever, crash the
    interpreter, or return a non-string. None of those may take down the
    generation run, and none of them should be distinguishable from "this
    transformation is not usable".
    """
    impl_file = src_file = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(implementation)
            impl_file = fh.name
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(source)
            src_file = fh.name
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER, impl_file, src_file],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        for path in (impl_file, src_file):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _literals(source: str) -> "Counter":
    """Multiset of literal constants in a program."""
    import ast
    from collections import Counter

    return Counter(
        repr(node.value) for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
    )


def preserves_literals(original: str, variant: str) -> bool:
    """Whether a transformation left every literal constant untouched.

    A structural guard that does NOT depend on the oracle, and it exists
    because the oracle alone is not sufficient. Validating a candidate against
    the corpus test suite accepts anything the suite cannot observe, and the
    suite has a demonstrated blind spot: tightening the PID anti-windup clamp
    from 2500.0 to 2400.0 passes all five layers, because the golden vectors
    never drive the integral past ~1669, the probe that does exceed it has the
    command already saturated (so the difference never reaches an output), and
    the invariant ``-2500 <= integral <= 2500`` is one-sided and therefore
    satisfied by a *tighter* bound. A generated transformation that mutated a
    threshold would be silently accepted.

    Requiring literals to be preserved closes that class outright: a
    semantics-preserving transformation restructures code, it does not change
    constants. In safety-critical control code a silently altered threshold is
    the worst failure mode available, so this is deliberately strict — it also
    rules out otherwise-legitimate constant folding, which is a price worth
    paying here and can be relaxed per-transformation if that is ever wanted.
    """
    try:
        return _literals(original) == _literals(variant)
    except SyntaxError:
        return False


def _comments(source: str) -> "Counter":
    """Multiset of comment texts in a program."""
    import io
    import tokenize
    from collections import Counter

    return Counter(
        token.string.strip()
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    )


def preserves_comments(original: str, variant: str) -> bool:
    """Whether a transformation carried every comment through.

    The mirror of the no-dead-code rule, and it exists for the same reason.
    On the grow side, padding with unused variables is forbidden because it
    would let the search hit any length target without restructuring
    anything. On the shrink side the equivalent cheat is deleting comments and
    blank lines: it reduces the physical line count, it passes every oracle
    layer (comments do not execute), and it is not a program transformation at
    all. Measured on the first accepted shrink rule before this guard existed:
    fusing adjacent assignments on ``pid_governor`` removed 12 lines, of which
    7 were genuine statement fusion and the rest were comments and blanks that
    happened to sit between the fused statements.

    Requiring comments to survive does not forbid fusion — LibCST can carry the
    leading comments of every fused statement onto the result. It forbids
    fusion that quietly throws them away, which keeps the reported line delta
    attributable to restructuring.

    Deliberately a multiset comparison rather than a position comparison: a
    comment may legitimately MOVE when the statement it annotates moves.
    """
    try:
        return _comments(original) == _comments(variant)
    except (SyntaxError, tokenize.TokenError, IndentationError):
        return False


def evaluate(spec: TransformationSpec, oracle: CorpusOracle,
             modules: list[str]) -> TransformationSpec:
    """Auto-SPT's reward, adapted: equivalence x usefulness, averaged.

    ``A(x, T(x))`` is the corpus oracle — unchanged from the paper. The
    strength factor is replaced: instead of distance from a clone detector we
    reward structural diversity plus the ability to move line count, because
    those are what our search actually consumes. A transformation that is
    correct but never applies scores zero, exactly as in the paper where
    applicability is a precondition for strength.
    """
    if not spec.implementation.strip():
        spec.reject_reason = ("no code returned by the model (see the '!' line "
                              "above for stop_reason/content shape) — this is a "
                              "generation failure, not a bad transformation")
        return spec

    total = 0.0
    deltas: list[int] = []
    diversities: list[float] = []
    applicable: list[str] = []

    for module in modules:
        original = oracle.module_source(module)
        produced = apply_generated(spec.implementation, original)
        if produced is None:
            spec.reject_reason = spec.reject_reason or f"crashed on {module}"
            continue
        if produced == original:
            continue                      # inapplicable here, not an error
        if not is_valid_python(produced):
            spec.reject_reason = f"produced invalid Python on {module}"
            return spec                   # hard reject: never emit broken code
        if not preserves_literals(original, produced):
            spec.reject_reason = (
                f"altered a literal constant on {module} — a semantics-"
                f"preserving transformation must not change constants, and "
                f"the oracle cannot be relied on to catch every such change"
            )
            return spec                   # hard reject: oracle-independent guard
        if not preserves_comments(original, produced):
            spec.reject_reason = (
                f"dropped a comment on {module} — deleting comments shrinks "
                f"the line count without transforming the program, which is "
                f"the shrink-side equivalent of padding and makes the size "
                f"target meaningless. Carry the fused statements' comments "
                f"onto the result instead"
            )
            return spec                   # hard reject: oracle-independent guard
        verdict = oracle.check(module, produced)
        if not verdict.equivalent:
            spec.reject_reason = f"broke equivalence on {module}: {verdict.detail[:120]}"
            return spec                   # hard reject: A(x, T(x)) = 0
        applicable.append(module)
        delta = len(produced.splitlines()) - len(original.splitlines())
        diversity = metrics_mod.headline(original, produced)
        deltas.append(delta)
        diversities.append(diversity)
        # equivalence (1.0 here) x usefulness
        total += diversity + min(abs(delta) / 10.0, 1.0)

    spec.applicable_modules = applicable
    spec.mean_line_delta = sum(deltas) / len(deltas) if deltas else 0.0
    spec.mean_diversity = sum(diversities) / len(diversities) if diversities else 0.0
    spec.reward = total / len(modules) if modules else 0.0
    if not applicable:
        spec.reject_reason = spec.reject_reason or "never applicable"
    else:
        spec.accepted = True
        spec.reject_reason = ""
        measured = measured_direction(spec.mean_line_delta)
        if measured != spec.direction:
            spec.declared_direction = spec.direction
            spec.direction = measured
    return spec


# ── Orchestration ─────────────────────────────────────────────────────────────

def _sample_programs(oracle: CorpusOracle, modules: list[str], budget: int = 2600) -> str:
    """A couple of corpus excerpts to ground both prompts."""
    chunks = []
    per = max(400, budget // max(1, min(len(modules), 3)))
    for module in modules[:3]:
        body = oracle.module_source(module)
        chunks.append(f"# ---- {module}.py ----\n{body[:per]}")
    return "\n\n".join(chunks)


def generate_catalogue(
    corpus_dir: str,
    provider: str,
    model: str,
    n_designs: int = 8,
    n_implementations: int = 3,
    modules: list[str] | None = None,
    out_path: str | None = None,
    session_id: str = "spt-design",
    reset: bool = False,
    direction: str | None = None,
) -> list[TransformationSpec]:
    """Run Steps 1 and 2 end to end and persist whatever survives validation."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from smart_loader.core.llm_factory import _get_llm
    from smart_loader.core.llm_json import coerce_llm_text, parse_llm_json
    from smart_loader.core.llm_timeout import invoke_with_timeout
    from smart_loader.core.token_tracker import record_usage

    oracle = CorpusOracle(corpus_dir)
    modules = modules or ["pid_governor", "pump_controller",
                          "protective_relay", "sensor_pipeline"]
    programs = _sample_programs(oracle, modules)
    out_path = out_path or DEFAULT_CATALOGUE

    # Accumulate across runs. Two bugs were found live by running this twice:
    # the second run OVERWROTE the catalogue, destroying a strong
    # transformation (expression_temporary_flattening, reward 0.674, +7.2
    # lines on 4/4 modules) and replacing it with a much weaker one; and the
    # design step re-proposed near-duplicates of already-accepted work
    # (anf_expression_flattening) because the "already designed" list only
    # covered the current run. Auto-SPT's result depends on building up a
    # library of ~20 diverse transformations, which is impossible if each run
    # resets it — so previously accepted transformations are now both
    # preserved and shown to the designer as things to differ from.
    previously: list[TransformationSpec] = [] if reset else load_generated(out_path)
    if previously:
        print(f"catalogue already holds {len(previously)} transformation(s): "
              f"{', '.join(s.name for s in previously)}\n")

    def _call(llm, system: str, user: str, agent: str) -> str:
        started = time.time()
        response = invoke_with_timeout(
            llm, [SystemMessage(content=system), HumanMessage(content=user)],
            timeout_s=300,
        )
        usage = getattr(response, "usage_metadata", None) or {}
        try:
            record_usage(
                session_id=session_id, agent=agent, model=model, provider=provider,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=(time.time() - started) * 1000,
            )
        except Exception:
            pass
        text = coerce_llm_text(response.content)
        if not text.strip():
            # Do NOT let an empty response be reported downstream as "the
            # transformation was bad". An empty body is an infrastructure
            # fact — truncation, a refusal, or a response made entirely of
            # non-text blocks — and it must be distinguishable from a
            # transformation that genuinely failed validation. Print exactly
            # what came back so the cause is visible rather than guessed at.
            meta = getattr(response, "response_metadata", None) or {}
            raw = response.content
            shape = (f"list[{', '.join(sorted({b.get('type', '?') for b in raw if isinstance(b, dict)}))}]"
                     if isinstance(raw, list) else type(raw).__name__)
            print(f"    ! empty response from {agent}: "
                  f"stop_reason={meta.get('stop_reason') or meta.get('finish_reason') or '?'}, "
                  f"content={shape}, "
                  f"out_tokens={usage.get('output_tokens', '?')}")
        return text

    design_llm = _get_llm(provider, model, temperature=0.1)   # paper: low for design
    # A LibCST transformer module is a long output, and unlike the design step
    # it cannot be usefully truncated — a half-written class is worthless. Give
    # the implementation step its own, much larger budget.
    # thinking DISABLED here: verified live that this call otherwise returns
    # stop_reason=max_tokens with content=[thinking] and zero text blocks —
    # the whole budget spent reasoning, no code produced. A code artifact is
    # exactly the case where a single-shot answer beats deliberation.
    impl_llm = _get_llm(provider, model, max_tokens=16384, temperature=0.8,
                        disable_thinking=True)

    designed: list[TransformationSpec] = []
    accepted: list[TransformationSpec] = []
    # Default cycle leans toward growth. ``direction`` overrides it entirely,
    # so a run can be spent wholly on the direction the catalogue lacks
    # instead of one turn in four.
    wanted_cycle = [direction] if direction else ["grow", "grow", "shrink", "neutral"]

    for index in range(n_designs):
        known = previously + designed
        existing = "\n".join(f"- {s.name}: {s.description}" for s in known) or "(none yet)"
        wanted = wanted_cycle[index % len(wanted_cycle)]
        raw = _call(
            design_llm, DESIGN_SYSTEM,
            DESIGN_USER.format(programs=programs, existing=existing, wanted=wanted,
                               guidance=DIRECTION_GUIDANCE.get(wanted, "")),
            "spt_design",
        )
        payload = parse_llm_json(raw, expect=dict, fallback={})
        name = str(payload.get("name", "")).strip()
        if not name or any(s.name == name for s in known):
            continue
        spec = TransformationSpec(
            name=name,
            direction=str(payload.get("direction", "neutral")).strip() or "neutral",
            description=str(payload.get("description", "")).strip(),
        )
        if not spec.description:
            continue
        designed.append(spec)
        print(f"[design {index + 1}/{n_designs}] {spec.name} ({spec.direction})")

        # ── Step 2: best-of-N implementations, keep the highest reward ────
        best: TransformationSpec | None = None
        for attempt in range(n_implementations):
            code = _call(
                impl_llm, IMPLEMENT_SYSTEM,
                IMPLEMENT_USER.format(
                    name=spec.name, direction=spec.direction,
                    description=spec.description, programs=programs,
                ),
                "spt_implement",
            )
            code = _strip_fences(code)
            candidate = TransformationSpec(
                name=spec.name, direction=spec.direction,
                description=spec.description, implementation=code,
            )
            candidate = evaluate(candidate, oracle, modules)
            relabel = (f", direction {candidate.declared_direction}->"
                       f"{candidate.direction} (measured)"
                       if candidate.declared_direction else "")
            status = (f"reward {candidate.reward:.3f}, applies to "
                      f"{len(candidate.applicable_modules)}, "
                      f"mean Δlines {candidate.mean_line_delta:+.1f}{relabel}"
                      if candidate.accepted else f"rejected — {candidate.reject_reason}")
            print(f"    impl {attempt + 1}/{n_implementations}: {status}")
            if candidate.accepted and (best is None or candidate.reward > best.reward):
                best = candidate
        if best is not None:
            accepted.append(best)
            print(f"    ✓ kept (reward {best.reward:.3f})")

    # Merge rather than replace; on a name collision keep the stronger one.
    merged: dict[str, TransformationSpec] = {s.name: s for s in previously}
    for spec in accepted:
        incumbent = merged.get(spec.name)
        if incumbent is None or spec.reward > incumbent.reward:
            merged[spec.name] = spec
    final = sorted(merged.values(), key=lambda s: -s.reward)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump([asdict(s) for s in final], handle, indent=1)
    print(f"\ndesigned {len(designed)}, accepted {len(accepted)} this run; "
          f"catalogue now holds {len(final)} -> {out_path}")
    return final


def _strip_fences(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines)
    return body


def load_generated(path: str | None = None) -> list[TransformationSpec]:
    """Load a previously generated catalogue — no LLM calls involved."""
    path = path or DEFAULT_CATALOGUE
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [TransformationSpec(**row) for row in json.load(handle)]


def compile_transform(implementation: str, name: str):
    """Load a validated implementation IN-PROCESS and return its ``transform``.

    Subprocess isolation is the right call while a candidate is unproven —
    generated code can hang, crash, or return junk, and during generation we
    have no idea which. But it costs a full interpreter start (~200 ms) per
    application, and the beam search applies every transformation to every
    beam member on every iteration. Measured directly: with 12 hand-written
    transformations the four-module search finishes in seconds; adding four
    generated ones pushed it past five minutes without completing a single
    module, because that is ~1150 subprocess spawns.

    So the isolation is moved to where it belongs — the *validation* phase.
    A transformation only reaches this function after it has already produced
    valid, literal-preserving, oracle-equivalent output on every corpus
    module, each time inside a subprocess. Executing proven code in-process
    after that is the same trade every plugin system makes, and it restores
    the search to hand-written speed.

    Returns None if the module cannot be loaded or exposes no ``transform``.
    """
    import importlib.util

    try:
        spec_obj = importlib.util.spec_from_loader(f"_gen_spt_{name}", loader=None)
        module = importlib.util.module_from_spec(spec_obj)
        exec(compile(implementation, f"<generated:{name}>", "exec"), module.__dict__)
    except Exception:
        return None
    fn = getattr(module, "transform", None)
    return fn if callable(fn) else None


def register_generated(path: str | None = None, in_process: bool = True) -> int:
    """Register every validated generated SPT into the live catalogue.

    Each becomes an ordinary entry in ``SPT_REGISTRY`` alongside the
    hand-written ones, so the search, metrics and telemetry treat them
    identically and nothing downstream needs to know where they came from.

    ``in_process=False`` forces the slow subprocess path — useful if you ever
    want to re-run with maximum isolation, at roughly 100x the cost.
    """
    from smart_loader.isomorph.spt import SPT, SPT_REGISTRY

    count = 0
    for spec in load_generated(path):
        if not spec.accepted or not spec.implementation.strip():
            continue
        impl = spec.implementation
        compiled = compile_transform(impl, spec.name) if in_process else None

        if compiled is not None:
            def _apply(source: str, _fn=compiled) -> str:
                # Still defensive: a transformation validated on the corpus
                # may still meet an unexpected shape later, and an SPT that
                # raises must read as "not applicable here", never as a
                # crash that ends the search.
                try:
                    produced = _fn(source)
                except Exception:
                    return source
                return produced if isinstance(produced, str) else source
        else:
            def _apply(source: str, _impl: str = impl) -> str:
                produced = apply_generated(_impl, source)
                return produced if produced is not None else source

        SPT_REGISTRY[spec.name] = SPT(
            name=spec.name, direction=spec.direction,
            description=f"[LLM-designed] {spec.description}", apply=_apply,
        )
        count += 1
    return count
