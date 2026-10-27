"""Pluggable diversity metrics for isomorphic variants.

Two properties must be shown at once, and they pull against each other:

  * EQUIVALENCE — the variant behaves identically (``oracle.py`` proves this);
  * DIVERSITY   — the variant is genuinely different (measured here).

Either alone is trivially satisfiable: an exact copy is perfectly equivalent
with zero diversity, and arbitrary text is maximally diverse and broken.
Achieving both simultaneously is the contribution, so diversity has to be a
number rather than an assertion. Baudry et al. (ISSTA 2014) measure
computation diversity for their "sosies"; Galápagos (Ron et al., TOSEM 2025)
reports both static difference and runtime divergence. We follow the same
two-axis shape.

Every metric is registered in ``METRIC_REGISTRY`` and has the same signature,
so adding one requires no change anywhere else — the modularity that lets the
metric set grow as the study develops.

All metrics return a float in [0, 1] where 0 means identical and 1 means
maximally different, so they can be reported side by side.
"""

from __future__ import annotations

import ast
import zlib
from collections import Counter
from dataclasses import dataclass
from typing import Callable


MetricFn = Callable[[str, str], float]
METRIC_REGISTRY: dict[str, "Metric"] = {}


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    rename_sensitive: bool
    compute: MetricFn

    def __call__(self, original: str, variant: str) -> float:
        try:
            return float(self.compute(original, variant))
        except Exception:
            return float("nan")


def register(name: str, description: str, rename_sensitive: bool):
    def _wrap(fn: MetricFn) -> MetricFn:
        METRIC_REGISTRY[name] = Metric(
            name=name, description=description,
            rename_sensitive=rename_sensitive, compute=fn,
        )
        return fn
    return _wrap


# ── Information-theoretic ─────────────────────────────────────────────────────

@register(
    "ncd",
    "Normalized Compression Distance over raw source bytes. Cheap whole-file "
    "summary; NOTE it is rename-sensitive, so it must not be the headline "
    "number on its own.",
    rename_sensitive=True,
)
def normalized_compression_distance(original: str, variant: str) -> float:
    """NCD(x,y) = (C(xy) - min(C(x),C(y))) / max(C(x),C(y))."""
    a, b = original.encode(), variant.encode()
    c = lambda data: len(zlib.compress(data, 9))
    ca, cb, cab = c(a), c(b), c(a + b)
    if max(ca, cb) == 0:
        return 0.0
    return max(0.0, min(1.0, (cab - min(ca, cb)) / max(ca, cb)))


# ── Structural (rename-insensitive) ───────────────────────────────────────────

def _node_sequence(source: str) -> list[str]:
    """Pre-order sequence of AST node types — identifiers deliberately dropped.

    A *sequence* rather than a histogram. A histogram of node types is far
    too insensitive at this scale: extracting a subexpression from an 89-line
    module moves a cosine distance by ~0.0003, which cannot rank anything.
    Order-aware comparison registers the same edit at a usable magnitude,
    while still ignoring every identifier — so the rename-insensitivity that
    makes this metric worth trusting is preserved.
    """
    tree = ast.parse(source)
    out: list[str] = []

    def walk(node) -> None:
        out.append(type(node).__name__)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree)
    return out


@register(
    "ast_structural",
    "Order-aware distance between AST node-type sequences (1 - difflib "
    "similarity ratio). Ignores all identifiers, so pure renaming cannot "
    "inflate it — the property that rebuts the 'you just renamed variables' "
    "objection.",
    rename_sensitive=False,
)
def ast_structural_distance(original: str, variant: str) -> float:
    import difflib

    a, b = _node_sequence(original), _node_sequence(variant)
    if not a and not b:
        return 0.0
    ratio = difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()
    return max(0.0, min(1.0, 1.0 - ratio))


def _du_edges(source: str) -> set[tuple[str, str]]:
    """Define-Use edges as (enclosing function, used-name) pairs.

    Approximates a Define-Use chain without a full dataflow engine: it
    captures which names are read inside which function. Renaming a local
    changes the name, so this IS partially rename-sensitive; it is reported
    alongside ``ast_profile`` rather than instead of it.
    """
    tree = ast.parse(source)
    edges: set[tuple[str, str]] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                edges.add((fn.name, node.id))
            elif isinstance(node, ast.Attribute):
                edges.add((fn.name, node.attr))
    return edges


@register(
    "du_chain",
    "Jaccard distance between Define-Use edge sets (function, used-name).",
    rename_sensitive=True,
)
def du_chain_distance(original: str, variant: str) -> float:
    a, b = _du_edges(original), _du_edges(variant)
    if not (a or b):
        return 0.0
    return 1.0 - len(a & b) / len(a | b)


def _cfg_shape(source: str) -> Counter:
    """Control-flow shape: branch/loop nesting depths, identifier-free."""
    tree = ast.parse(source)
    shape: Counter = Counter()

    def walk(node, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.Try)):
                shape[f"{type(child).__name__}@{depth}"] += 1
                walk(child, depth + 1)
            else:
                walk(child, depth)

    walk(tree, 0)
    return shape


@register(
    "cfg_shape",
    "Jaccard distance over control-flow constructs tagged by nesting depth. "
    "Identifier-free, so branch restructuring registers but renaming does not.",
    rename_sensitive=False,
)
def cfg_shape_distance(original: str, variant: str) -> float:
    a, b = _cfg_shape(original), _cfg_shape(variant)
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    inter = sum(min(a[k], b[k]) for k in keys)
    union = sum(max(a[k], b[k]) for k in keys)
    return 0.0 if union == 0 else 1.0 - inter / union


# ── Composite (rename-insensitive) ────────────────────────────────────────────

#: Weight on ``ast_structural`` in ``composite``; ``cfg_shape`` takes the rest.
COMPOSITE_WEIGHTS = {"ast_structural": 0.5, "cfg_shape": 0.5}


@register(
    "composite",
    "Equal-weight mean of the two rename-insensitive metrics, ast_structural "
    "and cfg_shape. Exists because ast_structural alone under-rewards "
    "control-flow restructuring, which is the diversity that matters most for "
    "a moving-target defence.",
    rename_sensitive=False,
)
def composite_distance(original: str, variant: str) -> float:
    """0.5 * ast_structural + 0.5 * cfg_shape.

    Only the two RENAME-INSENSITIVE metrics are combined, and that choice is
    forced rather than aesthetic:

    * ``ncd`` is excluded because it is nearly redundant with
      ``ast_structural`` (r = +0.967 across the 48-variant sweep) — averaging
      it in would mostly double-count one signal while looking like two.
    * ``du_chain`` is excluded because it is rename-SENSITIVE. Including it
      would hand ``rename_locals`` a non-zero score, and the whole reason the
      ranking signal is trustworthy is that the cheapest transformation in the
      catalogue scores exactly zero on it.

    Equal weights because there is no measurement that would justify picking
    an unequal pair: ``cfg_shape`` is ANTI-correlated with ``ast_structural``
    (r = -0.189), so the two are close to independent and neither has a claim
    to being the more informative half. The two live on different natural
    scales, so a composite value is NOT comparable with a bare
    ``ast_structural`` value — see the ablation note in ``search.py``.
    """
    return (
        COMPOSITE_WEIGHTS["ast_structural"] * ast_structural_distance(original, variant)
        + COMPOSITE_WEIGHTS["cfg_shape"] * cfg_shape_distance(original, variant)
    )


# ── Reporting ─────────────────────────────────────────────────────────────────

#: Ranking signal used when nothing asks for another one. Keeping this at
#: ``ast_structural`` is what makes the composite an ablation rather than a
#: silent change of objective — every previously published number is
#: reproducible by leaving it alone.
DEFAULT_RANKING_METRIC = "ast_structural"

#: Only rename-insensitive metrics may rank. A rename-sensitive ranking signal
#: would let ``rename_locals`` — the cheapest transformation there is — inflate
#: the objective without restructuring anything.
def ranking_metrics() -> list[str]:
    return sorted(n for n, m in METRIC_REGISTRY.items() if not m.rename_sensitive)


def measure_all(original: str, variant: str) -> dict[str, float]:
    """Every registered metric, as a plain dict for telemetry/JSON."""
    return {name: m(original, variant) for name, m in METRIC_REGISTRY.items()}


def headline(original: str, variant: str,
             metric: str = DEFAULT_RANKING_METRIC) -> float:
    """The single rename-insensitive number used to rank candidates.

    The beam search needs one scalar to sort by; it cannot order candidates by
    four numbers at once. Which scalar that is, is now a parameter rather than
    a hard-coded choice, so the alternative can be run as an ablation instead
    of asserted. Every metric registered here is still MEASURED and reported
    on every variant by ``measure_all`` — this function only selects which one
    steers the search.

    ``ast_structural`` remains the default: it is structural and provably
    unaffected by identifier choice, so it cannot be gamed by the cheapest
    transformation in the catalogue.
    """
    if metric not in METRIC_REGISTRY:
        raise KeyError(
            f"unknown ranking metric {metric!r}; registered: "
            f"{', '.join(sorted(METRIC_REGISTRY))}"
        )
    if METRIC_REGISTRY[metric].rename_sensitive:
        raise ValueError(
            f"{metric!r} is rename-sensitive and must not rank candidates — "
            f"it would let pure renaming inflate the objective. "
            f"Rename-insensitive options: {', '.join(ranking_metrics())}"
        )
    return METRIC_REGISTRY[metric](original, variant)
