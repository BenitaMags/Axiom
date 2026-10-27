"""state_engine -- a small generic finite-state transition table executor.

This module provides a reusable, data-driven replacement for a hand-rolled
per-machine transition dispatcher. Callers describe transitions declaratively
via `Transition` records and hand them to `StateEngine`, which resolves the
current state to the appropriate transition, checks guard conditions, applies
the state change, and runs any `after` side-effect callbacks -- all while
preserving the exact matching/priority/exception semantics of the original
hand-rolled engine.
"""
from typing import Any, Callable, Iterable, NamedTuple, Sequence, Union


class StateEngineError(Exception):
    pass


class Transition(NamedTuple):
    trigger: str
    source: Union[str, Sequence[str]]
    dest: str
    conditions: Union[None, str, Sequence[str]] = None
    after: Union[None, str, Sequence[str]] = None


def _as_tuple(value):
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _source_matches(source, current):
    if source == "*":
        return True
    if isinstance(source, (list, tuple)):
        return current in source
    return current == source


class StateEngine:
    """Drives state transitions for `owner` according to a transition table.

    Guard conditions and after-callbacks are resolved as attributes of
    `owner` (methods returning bool for conditions, methods called for
    side effects), mirroring attribute-based dispatch used by transition
    table libraries.
    """

    def __init__(self, owner: Any, initial: str,
                 transitions: Iterable[Transition],
                 error_cls: type = StateEngineError):
        self.owner = owner
        self.error_cls = error_cls
        self._by_trigger = {}
        for t in transitions:
            self._by_trigger.setdefault(t.trigger, []).append(t)
        owner.state = initial

    def fire(self, trigger: str, *args, **kwargs) -> bool:
        specs = self._by_trigger.get(trigger, [])
        current = self.owner.state
        matched_source = False
        for spec in specs:
            if not _source_matches(spec.source, current):
                continue
            matched_source = True
            if self._conditions_pass(spec.conditions):
                self.owner.state = spec.dest
                self._run_after(spec.after)
                return True
        if matched_source:
            return False
        raise self.error_cls(
            "Can't trigger event %s from state %s!" % (trigger, current)
        )

    def _conditions_pass(self, conditions) -> bool:
        for cond_name in _as_tuple(conditions):
            cond_fn: Callable[[], bool] = getattr(self.owner, cond_name)
            if not cond_fn():
                return False
        return True

    def _run_after(self, after) -> None:
        for after_name in _as_tuple(after):
            getattr(self.owner, after_name)()
