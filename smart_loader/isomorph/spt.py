"""Semantic Preserving Transformations (SPTs) — the substitution primitives.

Design follows Auto-SPT (Hooda et al., arXiv:2512.06042, 2025), which
separates an SPT's *design* from its *application*: an LLM designs and
implements each transformation once, and thereafter the transformation is a
deterministic program applied with no further LLM calls. That split is what
makes the hybrid tractable — the LLM contributes diversity of transformation
*types*, while application stays reproducible, auditable, and cheap.

Two departures from Auto-SPT, both driven by our problem rather than theirs:

1. Auto-SPT searches for the transformation that maximally *evades a clone
   detector*. We have no clone detector and no adversarial objective: we
   search for a variant of a requested SIZE, subject to equivalence. So each
   SPT here declares a ``direction`` (grow / shrink / neutral) and reports the
   line delta it achieved, which the search in ``search.py`` uses to steer.

2. Auto-SPT's generated implementations are free-form Python string
   manipulators. Ours are LibCST transformers. LibCST preserves formatting
   and comments exactly outside the nodes it rewrites, so a variant differs
   only where a transformation genuinely acted — which keeps the diversity
   measurements attributable to real transformations rather than to
   incidental reformatting.

Every SPT is registered in ``SPT_REGISTRY``. Adding one is a single
decorator, and nothing else in the pipeline needs to change — this is the
modularity the metric/telemetry layers depend on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

import libcst as cst


# ── SPT abstraction ───────────────────────────────────────────────────────────

GROW = "grow"       # reliably increases line count
SHRINK = "shrink"   # reliably decreases line count
NEUTRAL = "neutral" # changes structure at ~constant size


@dataclass(frozen=True)
class SPT:
    """One semantics-preserving transformation.

    ``apply`` takes source text and returns transformed source text. It must
    never raise on syntactically valid input: an SPT that cannot apply to a
    given program returns the input unchanged, and the search treats "no
    change" as "not applicable here" rather than as an error. This mirrors
    Auto-SPT's applicability notion, where a transformation is counted as
    applicable only when the result actually differs from the original.
    """

    name: str
    direction: str
    description: str
    apply: Callable[[str], str]

    def __call__(self, source: str) -> str:
        try:
            return self.apply(source)
        except Exception:
            # An SPT that fails on an edge case must not kill the search;
            # returning the input marks it inapplicable for this program.
            return source


SPT_REGISTRY: dict[str, SPT] = {}


def register(name: str, direction: str, description: str):
    """Decorator registering a transformer function as an SPT."""
    def _wrap(fn: Callable[[str], str]) -> Callable[[str], str]:
        SPT_REGISTRY[name] = SPT(
            name=name, direction=direction, description=description, apply=fn,
        )
        return fn
    return _wrap


def spts(direction: str | None = None) -> list[SPT]:
    """All registered SPTs, optionally filtered to one direction."""
    values = list(SPT_REGISTRY.values())
    if direction is None:
        return values
    return [s for s in values if s.direction == direction]


def _highest_temp(source: str, prefix: str) -> int:
    """Highest existing ``<prefix><n>`` index, so composed applications of a
    temp-introducing SPT never re-issue a name that is already bound."""
    import re as _re
    found = [int(m) for m in _re.findall(rf"{_re.escape(prefix)}(\d+)\b", source)]
    return max(found) if found else 0


def _rewrite(source: str, transformer: cst.CSTTransformer) -> str:
    """Parse, transform, and re-emit — preserving untouched formatting."""
    module = cst.parse_module(source)
    return module.visit(transformer).code


# ── GROW transformations ──────────────────────────────────────────────────────

class _ExpandAugAssign(cst.CSTTransformer):
    """``x += 1`` → ``x = x + 1``."""

    def leave_AugAssign(self, original, updated):
        if not isinstance(updated.target, (cst.Name, cst.Attribute)):
            return updated
        op_map = {
            cst.AddAssign: cst.Add, cst.SubtractAssign: cst.Subtract,
            cst.MultiplyAssign: cst.Multiply, cst.DivideAssign: cst.Divide,
            cst.ModuloAssign: cst.Modulo, cst.PowerAssign: cst.Power,
            cst.FloorDivideAssign: cst.FloorDivide,
        }
        binop = op_map.get(type(updated.operator))
        if binop is None:
            return updated
        read_target = updated.target.with_changes()
        return cst.Assign(
            targets=[cst.AssignTarget(target=updated.target)],
            value=cst.BinaryOperation(
                left=read_target, operator=binop(), right=updated.value,
            ),
        )


@register(
    "expand_aug_assign", GROW,
    "Rewrite augmented assignment (x += 1) as explicit assignment (x = x + 1).",
)
def expand_aug_assign(source: str) -> str:
    return _rewrite(source, _ExpandAugAssign())


class _ExpandChainedCompare(cst.CSTTransformer):
    """``a < b < c`` → ``a < b and b < c`` (only when operands are pure names/numbers).

    Restricted to side-effect-free operands: Python evaluates the middle
    operand of a chained comparison exactly once, so duplicating a call
    expression would change semantics. Names and literals are safe to repeat.
    """

    @staticmethod
    def _pure(node: cst.BaseExpression) -> bool:
        return isinstance(node, (cst.Name, cst.Integer, cst.Float, cst.Attribute))

    def leave_Comparison(self, original, updated):
        if len(updated.comparisons) < 2:
            return updated
        operands = [updated.left] + [c.comparator for c in updated.comparisons]
        if not all(self._pure(o) for o in operands):
            return updated
        pairs = []
        left = updated.left
        for comp in updated.comparisons:
            pairs.append(cst.Comparison(
                left=left,
                comparisons=[cst.ComparisonTarget(
                    operator=comp.operator, comparator=comp.comparator,
                )],
                lpar=[cst.LeftParen()], rpar=[cst.RightParen()],
            ))
            left = comp.comparator
        result = pairs[0]
        for nxt in pairs[1:]:
            result = cst.BooleanOperation(
                left=result, operator=cst.And(), right=nxt,
            )
        return result


@register(
    "expand_chained_compare", GROW,
    "Rewrite a chained comparison (a < b < c) as an explicit conjunction, "
    "only when every operand is side-effect free.",
)
def expand_chained_compare(source: str) -> str:
    return _rewrite(source, _ExpandChainedCompare())


#: Return values not worth extracting: naming them would add a line that
#: renames rather than computes, which is exactly the padding the length
#: target is supposed to exclude. Everything else — calls, tuples, operations,
#: comparisons, subscripts — is a real computation and is fair game.
_ATOMIC_RETURN = (
    cst.Name, cst.Integer, cst.Float, cst.Imaginary, cst.SimpleString,
    cst.ConcatenatedString, cst.FormattedString, cst.Ellipsis,
)


def _contains_yield(node: cst.CSTNode) -> bool:
    class _V(cst.CSTVisitor):
        def __init__(self) -> None:
            self.found = False

        def visit_Yield(self, node: cst.Yield) -> None:
            self.found = True

    visitor = _V()
    node.visit(visitor)
    return visitor.found


class _SplitReturnExpression(cst.CSTTransformer):
    """``return <expr>`` → ``_spt_rN = <expr>`` then ``return _spt_rN``.

    Introduces a genuine named intermediate: the value is still computed
    exactly once and returned, but the computation and the return become
    separate statements. Every added line executes and carries the result,
    so this grows the program without dead code.

    Originally restricted to ``BinaryOperation``, which was narrower than the
    rule's own description and narrower than anything in the ICS corpus —
    every return there is a ``Call`` (``return PumpState(...)``) or a bare
    ``Tuple`` (``return GovernorState(...), command``), so the rule never
    fired on any module. The extraction is equally valid for any non-atomic
    expression: the value is computed once, in the same order, and bound to a
    fresh name before being returned.
    """

    def __init__(self, start: int = 0) -> None:
        self._counter = start

    def leave_SimpleStatementLine(self, original, updated):
        if len(updated.body) != 1 or not isinstance(updated.body[0], cst.Return):
            return updated
        ret = updated.body[0]
        if ret.value is None or isinstance(ret.value, _ATOMIC_RETURN):
            return updated
        # ``return (yield x)`` binds a value produced by suspending the frame;
        # hoisting it out of the return is not obviously order-preserving, so
        # leave generators' yield-expressions alone.
        if _contains_yield(ret.value):
            return updated
        self._counter += 1
        tmp = f"_spt_r{self._counter}"
        assign = cst.SimpleStatementLine(body=[cst.Assign(
            targets=[cst.AssignTarget(target=cst.Name(tmp))], value=ret.value,
        )])
        new_ret = updated.with_changes(
            body=[ret.with_changes(value=cst.Name(tmp))],
        )
        return cst.FlattenSentinel([assign, new_ret])


@register(
    "split_return_expression", GROW,
    "Split 'return <expression>' into a named intermediate assignment "
    "followed by a return of that name.",
)
def split_return_expression(source: str) -> str:
    return _rewrite(source, _SplitReturnExpression(_highest_temp(source, "_spt_r")))


class _SplitBooleanIf(cst.CSTTransformer):
    """``if a and b:`` → ``if a:`` / ``if b:`` nested (no else branch only).

    Only applied when the ``if`` has no ``else``/``elif``, where nesting is
    exactly equivalent — including short-circuit order, which nesting
    preserves.
    """

    def leave_If(self, original, updated):
        if updated.orelse is not None:
            return updated
        test = updated.test
        if not isinstance(test, cst.BooleanOperation) or not isinstance(test.operator, cst.And):
            return updated
        inner = cst.If(test=test.right, body=updated.body)
        return updated.with_changes(
            test=test.left,
            body=cst.IndentedBlock(body=[inner]),
        )


@register(
    "split_boolean_if", GROW,
    "Rewrite 'if a and b:' (with no else) as nested if statements, "
    "preserving short-circuit order.",
)
def split_boolean_if(source: str) -> str:
    return _rewrite(source, _SplitBooleanIf())


class _ExtractSubexpression(cst.CSTTransformer):
    """Extract one compound operand of an assignment into a named temporary.

    The dominant shape in control code is a single assignment carrying a
    multi-term arithmetic expression::

        command = gains.kp * error + gains.ki * integral + gains.kd * derivative

    which becomes::

        _spt_t1 = gains.kp * error + gains.ki * integral
        command = _spt_t1 + gains.kd * derivative

    Bit-exactness matters here and is preserved: the temporary holds the
    *existing* left subtree verbatim, so operand grouping and left-to-right
    evaluation order are unchanged. This is extraction, NOT reassociation —
    reassociation would break IEEE-754 exactness (the corpus README flags
    this) and would force the tolerance-based oracle. Extraction keeps us on
    the bit-exact setting.

    Also handles a call whose first argument is compound
    (``clamp(state.integral + error * dt, lo, hi)``), which is the other
    shape these modules use constantly.
    """

    def __init__(self, start: int = 0) -> None:
        # Seeded from names already present in the source. Each application
        # constructs a fresh transformer, so a counter starting at 0 every
        # time re-issues ``_spt_t1`` on the second pass — producing a variant
        # that assigns the same temporary twice. It happens to be harmless
        # when the first value is fully consumed before reassignment (which
        # is why the oracle still passed), but it is a live correctness
        # hazard: an extraction whose temporary is still needed later would
        # be silently clobbered. Continuing the numbering makes every
        # temporary unique across composed applications.
        self._counter = start

    def _next_tmp(self) -> str:
        self._counter += 1
        return f"_spt_t{self._counter}"

    def leave_SimpleStatementLine(self, original, updated):
        if len(updated.body) != 1 or not isinstance(updated.body[0], cst.Assign):
            return updated
        assign = updated.body[0]
        if len(assign.targets) != 1:
            return updated
        value = assign.value

        # Case 1: value is a binary operation with a compound left subtree.
        if isinstance(value, cst.BinaryOperation) and isinstance(
            value.left, cst.BinaryOperation
        ):
            tmp = self._next_tmp()
            lead = cst.SimpleStatementLine(body=[cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name(tmp))],
                value=value.left,
            )])
            new_assign = updated.with_changes(body=[assign.with_changes(
                value=value.with_changes(left=cst.Name(tmp)),
            )])
            return cst.FlattenSentinel([lead, new_assign])

        # Case 2: value is a call whose first positional arg is compound.
        if isinstance(value, cst.Call) and value.args:
            first = value.args[0]
            if first.keyword is None and isinstance(first.value, cst.BinaryOperation):
                tmp = self._next_tmp()
                lead = cst.SimpleStatementLine(body=[cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name(tmp))],
                    value=first.value,
                )])
                new_args = [first.with_changes(value=cst.Name(tmp))] + list(value.args[1:])
                new_assign = updated.with_changes(body=[assign.with_changes(
                    value=value.with_changes(args=new_args),
                )])
                return cst.FlattenSentinel([lead, new_assign])

        return updated


@register(
    "extract_subexpression", GROW,
    "Extract a compound operand of an assignment (or a compound first call "
    "argument) into a named temporary, preserving evaluation order exactly.",
)
def extract_subexpression(source: str) -> str:
    return _rewrite(source, _ExtractSubexpression(_highest_temp(source, "_spt_t")))


class _TernaryToIfElse(cst.CSTTransformer):
    """``x = a if c else b`` → an explicit if/else assigning ``x``."""

    def leave_SimpleStatementLine(self, original, updated):
        if len(updated.body) != 1 or not isinstance(updated.body[0], cst.Assign):
            return updated
        assign = updated.body[0]
        if len(assign.targets) != 1 or not isinstance(assign.value, cst.IfExp):
            return updated
        ifexp = assign.value
        target = assign.targets[0].target
        make = lambda v: cst.SimpleStatementLine(body=[cst.Assign(
            targets=[cst.AssignTarget(target=target)], value=v,
        )])
        return cst.If(
            test=ifexp.test,
            body=cst.IndentedBlock(body=[make(ifexp.body)]),
            orelse=cst.Else(body=cst.IndentedBlock(body=[make(ifexp.orelse)])),
        )


@register(
    "ternary_to_if_else", GROW,
    "Rewrite a conditional expression assignment (x = a if c else b) as an "
    "explicit if/else block.",
)
def ternary_to_if_else(source: str) -> str:
    return _rewrite(source, _TernaryToIfElse())


# ── SHRINK transformations ────────────────────────────────────────────────────

class _IfElseAssignToTernary(cst.CSTTransformer):
    """``if c: x = a`` / ``else: x = b`` → ``x = a if c else b``.

    Inverse of _TernaryToIfElse. Requires both branches to be a single
    assignment to the *same* target, so the collapse is unambiguous.
    """

    @staticmethod
    def _single_assign(block) -> cst.Assign | None:
        if not isinstance(block, cst.IndentedBlock) or len(block.body) != 1:
            return None
        stmt = block.body[0]
        if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
            return None
        inner = stmt.body[0]
        if not isinstance(inner, cst.Assign) or len(inner.targets) != 1:
            return None
        return inner

    def __init__(self) -> None:
        # An ``elif`` is represented as an If node occupying the parent If's
        # ``orelse`` field. Collapsing such a node into a plain assignment
        # statement severs it from its chain: the oracle caught this on all
        # four corpus modules, where
        #     if a: x = 0
        #     elif b: x = 900
        #     else:  x = 300
        # became
        #     if a: x = 0
        #     x = 900 if b else 300
        # so x was overwritten unconditionally. Only If nodes that are real
        # statements (never an elif) may be collapsed, so we record the elif
        # nodes on the way down and skip them on the way back up.
        self._elif_ids: set[int] = set()

    def visit_If(self, node: cst.If) -> bool:
        if isinstance(node.orelse, cst.If):
            self._elif_ids.add(id(node.orelse))
        return True

    def leave_If(self, original, updated):
        if id(original) in self._elif_ids:
            return updated
        orelse = updated.orelse
        if not isinstance(orelse, cst.Else):
            return updated
        a = self._single_assign(updated.body)
        b = self._single_assign(orelse.body)
        if a is None or b is None:
            return updated
        ta, tb = a.targets[0].target, b.targets[0].target
        if not (isinstance(ta, cst.Name) and isinstance(tb, cst.Name) and ta.value == tb.value):
            return updated
        return cst.SimpleStatementLine(body=[cst.Assign(
            targets=[cst.AssignTarget(target=ta)],
            value=cst.IfExp(test=updated.test, body=a.value, orelse=b.value),
        )])


@register(
    "if_else_assign_to_ternary", SHRINK,
    "Collapse an if/else where both branches assign the same target into a "
    "single conditional-expression assignment.",
)
def if_else_assign_to_ternary(source: str) -> str:
    return _rewrite(source, _IfElseAssignToTernary())




class _CollapseAugAssign(cst.CSTTransformer):
    """``x = x + 1`` → ``x += 1`` (exact inverse of _ExpandAugAssign)."""

    def leave_Assign(self, original, updated):
        if len(updated.targets) != 1:
            return updated
        target = updated.targets[0].target
        value = updated.value
        if not isinstance(target, cst.Name) or not isinstance(value, cst.BinaryOperation):
            return updated
        if not isinstance(value.left, cst.Name) or value.left.value != target.value:
            return updated
        op_map = {
            cst.Add: cst.AddAssign, cst.Subtract: cst.SubtractAssign,
            cst.Multiply: cst.MultiplyAssign, cst.Divide: cst.DivideAssign,
            cst.Modulo: cst.ModuloAssign, cst.Power: cst.PowerAssign,
            cst.FloorDivide: cst.FloorDivideAssign,
        }
        aug = op_map.get(type(value.operator))
        if aug is None:
            return updated
        return cst.AugAssign(target=target, operator=aug(), value=value.right)


@register(
    "collapse_aug_assign", SHRINK,
    "Rewrite 'x = x + 1' as augmented assignment 'x += 1'.",
)
def collapse_aug_assign(source: str) -> str:
    return _rewrite(source, _CollapseAugAssign())


class _MergeNestedIf(cst.CSTTransformer):
    """``if a:`` containing only ``if b:`` → ``if a and b:``.

    Inverse of _SplitBooleanIf. Only when neither has an else and the outer
    body is exactly the single inner if.
    """

    def leave_If(self, original, updated):
        if updated.orelse is not None:
            return updated
        body = updated.body
        if not isinstance(body, cst.IndentedBlock) or len(body.body) != 1:
            return updated
        inner = body.body[0]
        if not isinstance(inner, cst.If) or inner.orelse is not None:
            return updated
        return updated.with_changes(
            test=cst.BooleanOperation(
                left=updated.test, operator=cst.And(), right=inner.test,
            ),
            body=inner.body,
        )


@register(
    "merge_nested_if", SHRINK,
    "Merge a nested 'if a: if b:' (no else on either) into 'if a and b:'.",
)
def merge_nested_if(source: str) -> str:
    return _rewrite(source, _MergeNestedIf())


def _contains_continue(node: cst.CSTNode) -> bool:
    """True if ``continue`` appears anywhere beneath ``node``.

    Deliberately conservative — it does not distinguish a ``continue`` bound to
    this loop from one bound to a nested loop. Getting that wrong turns a
    ``for`` into an infinite ``while``, and the oracle would catch it only by
    timing out, so the cheap over-rejection is the right trade.
    """

    class _Finder(cst.CSTVisitor):
        def __init__(self) -> None:
            self.found = False

        def visit_Continue(self, node: cst.Continue) -> None:
            self.found = True

    finder = _Finder()
    node.visit(finder)
    return finder.found


class _ForRangeToWhile(cst.CSTTransformer):
    """``for i in range(n): BODY`` → an explicit ``while`` with a counter.

        _lv0 = 0
        while _lv0 < n:
            i = _lv0
            BODY
            _lv0 += 1

    This exists to close a measured gap. Of the 16 catalogue entries, exactly
    zero could move ``cfg_shape`` on pid_governor or protective_relay: the five
    that can all need patterns (``if a and b``, ternary assignment, nested if)
    those modules do not contain. Every variant accepted for them was therefore
    statement padding with the branching structure untouched — which undercuts
    the central claim that the variant is *structurally* different. Both
    modules do contain ``for ... in range(...)``, and swapping a ``For`` node
    for a ``While`` changes the construct kind that ``cfg_shape`` counts, so
    this registers as real control-flow change rather than rearrangement.

    A first attempt rewrote ``elif`` into ``else: if``. It applied, passed the
    oracle, and scored 0.0000 on every rename-insensitive metric — because in
    Python's AST those two forms are the *same node*. Only the concrete syntax
    differs. Changing the node kind is what actually moves the measurement.

    The counter is a fresh name and the loop variable is assigned from it at
    the top of the body, so the variable holds the same value after the loop as
    it would have under ``for`` (n-1, not n). Rebinding the loop variable inside
    the body behaves identically too, since the next iteration reassigns it.
    """

    def __init__(self) -> None:
        self._done = False
        self._counter = 0

    def leave_For(self, original, updated):
        if self._done or updated.orelse is not None:
            return updated
        if not isinstance(updated.target, cst.Name):
            return updated
        call = updated.iter
        if not (isinstance(call, cst.Call) and isinstance(call.func, cst.Name)
                and call.func.value == "range"):
            return updated
        args = [a for a in call.args if a.keyword is None and a.star == ""]
        if len(args) != len(call.args) or len(args) not in (1, 2):
            return updated          # range(a, b, step) — increment would be wrong
        if not isinstance(updated.body, cst.IndentedBlock):
            return updated
        if _contains_continue(updated.body):
            return updated          # would skip the increment and never terminate

        start = args[0].value if len(args) == 2 else cst.Integer("0")
        stop = args[1].value if len(args) == 2 else args[0].value
        name = f"_lv{self._counter}"
        self._counter += 1
        self._done = True

        assign = cst.SimpleStatementLine(body=[cst.Assign(
            targets=[cst.AssignTarget(target=cst.Name(name))], value=start,
        )])
        bind = cst.SimpleStatementLine(body=[cst.Assign(
            targets=[cst.AssignTarget(target=updated.target)],
            value=cst.Name(name),
        )])
        bump = cst.SimpleStatementLine(body=[cst.AugAssign(
            target=cst.Name(name), operator=cst.AddAssign(), value=cst.Integer("1"),
        )])
        loop = cst.While(
            test=cst.Comparison(
                left=cst.Name(name),
                comparisons=[cst.ComparisonTarget(
                    operator=cst.LessThan(), comparator=stop,
                )],
            ),
            body=updated.body.with_changes(
                body=[bind, *updated.body.body, bump],
            ),
            leading_lines=(),
        )
        return cst.FlattenSentinel([
            assign.with_changes(leading_lines=updated.leading_lines), loop,
        ])


@register(
    "for_range_to_while", GROW,
    "Rewrite 'for i in range(n)' as an explicit while loop with its own "
    "counter, changing the loop construct itself rather than its contents.",
)
def for_range_to_while(source: str) -> str:
    return _rewrite(source, _ForRangeToWhile())


class _InlineSingleUseReturn(cst.CSTTransformer):
    """``t = <expr>`` immediately followed by a return using ``t`` exactly once
    → the return with ``<expr>`` substituted in, one line shorter.

    Inverse of _SplitReturnExpression. The original matched only the exact
    shape ``return t``, which appears nowhere in the ICS corpus — every
    function there ends by building its result inline
    (``rpm_next = ...`` then ``return GovernorState(rpm_next, ...), command``),
    so the rule was dead. Requiring the return to be *nothing but* the
    temporary was stricter than correctness demands.

    Safety conditions, all checked:

    * The two statements are adjacent in the same block, so nothing can run
      between them — folding the value into the return cannot reorder any
      side effect, whatever the value contains.
    * ``t`` occurs exactly once in the return, and nowhere later in the block,
      so the binding really is single-use and removing it loses nothing.
    * No ``global``/``nonlocal`` declares ``t`` in this block; there the
      assignment is observable outside the frame and is not dead.
    * The substituted expression is parenthesised unless it is already an
      atom. ``t * 2`` with ``t = a + b`` must become ``(a + b) * 2``, not
      ``a + b * 2``. Parentheses group; they never reassociate, so IEEE-754
      bit-exactness is preserved (and ``ast.parse`` discards them, so the
      structural metrics are unaffected by the added punctuation).
    """

    _ATOMIC = (
        cst.Name, cst.Attribute, cst.Call, cst.Subscript, cst.Integer,
        cst.Float, cst.Imaginary, cst.SimpleString, cst.ConcatenatedString,
        cst.FormattedString, cst.List, cst.Tuple, cst.Set, cst.Dict,
    )

    @staticmethod
    def _assigned_name(statement) -> str | None:
        if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
            return None
        assign = statement.body[0]
        if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
            return None
        target = assign.targets[0].target
        return target.value if isinstance(target, cst.Name) else None

    @staticmethod
    def _return_value(statement) -> cst.BaseExpression | None:
        if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
            return None
        ret = statement.body[0]
        return ret.value if isinstance(ret, cst.Return) else None

    @classmethod
    def _grouped(cls, value: cst.BaseExpression) -> cst.BaseExpression:
        if isinstance(value, cls._ATOMIC) or value.lpar:
            return value
        return value.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])

    @staticmethod
    def _declared_nonlocal(body, name: str) -> bool:
        for statement in body:
            if not isinstance(statement, cst.SimpleStatementLine):
                continue
            for small in statement.body:
                if isinstance(small, (cst.Global, cst.Nonlocal)):
                    if any(n.name.value == name for n in small.names):
                        return True
        return False

    def leave_IndentedBlock(self, original, updated):
        body = list(updated.body)
        out: list = []
        i = 0
        changed = False
        while i < len(body):
            cur = body[i]
            nxt = body[i + 1] if i + 1 < len(body) else None
            name = self._assigned_name(cur)
            returned = self._return_value(nxt) if nxt is not None else None
            if (
                name is not None
                and returned is not None
                and _count_name(returned, name) == 1
                and _count_name_in_rest(body[i + 2:], name) == 0
                and not self._declared_nonlocal(body, name)
            ):
                value = self._grouped(cur.body[0].value)
                out.append(nxt.with_changes(
                    body=[nxt.body[0].with_changes(
                        value=returned.visit(_ReplaceName(name, value)),
                    )],
                ))
                i += 2
                changed = True
                continue
            out.append(cur)
            i += 1
        return updated.with_changes(body=out) if changed else updated


@register(
    "inline_single_use_return", SHRINK,
    "Fold a temporary assigned immediately before a return, and used exactly "
    "once by it, into the return expression itself.",
)
def inline_single_use_return(source: str) -> str:
    return _rewrite(source, _InlineSingleUseReturn())


# ── NEUTRAL transformations ───────────────────────────────────────────────────

class _InvertIfElse(cst.CSTTransformer):
    """``if c: A else: B`` → ``if not c: B else: A``.

    Structural change at constant size. Guarded to a plain ``else`` (not
    ``elif``), where the swap is unambiguous.
    """

    def leave_If(self, original, updated):
        orelse = updated.orelse
        if not isinstance(orelse, cst.Else):
            return updated
        test = updated.test
        # Avoid double negation stacking on repeated application.
        if isinstance(test, cst.UnaryOperation) and isinstance(test.operator, cst.Not):
            new_test = test.expression
        else:
            new_test = cst.UnaryOperation(
                operator=cst.Not(),
                expression=cst.Name(test.value) if isinstance(test, cst.Name) else
                          test.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()]),
            )
        return updated.with_changes(
            test=new_test, body=orelse.body, orelse=cst.Else(body=updated.body),
        )


@register(
    "invert_if_else", NEUTRAL,
    "Invert an if/else: negate the condition and swap the two branches.",
)
def invert_if_else(source: str) -> str:
    return _rewrite(source, _InvertIfElse())


class _RenameLocals(cst.CSTTransformer):
    """Systematically rename a function's *local variables* within its scope.

    The weakest transformation class in the literature (Auto-SPT Table 5
    shows variable renaming is the one nearly every prior approach uses), and
    deliberately included as a baseline: our structural metrics should be
    largely INSENSITIVE to it, which is the property that lets us claim the
    diversity numbers aren't inflated by cosmetics.

    Scoped to names ASSIGNED IN THE BODY, never to parameters. Parameters
    are part of the public signature and a caller may pass them by keyword —
    the oracle caught exactly that: renaming ``_lcg``'s ``seed`` parameter
    broke the in-module call ``_lcg(seed=...)`` with a TypeError. A local
    variable's name, by contrast, is invisible outside the function, so
    renaming one is safe unconditionally. Attributes, globals, and imported
    names are all left alone for the same reason.
    """

    def __init__(self, prefix: str = "_r") -> None:
        self.prefix = prefix
        self._scope: dict[str, str] = {}
        self._depth = 0

    def visit_FunctionDef(self, node) -> bool:
        self._depth += 1
        if self._depth > 1:  # leave nested functions to their own pass
            return True
        params = {p.name.value for p in node.params.params}
        for p in (node.params.posonly_params + node.params.kwonly_params):
            params.add(p.name.value)
        # ``star_arg``/``star_kwarg`` are MaybeSentinel.DEFAULT when absent, and
        # MaybeSentinel is a truthy enum whose ``.name`` is the string "DEFAULT".
        # The old truthiness test therefore reached ``.name.value`` on a str and
        # raised AttributeError for EVERY function without *args — which
        # ``SPT.__call__`` then swallowed into "not applicable", making this
        # transformation silently dead on all four modules. Test the type.
        if isinstance(node.params.star_arg, cst.Param):
            params.add(node.params.star_arg.name.value)
        if isinstance(node.params.star_kwarg, cst.Param):
            params.add(node.params.star_kwarg.name.value)

        # Collect simple local assignment targets in this function's body.
        assigned: set[str] = set()
        class _Collect(cst.CSTVisitor):
            def visit_Assign(self, n: cst.Assign) -> None:
                for t in n.targets:
                    if isinstance(t.target, cst.Name):
                        assigned.add(t.target.value)
            def visit_AugAssign(self, n: cst.AugAssign) -> None:
                if isinstance(n.target, cst.Name):
                    assigned.add(n.target.value)
        node.body.visit(_Collect())

        renamable = sorted(assigned - params)
        self._scope = {
            name: f"{self.prefix}{i}_{name}" for i, name in enumerate(renamable)
        }
        return True

    def leave_FunctionDef(self, original, updated):
        self._depth -= 1
        if self._depth == 0:
            self._scope = {}
        return updated

    def leave_Name(self, original, updated):
        new = self._scope.get(updated.value)
        return updated.with_changes(value=new) if new else updated

    def leave_Attribute(self, original, updated):
        # ``state.rpm``: the attribute half is not a local binding.
        return updated.with_changes(attr=original.attr)

    def leave_Arg(self, original, updated):
        # Keyword argument NAMES belong to the callee's signature.
        if updated.keyword is not None:
            return updated.with_changes(keyword=original.keyword)
        return updated

    def leave_Param(self, original, updated):
        return updated.with_changes(name=original.name)


@register(
    "rename_locals", NEUTRAL,
    "Systematically rename function-local variables within their own scope, "
    "never parameters (baseline cosmetic transformation).",
)
def rename_locals(source: str) -> str:
    return _rewrite(source, _RenameLocals())


def applicable(source: str, candidates: Iterable[SPT] | None = None) -> list[SPT]:
    """SPTs that actually change ``source`` — Auto-SPT's applicability notion."""
    return [s for s in (candidates or spts()) if s(source) != source]


class _InlineTrivialTemps(cst.CSTTransformer):
    """Inline ``_tmp = <literal>`` back into its single following use.

    A normalisation pass, not a transformation. Argument-extraction rules
    that ask for "anything that isn't already a bare name or literal" treat
    ``-2500.0`` as non-trivial, because a negative number parses as a unary
    minus applied to a constant rather than as a literal. The result was
    lines like::

        _t1 = -2500.0
        integral = clamp(_t0, _t1, 2500.0)

    which is *technically* not dead code — the temporary is used — but it is
    a constant rename, not computation, and it is exactly what a reviewer
    looking for padding would point at. Rejecting the whole candidate would
    also discard the genuinely useful extraction on the line above it, so
    instead the trivial ones are folded back and the substantive ones kept.

    Restricted to a temp assigned a literal (or a negated literal) and used
    exactly once in the immediately following statement, which makes the
    inlining unconditionally safe.
    """

    #: Only names an extraction rule could have introduced. The test used to
    #: be "starts with an underscore", which is what a *temporary* looks like
    #: but not only what a temporary looks like: once ``rename_locals`` was
    #: repaired it began emitting ``_r0_a = 60.0`` for a genuine local, and
    #: the normaliser folded those away — turning a rename-only variant into a
    #: structurally changed one, which silently breaks the invariant that
    #: renaming alone must score exactly 0.0 on the structural metrics.
    #: ``_t`` is the prefix used by the generated ``call_argument_extraction``,
    #: ``_spt_`` by the hand-written extractors, ``_gen_`` by the prefix the
    #: implementation prompt mandates for future generated rules.
    TEMP_PREFIXES = ("_spt_", "_gen_", "_t")

    @staticmethod
    def _is_trivial_value(node) -> bool:
        if isinstance(node, (cst.Integer, cst.Float, cst.SimpleString)):
            return True
        return (
            isinstance(node, cst.UnaryOperation)
            and isinstance(node.operator, (cst.Minus, cst.Plus))
            and isinstance(node.expression, (cst.Integer, cst.Float))
        )

    def leave_IndentedBlock(self, original, updated):
        body = list(updated.body)
        out, index, changed = [], 0, False
        while index < len(body):
            current = body[index]
            following = body[index + 1] if index + 1 < len(body) else None
            name = self._trivial_temp_name(current)
            if name and following is not None:
                uses = _count_name(following, name)
                if uses == 1 and _count_name_in_rest(body[index + 2:], name) == 0:
                    value = current.body[0].value
                    out.append(following.visit(_ReplaceName(name, value)))
                    index += 2
                    changed = True
                    continue
            out.append(current)
            index += 1
        return updated.with_changes(body=out) if changed else updated

    def _trivial_temp_name(self, statement) -> str | None:
        if not isinstance(statement, cst.SimpleStatementLine) or len(statement.body) != 1:
            return None
        assign = statement.body[0]
        if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
            return None
        target = assign.targets[0].target
        if not isinstance(target, cst.Name) or not target.value.startswith(self.TEMP_PREFIXES):
            return None
        return target.value if self._is_trivial_value(assign.value) else None


class _ReplaceName(cst.CSTTransformer):
    def __init__(self, name: str, value: cst.BaseExpression) -> None:
        self.name, self.value = name, value

    def leave_Name(self, original, updated):
        return self.value if updated.value == self.name else updated


def _count_name(node, name: str) -> int:
    class _C(cst.CSTVisitor):
        def __init__(self) -> None:
            self.n = 0

        def visit_Name(self, node: cst.Name) -> None:
            if node.value == name:
                self.n += 1

    visitor = _C()
    node.visit(visitor)
    return visitor.n


def _count_name_in_rest(statements, name: str) -> int:
    return sum(_count_name(s, name) for s in statements)


def normalize(source: str) -> str:
    """Fold away degenerate extractions introduced by a transformation.

    Applied to every candidate in the search so line counts and diversity
    scores reflect substantive restructuring rather than constant renames.
    """
    try:
        return _rewrite(source, _InlineTrivialTemps())
    except Exception:
        return source
