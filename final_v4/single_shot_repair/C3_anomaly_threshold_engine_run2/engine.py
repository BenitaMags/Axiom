"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


def _build_event(tag: str, state: str, value) -> dict:
    return {"tag": tag, "state": state, "value": value}


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        self.detectors = {tag: Hysteresis(c.high, c.low) for tag, c in config.items()}
        self.notifier = notifier
        self.events = []

    def _detect(self, tag: str, value):
        detector = self.detectors.get(tag)
        if detector is None:
            return None
        previous = detector.state
        current = detector.update(value)
        if current == previous:
            return None
        return current

    def _emit(self, tag: str, state: str, value) -> dict:
        event = _build_event(tag, state, value)
        self.events.append(event)
        if self.notifier is not None:
            self.notifier.notify(f"{tag} {state}", str(event))
        return event

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag = reading["tag"]
        value = reading["value"]
        new_state = self._detect(tag, value)
        if new_state is None:
            return None
        return self._emit(tag, new_state, value)

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        return [
            event
            for event in (self.process(reading) for reading in readings)
            if event is not None
        ]
