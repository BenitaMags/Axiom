"""LLM-proposed program-isomorphic variants, gated by the equivalence oracle.

Why this exists alongside the SPT search
────────────────────────────────────────
``search.LengthTargetedSearch`` explores a catalogue of thirteen deterministic
transformations. Every one of them is a peephole rewrite *inside a function
body*: ``x += 1`` becomes ``x = x + 1``, a ternary becomes an if/else, a return
expression is split into an assignment and a return. They are provably
semantics-preserving, which is their whole appeal, and they cannot restructure
a program.

Measured on D1, against a hand-written reference variant:

                          ast_structural   cfg_shape    ncd
    reference (human)            0.4548      0.2000   0.7172
    SPT search                   0.2864      0.5556   0.5697

The reference introduces a ``Channel`` value object, collapses three duplicated
accessor methods into a register map plus one generic fetch/store pair, and
extracts a saturation element into its own module. No sequence of the thirteen
can produce any of that. What the search produces instead is branch churn --
``cfg_shape`` 0.5556 against the reference's 0.2000 -- because four of the
transformations manufacture branches. A blind judge rated the reference CLEAN
with zero findings.

So this module asks a model for the restructuring and keeps the oracle as the
gate. The trust boundary does not move: a variant is shipped only if
``oracle.check`` accepts it, exactly as an SPT-derived variant is. What changes
is where candidates come from.

The oracle is not optional here. An unverified LLM rewrite is a guess, and the
whole point of A(x, x') is that it does not have to be trusted.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from smart_loader.isomorph import contract_check
from smart_loader.isomorph import metrics as metrics_mod

#: Parsed out of the reply. Same shape the migration runners use, so a model
#: that can answer one can answer the other.
_FILE_RE = re.compile(
    r"###\s*FILENAME:\s*(?P<name>[\w./-]+\.py)\s*\n+```(?:python)?\s*\n"
    r"(?P<body>.*?)```",
    re.S,
)

PROMPT = """\
Rewrite the Python module below so that it is FUNCTIONALLY IDENTICAL and \
STRUCTURALLY DIFFERENT.

Functionally identical means: same public names, same signatures, same return \
types, same values for every input, same exceptions raised in the same cases, \
same side effects in the same order. An automated equivalence check will run \
the original and your rewrite against recorded outputs and property tests. If \
they disagree anywhere, your rewrite is discarded.

It also means the same ATTRIBUTES, and attributes that can still be ASSIGNED \
to. If the original set `self.kp = kp` in `__init__`, a caller can write \
`obj.kp = 3.0` afterwards. Replacing that with a read-only `@property` is a \
breaking change even though every recorded value still matches, because the \
checks only ever read those attributes -- they never retune the object. If you \
introduce a property, give it a setter that writes through to wherever the \
value now lives. The same goes for any attribute the original left mutable: \
`__slots__`, a frozen dataclass, or a NamedTuple field standing in for a \
plain attribute all break assignment that used to work.

The two are not in tension, and where they seem to be, FAITHFUL WINS. A \
rewrite that reorganises less and breaks nothing is worth more than an \
ambitious one that changes what callers can do. Restructure the INSIDE of this \
module freely; treat everything a caller can reach as a fixed contract.

Specifically, leave the public surface alone:

- an attribute the original assigned in ``__init__`` must stay ASSIGNABLE. \
`self.kp = kp` means a caller can write `obj.kp = 3.0` later. A getter-only \
`@property` breaks that. If you introduce a property, give it a setter.
- a class the original declared `@dataclass` must stay one. Callers use \
`dataclasses.fields()`, `.replace()` and `.astuple()`, and a hand-written \
class raises TypeError for all three.
- an import the original deferred inside a function must stay deferred. \
`import x` inside a factory means the module can be imported when `x` is not \
installed -- which is the whole point of injecting a collaborator. Hoisting it \
to the top, or into a new module you import at the top, breaks that.
- same class names, same method names, same argument names, same defaults, \
same return types, same exception types.

Those four are not hypothetical: each one is a real rewrite that reproduced \
every recorded value, passed the equivalence check, and was rejected on review.

Structurally different means the code is organised differently -- not the same \
code with renamed variables or an if/else swapped for a ternary. Within that \
fixed surface, reach for changes a reviewer would call a different design:

- replace repeated literal groups with a value object (a NamedTuple or \
dataclass) that carries them together, with the operations on them as methods;
- collapse several near-identical functions that differ only in constants into \
one generic operation plus a table of those constants;
- extract a cohesive helper into a NEW module and import it;
- replace an inline expression repeated at several call sites with one named \
helper;
- change how the work is decomposed: a pipeline of stages instead of one long \
function, or a dispatch table instead of a chain of conditionals.

Do NOT change observable behaviour to achieve any of this. Preserve every \
numeric constant, every register address, every scale factor, every error \
message, exactly. Where the original clamps, rounds, or short-circuits, your \
rewrite must do so identically -- including on edge inputs like NaN, zero, \
empty, and inverted bounds.

If a restructuring you are considering would require changing any of the four \
surface rules above, do not do it. Pick a different restructuring, or do less. \
An automated check rejects the rewrite for each of them, so a variant that \
trades one for a better shape is simply discarded.

{length}
Reply with one `### FILENAME: {module}.py` block for the rewritten module, \
followed by a `### FILENAME: <name>.py` block for EACH new module you add. \
Each block is a ```python fenced block containing that file's complete \
contents. No commentary outside the blocks.

### MODULE TO REWRITE: {module}.py

```python
{source}```
"""


@dataclass
class LLMVariantResult:
    """Mirrors ``search.SearchResult`` so callers need no special case."""
    module: str
    original_source: str
    variant_source: str
    requested_lines: int
    achieved_lines: int
    original_lines: int
    applied: list[str]
    diversity: dict
    headline_diversity: float
    ranking_metric: str
    oracle_passed: bool
    oracle_detail: str
    iterations: int
    candidates_generated: int
    candidates_equivalent: int
    duration_s: float
    extra_files: dict = field(default_factory=dict)

    @property
    def length_error(self) -> int:
        """Signed miss against the request, as ``SearchResult`` reports it."""
        return self.achieved_lines - self.requested_lines


#: The same header, but written INSIDE the fence instead of above it. Measured
#: on C2: two of three attempts opened with ```python and then
#: ``### FILENAME: fsm.py`` as the first line of the block, so the strict
#: pattern -- which wants the header first -- matched nothing and the run fell
#: back to the transformation search with ast_structural 0.07.
_INLINE_HEADER_RE = re.compile(
    r"```(?:python)?\s*\n\s*###\s*FILENAME:\s*(?P<name>[\w./-]+\.py)\s*\n"
    r"(?P<body>.*?)```",
    re.S,
)


def parse_reply(reply: str, module: str) -> tuple[str, dict]:
    """``(rewritten module source, {added filename: source})``.

    Returns ``("", {})`` when the module itself is absent: a reply that adds
    helpers without the module they serve is not usable, and guessing which
    block was meant to be the module would ship the wrong file.

    One fallback, and it involves no guessing: a reply that opens the fence
    before the ``### FILENAME:`` header rather than after it. The model named
    every file, in order, just inside the block instead of above it -- the same
    "produced the code and labelled it differently" case ``parse_files_lenient``
    allows for in the migration runners. Matching fences to filenames by
    POSITION is still refused: getting the order wrong writes valid code into
    the wrong module and invents a failure the model did not commit.
    """
    blocks = {os.path.basename(m.group("name")): m.group("body")
              for m in _FILE_RE.finditer(reply)}
    if f"{module}.py" not in blocks:
        for m in _INLINE_HEADER_RE.finditer(reply):
            blocks.setdefault(os.path.basename(m.group("name")), m.group("body"))
    main = blocks.pop(f"{module}.py", "")
    return main, blocks


def _diagnose(reply: str, module: str) -> str:
    """Why a reply did not parse, from the reply itself.

    "reply contained no block for the module itself" is true and useless: it
    does not distinguish a model that answered in prose, one that named the
    file differently, and one whose answer was cut off mid-fence. Those need
    different fixes and are indistinguishable from a character count --
    ``run_single_shot`` learned this the same way and writes its unparseable
    replies to disk for the same reason.
    """
    if not reply.strip():
        return "reply was empty"
    fences = reply.count("```")
    bits = [f"{len(reply)} chars", f"{fences} fence marker(s)"]
    if fences % 2:
        bits.append("ODD number of fences — the reply was cut off mid-block")
    names = re.findall(r"###\s*FILENAME:\s*([\w./-]+\.py)", reply)
    if not names:
        bits.append("no `### FILENAME:` line at all — answered in prose?")
    else:
        bits.append(f"named {names}, wanted {module}.py")
    return "; ".join(bits)


def generate(oracle, module: str, source: str, llm, *,
             target_lines: int | None = None,
             attempts: int = 3,
             ranking_metric: str = metrics_mod.DEFAULT_RANKING_METRIC,
             failure_dir: str = "llm_variant_failures",
             siblings: set | None = None,
             on_event=None) -> LLMVariantResult:
    """Ask for a restructuring, keep the first one the oracle accepts.

    ``attempts`` are sequential and independent -- the model is not shown its
    rejected attempt. A rejection means the rewrite was not equivalent, and the
    oracle's detail names the layer that caught it, but feeding that back
    invites the model to edit toward the check rather than toward a faithful
    rewrite. Whether showing it helps is worth measuring; asserting that it
    does is not.

    The best REJECTED candidate is never returned. ``oracle_passed=False`` with
    an empty ``variant_source`` is the honest answer: the caller falls back to
    the unmodified patch, which is what shipping nothing means here.
    """
    started = time.time()
    original_lines = len(source.splitlines())
    length = ""
    if target_lines:
        length = (f"Aim for roughly {target_lines} lines in the rewritten "
                  f"module (currently {original_lines}); being close is "
                  f"enough, and never at the cost of behaviour.\n\n")
    prompt = PROMPT.format(module=module, source=source, length=length)

    generated = equivalent = 0
    last_detail = "no candidate produced"
    for attempt in range(1, attempts + 1):
        try:
            reply = llm(prompt)
        except Exception as exc:                              # noqa: BLE001
            last_detail = f"generation error: {type(exc).__name__}: {exc}"
            if on_event:
                on_event(attempt, "error", last_detail)
            continue
        variant, extra = parse_reply(reply or "", module)
        if not variant.strip():
            last_detail = _diagnose(reply or "", module)
            if failure_dir:
                try:
                    os.makedirs(failure_dir, exist_ok=True)
                    where = os.path.join(
                        failure_dir, f"{module}_attempt{attempt}.txt")
                    with open(where, "w", encoding="utf-8") as fh:
                        fh.write(reply or "")
                    last_detail += f"; raw reply -> {where}"
                except OSError:
                    pass
            if on_event:
                on_event(attempt, "unparsed", last_detail)
            continue
        generated += 1

        # The public surface, which the oracle structurally cannot see: it
        # exercises the normal call path and never assigns to an attribute,
        # introspects a dataclass, or imports the module with the collaborator
        # absent. Three oracle-PASS variants were caught by a blind judge on
        # exactly those three shapes -- see contract_check.
        broken = contract_check.divergences(source, variant, extra, siblings)
        if broken:
            last_detail = "public contract changed: " + "; ".join(broken)[:300]
            if on_event:
                on_event(attempt, "contract", last_detail)
            continue

        verdict = oracle.check(module, variant, extra_files=extra)
        if not verdict.equivalent:
            last_detail = verdict.detail
            if on_event:
                on_event(attempt, "rejected", verdict.detail)
            continue
        equivalent += 1

        # Measured over the module plus whatever it extracted, because a
        # restructuring that moves half the file into a new module has not
        # diversified anything if the two halves are read separately.
        joined = variant + "\n" + "\n".join(extra[k] for k in sorted(extra))
        diversity = metrics_mod.measure_all(source, joined)
        return LLMVariantResult(
            module=module, original_source=source, variant_source=variant,
            requested_lines=(target_lines or len(variant.splitlines())),
            achieved_lines=len(variant.splitlines()),
            original_lines=original_lines,
            applied=["llm_restructure"] + [f"added:{k}" for k in sorted(extra)],
            diversity=diversity,
            headline_diversity=diversity.get(ranking_metric, 0.0),
            ranking_metric=ranking_metric,
            oracle_passed=True, oracle_detail=verdict.detail,
            iterations=attempt, candidates_generated=generated,
            candidates_equivalent=equivalent,
            duration_s=round(time.time() - started, 3),
            extra_files=extra,
        )

    return LLMVariantResult(
        module=module, original_source=source, variant_source="",
        requested_lines=(target_lines or original_lines),
        achieved_lines=original_lines, original_lines=original_lines,
        applied=[], diversity={}, headline_diversity=0.0,
        ranking_metric=ranking_metric,
        oracle_passed=False, oracle_detail=last_detail,
        iterations=attempts, candidates_generated=generated,
        candidates_equivalent=0,
        duration_s=round(time.time() - started, 3),
    )
