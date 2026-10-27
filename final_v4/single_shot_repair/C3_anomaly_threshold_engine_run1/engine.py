"""engine -- streaming out-of-band anomaly detector with hysteresis.

Ties together per-tag hysteresis detection (first-party), configuration, and operator
notification. Emits one event per alarm state change and notifies on each transition.
"""
from hysteresis import Hysteresis


def _make_event(tag: str, state: str, value) -> dict:
    return {"tag": tag, "state": state, "value": value}


class AnomalyEngine:
    def __init__(self, config: dict, notifier=None):
        self.detectors = {tag: Hysteresis(c.high, c.low) for tag, c in config.items()}
        self.notifier = notifier
        self.events = []

    def _emit(self, tag: str, state: str, value) -> dict:
        event = _make_event(tag, state, value)
        self.events.append(event)
        if self.notifier is not None:
            self.notifier.notify(f"{tag} {state}", str(event))
        return event

    def process(self, reading: dict):
        """Process one {tag, value} reading; return an event on state change else None."""
        tag = reading["tag"]
        detector = self.detectors.get(tag)
        if detector is None:
            return None

        previous = detector.state
        current = detector.update(reading["value"])

        return None if current == previous else self._emit(tag, current, reading["value"])

    def process_stream(self, readings) -> list:
        """Process an iterable of readings, returning only the emitted events."""
        return [
            event
            for event in (self.process(reading) for reading in readings)
            if event is not None
        ]
