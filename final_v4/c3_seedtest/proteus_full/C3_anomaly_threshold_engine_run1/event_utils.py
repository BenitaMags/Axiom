"""event_utils -- helpers for constructing anomaly engine events."""
from typing import NamedTuple, Any


class StateTransition(NamedTuple):
    tag: Any
    previous: Any
    current: Any
    value: Any


def build_event(transition: StateTransition) -> dict:
    """Build the public event dict from a state transition."""
    return {
        "tag": transition.tag,
        "state": transition.current,
        "value": transition.value,
    }
